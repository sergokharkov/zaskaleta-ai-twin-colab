import argparse
import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_metric(metrics, key):
    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description="Evaluate whether a clone candidate may proceed to manual review")
    ap.add_argument("--metrics", required=True, help="Candidate metrics JSON")
    ap.add_argument("--gate", default="content/clone_quality_gate_v1.json")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    metrics = load_json(args.metrics)
    gate = load_json(args.gate)
    thresholds = gate["thresholds"]
    failures = []
    checks = []

    rules = [
        ("identity_similarity", ">=", thresholds["identity_similarity_min"]),
        ("identity_drift", "<=", thresholds["identity_drift_max"]),
        ("lip_sync", ">=", thresholds["lip_sync_min"]),
        ("voice_similarity", ">=", thresholds["voice_similarity_min"]),
        ("motion_naturalness", ">=", thresholds["motion_naturalness_min"]),
        ("artifact_score", "<=", thresholds["artifact_score_max"]),
        ("face_occlusion_ratio", "<=", thresholds["face_occlusion_ratio_max"]),
    ]

    for key, op, threshold in rules:
        value = get_metric(metrics, key)
        if value is None:
            failures.append(f"missing_metric:{key}")
            checks.append({"metric": key, "status": "missing", "threshold": threshold})
            continue
        passed = value >= threshold if op == ">=" else value <= threshold
        checks.append({"metric": key, "value": value, "operator": op, "threshold": threshold, "passed": passed})
        if not passed:
            failures.append(f"threshold_failed:{key}")

    flags = set(metrics.get("flags") or [])
    hard_fail_flags = set(gate.get("hard_fail_flags") or [])
    hard_hits = sorted(flags & hard_fail_flags)
    failures.extend(f"hard_fail:{flag}" for flag in hard_hits)

    references = metrics.get("references") or {}
    if references.get("all_approved") is not True:
        failures.append("references_not_fully_approved")

    decision = "PASS_TO_MANUAL_REVIEW" if not failures else "REJECT_CANDIDATE"
    report = {
        "schema": "zaskaleta-clone-release-evaluation-v1",
        "candidate_id": metrics.get("candidate_id"),
        "decision": decision,
        "eligible_for_master": False,
        "manual_approval_required": True,
        "checks": checks,
        "hard_fail_flags_detected": hard_hits,
        "failures": failures,
        "safety": {
            "auto_promote_to_master": False,
            "master_clone_unchanged": True
        }
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(decision)
    if failures:
        for item in failures:
            print("-", item)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
