#!/usr/bin/env python3
"""Validate mounted private MASTER CLONE assets without rendering or exposing them.

The validator is intentionally fail-closed. It requires exact, unique filenames for
canonical identity, all support identity photos, the master voice, and explicitly
approved MASTER_BEHAVIOR references. It writes only a local manifest under
/kaggle/working and never uploads raw biometric media or hashes to GitHub.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def unique_exact(root: Path, filename: str) -> Path:
    matches = [p for p in root.rglob(filename) if p.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f'exact asset {filename!r} must resolve uniquely; matches={len(matches)}')
    return matches[0]


def ffprobe_ok(path: Path, kind: str) -> dict:
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels',
        '-of', 'json', str(path)
    ]
    cp = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    data = json.loads(cp.stdout or '{}')
    streams = data.get('streams') or []
    if kind == 'audio' and not any(s.get('codec_type') == 'audio' for s in streams):
        raise RuntimeError(f'no audio stream in {path.name}')
    if kind == 'video' and not any(s.get('codec_type') == 'video' for s in streams):
        raise RuntimeError(f'no video stream in {path.name}')
    return data


def image_ok(path: Path) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        w, h = im.size
        mode = im.mode
    if w < 512 or h < 512:
        raise RuntimeError(f'image too small for master reference: {path.name} {w}x{h}')
    return {'width': w, 'height': h, 'mode': mode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--profile', default='content/clone_reference_profile.json')
    ap.add_argument('--package', default='content/master_clone_package.json')
    ap.add_argument('--output', default='/kaggle/working/private_asset_manifest.json')
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f'private asset root missing: {root}')

    profile = json.loads(Path(args.profile).read_text(encoding='utf-8'))
    package = json.loads(Path(args.package).read_text(encoding='utf-8'))

    identity_names = list(profile.get('photo_filenames') or [])
    if len(identity_names) != int(profile.get('master_photo_count', 0)) or len(identity_names) < 6:
        raise RuntimeError('identity photo policy malformed or incomplete')

    canonical = profile.get('canonical_identity_photo')
    if not canonical or canonical not in identity_names:
        raise RuntimeError('canonical identity photo is not explicitly present in approved photo list')

    motion = ((package.get('components') or {}).get('motion') or {})
    learning = motion.get('learning_policy') or {}
    if learning.get('manual_approval_required') is not True or learning.get('approved_motion_only') is not True:
        raise RuntimeError('motion approval policy weakened')
    approved_motion = [x for x in [motion.get('primary_reference'), motion.get('supporting_reference')] if isinstance(x, str) and x]
    if len(approved_motion) < 2:
        raise RuntimeError('approved motion references incomplete')

    voice_name = profile.get('master_voice_filename')
    if not isinstance(voice_name, str) or not voice_name:
        raise RuntimeError('master voice filename missing')

    records = []
    for name in identity_names:
        p = unique_exact(root, name)
        meta = image_ok(p)
        records.append({'role': 'canonical_identity' if name == canonical else 'support_identity', 'filename': name, 'size': p.stat().st_size, 'sha256': sha256_file(p), 'media': meta})

    voice = unique_exact(root, voice_name)
    voice_meta = ffprobe_ok(voice, 'audio')
    records.append({'role': 'master_voice', 'filename': voice_name, 'size': voice.stat().st_size, 'sha256': sha256_file(voice), 'media': voice_meta})

    for idx, name in enumerate(approved_motion):
        p = unique_exact(root, name)
        video_meta = ffprobe_ok(p, 'video')
        records.append({'role': 'primary_motion' if idx == 0 else 'support_motion', 'filename': name, 'size': p.stat().st_size, 'sha256': sha256_file(p), 'media': video_meta})

    # Newly named MASTER_BEHAVIOR_* files never gain approval by discovery.
    discovered = {p.name for p in root.rglob('MASTER_BEHAVIOR_*.mp4') if p.is_file()}
    quarantined = sorted(discovered - set(approved_motion))

    out = {
        'schema': 'zaskaleta-private-asset-manifest-v1',
        'validated': True,
        'canonical_identity': canonical,
        'identity_count': len(identity_names),
        'voice_present': True,
        'approved_motion_count': len(approved_motion),
        'quarantined_motion_candidates': quarantined,
        'auto_promote': False,
        'render_started': False,
        'records': records,
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    # Console output avoids paths/hashes/raw metadata.
    print(json.dumps({
        'schema': out['schema'],
        'validated': True,
        'identity_count': out['identity_count'],
        'voice_present': True,
        'approved_motion_count': out['approved_motion_count'],
        'quarantined_motion_candidate_count': len(quarantined),
        'auto_promote': False,
        'render_started': False,
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
