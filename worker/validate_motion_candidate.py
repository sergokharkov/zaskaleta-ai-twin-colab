import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description='Validate an extracted motion candidate before manual MASTER CLONE approval')
    ap.add_argument('--profile', required=True)
    ap.add_argument('--min-detection-ratio', type=float, default=0.75)
    ap.add_argument('--min-samples', type=int, default=24)
    args = ap.parse_args()

    path = Path(args.profile)
    data = json.loads(path.read_text(encoding='utf-8'))
    motion = data.get('motion', {})
    privacy = data.get('privacy', {})
    approval = data.get('approval', {})
    frames = motion.get('frames', []) or []
    samples = int(motion.get('samples_kept', 0))

    dimensions_ok = bool(frames)
    monotonic_time = True
    expected_points = None
    previous_time = -1.0
    normalized_points_ok = True

    for frame in frames:
        points = frame.get('keypoints', []) or []
        scores = frame.get('scores', []) or []
        if expected_points is None:
            expected_points = len(points)
        if len(points) < 5 or len(points) != expected_points or len(scores) != len(points):
            dimensions_ok = False
        current_time = float(frame.get('time', 0.0) or 0.0)
        if current_time < previous_time:
            monotonic_time = False
        previous_time = current_time
        for point in points:
            if not isinstance(point, list) or len(point) < 2:
                normalized_points_ok = False
                break
            x, y = float(point[0]), float(point[1])
            if not (-0.25 <= x <= 1.25 and -0.25 <= y <= 1.25):
                normalized_points_ok = False
                break

    checks = {
        'schema_ok': data.get('schema') == 'zaskaleta-motion-profile-v1',
        'candidate_status_only': data.get('status') in {'extracted_candidate', 'validated_candidate'},
        'identity_independent': privacy.get('identity_independent') is True,
        'no_face_identity': privacy.get('face_identity_stored') is False,
        'no_voice': privacy.get('voice_stored') is False,
        'no_background': privacy.get('background_stored') is False,
        'no_wardrobe': privacy.get('wardrobe_stored') is False,
        'no_raw_frames': privacy.get('raw_frames_embedded') is False,
        'enough_samples': samples >= args.min_samples,
        'sample_count_matches_frames': len(frames) == samples,
        'detection_ratio_ok': float(motion.get('detection_ratio', 0.0)) >= args.min_detection_ratio,
        'keypoint_dimensions_ok': dimensions_ok,
        'timestamps_monotonic': monotonic_time,
        'normalized_points_ok': normalized_points_ok,
        'manual_review_required': approval.get('manual_review_required') is True,
        'never_auto_approved': approval.get('approved_for_master_clone') is False,
    }

    passed = all(checks.values())
    report = {
        'profile': str(path),
        'passed_automatic_gate': passed,
        'checks': checks,
        'next_step': 'manual_review' if passed else 'reject_or_reextract',
        'manual_review_required': True,
        'approved_for_master_clone': False,
        'policy': 'Automatic validation may qualify a candidate for review but can never approve it for MASTER CLONE.',
    }
    report_path = path.with_name(path.stem + '_validation.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    for name, ok in checks.items():
        print(('✅' if ok else '❌') + ' ' + name)
    print('REPORT=' + str(report_path))
    if not passed:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
