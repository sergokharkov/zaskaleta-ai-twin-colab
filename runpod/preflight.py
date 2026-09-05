#!/usr/bin/env python3
"""Non-destructive preflight checks for the Zaskaleta AI Clone RunPod API.

This script never prints secret values. It validates only configuration presence,
required repository files, writable storage, and clone quality/integrity assets.
Exit code 0 means the host is structurally ready to start the API; GPU model
installation and paid RunPod capacity are intentionally outside this check.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("AI_TWIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
STORAGE = Path(os.environ.get("AI_TWIN_STORAGE", "/workspace/zaskaleta-storage")).resolve()
TOKEN = os.environ.get("AI_TWIN_TOKEN", "").strip()
DRIVE_SYNC = os.environ.get("AI_TWIN_DRIVE_SYNC", "0").strip().lower() in {"1", "true", "yes", "on"}
DRIVE_FOLDER = os.environ.get("AI_TWIN_DRIVE_FOLDER_ID", "1_7G-rAGQ80Vpe_CWdGOzPIg0nuprDp3s").strip()
CORS = [x.strip() for x in os.environ.get("AI_TWIN_CORS_ORIGINS", "https://ai.zaskaleta.net").split(",") if x.strip()]

REQUIRED_FILES = [
    "runpod/api_server.py",
    "runpod/requirements-api.txt",
    "worker/run_clone_v2_test.py",
    "worker/evaluate_clone_release.py",
    "worker/build_master_clone_package.py",
    "worker/extract_motion_profile.py",
    "worker/validate_motion_candidate.py",
    "content/master_clone_package.json",
    "content/clone_quality_gate_v1.json",
]


def result(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def main() -> int:
    checks: list[dict] = []
    checks.append(result("root_exists", ROOT.is_dir(), str(ROOT)))

    missing = [rel for rel in REQUIRED_FILES if not (ROOT / rel).is_file()]
    checks.append(result("required_files", not missing, "all present" if not missing else "missing: " + ", ".join(missing)))

    gate_path = ROOT / "content/clone_quality_gate_v1.json"
    gate_ok = False
    gate_detail = "missing"
    if gate_path.is_file():
        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            gate_ok = isinstance(gate, dict) and bool(gate)
            gate_detail = "valid JSON object" if gate_ok else "empty/invalid structure"
        except Exception as exc:
            gate_detail = f"invalid JSON: {type(exc).__name__}"
    checks.append(result("quality_gate", gate_ok, gate_detail))

    token_ok = len(TOKEN) >= 24
    checks.append(result("api_token", token_ok, "configured" if token_ok else "missing or too short (minimum 24 chars)"))

    cors_ok = bool(CORS) and all(origin.startswith("https://") for origin in CORS) and "*" not in CORS
    checks.append(result("cors", cors_ok, ", ".join(CORS) if CORS else "not configured"))

    checks.append(result("drive_folder", bool(DRIVE_FOLDER), "configured" if DRIVE_FOLDER else "missing"))
    if DRIVE_SYNC:
        has_google_creds = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip())
        checks.append(result("drive_credentials", has_google_creds, "configured" if has_google_creds else "AI_TWIN_DRIVE_SYNC is enabled but GOOGLE_APPLICATION_CREDENTIALS is missing"))
    else:
        checks.append(result("drive_credentials", True, "drive sync disabled; credentials not required"))

    storage_ok = False
    storage_detail = str(STORAGE)
    try:
        STORAGE.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="zaskaleta-preflight-", dir=STORAGE, delete=True):
            pass
        storage_ok = True
    except Exception as exc:
        storage_detail = f"not writable: {type(exc).__name__}"
    checks.append(result("storage_writable", storage_ok, storage_detail))

    failed = [item for item in checks if not item["ok"]]
    report = {
        "service": "zaskaleta-ai-clone",
        "preflight": "pass" if not failed else "fail",
        "paid_gpu_started": False,
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
