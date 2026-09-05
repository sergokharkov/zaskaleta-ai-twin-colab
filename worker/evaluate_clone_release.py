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

    metrics_path = Path(args.metrics)
    gate_path = Path(args.gate)
    metrics = load_json(metrics_path)
    gate = load_json(gate_path)
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

    candidate_id = metrics.get("candidate_id")
    if not candidate_id or not isinstance(candidate_id, str):
        failures.append("missing_candidate_id")

    decision = "PASS_TO_MANUAL_REVIEW" if not failures else "REJECT_CANDIDATE"
    report = {
        "schema": "zaskaleta-clone-release-evaluation-v2",
        "candidate_id": candidate_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "eligible_for_master": False,
        "manual_approval_required": True,
        "checks": checks,
        "hard_fail_flags_detected": hard_hits,
        "failures": failures,
        "provenance": {
            "metrics_sha256": sha256_file(metrics_path),
            "gate_sha256": sha256_file(gate_path),
            "gate_schema": gate.get("schema"),
        },
        "safety": {
            "auto_promote_to_master": False,
            "master_clone_unchanged": True,
            "report_is_advisory_until_manual_approval": True,
        }
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(decision)
    print("METRICS_SHA256=" + report["provenance"]["metrics_sha256"])
    print("GATE_SHA256=" + report["provenance"]["gate_sha256"])
    if failures:
        for item in failures:
            print("-", item)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
