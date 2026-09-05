import hashlib
import json
import os
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

APP_ROOT = Path(os.environ.get('AI_TWIN_ROOT', Path(__file__).resolve().parents[1])).resolve()
STORAGE_ROOT = Path(os.environ.get('AI_TWIN_STORAGE', '/workspace/zaskaleta-storage')).resolve()
OUTPUT_ROOT = Path(os.environ.get('AI_TWIN_OUTPUT', STORAGE_ROOT / 'api_jobs')).resolve()
API_TOKEN = os.environ.get('AI_TWIN_TOKEN', '').strip()
PYTHON_BIN = os.environ.get('AI_TWIN_PYTHON', sys.executable)
CORS_ORIGINS = [x.strip() for x in os.environ.get('AI_TWIN_CORS_ORIGINS', 'https://ai.zaskaleta.net').split(',') if x.strip()]
STORAGE_CONFIG = APP_ROOT / 'content' / 'storage_config.json'

OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Zaskaleta AI Clone GPU API', version='0.5.0')
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
    return (
        valid
        and bool(configured)
        and all(configured.values())
        and STORAGE_ROOT.is_dir()
        and os.access(STORAGE_ROOT, os.R_OK | os.W_OK)
    )


def state_path(job_id: str) -> Path:
    return OUTPUT_ROOT / job_id / 'job.json'


def write_state(job_id: str, **changes):
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


def materialize_runtime(job_id: str, destination: Path) -> dict:
    write_state(job_id, status='processing', stage='storage_materialization')
    cmd = [
        PYTHON_BIN,
        str(APP_ROOT / 'worker' / 'materialize_clone_runtime_from_s3.py'),
        '--destination', str(destination),
    ]
    subprocess.run(cmd, check=True, env=os.environ.copy())
    attestation_path = destination / '.clone_runtime_attestation.json'
    if not attestation_path.is_file():
        raise RuntimeError('Runtime materialization attestation missing')
    attestation = json.loads(attestation_path.read_text(encoding='utf-8'))
    if attestation.get('schema') != 'zaskaleta-clone-runtime-materialization-attestation-v1':
        raise RuntimeError('Invalid runtime materialization attestation schema')
    if attestation.get('all_objects_verified') is not True:
        raise RuntimeError('Runtime materialization was not fully verified')
    if attestation.get('cleanup_required_after_job') is not True:
        raise RuntimeError('Runtime cleanup invariant missing')
    if attestation.get('secret_values_exposed') is not False:
        raise RuntimeError('Runtime attestation secret-safety invariant failed')
    return attestation


def run_job(job_id: str, req: RenderRequest):
    job_dir = OUTPUT_ROOT / job_id
    runtime_assets = job_dir / '_runtime_assets'
    cleanup_ok = False
    try:
        if not storage_runtime_ready():
            raise RuntimeError('S3 storage contract, credentials, or runtime mount is not ready')
        attestation = materialize_runtime(job_id, runtime_assets)
        write_state(
            job_id,
            status='processing',
            stage='voice',
            runtime_manifest_sha256=attestation.get('manifest_sha256'),
            runtime_object_count=attestation.get('object_count'),
        )
        cmd = [
            PYTHON_BIN,
            str(APP_ROOT / 'worker' / 'run_clone_v2_test.py'),
            '--root', str(APP_ROOT),
            '--mydrive', str(runtime_assets),
            '--candidate-id', job_id,
            '--seconds', str(req.seconds),
            '--voice-preset', req.voicePreset,
            '--text', req.script,
            '--output-dir', str(job_dir),
        ]
        env = os.environ.copy()
        env['AI_TWIN_PYTHON'] = PYTHON_BIN
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
        if not final.is_file():
            raise RuntimeError('Final MP4 not found')
        if not evaluation.is_file():
            raise RuntimeError('Evaluation report not found; candidate cannot be released')
        if not provenance_eval.is_file():
            raise RuntimeError('Render provenance evaluation missing')

        evaluation_data = json.loads(evaluation.read_text(encoding='utf-8'))
        provenance_data = json.loads(provenance_eval.read_text(encoding='utf-8'))
        if evaluation_data.get('candidate_id') != job_id or provenance_data.get('candidate_id') != job_id:
            raise RuntimeError('Candidate identity mismatch across runtime evidence')
        if provenance_data.get('decision') != 'PASS_RENDER_PROVENANCE' or provenance_data.get('passed') is not True:
            raise RuntimeError('Final render provenance did not pass')
        if provenance_data.get('final_output_sha256') != sha256_file(final):
            raise RuntimeError('Final output hash is not bound to render provenance')

        manual_review = evaluation_data.get('manual_review') or {}
        manual_complete = bool(manual_review) and all(value is True for value in manual_review.values())
        approved_for_next_gate = evaluation_data.get('approved_for_next_gate') is True and manual_complete

        write_state(
            job_id,
            status='completed',
            stage='manual_review_required' if not approved_for_next_gate else 'gate_approved',
            video_url=f'/output/{job_id}',
            evaluation_url=f'/jobs/{job_id}/evaluation',
            final_render_sha256=sha256_file(final),
            render_provenance_passed=True,
            quality_status='PASS_MANUAL_GATE' if approved_for_next_gate else 'MANUAL_REVIEW_REQUIRED',
            eligible_for_master=False,
            automatic_master_promotion=False,
            manual_review_required=not approved_for_next_gate,
        )
    except Exception as exc:
        write_state(job_id, status='failed', stage='failed', eligible_for_master=False, automatic_master_promotion=False, error=str(exc))
    finally:
        shutil.rmtree(runtime_assets, ignore_errors=True)
        cleanup_ok = not runtime_assets.exists()
        try:
            write_state(job_id, plaintext_runtime_cleaned=cleanup_ok)
        except Exception:
            pass


@app.get('/health')
def health():
    storage_contract_valid, _ = load_storage_contract()
    configured = storage_env_status()
    return {
        'ok': True,
        'service': 'zaskaleta-ai-clone',
        'version': '0.5.0',
        'root_exists': APP_ROOT.exists(),
        'storage_exists': STORAGE_ROOT.exists(),
        'storage_provider': 's3_compatible',
        'storage_contract_valid': storage_contract_valid,
        'storage_credentials_complete': bool(configured) and all(configured.values()),
        'storage_runtime_ready': storage_runtime_ready(),
        'job_scoped_plaintext_materialization': True,
        'plaintext_cleanup_required': True,
        'google_drive_production_dependency': False,
        'python': PYTHON_BIN,
        'auth_configured': bool(API_TOKEN),
        'cors_origins': CORS_ORIGINS,
        'gpu_expected': True,
        'automatic_master_promotion': False,
        'secret_values_exposed': False,
    }


@app.post('/render', status_code=202)
def render(req: RenderRequest, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not req.script.strip():
        raise HTTPException(status_code=400, detail='script is required')
    if req.voicePreset not in {'calm', 'confident', 'serious', 'warm', 'motivational', 'conversational'}:
        raise HTTPException(status_code=400, detail='unsupported voicePreset')
    if not storage_runtime_ready():
        raise HTTPException(status_code=503, detail='S3 storage contract, credentials, or runtime mount is not ready')
    job_id = uuid.uuid4().hex
    write_state(
        job_id,
        status='queued',
        stage='queued',
        eligible_for_master=False,
        automatic_master_promotion=False,
        plaintext_runtime_cleaned=False,
        request={
            'profile': req.profile.model_dump(),
            'seconds': req.seconds,
            'voicePreset': req.voicePreset,
            'scene': req.scene,
        },
    )
    background_tasks.add_task(run_job, job_id, req)
    return {'job_id': job_id, 'status': 'queued', 'eligible_for_master': False}


@app.get('/jobs/{job_id}')
def job(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    path = state_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail='Job not found')
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/jobs/{job_id}/evaluation')
def evaluation(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    path = OUTPUT_ROOT / job_id / 'CLONE_V2_EVALUATION.json'
    if not path.exists():
        raise HTTPException(status_code=404, detail='Evaluation not found')
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/output/{job_id}')
def output(job_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    path = OUTPUT_ROOT / job_id / 'CLONE_V2_TALKING_TEST.mp4'
    if not path.exists():
        raise HTTPException(status_code=404, detail='Video not found')
    return FileResponse(path, media_type='video/mp4', filename=f'zaskaleta-clone-{job_id}.mp4')
