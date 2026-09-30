#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

SCHEMA = "zaskaleta-c006-clone-reference-profile-v1"
CANDIDATE = "MASTER_CLONE_CANDIDATE_006"

ROLE_KEYS = (
    "identity_primary",
    "identity_secondary",
    "face_motion_primary",
    "talking_behavior_only",
    "body_movement_only",
    "voice_source_backup",
    "style_reference_only",
)


def find_exactly_one(root: Path, filename: str) -> Path:
    matches = [p for p in root.rglob(filename) if p.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"{filename}: expected exactly one private source, found {len(matches)}")
    return matches[0]


def resolve_list(root: Path, names):
    return [find_exactly_one(root, name) for name in names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--private-root", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = Path(args.private_root)
    if not root.is_dir():
        raise SystemExit("private root missing")

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    if profile.get("schema") != SCHEMA:
        raise SystemExit("wrong C006 profile schema")
    if profile.get("candidate_id") != CANDIDATE:
        raise SystemExit("wrong candidate")

    policy = profile.get("runtime_policy") or {}
    required_true = (
        "allowlist_only",
        "use_identity_primary_for_identity",
        "identity_secondary_evaluation_only",
        "face_motion_may_support_talking",
        "talking_behavior_must_not_replace_identity",
        "body_movement_must_not_replace_identity",
        "style_reference_must_not_replace_identity",
    )
    for key in required_true:
        if policy.get(key) is not True:
            raise SystemExit(f"runtime policy weakened: {key}")
    if policy.get("unknown_source_policy") != "DENY":
        raise SystemExit("unknown source policy must be DENY")
    if policy.get("pending_sources_ingestion_allowed") is not False:
        raise SystemExit("pending source ingestion must remain blocked")
    if policy.get("generated_media_ingestion_allowed") is not False:
        raise SystemExit("generated media ingestion must remain blocked")
    if policy.get("auto_discovery_grants_approval") is not False:
        raise SystemExit("auto-discovery approval forbidden")
    if policy.get("auto_promote") is not False or policy.get("stable_release_modified") is not False:
        raise SystemExit("promotion safety policy weakened")

    canonical_name = profile.get("canonical_identity_photo")
    primary_names = profile.get("identity_primary") or []
    if len(primary_names) != 12 or canonical_name not in primary_names:
        raise SystemExit("C006 identity primary contract invalid")

    resolved = {}
    all_allowed_names = set()

    for role in ROLE_KEYS:
        names = profile.get(role) or []
        paths = resolve_list(root, names)
        resolved[role] = [str(p) for p in paths]
        for name in names:
            if name in all_allowed_names:
                raise SystemExit(f"filename crosses roles: {name}")
            all_allowed_names.add(name)

    voice_name = profile.get("master_voice_filename")
    if not voice_name:
        raise SystemExit("master voice missing from profile")
    master_voice = find_exactly_one(root, voice_name)
    if voice_name in all_allowed_names:
        raise SystemExit("master voice duplicated in another role")
    all_allowed_names.add(voice_name)

    pending_names = profile.get("pending_excluded") or []
    if pending_names != ["VID_20260901_183436.mp4"]:
        raise SystemExit("pending exclusion contract changed")
    pending_present = []
    for name in pending_names:
        matches = [p for p in root.rglob(name) if p.is_file()]
        pending_present.extend(str(p) for p in matches)

    canonical_path = find_exactly_one(root, canonical_name)
    if str(canonical_path) not in resolved["identity_primary"]:
        raise SystemExit("canonical identity is not in resolved primary set")

    # Fail closed against ambiguous extra copies of allowlisted names.
    for name in sorted(all_allowed_names):
        count = sum(1 for p in root.rglob(name) if p.is_file())
        if count != 1:
            raise SystemExit(f"ambiguous approved source: {name} count={count}")

    result = {
        "schema": "zaskaleta-c006-private-runtime-assets-v1",
        "candidate_id": CANDIDATE,
        "canonical_identity_photo": str(canonical_path),
        "master_voice": str(master_voice),
        "roles": resolved,
        "pending_present_but_blocked": pending_present,
        "allowlisted_source_count": len(all_allowed_names),
        "pending_ingestion_allowed": False,
        "unknown_source_policy": "DENY",
        "auto_discovery_grants_approval": False,
        "gpu_launch_allowed": False,
        "auto_promote": False,
        "stable_release_modified": False
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("C006_PRIVATE_RUNTIME_ASSETS_RESOLVED")
    print("identity_primary=12")
    print("pending_ingestion_allowed=false")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("FAILED_CLOSED:", repr(exc), file=sys.stderr)
        raise SystemExit(1)
