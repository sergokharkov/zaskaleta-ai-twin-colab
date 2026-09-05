import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

APP_ROOT = Path(os.environ.get('AI_TWIN_ROOT', Path(__file__).resolve().parents[1])).resolve()
STORAGE_ROOT = Path(os.environ.get('AI_TWIN_STORAGE', '/workspace/zaskaleta-storage')).resolve()
OUTPUT_ROOT = Path(os.environ.get('AI_TWIN_OUTPUT', STORAGE_ROOT / 'api_jobs')).resolve()
API_TOKEN = os.environ.get('AI_TWIN_TOKEN', '').strip()
PYTHON_BIN = os.environ.get('AI_TWIN_PYTHON', sys.executable)
CORS_ORIGINS = [x.strip() for x in os.environ.get('AI_TWIN_CORS_ORIGINS', 'https://ai.zaskaleta.net').split(',') if x.strip()]
STORAGE_CONFIG = APP_ROOT / 'content' / 'storage_config.json'
JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')

OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Zaskaleta AI Clone GPU API', version='0.6.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type'],
)


class Profile(BaseModel):
    name: str = Field(default='Zaskaleta AI Clone', max_length=120)
    language: str = Field(default='uk', max_length=16)


class RenderRequest(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    scene: str = Field(default='', max_length=2000)
    script: str = Field(min_length=1, max_length=8000)
    format: str = Field(default='9:16', pattern='^(9:16)$')
    voicePreset: str = 'conversational'
    seconds: float = Field(default=12.0, ge=8.0, le=15.0)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail='Job not found')
    return job_id


def auth(authorization: str | None):
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail='AI_TWIN_TOKEN is not configured')
    expected = f'Bearer {API_TOKEN}'
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail='Unauthorized')


def load_storage_contract() -> tuple[bool, dict]:
    try:
        cfg = json.loads(STORAGE_CONFIG.read_text(encoding='utf-8'))
    except Exception:
        return False, {}
    canonical = cfg.get('canonical_storage') or {}
    runtime = cfg.get('runtime') or {}
    legacy = cfg.get('legacy_source_import') or {}
    migration = cfg.get('legacy_canonical_migration') or {}
    valid = (
        cfg.get('schema') == 'zaskaleta-storage-v2'
        and canonical.get('provider') == 's3_compatible'
        and canonical.get('required_region_policy') == 'EU_ONLY'
        and canonical.get('versioning_required') is True
        and canonical.get('required_client_side_encryption_for_biometrics') is True
        and isinstance(canonical.get('migration_manifest_key'), str)
        and bool(canonical.get('migration_manifest_key'))
        and isinstance(canonical.get('job_artifact_prefix'), str)
        and bool(canonical.get('job_artifact_prefix'))
        and runtime.get('job_scoped_plaintext_materialization_required') is True
        and runtime.get('runtime_attestation_required') is True
        and runtime.get('delete_temporary_plaintext_after_job') is True
        and legacy.get('production_dependency') is False
        and migration.get('production_dependency') is False
    )
    return valid, cfg


def storage_env_status() -> dict[str, bool]:
    valid, cfg = load_storage_contract()
    if not valid:
        return {}
    canonical = cfg['canonical_storage']
    encryption = cfg['encryption']
    env_names = {
        'bucket': canonical.get('bucket_env'),
        'endpoint': canonical.get('endpoint_env'),
        'region': canonical.get('region_env'),
        'access_key': canonical.get('access_key_env'),
        'secret_key': canonical.get('secret_key_env'),
        'encryption_key': encryption.get('key_env'),
    }
    return {key: bool(name and os.environ.get(name, '').strip()) for key, name in env_names.items()}


def storage_runtime_ready() -> bool:
    valid, _ = load_storage_contract()
    configured = storage_env_status()
    return valid and bool(configured) and all(configured.values()) and STORAGE_ROOT.is_dir() and os.access(STORAGE_ROOT, os.R_OK | os.W_OK)


def state_path(job_id: str) -> Path:
    return OUTPUT_ROOT / job_id / 'job.json'


def read_state(job_id: str) -> dict:
    validate_job_id(job_id)
    path = state_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail='Job not found')
    return json.loads(path.read_text(encoding='utf-8'))


def write_state(job_id: str, **changes):
    if not JOB_ID_RE.fullmatch(job_id):
        raise RuntimeError('Invalid internal job id')
    folder = OUTPUT_ROOT / job_id
    folder.mkdir(parents=True, exist_ok=True)
    path = state_path(job_id)
    state = {'job_id': job_id, 'status': 'queued', 'stage': 'queued'}
    if path.exists():
        try:
            state.update(json.loads(path.read_text(encoding='utf-8')))
        except Exception:
            pass
    state.update(changes)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=folder, delete=False) as tmp:
        json.dump(state, tmp, ensure_ascii=False, indent=2)
        tmp.write('\n')
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def cleanup_job_plaintext(job_dir: Path, keep_state: bool = True) -> bool:
    if not job_dir.exists():
        return True
    for child in list(job_dir.iterdir()):
        if keep_state and child.name == 'job.json':
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                pass
    remaining = [p.name for p in job_dir.iterdir() if not (keep_state and p.name == 'job.json')]
    return not remaining


def materialize_runtime(job_id: str, destination: Path) -> dict:
    write_state(job_id, status='processing', stage='storage_materialization')
    subprocess.run([
        PYTHON_BIN, str(APP_ROOT / 'worker' / 'materialize_clone_runtime_from_s3.py'),
        '--destination', str(destination),
    ], check=True, env=os.environ.copy())
    attestation_path = destination / '.clone_runtime_attestation.json'
    if not attestation_path.is_file():
        raise RuntimeError('Runtime materialization attestation missing')
    attestation = json.loads(attestation_path.read_text(encoding='utf-8'))
    if attestation.get('schema') != 'zaskaleta-clone-runtime-materialization-attestation-v1':
        raise RuntimeError('Invalid runtime materialization attestation schema')
    if attestation.get('all_objects_verified') is not True or attestation.get('cleanup_required_after_job') is not True:
        raise RuntimeError('Runtime materialization safety invariant failed')
    if attestation.get('secret_values_exposed') is not False:
        raise RuntimeError('Runtime attestation secret-safety invariant failed')
    return attestation


def persist_job_artifacts(job_id: str, job_dir: Path) -> dict:
    manifest_out = job_dir / 'CLONE_V2_JOB_ARTIFACT_MANIFEST.json'
    subprocess.run([
        PYTHON_BIN, str(APP_ROOT / 'worker' / 'clone_job_artifact_store.py'), 'persist',
        '--job-dir', str(job_dir), '--candidate-id', job_id, '--manifest-out', str(manifest_out),
    ], check=True, env=os.environ.copy())
    manifest = json.loads(manifest_out.read_text(encoding='utf-8'))
    if manifest.get('schema') != 'zaskaleta-clone-job-artifact-manifest-v1':
        raise RuntimeError('Invalid persisted artifact manifest')
    if manifest.get('candidate_id') != job_id or manifest.get('all_artifacts_verified') is not True:
        raise RuntimeError('Persisted artifact manifest failed validation')
    if manifest.get('secret_values_exposed') is not False:
        raise RuntimeError('Persisted artifact secret-safety invariant failed')
    _, cfg = load_storage_contract()
    manifest_key = cfg['canonical_storage']['job_artifact_prefix'].rstrip('/') + '/' + job_id + '/artifact_manifest_v1.json'
    return {'manifest_key': manifest_key, 'artifact_count': manifest.get('artifact_count')}


def restore_artifact(job_id: str, relative_path: str, suffix: str) -> Path:
    state = read_state(job_id)
    if state.get('artifacts_persisted_encrypted') is not True:
        raise HTTPException(status_code=409, detail='Encrypted job artifacts are not available')
    with tempfile.NamedTemporaryFile(prefix=f'zaskaleta-{job_id}-', suffix=suffix, delete=False) as tf:
        temp_path = Path(tf.name)
    try:
        subprocess.run([
            PYTHON_BIN, str(APP_ROOT / 'worker' / 'clone_job_artifact_store.py'), 'restore',
            '--candidate-id', job_id, '--relative-path', relative_path, '--output', str(temp_path),
        ], check=True, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail='Encrypted artifact restore failed')


def run_job(job_id: str, req: RenderRequest):
    job_dir = OUTPUT_ROOT / job_id
    runtime_assets = job_dir / '_runtime_assets'
    try:
        if not storage_runtime_ready():
            raise RuntimeError('S3 storage contract, credentials, or runtime mount is not ready')
        attestation = materialize_runtime(job_id, runtime_assets)
        write_state(job_id, status='processing', stage='voice', runtime_manifest_sha256=attestation.get('manifest_sha256'), runtime_object_count=attestation.get('object_count'))
        cmd = [
            PYTHON_BIN, str(APP_ROOT / 'worker' / 'run_clone_v2_test.py'),
            '--root', str(APP_ROOT), '--mydrive', str(runtime_assets), '--candidate-id', job_id,
            '--seconds', str(req.seconds), '--voice-preset', req.voicePreset, '--text', req.script,
            '--output-dir', str(job_dir),
        ]
        env = os.environ.copy(); env['AI_TWIN_PYTHON'] = PYTHON_BIN
        log = job_dir / 'render.log'
        with log.open('w', encoding='utf-8') as fh:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env)
            write_state(job_id, status='processing', stage='lipsync', pid=proc.pid)
            code = proc.wait()
        if code != 0:
            raise RuntimeError(f'Clone pipeline exited with code {code}')

        final = job_dir / 'CLONE_V2_TALKING_TEST.mp4'
        evaluation = job_dir / 'CLONE_V2_EVALUATION.json'
        provenance_eval = job_dir / 'CLONE_V2_RENDER_PROVENANCE_EVALUATION.json'
        if not final.is_file() or not evaluation.is_file() or not provenance_eval.is_file():
            raise RuntimeError('Mandatory render output/evidence missing')
        evaluation_data = json.loads(evaluation.read_text(encoding='utf-8'))
        provenance_data = json.loads(provenance_eval.read_text(encoding='utf-8'))
        if evaluation_data.get('candidate_id') != job_id or provenance_data.get('candidate_id') != job_id:
            raise RuntimeError('Candidate identity mismatch across runtime evidence')
        if provenance_data.get('decision') != 'PASS_RENDER_PROVENANCE' or provenance_data.get('passed') is not True:
            raise RuntimeError('Final render provenance did not pass')
        final_sha = sha256_file(final)
        if provenance_data.get('final_output_sha256') != final_sha:
            raise RuntimeError('Final output hash is not bound to render provenance')

        manual_review = evaluation_data.get('manual_review') or {}
        manual_complete = bool(manual_review) and all(value is True for value in manual_review.values())
        approved_for_next_gate = evaluation_data.get('approved_for_next_gate') is True and manual_complete

        write_state(job_id, status='processing', stage='encrypted_artifact_persistence', final_render_sha256=final_sha, render_provenance_passed=True)
        persisted = persist_job_artifacts(job_id, job_dir)
        write_state(
            job_id,
            status='completed', stage='manual_review_required' if not approved_for_next_gate else 'gate_approved',
            video_url=f'/output/{job_id}', evaluation_url=f'/jobs/{job_id}/evaluation',
            artifact_manifest_key=persisted['manifest_key'], artifact_count=persisted['artifact_count'],
            artifacts_persisted_encrypted=True, final_render_sha256=final_sha, render_provenance_passed=True,
            quality_status='PASS_MANUAL_GATE' if approved_for_next_gate else 'MANUAL_REVIEW_REQUIRED',
            eligible_for_master=False, automatic_master_promotion=False, manual_review_required=not approved_for_next_gate,
            plaintext_runtime_cleaned=False, plaintext_job_artifacts_cleaned=False,
        )
        runtime_cleaned = not runtime_assets.exists() or (shutil.rmtree(runtime_assets, ignore_errors=True) is None and not runtime_assets.exists())
        job_plaintext_cleaned = cleanup_job_plaintext(job_dir, keep_state=True)
        write_state(job_id, plaintext_runtime_cleaned=runtime_cleaned, plaintext_job_artifacts_cleaned=job_plaintext_cleaned)
    except Exception as exc:
        shutil.rmtree(runtime_assets, ignore_errors=True)
        cleanup_ok = cleanup_job_plaintext(job_dir, keep_state=True)
        write_state(
            job_id, status='failed', stage='failed', eligible_for_master=False, automatic_master_promotion=False,
            error_code=type(exc).__name__, error='Clone job failed before release eligibility.',
            plaintext_runtime_cleaned=True, plaintext_job_artifacts_cleaned=cleanup_ok,
        )


@app.get('/health')
def health():
    storage_contract_valid, _ = load_storage_contract(); configured = storage_env_status()
    return {
        'ok': True, 'service': 'zaskaleta-ai-clone', 'version': '0.6.0',
        'root_exists': APP_ROOT.exists(), 'storage_exists': STORAGE_ROOT.exists(),
        'storage_provider': 's3_compatible', 'storage_contract_valid': storage_contract_valid,
        'storage_credentials_complete': bool(configured) and all(configured.values()),
        'storage_runtime_ready': storage_runtime_ready(), 'job_scoped_plaintext_materialization': True,
        'encrypted_job_artifact_persistence': True, 'plaintext_cleanup_required': True,
        'google_drive_production_dependency': False, 'python': PYTHON_BIN, 'auth_configured': bool(API_TOKEN),
        'cors_origins': CORS_ORIGINS, 'gpu_expected': True, 'automatic_master_promotion': False,
        'secret_values_exposed': False,
    }


@app.post('/render', status_code=202)
def render(req: RenderRequest, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not req.script.strip(): raise HTTPException(status_code=400, detail='script is required')
    if req.voicePreset not in {'calm', 'confident', 'serious', 'warm', 'motivational', 'conversational'}: raise HTTPException(status_code=400, detail='unsupported voicePreset')
    if not storage_runtime_ready(): raise HTTPException(status_code=503, detail='S3 storage contract, credentials, or runtime mount is not ready')
    job_id = uuid.uuid4().hex
    write_state(job_id, status='queued', stage='queued', eligible_for_master=False, automatic_master_promotion=False, artifacts_persisted_encrypted=False, plaintext_runtime_cleaned=False, plaintext_job_artifacts_cleaned=False, request={'profile': req.profile.model_dump(), 'seconds': req.seconds, 'voicePreset': req.voicePreset, 'scene': req.scene})
    background_tasks.add_task(run_job, job_id, req)
    return {'job_id': job_id, 'status': 'queued', 'eligible_for_master': False}


@app.get('/jobs/{job_id}')
def job(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization); return read_state(job_id)


@app.get('/jobs/{job_id}/evaluation')
def evaluation(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization); validate_job_id(job_id)
    path = restore_artifact(job_id, 'CLONE_V2_EVALUATION.json', '.json')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    finally:
        path.unlink(missing_ok=True)


def _delete_temp(path: Path):
    path.unlink(missing_ok=True)


@app.get('/output/{job_id}')
def output(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization); validate_job_id(job_id)
    path = restore_artifact(job_id, 'CLONE_V2_TALKING_TEST.mp4', '.mp4')
    return FileResponse(path, media_type='video/mp4', filename=f'zaskaleta-clone-{job_id}.mp4', background=BackgroundTask(_delete_temp, path))
