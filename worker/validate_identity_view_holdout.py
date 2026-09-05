import json
from pathlib import Path


def fail(message: str):
    raise SystemExit(message)


def main():
    root = Path(__file__).resolve().parents[1]
    profile = json.loads((root / 'content/clone_reference_profile.json').read_text(encoding='utf-8'))
    release = json.loads((root / 'content/clone_release_policy_v1.json').read_text(encoding='utf-8'))
    holdout = json.loads((root / 'content/identity_view_holdout_v1.json').read_text(encoding='utf-8'))

    required_views = release.get('face_regression_policy', {}).get('required_views') or []
    holdout_views = holdout.get('required_views') or []
    if required_views != holdout_views:
        fail(f'Required view mismatch: release={required_views!r} holdout={holdout_views!r}')

    canonical = profile.get('canonical_identity_photo')
    if holdout.get('canonical_identity_photo') != canonical:
        fail('Holdout canonical identity does not match clone_reference_profile')

    approved_photos = set(profile.get('photo_filenames') or [])
    supporting = set(holdout.get('candidate_supporting_photos') or [])
    if canonical not in approved_photos:
        fail('Canonical identity is not in approved master photo set')
    if not supporting.issubset(approved_photos - {canonical}):
        fail('Supporting holdout references must be approved master photos and must not duplicate canonical')

    rules = holdout.get('rules') or {}
    invariants = {
        'manual_view_assignment_required': True,
        'do_not_infer_view_from_filename': True,
        'all_required_views_must_be_assigned_before_release': True,
        'all_required_views_must_pass': True,
        'manual_identity_override_allowed': False,
        'stable_release_immutable': True,
        'supporting_reference_must_come_from_approved_master_photo_set': True,
    }
    for key, expected in invariants.items():
        if rules.get(key) is not expected:
            fail(f'Identity holdout invariant failed: {key}={rules.get(key)!r}')
    if float(rules.get('identity_regression_tolerance', 1)) != 0.0:
        fail('Identity holdout regression tolerance must remain exactly 0.0')

    assignments = holdout.get('view_assignments') or {}
    if set(assignments) != set(required_views):
        fail('view_assignments must contain every and only the mandatory identity views')

    allowed_status = {'UNASSIGNED', 'ASSIGNED', 'APPROVED_REFERENCE'}
    for view in required_views:
        row = assignments[view]
        status = row.get('review_status')
        reference = row.get('reference')
        if status not in allowed_status:
            fail(f'{view}: unsupported review_status {status!r}')
        if reference is not None and reference not in approved_photos:
            fail(f'{view}: reference must belong to approved master photo set')
        if status == 'UNASSIGNED' and reference is not None:
            fail(f'{view}: UNASSIGNED view cannot already have a reference')
        if status in {'ASSIGNED', 'APPROVED_REFERENCE'} and reference is None:
            fail(f'{view}: assigned view requires a reference')

    review_fields = set(holdout.get('required_review_fields_per_view') or [])
    must_have = {
        'identity_similarity', 'identity_drift', 'face_structure_drift', 'age_regression',
        'beard_regression', 'hairline_regression', 'jaw_regression', 'mouth_regression',
        'asymmetry_loss', 'decision'
    }
    if review_fields != must_have:
        fail('Identity holdout review field set changed unexpectedly')

    if holdout.get('decision_values') != ['PASS', 'FAIL', 'UNREVIEWED']:
        fail('Identity holdout decision values changed unexpectedly')

    print('Identity multi-angle holdout policy OK: required views locked, zero regression, no filename inference')


if __name__ == '__main__':
    main()
