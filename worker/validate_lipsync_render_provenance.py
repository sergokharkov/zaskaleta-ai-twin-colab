#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate MASTER CLONE lipsync/render provenance evidence')
    ap.add_argument('--provenance', required=True)
    ap.add_argument('--output-file', default='')
    args = ap.parse_args()

    provenance_path = Path(args.provenance)
    doc = json.loads(provenance_path.read_text(encoding='utf-8'))
    blockers: list[str] = []

    if doc.get('schema') != 'zaskaleta-lipsync-render-provenance-v1':
        blockers.append('invalid_schema')
    if doc.get('engine') != 'MuseTalk' or doc.get('engine_version') != '1.5':
        blockers.append('unexpected_lipsync_engine')
    if doc.get('approved_motion_reference_required') is not True:
        blockers.append('approved_motion_reference_not_required')
    if doc.get('provenance_complete') is not True:
        blockers.append('provenance_not_complete')
    if doc.get('auto_promote') is not False:
        blockers.append('auto_promote_not_false')
    if doc.get('photo_fallback_used') is True:
        blockers.append('photo_fallback_used')

    hashes = doc.get('source_hashes') or {}
    for key in ('canonical_photo_sha256', 'approved_reference_video_sha256', 'speech_audio_sha256'):
        value = hashes.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdefABCDEF' for c in value):
            blockers.append(f'invalid_hash:{key}')

    output = doc.get('output') or {}
    out_path_value = output.get('path')
    out_hash = output.get('sha256')
    size = output.get('size_bytes')
    if not isinstance(out_path_value, str) or not out_path_value:
        blockers.append('output_path_missing')
    else:
        out_path = Path(out_path_value)
        if not out_path.is_file():
            blockers.append('output_file_missing')
        else:
            actual_hash = sha256_file(out_path)
            if actual_hash != out_hash:
                blockers.append('output_sha256_mismatch')
            if not isinstance(size, int) or size <= 0 or size != out_path.stat().st_size:
                blockers.append('output_size_mismatch')

    report = {
        'schema': 'zaskaleta-lipsync-render-provenance-evaluation-v1',
        'passed': not blockers,
        'decision': 'PASS_RENDER_PROVENANCE' if not blockers else 'BLOCK_RENDER_PROVENANCE',
        'candidate_id': doc.get('candidate_id'),
        'blockers': blockers,
        'manual_promotion_required': True,
        'auto_promote': False,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output_file:
        Path(args.output_file).write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if not blockers else 2


if __name__ == '__main__':
    raise SystemExit(main())
