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
import zipfile
from pathlib import Path

WORK = Path("/kaggle/working")
INPUT_ROOT = Path("/kaggle/input")
REPO = WORK / "zaskaleta-ai-twin-colab"
REPO_URL = "https://github.com/sergokharkov/zaskaleta-ai-twin-colab.git"
PY = WORK / "clone311" / "bin" / "python"
STATUS = WORK / "zaskaleta_autopilot_status.json"
PRIVATE_MANIFEST = WORK / "private_asset_manifest.json"
PREFERRED_PRIVATE_ROOT = Path(os.environ.get("ZASKALETA_PRIVATE_ASSET_ROOT", "/kaggle/input/zaskaleta-master-clone-private"))
RUNTIME_PRIVATE_ROOT = WORK / "_runtime_private_assets"
REQUIRED_MARKERS = {
    "Zaskaleta_AI_Voice_Master.mp3",
    "MASTER_BEHAVIOR_01.mp4",
    "MASTER_BEHAVIOR_02.mp4",
}


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 7200) -> None:
    print("$", " ".join(str(x) for x in cmd))
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def mounted_input_dirs() -> list[str]:
    if not INPUT_ROOT.is_dir():
        return []
    return sorted(p.name for p in INPUT_ROOT.iterdir() if p.is_dir())


def has_required_markers(root: Path) -> bool:
    if not root.is_dir():
        return False
    found = {p.name for p in root.rglob("*") if p.is_file() and p.name in REQUIRED_MARKERS}
    return REQUIRED_MARKERS.issubset(found)


def zip_has_required_markers(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            basenames = {Path(name).name for name in zf.namelist() if not name.endswith("/")}
        return REQUIRED_MARKERS.issubset(basenames)
    except (OSError, zipfile.BadZipFile):
        return False


def resolve_private_root() -> tuple[Path | None, str]:
    if has_required_markers(PREFERRED_PRIVATE_ROOT):
        return PREFERRED_PRIVATE_ROOT, "preferred_path"

    if INPUT_ROOT.is_dir():
        for candidate in sorted((p for p in INPUT_ROOT.iterdir() if p.is_dir()), key=lambda p: p.name):
            if has_required_markers(candidate):
                return candidate, "auto_discovered_directory"

        for archive in sorted(INPUT_ROOT.rglob("*.zip")):
            if not archive.is_file() or not zip_has_required_markers(archive):
                continue
            if RUNTIME_PRIVATE_ROOT.exists():
                shutil.rmtree(RUNTIME_PRIVATE_ROOT)
            RUNTIME_PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(RUNTIME_PRIVATE_ROOT)
            if has_required_markers(RUNTIME_PRIVATE_ROOT):
                return RUNTIME_PRIVATE_ROOT, "auto_extracted_zip"

    return None, "not_found"


def write_status(private_root: Path | None = None, **extra) -> None:
    payload = {
        "schema": "zaskaleta-kaggle-autopilot-status-v3",
        "repo": str(REPO),
        "private_asset_root": str(private_root) if private_root else None,
        "mounted_input_dirs": mounted_input_dirs(),
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
    private_root: Path | None = None
    try:
        ensure_repo()
        bootstrap = REPO / "kaggle" / "auto_prepare.py"
        if not bootstrap.is_file():
            raise RuntimeError(f"Missing automatic prepare script: {bootstrap}")

        run([sys.executable, str(bootstrap)], cwd=REPO)

        if not PY.is_file():
            raise RuntimeError("clone311 Python missing after auto_prepare")

        run([str(PY), str(REPO / "kaggle" / "prepare_models.py"), "--verify-only"], cwd=REPO)
        run([str(PY), str(REPO / "kaggle" / "preflight.py")], cwd=REPO)

        private_root, discovery_mode = resolve_private_root()
        if private_root is None:
            write_status(
                private_root=None,
                automatic_prepare_complete=True,
                public_models_verified=True,
                preflight_passed=True,
                private_assets_present=False,
                private_assets_validated=False,
                private_asset_discovery_mode=discovery_mode,
                render_started=False,
                state="WAITING_FOR_PRIVATE_ASSETS",
            )
            return 0

        validator = REPO / "kaggle" / "validate_private_assets.py"
        if not validator.is_file():
            raise RuntimeError(f"Missing private asset validator: {validator}")

        run([
            str(PY), str(validator),
            "--root", str(private_root),
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
            private_root=private_root,
            automatic_prepare_complete=True,
            public_models_verified=True,
            preflight_passed=True,
            private_assets_present=True,
            private_assets_validated=True,
            private_asset_discovery_mode=discovery_mode,
            private_asset_identity_count=manifest.get("identity_count"),
            private_asset_approved_motion_count=manifest.get("approved_motion_count"),
            render_started=False,
            first_gate_seconds=[8, 15],
            state="READY_FOR_FIRST_GATE_RENDER",
        )
        return 0
    except Exception as exc:
        write_status(
            private_root=private_root,
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
