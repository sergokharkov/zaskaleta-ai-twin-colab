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
    ap.add_argument('--render-provenance', required=True)
    ap.add_argument('--policy', default='content/clone_promotion_bundle_policy_v1.json')
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    paths = {
        'clone_evaluation': Path(args.clone_evaluation),
        'identity_view_evaluation': Path(args.identity_view_evaluation),
        'challenger_comparison': Path(args.challenger_comparison),
        'temporal_face_guard': Path(args.temporal_face_guard),
        'render_provenance': Path(args.render_provenance),
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

    rules = policy.get('rules') or {}
    if rules.get('candidate_id_required_on_all_artifacts') is not True:
        blockers.append('policy_candidate_id_not_required')
    if rules.get('candidate_id_must_match_across_all_artifacts') is not True:
        blockers.append('policy_candidate_id_match_not_required')

    required = policy.get('required_artifacts') or {}
    clone_eval = docs['clone_evaluation']
    identity_eval = docs['identity_view_evaluation']
    challenger = docs['challenger_comparison']
    temporal = docs['temporal_face_guard']
    render_provenance = docs['render_provenance']

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
        drift = row.get('identity_drift')
        if isinstance(drift, bool) or not isinstance(drift, (int, float)):
            blockers.append(f'identity_drift_invalid_type:{view}')
        elif float(drift) != 0.0:
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

    render_policy = required.get('render_provenance') or {}
    if render_provenance.get('decision') != render_policy.get('required_decision'):
        blockers.append('render_provenance_not_passed')
    if render_provenance.get('auto_promote') is not False:
        blockers.append('render_provenance_auto_promote_not_false')
    if render_provenance.get('passed') is not True:
        blockers.append('render_provenance_failed')

    artifact_docs = {
        'clone_evaluation': clone_eval,
        'identity_view_evaluation': identity_eval,
        'challenger_comparison': challenger,
        'temporal_face_guard': temporal,
        'render_provenance': render_provenance,
    }
    candidate_ids = {}
    for name, doc in artifact_docs.items():
        raw = doc.get('candidate_id')
        if not isinstance(raw, str) or not raw.strip():
            blockers.append(f'candidate_id_missing:{name}')
            continue
        candidate_ids[name] = raw.strip()

    unique_candidate_ids = set(candidate_ids.values())
    if len(candidate_ids) == len(artifact_docs) and len(unique_candidate_ids) != 1:
        blockers.append('candidate_id_mismatch_across_artifacts')

    hashes = {name: sha256_file(path) for name, path in paths.items()}
    ready = not blockers
    resolved_candidate_id = next(iter(unique_candidate_ids)) if len(unique_candidate_ids) == 1 and len(candidate_ids) == len(artifact_docs) else None
    report = {
        'schema': 'zaskaleta-clone-promotion-bundle-evaluation-v1',
        'decision': policy['eligible_decision'] if ready else policy['blocked_decision'],
        'ready_for_manual_promotion_review': ready,
        'manual_promotion_required': True,
        'auto_promote': False,
        'stable_release_immutable': True,
        'rollback_required': True,
        'zero_identity_regression_enforced': True,
        'render_provenance_required': True,
        'candidate_id_required_on_all_artifacts': True,
        'candidate_id': resolved_candidate_id,
        'blockers': blockers,
        'artifact_sha256': hashes,
        'note': 'This validator never promotes MASTER CLONE. It only verifies that all mandatory evidence, including render provenance and one shared candidate_id across every artifact, is present and consistent before human review.'
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ready:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
