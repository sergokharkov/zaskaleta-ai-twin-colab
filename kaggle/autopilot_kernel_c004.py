#!/usr/bin/env python3
"""Fail-closed Kaggle entrypoint for MASTER CLONE CANDIDATE_004.

C004 is candidate-only. The renderer starts only when the private source, explicit
manual approval record, and two-pass CPU QA record all match the public selection.
Stable assets are never overwritten and no candidate is auto-promoted.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

WORK = Path('/kaggle/working')
INPUT_ROOT = Path('/kaggle/input')
REPO = WORK / 'zaskaleta-ai-twin-colab'
REPO_URL = 'https://github.com/sergokharkov/zaskaleta-ai-twin-colab.git'
PY = WORK / 'clone311' / 'bin' / 'python'
STATUS = WORK / 'zaskaleta_c004_status.json'
OUT = WORK / 'c004_first_gate'
CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_004'
SOURCE = 'C004_SOURCE_50380_FACE_MOTION_8_25_CROPPED.mp4'
SEGMENT = '50380_face_motion_25_32_5_stretched_8_24'
REQUIRED_MARKERS = {
    'Zaskaleta_AI_Voice_Master.mp3',
    'MASTER_BEHAVIOR_01.mp4',
    'MASTER_BEHAVIOR_02.mp4',
    SOURCE,
}


def run(cmd, *, cwd=None, timeout=7200):
    print('$', ' '.join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def has_markers(root: Path) -> bool:
    if not root.is_dir():
        return False
    names = {p.name for p in root.rglob('*') if p.is_file() and p.name in REQUIRED_MARKERS}
    return REQUIRED_MARKERS.issubset(names)


def zip_has_markers(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            names = {Path(x).name for x in zf.namelist() if not x.endswith('/')}
        return REQUIRED_MARKERS.issubset(names)
    except (OSError, zipfile.BadZipFile):
        return False


def resolve_private_root() -> Path | None:
    preferred = Path(os.environ.get('ZASKALETA_PRIVATE_ASSET_ROOT', '/kaggle/input/zaskaleta-master-clone-private'))
    if has_markers(preferred):
        return preferred
    if not INPUT_ROOT.is_dir():
        return None
    for p in sorted((x for x in INPUT_ROOT.iterdir() if x.is_dir()), key=lambda x: x.name):
        if has_markers(p):
            return p
    for z in sorted(INPUT_ROOT.rglob('*.zip')):
        if not z.is_file() or not zip_has_markers(z):
            continue
        dst = WORK / '_c004_private_assets'
        shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dst)
        if has_markers(dst):
            return dst
    return None


def unique(root: Path, name: str) -> Path:
    matches = [p for p in root.rglob(name) if p.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f'{name} must resolve exactly once; matches={len(matches)}')
    return matches[0]


def write_status(**extra):
    data = {
        'schema': 'zaskaleta-c004-kaggle-status-v1',
        'candidate_id': CANDIDATE,
        'auto_promote': False,
        'stable_release_modified': False,
        'paid_gpu_provisioned': False,
        'raw_private_media_exported_to_github': False,
        **extra,
    }
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def ensure_repo():
    if (REPO / '.git').is_dir():
        run(['git', '-C', REPO, 'fetch', 'origin', 'main', '--depth', '1'])
        run(['git', '-C', REPO, 'reset', '--hard', 'origin/main'])
        return
    if REPO.exists():
        shutil.rmtree(REPO)
    run(['git', 'clone', '--depth', '1', REPO_URL, REPO])


def prepare_runtime_manifest(private_root: Path) -> tuple[Path, Path, Path]:
    public = json.loads((REPO / 'content' / 'c004_source_manifest_v1.json').read_text(encoding='utf-8'))
    s = public.get('source_selection') or {}
    if public.get('candidate_id') != CANDIDATE or s.get('source_segment_id') != SEGMENT or s.get('source_path') != SOURCE:
        raise RuntimeError('public C004 source selection does not match the pinned C004 runtime')
    approval = unique(private_root, 'c004_private_approval.json')
    qa = unique(private_root, 'c004_cpu_qa.json')
    a = json.loads(approval.read_text(encoding='utf-8'))
    if a.get('approved') is not True:
        raise RuntimeError('manual C004 approval is absent')
    runtime = json.loads(json.dumps(public))
    runtime['status'] = 'APPROVED_FOR_CPU_QA'
    rs = runtime['source_selection']
    rs['approval_record'] = a.get('approval_record')
    rs['source_authenticity_verified'] = a.get('source_authenticity_verified') is True
    rs['approved_for_motion'] = a.get('approved_for_motion') is True
    rs['approved_for_talking'] = a.get('approved_for_talking') is True
    runtime_path = WORK / 'c004_runtime_manifest.json'
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    priors = WORK / 'c004_prior_inventory.json'
    priors.write_text(json.dumps([
        'MASTER_CLONE_GATE_08_15_CANDIDATE_001',
        'MASTER_CLONE_GATE_08_15_CANDIDATE_002',
        'MASTER_CLONE_GATE_08_15_CANDIDATE_003',
    ], indent=2) + '\n', encoding='utf-8')
    return runtime_path, approval, qa


def main() -> int:
    private_root = None
    render_started = False
    try:
        ensure_repo()
        run([sys.executable, REPO / 'kaggle' / 'auto_prepare.py'], cwd=REPO)
        if not PY.is_file():
            raise RuntimeError('clone311 Python missing after preparation')
        run([PY, REPO / 'kaggle' / 'prepare_models.py', '--verify-only'], cwd=REPO)
        run([PY, REPO / 'kaggle' / 'preflight.py'], cwd=REPO)
        private_root = resolve_private_root()
        if private_root is None:
            raise RuntimeError('private C004 dataset is not mounted')
        unique(private_root, SOURCE)
        runtime_manifest, approval, qa = prepare_runtime_manifest(private_root)
        OUT.mkdir(parents=True, exist_ok=True)
        render_started = True
        run([
            PY, REPO / 'worker' / 'run_c004_authorized.py',
            '--root', REPO,
            '--mydrive', private_root,
            '--manifest', runtime_manifest,
            '--approval', approval,
            '--qa', qa,
            '--prior-inventory', WORK / 'c004_prior_inventory.json',
            '--output-dir', OUT,
            '--seconds', '8.0',
            '--voice-preset', 'conversational',
        ], cwd=REPO, timeout=7200)
        video = OUT / 'CLONE_V2_TALKING_TEST.mp4'
        evaluation = OUT / 'CLONE_V2_EVALUATION.json'
        if not video.is_file() or not evaluation.is_file():
            raise RuntimeError('C004 mandatory review output missing')
        ev = json.loads(evaluation.read_text(encoding='utf-8'))
        if ev.get('candidate_id') != CANDIDATE:
            raise RuntimeError('C004 output candidate binding failed')
        write_status(
            state='C004_RENDER_READY_FOR_MANUAL_REVIEW',
            render_started=True,
            render_completed=True,
            source_segment_id=SEGMENT,
            promotion_allowed=False,
            subjective_identity_review='PENDING_MANUAL_REVIEW',
            output_path=str(video),
        )
        return 0
    except Exception as exc:
        write_status(
            state='FAILED_CLOSED',
            render_started=render_started,
            render_completed=False,
            promotion_allowed=False,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
