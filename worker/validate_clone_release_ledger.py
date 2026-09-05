#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_STATUS = {"STABLE", "ROLLED_BACK"}
REQUIRED_PROVENANCE = {
    "code_commit",
    "quality_gate_sha256",
    "identity_holdout_sha256",
    "release_manifest_sha256",
    "approved_by",
    "approved_at",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str):
    raise SystemExit(message)


def validate(data):
    if data.get("schema") != "zaskaleta-clone-release-ledger-v1":
        fail("Unexpected release ledger schema")
    policy = data.get("policy") or {}
    expected = {
        "append_only": True,
        "stable_entries_immutable": True,
        "manual_promotion_required": True,
        "raw_biometric_media_forbidden": True,
    }
    for key, value in expected.items():
        if policy.get(key) is not value:
            fail(f"Release ledger policy weakened: {key}")
    if float(policy.get("identity_regression_tolerance", 1)) != 0.0:
        fail("Identity regression tolerance must remain exactly zero")

    releases = data.get("releases")
    if not isinstance(releases, list):
        fail("releases must be a list")
    seen = set()
    for entry in releases:
        if not isinstance(entry, dict):
            fail("Each release entry must be an object")
        version = entry.get("version")
        if not isinstance(version, str) or not version.strip() or version in seen:
            fail(f"Invalid or duplicate release version: {version!r}")
        seen.add(version)
        if entry.get("status") not in ALLOWED_STATUS:
            fail(f"Invalid release status for {version}")
        if entry.get("identity_regression") not in (0, 0.0):
            fail(f"Stable release {version} has non-zero identity regression")
        if entry.get("manual_approval") is not True:
            fail(f"Stable release {version} lacks explicit manual approval")
        provenance = entry.get("provenance") or {}
        missing = sorted(REQUIRED_PROVENANCE - set(provenance))
        if missing:
            fail(f"Release {version} missing provenance fields: {', '.join(missing)}")
        for key in ("quality_gate_sha256", "identity_holdout_sha256", "release_manifest_sha256"):
            value = provenance.get(key)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                fail(f"Release {version} has invalid {key}")
        commit = provenance.get("code_commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            fail(f"Release {version} has invalid code_commit")
        serialized = json.dumps(entry, sort_keys=True).lower()
        forbidden = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".wav", ".mp3", "signed_url", "download_url", "/mnt/", "/content/")
        if any(token in serialized for token in forbidden):
            fail(f"Release {version} contains raw/private media location metadata")

    current = data.get("current_stable")
    if current is not None:
        matches = [r for r in releases if r.get("version") == current]
        if len(matches) != 1 or matches[0].get("status") != "STABLE":
            fail("current_stable must reference exactly one STABLE ledger entry")
    return {r["version"]: r for r in releases}


def compare_append_only(old, new):
    old_entries = validate(old)
    new_entries = validate(new)
    for version, old_entry in old_entries.items():
        if version not in new_entries:
            fail(f"Immutable release removed from ledger: {version}")
        if new_entries[version] != old_entry:
            fail(f"Immutable release entry modified: {version}")
    if len(new_entries) < len(old_entries):
        fail("Release ledger is not append-only")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", default="content/clone_release_ledger_v1.json")
    parser.add_argument("--baseline")
    args = parser.parse_args()
    current = load(Path(args.ledger))
    validate(current)
    if args.baseline:
        baseline = Path(args.baseline)
        if baseline.exists():
            compare_append_only(load(baseline), current)
    print("Clone release ledger OK: append-only, immutable stable entries, zero identity regression, manual promotion only")


if __name__ == "__main__":
    main()
