#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def valid_hash(value) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def verify_file_record(record: dict, prefix: str, blockers: list[str]):
    path_value = record.get('path')
    expected_hash = record.get('sha256')
    size = record.get('size_bytes')
    if not isinstance(path_value, str) or not path_value:
        blockers.append(f'{prefix}_path_missing')
        return
    if not valid_hash(expected_hash):
        blockers.append(f'{prefix}_sha256_invalid')
        return
    path = Path(path_value)
    if not path.is_file():
        blockers.append(f'{prefix}_file_missing')
        return
    if sha256_file(path) != expected_hash:
        blockers.append(f'{prefix}_sha256_mismatch')
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size != path.stat().st_size:
        blockers.append(f'{prefix}_size_mismatch')


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate MASTER CLONE lipsync/final-render provenance evidence')
    ap.add_argument('--provenance', required=True)
    ap.add_argument('--output-file', default='')
    args = ap.parse_args()

    provenance_path = Path(args.provenance)
    doc = json.loads(provenance_path.read_text(encoding='utf-8'))
    blockers: list[str] = []

    schema = doc.get('schema')
    if schema not in {'zaskaleta-lipsync-render-provenance-v1', 'zaskaleta-lipsync-render-provenance-v2'}:
        blockers.append('invalid_schema')
    candidate_id = doc.get('candidate_id')
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        blockers.append('candidate_id_missing')
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
        if not valid_hash(hashes.get(key)):
            blockers.append(f'invalid_hash:{key}')

    if schema == 'zaskaleta-lipsync-render-provenance-v2':
        verify_file_record(doc.get('raw_output') or {}, 'raw_output', blockers)
        postprocess = doc.get('postprocess') or {}
        if postprocess.get('tool') != 'ffmpeg':
            blockers.append('postprocess_tool_not_ffmpeg')
        if not isinstance(postprocess.get('video_filter'), str) or not postprocess.get('video_filter'):
            blockers.append('postprocess_filter_missing')
        if postprocess.get('final_container') != 'mp4':
            blockers.append('final_container_not_mp4')
        if not valid_hash(doc.get('identity_preflight_sha256')):
            blockers.append('identity_preflight_sha256_invalid')

    verify_file_record(doc.get('output') or {}, 'output', blockers)

    report = {
        'schema': 'zaskaleta-lipsync-render-provenance-evaluation-v2',
        'passed': not blockers,
        'decision': 'PASS_RENDER_PROVENANCE' if not blockers else 'BLOCK_RENDER_PROVENANCE',
        'candidate_id': candidate_id if isinstance(candidate_id, str) and candidate_id.strip() else None,
        'final_output_sha256': (doc.get('output') or {}).get('sha256'),
        'blockers': blockers,
        'manual_promotion_required': True,
        'auto_promote': False,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output_file:
        out = Path(args.output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if not blockers else 2


if __name__ == '__main__':
    raise SystemExit(main())
