#!/usr/bin/env python3
"""Resumable one-command Kaggle preparation for MASTER CLONE.

Runs bootstrap -> public model preparation -> verify-only -> final preflight.
It never touches private biometric media, never renders, never promotes a clone,
and never provisions paid GPU. Safe to re-run after a Kaggle refresh/reconnect.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("KAGGLE_WORKING", "/kaggle/working")).resolve()
PY = WORK / "clone311" / "bin" / "python"
STATE = WORK / "zaskaleta_auto_prepare_state.json"
LOCK = WORK / "zaskaleta_auto_prepare.lock"


def write_state(stage: str, ok: bool | None, detail: str = "") -> None:
    payload = {
        "schema": "zaskaleta-kaggle-auto-prepare-state-v1",
        "stage": stage,
        "ok": ok,
        "detail": detail,
        "updated_at_epoch": int(time.time()),
        "private_biometric_media_touched": False,
        "render_started": False,
        "auto_promote": False,
        "paid_gpu_provisioned": False,
    }
    STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run(stage: str, cmd: list[str], timeout: int = 7200) -> None:
    write_state(stage, None, "running")
    print("$", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), timeout=timeout)
    if proc.returncode != 0:
        write_state(stage, False, f"exit_code={proc.returncode}")
        raise SystemExit(proc.returncode)
    write_state(stage, True, "complete")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        try:
            age = time.time() - LOCK.stat().st_mtime
        except OSError:
            age = 0
        if age < 4 * 3600:
            raise SystemExit(f"Auto-prepare lock already exists: {LOCK}")
        LOCK.unlink(missing_ok=True)

    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        # Bootstrap is idempotent and creates clone311 if needed.
        bootstrap_py = str(PY) if PY.is_file() else sys.executable
        run("bootstrap", [bootstrap_py, str(ROOT / "kaggle" / "bootstrap.py")])

        if not PY.is_file():
            raise SystemExit(f"Expected Python 3.11 environment missing after bootstrap: {PY}")

        run("prepare_public_models", [str(PY), str(ROOT / "kaggle" / "prepare_models.py")])
        run("verify_public_models", [str(PY), str(ROOT / "kaggle" / "prepare_models.py"), "--verify-only"])
        run("final_preflight", [str(PY), str(ROOT / "kaggle" / "preflight.py")])

        result = {
            "schema": "zaskaleta-kaggle-auto-prepare-result-v1",
            "automatic_prepare_complete": True,
            "preflight_passed": True,
            "public_models_verified": True,
            "ready_for_private_asset_mount": True,
            "private_biometric_media_touched": False,
            "render_started": False,
            "auto_promote": False,
            "paid_gpu_provisioned": False,
            "state_file": str(STATE),
        }
        STATE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
