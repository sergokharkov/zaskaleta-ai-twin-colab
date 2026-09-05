import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metric(data, key):
    value = data.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description="Compare a clone challenger against the current stable release")
    ap.add_argument("--candidate-metrics", required=True)
    ap.add_argument("--stable-metrics", required=True)
    ap.add_argument("--candidate-evaluation", required=True)
    ap.add_argument("--policy", default="content/clone_release_policy_v1.json")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    candidate_path = Path(args.candidate_metrics)
    stable_path = Path(args.stable_metrics)
    eval_path = Path(args.candidate_evaluation)
    policy_path = Path(args.policy)

    candidate = load_json(candidate_path)
    stable = load_json(stable_path)
    evaluation = load_json(eval_path)
    policy = load_json(policy_path)

    failures = []
    comparisons = []

    required_decision = policy["required_candidate_decision"]
    if evaluation.get("decision") != required_decision:
        failures.append("candidate_did_not_pass_quality_gate")

    candidate_id = candidate.get("candidate_id")
    stable_id = stable.get("candidate_id") or stable.get("release_id") or stable.get("version")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        failures.append("missing_candidate_id")
    if not stable_id:
        failures.append("missing_stable_release_id")

    higher = set(policy.get("higher_is_better") or [])
    lower = set(policy.get("lower_is_better") or [])
    identity = set(policy.get("identity_metrics") or [])
    tolerances = policy.get("metric_regression_tolerances") or {}
    identity_tolerance = float(policy.get("identity_regression_tolerance", 0.0))

    for key in sorted(higher | lower):
        c = metric(candidate, key)
        s = metric(stable, key)
        if c is None or s is None:
            failures.append(f"missing_comparison_metric:{key}")
            comparisons.append({"metric": key, "status": "missing", "candidate": c, "stable": s})
            continue

        tolerance = identity_tolerance if key in identity else float(tolerances.get(key, 0.0))
        if key in higher:
            regression = s - c
            passed = regression <= tolerance
            delta = c - s
        else:
            regression = c - s
            passed = regression <= tolerance
            delta = s - c

        comparisons.append({
            "metric": key,
            "candidate": c,
            "stable": s,
            "delta_in_better_direction": delta,
            "allowed_regression": tolerance,
            "passed": passed,
            "identity_metric": key in identity,
        })
        if not passed:
            prefix = "identity_regression" if key in identity else "metric_regression"
            failures.append(f"{prefix}:{key}")

    if candidate.get("references", {}).get("all_approved") is not True:
        failures.append("candidate_references_not_fully_approved")

    decision = policy["promotion_decision"] if not failures else policy["blocked_decision"]
    report = {
        "schema": "zaskaleta-clone-challenger-comparison-v1",
        "compared_at": datetime.now(timezone.utc).isoformat(),
        "candidate_id": candidate_id,
        "stable_release_id": stable_id,
        "decision": decision,
        "manual_promotion_required": True,
        "auto_promote": False,
        "comparisons": comparisons,
        "failures": failures,
        "provenance": {
            "candidate_metrics_sha256": sha256_file(candidate_path),
            "stable_metrics_sha256": sha256_file(stable_path),
            "candidate_evaluation_sha256": sha256_file(eval_path),
            "policy_sha256": sha256_file(policy_path),
        },
        "safety": {
            "stable_release_immutable": True,
            "rollback_must_remain_available": True,
            "identity_regression_blocks_promotion": True,
            "human_review_still_required": True,
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(decision)
    for failure in failures:
        print("-", failure)
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
