import argparse
import json
import math
import sys
from pathlib import Path

SCHEMA = 'zaskaleta-c005-reference-manifest-v1'
CANDIDATE = 'MASTER_CLONE_CANDIDATE_005'
REQUIRED_GATES = (
    'private_asset_import_verified',
    'source_integrity_verified',
    'two_independent_cpu_qa_passes',
    'identity_holdout_passed',
    'video_stream_duration_matches_container_duration',
    'audio_video_duration_consistent',
    'duplicate_gate_passed',
)


def validate_manifest(data):
    if data.get('schema') != SCHEMA:
        raise ValueError('invalid_schema')
    if data.get('candidate_id') != CANDIDATE:
        raise ValueError('invalid_candidate_id')
    if data.get('status') != 'PREPARED_FOR_PRIVATE_ASSET_IMPORT':
        raise ValueError('unexpected_status')

    baseline = data.get('baseline') or {}
    if baseline.get('candidate') != 'C003' or baseline.get('role') != 'comparison_only':
        raise ValueError('c003_baseline_not_locked')
    if baseline.get('stable_release_modified') is not False:
        raise ValueError('stable_release_mutation_forbidden')

    rejected = data.get('rejected_predecessor') or {}
    if rejected.get('candidate') != 'C004' or rejected.get('status') != 'REJECTED_FOR_C005_REFERENCE_USE':
        raise ValueError('c004_rejection_not_locked')

    privacy = data.get('privacy') or {}
    if privacy.get('raw_biometric_media_in_git') is not False:
        raise ValueError('raw_biometric_media_in_git_forbidden')
    if privacy.get('private_biometric_hashes_in_git') is not False:
        raise ValueError('private_biometric_hashes_in_git_forbidden')

    refs = data.get('private_reference_set') or {}
    photos = refs.get('identity_photos') or []
    videos = refs.get('behavior_videos') or []
    if len(photos) < 4 or len(videos) < 3:
        raise ValueError('insufficient_private_reference_registry')

    aliases = set()
    for item in photos + videos:
        alias = item.get('asset_alias')
        if not isinstance(alias, str) or not alias.strip() or alias in aliases:
            raise ValueError('invalid_or_duplicate_asset_alias')
        aliases.add(alias)
        if item.get('storage') != 'PRIVATE_ONLY':
            raise ValueError('reference_not_private_only')
        if item.get('approval') != 'PENDING_PRIVATE_IMPORT_QA':
            raise ValueError('unexpected_reference_approval_state')

    for item in videos:
        duration = item.get('duration_seconds')
        fps = item.get('fps')
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('invalid_video_duration')
        if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or fps <= 0:
            raise ValueError('invalid_video_fps')

    gates = data.get('c005_gates') or {}
    for gate in REQUIRED_GATES:
        if gates.get(gate) is not False:
            raise ValueError('gate_must_start_false_' + gate)
    if gates.get('gpu_launch_allowed') is not False:
        raise ValueError('gpu_must_be_blocked_before_private_qa')
    if gates.get('manual_promotion_required') is not True:
        raise ValueError('manual_promotion_must_remain_required')
    if gates.get('stable_release_modified') is not False:
        raise ValueError('stable_release_must_remain_immutable')

    return {
        'schema': 'zaskaleta-c005-manifest-validation-v1',
        'candidate_id': CANDIDATE,
        'decision': 'C005_REFERENCE_MANIFEST_VALID',
        'gpu_launch_allowed': False,
        'stable_release_modified': False,
        'next_stage': 'PRIVATE_ASSET_IMPORT_QA',
        'registered_identity_photos': len(photos),
        'registered_behavior_videos': len(videos),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--output', required=False)
    args = ap.parse_args()
    try:
        data = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        result = validate_manifest(data)
        code = 0
    except Exception as exc:
        result = {
            'schema': 'zaskaleta-c005-manifest-validation-v1',
            'candidate_id': CANDIDATE,
            'decision': 'C005_REFERENCE_MANIFEST_INVALID',
            'gpu_launch_allowed': False,
            'stable_release_modified': False,
            'error': type(exc).__name__ + ': ' + str(exc),
        }
        code = 3
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8')
    print(text, end='')
    return code


if __name__ == '__main__':
    sys.exit(main())
