import json
import os
import secrets
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
CANONICAL_DRIVE_FOLDER_ID = os.environ.get('AI_TWIN_DRIVE_FOLDER_ID', '1_7G-rAGQ80Vpe_CWdGOzPIg0nuprDp3s').strip()
DRIVE_SYNC_ENABLED = os.environ.get('AI_TWIN_DRIVE_SYNC', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
CORS_ORIGINS = [x.strip() for x in os.environ.get('AI_TWIN_CORS_ORIGINS', 'https://ai.zaskaleta.net').split(',') if x.strip()]

OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Zaskaleta AI Clone GPU API', version='0.3.0')
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
    photo: str | None = None
    voice: str | None = None
    referenceVideo: str | None = None


def auth(authorization: str | None):
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail='AI_TWIN_TOKEN is not configured')
    expected = f'Bearer {API_TOKEN}'
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail='Unauthorized')


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


def pull_canonical_drive(job_id: str):
    if not DRIVE_SYNC_ENABLED:
        return
    write_state(job_id, status='processing', stage='storage_sync')
    cmd = [
        PYTHON_BIN,
        str(APP_ROOT / 'worker' / 'fixed_drive_folder_sync.py'),
        'pull',
        '--folder-id', CANONICAL_DRIVE_FOLDER_ID,
        '--local-dir', str(STORAGE_ROOT),
    ]
    subprocess.run(cmd, check=True, env=os.environ.copy())


def run_job(job_id: str, req: RenderRequest):
    job_dir = OUTPUT_ROOT / job_id
    try:
        pull_canonical_drive(job_id)
        write_state(job_id, status='processing', stage='voice')
        cmd = [
            PYTHON_BIN,
            str(APP_ROOT / 'worker' / 'run_clone_v2_test.py'),
            '--root', str(APP_ROOT),
            '--mydrive', str(STORAGE_ROOT),
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
        if not final.is_file():
            raise RuntimeError('Final MP4 not found')
        if not evaluation.is_file():
            raise RuntimeError('Evaluation report not found; candidate cannot be released')

        evaluation_data = json.loads(evaluation.read_text(encoding='utf-8'))
        manual_review = evaluation_data.get('manual_review') or {}
        manual_complete = bool(manual_review) and all(value is True for value in manual_review.values())
        approved_for_next_gate = evaluation_data.get('approved_for_next_gate') is True and manual_complete

        write_state(
            job_id,
            status='completed',
            stage='manual_review_required' if not approved_for_next_gate else 'gate_approved',
            video_url=f'/output/{job_id}',
            evaluation_url=f'/jobs/{job_id}/evaluation',
            quality_status='PASS_MANUAL_GATE' if approved_for_next_gate else 'MANUAL_REVIEW_REQUIRED',
            eligible_for_master=False,
            automatic_master_promotion=False,
            manual_review_required=not approved_for_next_gate,
        )
    except Exception as exc:
        write_state(job_id, status='failed', stage='failed', eligible_for_master=False, error=str(exc))


@app.get('/health')
def health():
    return {
        'ok': True,
        'service': 'zaskaleta-ai-clone',
        'version': '0.3.0',
        'root_exists': APP_ROOT.exists(),
        'storage_exists': STORAGE_ROOT.exists(),
        'python': PYTHON_BIN,
        'auth_configured': bool(API_TOKEN),
        'drive_sync_enabled': DRIVE_SYNC_ENABLED,
        'canonical_drive_folder_configured': bool(CANONICAL_DRIVE_FOLDER_ID),
        'cors_origins': CORS_ORIGINS,
        'gpu_expected': True,
        'automatic_master_promotion': False,
    }


@app.post('/render', status_code=202)
def render(req: RenderRequest, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not req.script.strip():
        raise HTTPException(status_code=400, detail='script is required')
    if req.voicePreset not in {'calm', 'confident', 'serious', 'warm', 'motivational', 'conversational'}:
        raise HTTPException(status_code=400, detail='unsupported voicePreset')
    job_id = uuid.uuid4().hex
    write_state(
        job_id,
        status='queued',
        stage='queued',
        eligible_for_master=False,
        automatic_master_promotion=False,
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
