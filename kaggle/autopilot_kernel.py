#!/usr/bin/env python3
"""Kaggle autopilot entrypoint for MASTER CLONE.

This script is designed to be submitted as a private Kaggle kernel by CI.
It prepares the environment and public model weights automatically, validates
mounted private MASTER CLONE assets fail-closed, and stops before render until
an explicit render stage is available and all gates are satisfied.

Safety invariants:
- no raw biometric media is uploaded to GitHub
- no auto-promotion
- no paid GPU provisioning
- no render starts without validated private assets
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path("/kaggle/working")
REPO = WORK / "zaskaleta-ai-twin-colab"
REPO_URL = "https://github.com/sergokharkov/zaskaleta-ai-twin-colab.git"
PY = WORK / "clone311" / "bin" / "python"
STATUS = WORK / "zaskaleta_autopilot_status.json"
PRIVATE_MANIFEST = WORK / "private_asset_manifest.json"
PRIVATE_ROOT = Path(os.environ.get("ZASKALETA_PRIVATE_ASSET_ROOT", "/kaggle/input/zaskaleta-master-clone-private"))


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 7200) -> None:
    print("$", " ".join(str(x) for x in cmd))
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def write_status(**extra) -> None:
    payload = {
        "schema": "zaskaleta-kaggle-autopilot-status-v2",
        "repo": str(REPO),
        "private_asset_root": str(PRIVATE_ROOT),
        "auto_promote": False,
        "paid_gpu_provisioned": False,
        "raw_biometrics_written_to_github": False,
        **extra,
    }
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def ensure_repo() -> None:
    if (REPO / ".git").is_dir():
        run(["git", "-C", str(REPO), "fetch", "origin", "main", "--depth", "1"])
        run(["git", "-C", str(REPO), "reset", "--hard", "origin/main"])
        return
    if REPO.exists():
        shutil.rmtree(REPO)
    run(["git", "clone", "--depth", "1", REPO_URL, str(REPO)])


def main() -> int:
    try:
        ensure_repo()
        bootstrap = REPO / "kaggle" / "auto_prepare.py"
        if not bootstrap.is_file():
            raise RuntimeError(f"Missing automatic prepare script: {bootstrap}")

        # First call uses system Python because clone311 may not exist yet.
        run([sys.executable, str(bootstrap)], cwd=REPO)

        if not PY.is_file():
            raise RuntimeError("clone311 Python missing after auto_prepare")

        # Re-run final model verification and preflight in the pinned env.
        run([str(PY), str(REPO / "kaggle" / "prepare_models.py"), "--verify-only"], cwd=REPO)
        run([str(PY), str(REPO / "kaggle" / "preflight.py")], cwd=REPO)

        private_present = PRIVATE_ROOT.is_dir() and any(p.is_file() for p in PRIVATE_ROOT.rglob("*"))
        if not private_present:
            write_status(
                automatic_prepare_complete=True,
                public_models_verified=True,
                preflight_passed=True,
                private_assets_present=False,
                private_assets_validated=False,
                render_started=False,
                state="WAITING_FOR_PRIVATE_ASSETS",
            )
            return 0

        validator = REPO / "kaggle" / "validate_private_assets.py"
        if not validator.is_file():
            raise RuntimeError(f"Missing private asset validator: {validator}")

        run([
            str(PY), str(validator),
            "--root", str(PRIVATE_ROOT),
            "--profile", str(REPO / "content" / "clone_reference_profile.json"),
            "--package", str(REPO / "content" / "master_clone_package.json"),
            "--output", str(PRIVATE_MANIFEST),
        ], cwd=REPO, timeout=3600)

        manifest = json.loads(PRIVATE_MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("validated") is not True:
            raise RuntimeError("private asset manifest did not validate")
        if manifest.get("auto_promote") is not False or manifest.get("render_started") is not False:
            raise RuntimeError("private asset validator safety contract weakened")

        write_status(
            automatic_prepare_complete=True,
            public_models_verified=True,
            preflight_passed=True,
            private_assets_present=True,
            private_assets_validated=True,
            private_asset_identity_count=manifest.get("identity_count"),
            private_asset_approved_motion_count=manifest.get("approved_motion_count"),
            render_started=False,
            first_gate_seconds=[8, 15],
            state="READY_FOR_FIRST_GATE_RENDER",
        )
        return 0
    except Exception as exc:
        write_status(
            automatic_prepare_complete=False,
            private_assets_validated=False,
            render_started=False,
            state="FAILED_CLOSED",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
