import argparse
import hashlib
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description='Validate the complete MASTER CLONE promotion evidence bundle without promoting anything')
    ap.add_argument('--clone-evaluation', required=True)
    ap.add_argument('--identity-view-evaluation', required=True)
    ap.add_argument('--challenger-comparison', required=True)
    ap.add_argument('--temporal-face-guard', required=True)
    ap.add_argument('--policy', default='content/clone_promotion_bundle_policy_v1.json')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    paths = {
        'clone_evaluation': Path(args.clone_evaluation),
        'identity_view_evaluation': Path(args.identity_view_evaluation),
        'challenger_comparison': Path(args.challenger_comparison),
        'temporal_face_guard': Path(args.temporal_face_guard),
        'policy': Path(args.policy),
    }
    missing_files = [name for name, path in paths.items() if not path.is_file()]
    if missing_files:
        raise SystemExit('Missing promotion evidence artifact(s): ' + ', '.join(missing_files))

    policy = load_json(paths['policy'])
    docs = {name: load_json(path) for name, path in paths.items() if name != 'policy'}
    blockers = []

    if policy.get('auto_promote') is not False:
        blockers.append('policy_auto_promote_not_false')
    if policy.get('manual_promotion_required') is not True:
        blockers.append('policy_manual_promotion_not_required')
    if policy.get('stable_release_immutable') is not True:
        blockers.append('stable_release_not_immutable')
    if float(policy.get('identity_regression_tolerance', 1)) != 0.0:
        blockers.append('identity_regression_tolerance_not_zero')

    required = policy.get('required_artifacts') or {}
    clone_eval = docs['clone_evaluation']
    identity_eval = docs['identity_view_evaluation']
    challenger = docs['challenger_comparison']
    temporal = docs['temporal_face_guard']

    if clone_eval.get('decision') != required['clone_evaluation']['required_decision']:
        blockers.append('clone_evaluation_not_passed')

    identity_policy = required['identity_view_evaluation']
    if identity_eval.get('decision') != identity_policy['required_decision']:
        blockers.append('identity_view_evaluation_not_passed')
    if identity_eval.get('zero_identity_regression_enforced') is not True:
        blockers.append('identity_zero_regression_not_enforced')
    if identity_eval.get('manual_override_allowed') is not False:
        blockers.append('identity_manual_override_enabled')

    expected_views = policy.get('required_identity_views') or []
    actual_views = identity_eval.get('required_views') or []
    if actual_views != expected_views:
        blockers.append('identity_view_set_mismatch')
    view_rows = identity_eval.get('views') or {}
    for view in expected_views:
        row = view_rows.get(view)
        if not isinstance(row, dict):
            blockers.append(f'missing_identity_view:{view}')
            continue
        if row.get('decision') != 'PASS':
            blockers.append(f'identity_view_failed:{view}')
        try:
            drift = float(row.get('identity_drift'))
        except (TypeError, ValueError):
            blockers.append(f'identity_drift_missing:{view}')
        else:
            if drift > 0.0:
                blockers.append(f'identity_drift_above_zero:{view}')

    challenger_policy = required['challenger_comparison']
    if challenger.get('decision') != challenger_policy['required_decision']:
        blockers.append('challenger_comparison_not_eligible')
    if challenger.get('manual_promotion_required') is not True:
        blockers.append('challenger_manual_promotion_not_required')
    if challenger.get('auto_promote') is not False:
        blockers.append('challenger_auto_promote_not_false')
    if challenger.get('failures'):
        blockers.append('challenger_contains_failures')

    if temporal.get('passed') is not True:
        blockers.append('temporal_face_guard_failed')

    candidate_ids = []
    for doc in (clone_eval, identity_eval, challenger, temporal):
        candidate_id = doc.get('candidate_id')
        if isinstance(candidate_id, str) and candidate_id.strip():
            candidate_ids.append(candidate_id.strip())
    if candidate_ids and len(set(candidate_ids)) != 1:
        blockers.append('candidate_id_mismatch_across_artifacts')

    hashes = {name: sha256_file(path) for name, path in paths.items()}
    ready = not blockers
    report = {
        'schema': 'zaskaleta-clone-promotion-bundle-evaluation-v1',
        'decision': policy['eligible_decision'] if ready else policy['blocked_decision'],
        'ready_for_manual_promotion_review': ready,
        'manual_promotion_required': True,
        'auto_promote': False,
        'stable_release_immutable': True,
        'rollback_required': True,
        'zero_identity_regression_enforced': True,
        'candidate_id': candidate_ids[0] if candidate_ids and len(set(candidate_ids)) == 1 else None,
        'blockers': blockers,
        'artifact_sha256': hashes,
        'note': 'This validator never promotes MASTER CLONE. It only verifies that all mandatory evidence is present and consistent before human review.'
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ready:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
