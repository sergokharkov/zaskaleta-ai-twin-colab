#!/usr/bin/env python3
"""Validate controlled-learning candidate registries without opening media files.

This gate is intentionally metadata-only: raw biometric media must stay outside GitHub.
It fails closed if a registry weakens manual approval or embeds private media locations.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ALLOWED_TARGETS = {"MOTION", "TALKING", "VOICE", "IDENTITY_SUPPORT"}
ALLOWED_STATUSES = {"candidate", "candidate_hold", "approved", "rejected"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".wav", ".m4a", ".mp3"}
FORBIDDEN_LOCATION_FIELDS = {
    "path",
    "sourcePath",
    "rawPath",
    "localPath",
    "downloadUrl",
    "mediaUrl",
    "sourceUrl",
    "signedUrl",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def fail(message: str) -> None:
    raise SystemExit(message)


def validate_registry(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "zaskaleta-user-video-candidates-v1":
        fail(f"{path}: unsupported schema")

    policy = data.get("policy")
    if not isinstance(policy, dict):
        fail(f"{path}: policy must be an object")
    required_policy = {
        "rawMediaInGitHub": False,
        "autoApprove": False,
        "manualReviewRequired": True,
        "identityAnchorReplacementAllowed": False,
    }
    for key, expected in required_policy.items():
        if policy.get(key) is not expected:
            fail(f"{path}: policy.{key} must remain {expected!r}")

    promotion_targets = policy.get("allowedPromotionTargets")
    if not isinstance(promotion_targets, list) or not set(promotion_targets).issubset(ALLOWED_TARGETS):
        fail(f"{path}: invalid allowedPromotionTargets")

    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        fail(f"{path}: candidates must be a list")

    seen = set()
    for index, candidate in enumerate(candidates):
        label = f"{path}: candidate #{index}"
        if not isinstance(candidate, dict):
            fail(f"{label} must be an object")

        forbidden = FORBIDDEN_LOCATION_FIELDS.intersection(candidate)
        if forbidden:
            fail(f"{label} contains private/raw media location fields: {sorted(forbidden)}")

        filename = candidate.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            fail(f"{label} missing filename")
        filename = filename.strip()
        if filename != Path(filename).name or "/" in filename or "\\" in filename:
            fail(f"{label} filename must be a basename only, never a path")
        if Path(filename).suffix.lower() not in MEDIA_EXTENSIONS:
            fail(f"{label} has unsupported media extension")
        if filename in seen:
            fail(f"{label} duplicates filename {filename!r}")
        seen.add(filename)

        status = candidate.get("status")
        if status not in ALLOWED_STATUSES:
            fail(f"{label} invalid status {status!r}")

        targets = candidate.get("recommendedTargets")
        if not isinstance(targets, list) or not set(targets).issubset(ALLOWED_TARGETS):
            fail(f"{label} invalid recommendedTargets")

        notes = candidate.get("notes")
        if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
            fail(f"{label} notes must be a string list")

        approved = candidate.get("approvedForMasterClone")
        if approved is not False:
            fail(f"{label} approvedForMasterClone must remain false in candidate registries")

        # Optional provenance fingerprints are allowed, but never raw bytes or URLs.
        sha256 = candidate.get("sha256")
        if sha256 is not None and (not isinstance(sha256, str) or not HEX64.fullmatch(sha256.lower())):
            fail(f"{label} sha256 must be a 64-character hexadecimal digest")

    print(f"Validated {len(candidates)} controlled-learning candidates in {path}")
    return len(candidates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Registry JSON files")
    args = parser.parse_args()

    paths = [Path(p) for p in args.paths]
    if not paths:
        paths = sorted(Path("content").glob("user_video_candidates_*.json"))
    if not paths:
        fail("No controlled-learning candidate registries found")

    total = 0
    for path in paths:
        if not path.is_file():
            fail(f"Missing registry: {path}")
        total += validate_registry(path)
    print(f"Controlled-learning policy gate passed for {total} candidate records")


if __name__ == "__main__":
    main()
