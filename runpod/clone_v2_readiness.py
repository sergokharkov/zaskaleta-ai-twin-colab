#!/usr/bin/env python3
"""Static/non-paid readiness checks for the first MASTER CLONE v2 GPU test.

This script never starts GPU resources, never prints secret values, and never promotes a clone.
It validates that the repository policies and runtime entrypoints required for the first 8-15s
Clone v2 test are present and mutually consistent before any paid RunPod session is considered.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "worker/run_clone_v2_test.py",
    "worker/lipsync_musetalk.py",
    "worker/generate_scene_speech.py",
    "worker/evaluate_clone_release.py",
    "worker/evaluate_identity_view_results.py",
    "worker/compare_clone_challenger.py",
    "worker/validate_clone_promotion_bundle.py",
    "worker/validate_clone_duration_progression.py",
    "worker/validate_clone_v2_temporal_output.py",
    "content/clone_reference_profile.json",
    "content/talking_profile_v2.json",
    "content/clone_quality_gate_v1.json",
    "content/clone_release_policy_v1.json",
    "content/render_face_guard_v1.json",
    "content/talking_temporal_guard_v1.json",
    "content/identity_view_holdout_v1.json",
    "content/clone_promotion_bundle_policy_v1.json",
    "content/clone_duration_gate_policy_v1.json",
    "content/storage_config.json",
]


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def main() -> int:
    failures: list[str] = []

    missing = [rel for rel in REQUIRED_FILES if not (ROOT / rel).is_file()]
    if missing:
        failures.append("missing_required_files:" + ",".join(missing))

    if not missing:
        profile = load("content/clone_reference_profile.json")
        talking = load("content/talking_profile_v2.json")
        quality = load("content/clone_quality_gate_v1.json")
        release = load("content/clone_release_policy_v1.json")
        render_guard = load("content/render_face_guard_v1.json")
        temporal = load("content/talking_temporal_guard_v1.json")
        holdout = load("content/identity_view_holdout_v1.json")
        duration = load("content/clone_duration_gate_policy_v1.json")
        storage = load("content/storage_config.json")

        canonical = profile.get("canonical_identity_photo")
        photos = profile.get("photo_filenames") or []
        if not canonical or canonical not in photos:
            failures.append("canonical_identity_not_locked")

        if quality.get("manual_approval_required") is not True or quality.get("auto_promote_to_master") is not False:
            failures.append("quality_gate_promotion_policy_weakened")

        if float(release.get("identity_regression_tolerance", 1)) != 0.0:
            failures.append("identity_regression_tolerance_not_zero")
        if release.get("manual_promotion_required") is not True or release.get("auto_promote") is not False:
            failures.append("release_promotion_policy_weakened")

        identity_policy = render_guard.get("identity_policy") or {}
        for key in [
            "canonical_reference_only_for_generation",
            "no_beautification",
            "no_rejuvenation",
            "no_face_redesign",
            "no_identity_substitution",
        ]:
            if identity_policy.get(key) is not True:
                failures.append(f"render_identity_policy_weakened:{key}")
        if float(identity_policy.get("release_identity_regression_tolerance", 1)) != 0.0:
            failures.append("render_guard_identity_tolerance_not_zero")

        first_gate = (duration.get("ordered_gates") or [{}])[0]
        test_range = talking.get("test_gate", {}).get("range_seconds") or []
        if test_range != [8, 15] or first_gate.get("min_seconds") != 8 or first_gate.get("max_seconds") != 15:
            failures.append("first_duration_gate_mismatch")
        rules = duration.get("rules") or {}
        if rules.get("sequential_only") is not True or rules.get("skip_gate_allowed") is not False:
            failures.append("duration_progression_policy_weakened")

        required_views = release.get("face_regression_policy", {}).get("required_views") or []
        if holdout.get("required_views") != required_views or len(required_views) != 5:
            failures.append("identity_view_holdout_mismatch")

        if temporal.get("identity_policy", {}).get("identity_regression_tolerance") not in (0, 0.0):
            failures.append("temporal_identity_tolerance_not_zero")

        canonical_storage = storage.get("canonical_storage") or {}
        if canonical_storage.get("provider") != "google_drive" or not canonical_storage.get("folder_id"):
            failures.append("canonical_storage_not_configured")
        runtime = storage.get("runtime") or {}
        if runtime.get("preferred_backend") != "runpod":
            failures.append("runpod_not_preferred_runtime")
        if runtime.get("never_commit_service_account_json") is not True:
            failures.append("credential_safety_policy_weakened")

    report = {
        "schema": "zaskaleta-clone-v2-runpod-readiness-v1",
        "ready_for_paid_gpu_consideration": not failures,
        "paid_gpu_started": False,
        "manual_budget_approval_required_before_gpu_start": True,
        "first_test_gate_seconds": [8, 15],
        "failures": failures,
        "note": "Static readiness only. Passing this check does not prove render quality and does not start RunPod or any paid GPU.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
