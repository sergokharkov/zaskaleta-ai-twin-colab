#!/usr/bin/env python3
import argparse
import json
import math
import re
import sys
from pathlib import Path

SCHEMA = "zaskaleta-c006-reference-manifest-v1"
CANDIDATE = "MASTER_CLONE_CANDIDATE_006"

EXPECTED_COUNTS = {
    "identity_primary": 12,
    "identity_secondary": 2,
    "face_motion_primary": 3,
    "talking_behavior": 4,
    "body_movement": 1,
    "voice_primary": 1,
    "voice_source_backup": 3,
    "style_reference_only": 1,
    "pending_review": 1,
}

BLOCKED_PUBLIC_KEYS = {
    "drive_id", "driveId", "webViewLink", "download_url", "downloadUrl",
    "sha256", "sha1", "md5", "biometric_hash", "embedding",
    "encryption_key", "private_key", "secret_key", "api_key",
}
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def walk_public_safety(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in BLOCKED_PUBLIC_KEYS:
                raise ValueError(f"private_key_forbidden:{path}.{k}")
            walk_public_safety(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_public_safety(v, f"{path}[{i}]")
    elif isinstance(node, str):
        low = node.lower()
        if "drive.google.com/" in low or "docs.google.com/" in low:
            raise ValueError(f"private_drive_locator_forbidden:{path}")
        if HEX64.fullmatch(node.strip()):
            raise ValueError(f"private_hash_forbidden:{path}")


def require_private_items(items, approval=None):
    aliases = set()
    filenames = set()
    for item in items:
        alias = item.get("asset_alias")
        filename = item.get("filename")
        if not isinstance(alias, str) or not alias.strip() or alias in aliases:
            raise ValueError("invalid_or_duplicate_alias")
        if not isinstance(filename, str) or not filename.strip() or filename in filenames:
            raise ValueError("invalid_or_duplicate_filename")
        aliases.add(alias)
        filenames.add(filename)
        if item.get("storage") != "PRIVATE_ONLY":
            raise ValueError(f"not_private_only:{filename}")
        if approval is not None and item.get("approval") != approval:
            raise ValueError(f"unexpected_approval:{filename}")
    return aliases, filenames


def validate(data):
    walk_public_safety(data)

    if data.get("schema") != SCHEMA:
        raise ValueError("invalid_schema")
    if data.get("candidate_id") != CANDIDATE:
        raise ValueError("invalid_candidate")
    if data.get("status") != "CANONICAL_SOURCE_FROZEN_PENDING_PRIVATE_QA":
        raise ValueError("unexpected_status")
    if data.get("training_policy") != "ALLOWLIST_ONLY":
        raise ValueError("training_policy_must_be_allowlist_only")
    if data.get("default_policy") != "DENY":
        raise ValueError("default_policy_must_be_deny")

    baseline = data.get("comparison_baseline") or {}
    if baseline.get("candidate") != "C003" or baseline.get("role") != "historical_comparison_only":
        raise ValueError("c003_comparison_baseline_not_locked")
    if baseline.get("stable_release_modified") is not False:
        raise ValueError("baseline_stable_mutation_forbidden")

    prev = data.get("previous_challenger") or {}
    if prev.get("candidate") != "C005" or prev.get("role") != "comparison_only":
        raise ValueError("c005_previous_challenger_not_locked")
    if prev.get("may_become_identity_source") is not False:
        raise ValueError("c005_identity_source_forbidden")
    if prev.get("stable_release_modified") is not False:
        raise ValueError("c005_stable_mutation_forbidden")

    rejected = data.get("excluded_predecessor") or {}
    if rejected.get("candidate") != "C004" or rejected.get("status") != "EXCLUDED_FROM_C006_REFERENCE_USE":
        raise ValueError("c004_exclusion_not_locked")

    privacy = data.get("privacy") or {}
    for key in ("raw_biometric_media_in_git", "private_biometric_hashes_in_git", "drive_ids_in_git", "encryption_keys_in_git"):
        if privacy.get(key) is not False:
            raise ValueError(f"privacy_flag_weakened:{key}")
    if privacy.get("public_manifest_contains_only_sanitized_aliases_and_non_biometric_technical_metadata") is not True:
        raise ValueError("public_manifest_sanitization_not_asserted")

    refs = data.get("private_reference_set") or {}
    for group, count in EXPECTED_COUNTS.items():
        if len(refs.get(group) or []) != count:
            raise ValueError(f"unexpected_count:{group}")

    all_aliases = set()
    all_names = set()
    for group in ("identity_primary","identity_secondary","face_motion_primary","talking_behavior","body_movement","voice_primary","voice_source_backup","style_reference_only"):
        items = refs[group]
        aliases, names = require_private_items(items)
        if all_aliases & aliases:
            raise ValueError(f"duplicate_alias_across_groups:{group}")
        if all_names & names:
            raise ValueError(f"duplicate_filename_across_groups:{group}")
        all_aliases |= aliases
        all_names |= names

    for item in refs["identity_primary"]:
        if item.get("approval") != "FROZEN_PENDING_PRIVATE_QA":
            raise ValueError("identity_primary_not_frozen")
    for item in refs["identity_secondary"]:
        if item.get("approval") != "FROZEN_PENDING_PRIVATE_QA":
            raise ValueError("identity_secondary_not_frozen")

    for group in ("face_motion_primary","talking_behavior","body_movement","voice_primary","voice_source_backup"):
        for item in refs[group]:
            d = item.get("duration_seconds")
            if not isinstance(d, (int, float)) or isinstance(d, bool) or not math.isfinite(d) or d <= 0:
                raise ValueError(f"invalid_duration:{group}:{item.get('filename')}")

    for item in refs["talking_behavior"]:
        if item.get("role") != "behavior_only":
            raise ValueError("behavior_role_not_isolated")
    for item in refs["body_movement"]:
        if item.get("role") != "body_only":
            raise ValueError("body_role_not_isolated")
    for item in refs["style_reference_only"]:
        if item.get("role") != "style_only" or item.get("approval") != "STYLE_ONLY":
            raise ValueError("style_role_not_isolated")

    pending = refs["pending_review"][0]
    if pending.get("approval") != "PENDING" or pending.get("ingestion_allowed") is not False:
        raise ValueError("pending_must_be_blocked")

    exclusions = data.get("hard_exclusions") or []
    if len(exclusions) < 7:
        raise ValueError("hard_exclusion_set_too_weak")

    gates = data.get("c006_gates") or {}
    if gates.get("private_integrity_registry_created") is not True:
        raise ValueError("private_integrity_registry_not_created")
    for key in (
        "private_asset_staging_verified",
        "source_integrity_verified",
        "two_independent_cpu_qa_passes",
        "multi_photo_identity_consistency_passed",
        "face_motion_identity_holdout_passed",
        "behavior_role_isolation_passed",
        "voice_lineage_passed",
        "duplicate_gate_passed",
        "manual_likeness_review_passed",
        "gpu_launch_allowed",
    ):
        if gates.get(key) is not False:
            raise ValueError(f"pre_qa_gate_must_start_false:{key}")
    if gates.get("manual_promotion_required") is not True:
        raise ValueError("manual_promotion_required")
    if gates.get("auto_promote") is not False:
        raise ValueError("auto_promote_forbidden")
    if gates.get("stable_release_modified") is not False:
        raise ValueError("stable_release_must_remain_immutable")

    return {
        "schema": "zaskaleta-c006-manifest-validation-v1",
        "candidate_id": CANDIDATE,
        "decision": "C006_REFERENCE_MANIFEST_VALID",
        "registered_source_count": sum(EXPECTED_COUNTS.values()),
        "registered_identity_primary": EXPECTED_COUNTS["identity_primary"],
        "pending_ingestion_allowed": False,
        "gpu_launch_allowed": False,
        "auto_promote": False,
        "stable_release_modified": False,
        "next_stage": "PRIVATE_SOURCE_INTEGRITY_QA",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--output")
    args = ap.parse_args()
    try:
        data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        result = validate(data)
        rc = 0
    except Exception as exc:
        result = {
            "schema": "zaskaleta-c006-manifest-validation-v1",
            "candidate_id": CANDIDATE,
            "decision": "C006_REFERENCE_MANIFEST_INVALID",
            "gpu_launch_allowed": False,
            "auto_promote": False,
            "stable_release_modified": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        rc = 3

    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return rc


if __name__ == "__main__":
    sys.exit(main())
