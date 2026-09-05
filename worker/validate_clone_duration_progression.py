import argparse
import json
from pathlib import Path


def fail(message: str):
    raise SystemExit(message)


def main():
    ap = argparse.ArgumentParser(description='Validate sequential MASTER CLONE duration-gate progression')
    ap.add_argument('--evidence', required=True, help='JSON evidence for the current or requested duration gate')
    ap.add_argument('--policy', default=None)
    ap.add_argument('--output', default=None)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    policy_path = Path(args.policy) if args.policy else root / 'content/clone_duration_gate_policy_v1.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    evidence_path = Path(args.evidence)
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))

    gates = policy.get('ordered_gates') or []
    if not gates:
        fail('Duration policy has no gates')
    ids = [g.get('id') for g in gates]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        fail('Duration gate IDs must be unique and non-empty')

    for i, gate in enumerate(gates):
        lo = float(gate['min_seconds'])
        hi = float(gate['max_seconds'])
        if lo <= 0 or hi <= lo:
            fail(f'Invalid duration range for {gate["id"]}')
        if i and lo != float(gates[i - 1]['max_seconds']):
            fail('Duration gates must be contiguous and ordered')

    rules = policy.get('rules') or {}
    required_true = [
        'start_at_first_gate', 'sequential_only', 'previous_gate_must_pass',
        'stable_release_must_remain_immutable', 'manual_promotion_required'
    ]
    for key in required_true:
        if rules.get(key) is not True:
            fail(f'Duration policy invariant failed: {key}')
    if rules.get('skip_gate_allowed') is not False or rules.get('manual_override_allowed') is not False:
        fail('Skipping/manual override must remain disabled')
    if float(rules.get('identity_regression_tolerance', 1)) != 0.0:
        fail('Identity regression tolerance must remain exactly 0.0')

    requested = evidence.get('requested_gate')
    if requested not in ids:
        fail('Unknown requested_gate')
    requested_index = ids.index(requested)

    current_passed = evidence.get('passed_gates') or []
    if len(current_passed) != len(set(current_passed)) or any(x not in ids for x in current_passed):
        fail('passed_gates contains invalid or duplicate gate IDs')

    blockers = []
    if requested_index == 0:
        if current_passed:
            blockers.append('first_gate_request_must_not_claim_prior_passes')
    else:
        expected_prior = ids[:requested_index]
        if current_passed != expected_prior:
            blockers.append('prior_gates_not_sequentially_complete')

        prev = evidence.get('previous_gate_evidence') or {}
        for key in policy.get('required_previous_gate_evidence') or []:
            if prev.get(key) is not True:
                blockers.append(f'missing_or_failed_previous_evidence:{key}')

        if float(prev.get('identity_regression', 1)) != 0.0:
            blockers.append('identity_regression_above_zero')

    eligible = not blockers
    report = {
        'schema': 'zaskaleta-clone-duration-progression-evaluation-v1',
        'requested_gate': requested,
        'passed_gates': current_passed,
        'eligible_to_attempt_gate': eligible,
        'decision': 'ALLOW_GATE_ATTEMPT' if eligible else 'BLOCK_GATE_ATTEMPT',
        'blockers': blockers,
        'manual_override_allowed': False,
        'note': 'This validator controls test-duration progression only. It never promotes a clone release.'
    }
    out = Path(args.output) if args.output else evidence_path.with_name(evidence_path.stem + '.duration-evaluation.json')
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not eligible:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
