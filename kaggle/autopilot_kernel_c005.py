#!/usr/bin/env python3
"""Fail-closed Kaggle renderer for MASTER_CLONE_CANDIDATE_005.

C005 is candidate-only. Stable identity remains anchored by the canonical master photo.
The new real C005 videos are used only as runtime motion/talking references in an
isolated Kaggle workspace. No stable package or release is modified.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path('/kaggle/working')
INPUT_ROOT = Path('/kaggle/input')
REPO = WORK / 'zaskaleta-ai-twin-colab'
PY = WORK / 'clone311' / 'bin' / 'python'
STATUS = WORK / 'zaskaleta_c005_status.json'
OUT = WORK / 'c005_first_gate'
CANDIDATE = 'MASTER_CLONE_CANDIDATE_005'
PRIMARY_BEHAVIOR = '54194.mp4'
SUPPORT_BEHAVIOR = '54193.mp4'
LONG_BEHAVIOR = '54197.mp4'
C005_PHOTOS = {'53665.jpg','53666.jpg','53668.jpg','53669.jpg'}


def run(cmd, *, cwd=None, timeout=7200):
    print('$', ' '.join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def find_unique(root: Path, name: str) -> Path:
    matches = [p for p in root.rglob(name) if p.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f'{name} must resolve exactly once; matches={len(matches)}')
    return matches[0]


def resolve_private_root() -> Path:
    preferred = Path(os.environ.get('ZASKALETA_PRIVATE_ASSET_ROOT', '/kaggle/input'))
    roots = [preferred] if preferred.is_dir() else []
    if INPUT_ROOT.is_dir() and INPUT_ROOT not in roots:
        roots.append(INPUT_ROOT)
    for root in roots:
        try:
            find_unique(root, 'Zaskaleta_AI_Voice_Master.mp3')
            find_unique(root, 'MVIMG_20260830_144834.jpg')
            find_unique(root, PRIMARY_BEHAVIOR)
            find_unique(root, SUPPORT_BEHAVIOR)
            find_unique(root, LONG_BEHAVIOR)
            for p in C005_PHOTOS:
                find_unique(root, p)
            return root
        except RuntimeError:
            continue
    raise RuntimeError('private C005 dataset with canonical clone assets is not mounted')


def patch_runtime_motion_policy():
    """Candidate-only runtime override. Never written back to GitHub/Kaggle dataset."""
    package_path = REPO / 'content' / 'master_clone_package.json'
    package = json.loads(package_path.read_text(encoding='utf-8'))
    motion = package['components']['motion']
    policy = motion.get('learning_policy') or {}
    if policy.get('manual_approval_required') is not True or policy.get('approved_motion_only') is not True:
        raise RuntimeError('stable motion approval policy is weakened')
    if package.get('improvement_lifecycle', {}).get('stable_release_is_immutable') is not True:
        raise RuntimeError('stable release immutability policy missing')
    motion['primary_reference'] = PRIMARY_BEHAVIOR
    motion['supporting_reference'] = SUPPORT_BEHAVIOR
    motion['candidate_runtime_only'] = True
    motion['candidate_id'] = CANDIDATE
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def ffprobe_streams(video: Path) -> dict:
    raw = subprocess.check_output([
        'ffprobe','-v','error','-show_entries',
        'format=duration:stream=index,codec_type,duration,nb_frames,avg_frame_rate',
        '-of','json',str(video)
    ], text=True)
    d = json.loads(raw)
    fmt = float((d.get('format') or {}).get('duration') or 0)
    streams = d.get('streams') or []
    vids = [s for s in streams if s.get('codec_type') == 'video']
    auds = [s for s in streams if s.get('codec_type') == 'audio']
    if len(vids) != 1 or not auds:
        raise RuntimeError('C005 output must contain exactly one video stream and at least one audio stream')
    vd = float(vids[0].get('duration') or fmt or 0)
    ad = float(auds[0].get('duration') or fmt or 0)
    frames = int(vids[0].get('nb_frames') or 0)
    if vd < 7.5 or vd > 15.5:
        raise RuntimeError(f'C005 video-stream duration invalid: {vd:.3f}s')
    if frames and frames < 150:
        raise RuntimeError(f'C005 output has too few video frames: {frames}')
    if abs(vd - ad) > 0.40:
        raise RuntimeError(f'C005 A/V duration mismatch: video={vd:.3f}s audio={ad:.3f}s')
    if abs(fmt - vd) > 0.40:
        raise RuntimeError(f'C005 container/video duration mismatch: container={fmt:.3f}s video={vd:.3f}s')
    return {'container_duration': round(fmt,6), 'video_duration': round(vd,6), 'audio_duration': round(ad,6), 'frames': frames}


def write_status(**extra):
    data = {
        'schema':'zaskaleta-c005-kaggle-status-v1',
        'candidate_id':CANDIDATE,
        'auto_promote':False,
        'stable_release_modified':False,
        'paid_gpu_provisioned':False,
        'raw_private_media_exported_to_github':False,
        **extra,
    }
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    render_started = False
    try:
        private_root = resolve_private_root()
        run([sys.executable, REPO / 'kaggle' / 'auto_prepare.py'], cwd=REPO)
        if not PY.is_file():
            raise RuntimeError('clone311 Python missing after preparation')
        run([PY, REPO / 'kaggle' / 'prepare_models.py', '--verify-only'], cwd=REPO)
        run([PY, REPO / 'kaggle' / 'preflight.py'], cwd=REPO)
        patch_runtime_motion_policy()
        OUT.mkdir(parents=True, exist_ok=True)
        render_started = True
        run([
            PY, REPO / 'worker' / 'run_clone_v2_test.py',
            '--root', REPO,
            '--mydrive', private_root,
            '--output-dir', OUT,
            '--candidate-id', CANDIDATE,
            '--seconds', '8.0',
            '--voice-preset', 'conversational',
        ], cwd=REPO, timeout=7200)
        video = OUT / 'CLONE_V2_TALKING_TEST.mp4'
        evaluation = OUT / 'CLONE_V2_EVALUATION.json'
        if not video.is_file() or not evaluation.is_file():
            raise RuntimeError('C005 mandatory review output missing')
        ev = json.loads(evaluation.read_text(encoding='utf-8'))
        if ev.get('candidate_id') != CANDIDATE:
            raise RuntimeError('C005 output candidate binding failed')
        stream_qa = ffprobe_streams(video)
        write_status(
            state='C005_RENDER_READY_FOR_MANUAL_REVIEW',
            render_started=True,
            render_completed=True,
            promotion_allowed=False,
            subjective_identity_review='PENDING_MANUAL_REVIEW',
            behavior_reference=PRIMARY_BEHAVIOR,
            stable_identity_anchor='MVIMG_20260830_144834.jpg',
            output_path=str(video),
            stream_qa=stream_qa,
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
