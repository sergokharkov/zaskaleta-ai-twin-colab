import asyncio
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

APP_ROOT = Path(os.environ.get('AI_TWIN_ROOT', Path(__file__).resolve().parents[1])).resolve()
STORAGE_ROOT = Path(os.environ.get('AI_TWIN_STORAGE', '/workspace/zaskaleta-storage')).resolve()
OUTPUT_ROOT = Path(os.environ.get('AI_TWIN_OUTPUT', STORAGE_ROOT / 'api_jobs')).resolve()
API_TOKEN = os.environ.get('AI_TWIN_TOKEN', '').strip()
PYTHON_BIN = os.environ.get('AI_TWIN_PYTHON', sys.executable)

OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Zaskaleta AI Clone GPU API', version='0.1.0')


class Profile(BaseModel):
    name: str = 'Zaskaleta AI Clone'
    language: str = 'uk'


class RenderRequest(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    scene: str = ''
    script: str
    format: str = '9:16'
    voicePreset: str = 'conversational'
    seconds: float = 12.0
    photo: str | None = None
    voice: str | None = None
    referenceVideo: str | None = None


def auth(authorization: str | None):
    if not API_TOKEN:
        return
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
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def run_job(job_id: str, req: RenderRequest):
    job_dir = OUTPUT_ROOT / job_id
    try:
        write_state(job_id, status='processing', stage='voice')
        cmd = [
            PYTHON_BIN,
            str(APP_ROOT / 'worker' / 'run_clone_v2_test.py'),
            '--root', str(APP_ROOT),
            '--mydrive', str(STORAGE_ROOT),
            '--seconds', str(max(8.0, min(15.0, req.seconds))),
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
        write_state(
            job_id,
            status='completed',
            stage='completed',
            video_url=f'/output/{job_id}',
            evaluation_url=f'/jobs/{job_id}/evaluation' if evaluation.exists() else None,
        )
    except Exception as exc:
        write_state(job_id, status='failed', stage='failed', error=str(exc))


@app.get('/health')
def health():
    return {
        'ok': True,
        'service': 'zaskaleta-ai-clone',
        'root_exists': APP_ROOT.exists(),
        'storage_exists': STORAGE_ROOT.exists(),
        'python': PYTHON_BIN,
        'gpu_expected': True,
    }


@app.post('/render', status_code=202)
def render(req: RenderRequest, background_tasks: BackgroundTasks, authorization: str | None = Header(default=None)):
    auth(authorization)
    if not req.script.strip():
        raise HTTPException(status_code=400, detail='script is required')
    job_id = uuid.uuid4().hex
    write_state(job_id, status='queued', stage='queued', request={'profile': req.profile.model_dump(), 'seconds': req.seconds, 'voicePreset': req.voicePreset, 'scene': req.scene})
    background_tasks.add_task(run_job, job_id, req)
    return {'job_id': job_id, 'status': 'queued'}


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
