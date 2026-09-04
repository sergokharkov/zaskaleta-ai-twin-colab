import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description='Validate an extracted motion candidate before manual approval')
    ap.add_argument('--profile', required=True)
    ap.add_argument('--min-detection-ratio', type=float, default=0.75)
    ap.add_argument('--min-samples', type=int, default=24)
    args = ap.parse_args()

    path = Path(args.profile)
    data = json.loads(path.read_text(encoding='utf-8'))
    motion = data.get('motion', {})
    privacy = data.get('privacy', {})

    checks = {
        'schema_ok': data.get('schema') == 'zaskaleta-motion-profile-v1',
        'identity_independent': privacy.get('identity_independent') is True,
        'no_face_identity': privacy.get('face_identity_stored') is False,
        'no_voice': privacy.get('voice_stored') is False,
        'no_background': privacy.get('background_stored') is False,
        'enough_samples': int(motion.get('samples_kept', 0)) >= args.min_samples,
        'detection_ratio_ok': float(motion.get('detection_ratio', 0.0)) >= args.min_detection_ratio,
        'manual_review_required': data.get('approval', {}).get('manual_review_required') is True,
    }

    passed = all(checks.values())
    report = {
        'profile': str(path),
        'passed_automatic_gate': passed,
        'checks': checks,
        'next_step': 'manual_review' if passed else 'reject_or_reextract',
        'approved_for_master_clone': False,
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
