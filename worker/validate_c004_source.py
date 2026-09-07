import argparse
import json
import math
import sys
from pathlib import Path

SCHEMA = 'zaskaleta-c004-source-manifest-v1'
CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_004'


def validate(manifest, approval, storage):
    if manifest.get('schema') != SCHEMA or manifest.get('candidate_id') != CANDIDATE:
        raise ValueError('invalid_c004_manifest')
    if manifest.get('status') != 'APPROVED_FOR_CPU_QA':
        raise ValueError('source_not_approved_for_cpu_qa')
    if manifest.get('gpu_launch_allowed') is not False or manifest.get('auto_promote') is not False or manifest.get('stable_clone_immutable') is not True:
        raise ValueError('unsafe_candidate_policy')
    s = manifest['source_selection']
    if s.get('mode') != 'explicit_approved_segment_only' or s.get('fallback_to_primary_behavior_allowed') is not False or s.get('fallback_to_c003_allowed') is not False or s.get('identity_anchor_replacement_allowed') is not False:
        raise ValueError('unsafe_source_selection')
    if not isinstance(approval, dict) or approval.get('candidate_id') != CANDIDATE or approval.get('approved') is not True:
        raise ValueError('missing_private_approval')
    if approval.get('source_authenticity_verified') is not True or approval.get('identity_anchor_replacement_allowed') is not False:
        raise ValueError('invalid_private_approval')
    for key in ('source_segment_id', 'source_asset_id', 'source_path'):
        if not isinstance(s.get(key), str) or not s[key].strip() or s[key] != approval.get(key):
            raise ValueError('source_mismatch_' + key)
    start, end = s.get('segment_start_seconds'), s.get('segment_end_seconds')
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (start, end)) or not 0 <= start < end:
        raise ValueError('invalid_segment_bounds')
    if start != approval.get('segment_start_seconds') or end != approval.get('segment_end_seconds'):
        raise ValueError('segment_bounds_mismatch')
    if not isinstance(s.get('approval_record'), str) or not s['approval_record'].strip() or s['approval_record'] != approval.get('approval_record'):
        raise ValueError('approval_record_mismatch')
    if s.get('source_authenticity_verified') is not True:
        raise ValueError('source_authenticity_not_verified')
    roles = [k for k in ('motion', 'talking') if s.get('approved_for_' + k) is True]
    if not roles or any(approval.get('approved_for_' + k) is not True for k in roles):
        raise ValueError('source_role_not_approved')

    root = storage.resolve(strict=True)
    requested = Path(s['source_path'])
    if requested.is_absolute() or '..' in requested.parts:
        raise ValueError('unsafe_source_path')

    direct = root / requested
    if direct.is_file():
        source = direct.resolve(strict=True)
    else:
        # Kaggle may mount a private dataset one directory deeper than the logical
        # dataset root. Permit that layout only when the approved relative name
        # resolves to exactly one file beneath the private storage root.
        matches = [p.resolve(strict=True) for p in root.rglob(requested.name) if p.is_file() and p.name == requested.name]
        matches = [p for p in matches if p.is_relative_to(root)]
        if len(matches) != 1:
            raise ValueError('source_path_resolution_count_' + str(len(matches)))
        source = matches[0]

    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('source_outside_private_storage')
    if source.name in {'55572.mp4', '55573.mp4'} or 'C003' in source.name.upper():
        raise ValueError('known_prior_candidate_source')
    return {'candidate_id': CANDIDATE, 'source_segment_id': s['source_segment_id'], 'source_path': str(source), 'segment_start_seconds': start, 'segment_end_seconds': end, 'approved_roles': roles, 'decision': 'SOURCE_APPROVED_FOR_CPU_QA', 'gpu_launch_allowed': False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--approval', required=True, help='Private approval record; never export it to GitHub')
    ap.add_argument('--storage-root', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    try:
        result = validate(json.loads(Path(args.manifest).read_text()), json.loads(Path(args.approval).read_text()), Path(args.storage_root))
        code = 0
    except Exception as exc:
        result = {'candidate_id': CANDIDATE, 'decision': 'SOURCE_VALIDATION_ERROR', 'gpu_launch_allowed': False, 'error': type(exc).__name__ + ': ' + str(exc)}
        code = 3
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(result['decision'])
    return code


if __name__ == '__main__':
    sys.exit(main())
