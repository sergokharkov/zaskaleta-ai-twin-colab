import argparse
import json
from pathlib import Path


def fail(message: str):
    raise SystemExit(message)


def main():
    ap = argparse.ArgumentParser(description='Evaluate multi-angle MASTER CLONE identity results with zero regression tolerance')
    ap.add_argument('--results', required=True, help='JSON file with one result object per required identity view')
    ap.add_argument('--policy', default=None, help='Optional identity_view_holdout_v1.json path')
    ap.add_argument('--output', default=None, help='Optional evaluation output JSON path')
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    policy_path = Path(args.policy) if args.policy else root / 'content/identity_view_holdout_v1.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    results_path = Path(args.results)
    payload = json.loads(results_path.read_text(encoding='utf-8'))

    required_views = policy.get('required_views') or []
    assignments = policy.get('view_assignments') or {}
    review_fields = set(policy.get('required_review_fields_per_view') or [])
    rows = payload.get('views') or {}

    if set(rows) != set(required_views):
        fail('Results must contain every and only the mandatory identity views')

    evaluated = {}
    blockers = []

    for view in required_views:
        assignment = assignments.get(view) or {}
        reference = assignment.get('reference')
        review_status = assignment.get('review_status')
        row = rows.get(view) or {}

        if review_status != 'APPROVED_REFERENCE' or not reference:
            blockers.append(f'{view}: approved reference not assigned')
            evaluated[view] = {'decision': 'FAIL', 'blockers': ['reference_not_approved']}
            continue

        missing = sorted(review_fields - set(row))
        if missing:
            blockers.append(f'{view}: missing review fields: {", ".join(missing)}')
            evaluated[view] = {'decision': 'FAIL', 'blockers': ['missing_review_fields']}
            continue

        if row.get('reference') != reference:
            blockers.append(f'{view}: result reference mismatch')

        identity_similarity = float(row['identity_similarity'])
        identity_drift = float(row['identity_drift'])
        face_structure_drift = bool(row['face_structure_drift'])
        age_regression = bool(row['age_regression'])
        beard_regression = bool(row['beard_regression'])
        hairline_regression = bool(row['hairline_regression'])
        jaw_regression = bool(row['jaw_regression'])
        mouth_regression = bool(row['mouth_regression'])
        asymmetry_loss = bool(row['asymmetry_loss'])
        declared_decision = row.get('decision')

        view_blockers = []
        if identity_drift > 0.0:
            view_blockers.append('identity_drift_above_zero')
        if face_structure_drift:
            view_blockers.append('face_structure_drift')
        if age_regression:
            view_blockers.append('age_regression')
        if beard_regression:
            view_blockers.append('beard_regression')
        if hairline_regression:
            view_blockers.append('hairline_regression')
        if jaw_regression:
            view_blockers.append('jaw_regression')
        if mouth_regression:
            view_blockers.append('mouth_regression')
        if asymmetry_loss:
            view_blockers.append('asymmetry_loss')
        if declared_decision != 'PASS':
            view_blockers.append('declared_decision_not_pass')

        decision = 'PASS' if not view_blockers else 'FAIL'
        evaluated[view] = {
            'reference': reference,
            'identity_similarity': identity_similarity,
            'identity_drift': identity_drift,
            'decision': decision,
            'blockers': view_blockers,
        }
        if view_blockers:
            blockers.append(f'{view}: ' + ', '.join(view_blockers))

    eligible = not blockers and all(evaluated[v]['decision'] == 'PASS' for v in required_views)
    output = {
        'schema': 'zaskaleta-identity-view-evaluation-v1',
        'policy': policy_path.name,
        'results': results_path.name,
        'required_views': required_views,
        'views': evaluated,
        'zero_identity_regression_enforced': True,
        'manual_override_allowed': False,
        'eligible_for_manual_promotion_review': eligible,
        'decision': 'PASS_TO_MANUAL_PROMOTION_REVIEW' if eligible else 'BLOCK_PROMOTION',
        'blockers': blockers,
        'note': 'This evaluator never auto-promotes MASTER CLONE. Any identity regression in any required angle blocks promotion.'
    }

    out_path = Path(args.output) if args.output else results_path.with_name(results_path.stem + '.evaluation.json')
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not eligible:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
