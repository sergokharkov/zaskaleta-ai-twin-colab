#!/usr/bin/env python3
"""Non-destructive preflight checks for the Zaskaleta AI Clone RunPod API.

This script never prints secret values, never provisions GPU resources, and never promotes a clone.
It validates repository readiness, storage, security-sensitive runtime configuration, and the static
Clone v2 safety contract required before the first paid 8-15 second GPU test is even considered.
"""

from __future__ import annotations

import json
import os
import subprocess
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
    "runpod/clone_v2_readiness.py",
    "worker/run_clone_v2_test.py",
    "worker/lipsync_musetalk.py",
    "worker/generate_scene_speech.py",
    "worker/evaluate_clone_release.py",
    "worker/evaluate_identity_view_results.py",
    "worker/compare_clone_challenger.py",
    "worker/validate_clone_promotion_bundle.py",
    "worker/validate_clone_duration_progression.py",
    "worker/validate_clone_v2_temporal_output.py",
    "content/master_clone_package.json",
    "content/clone_quality_gate_v1.json",
    "content/clone_release_policy_v1.json",
    "content/render_face_guard_v1.json",
    "content/talking_temporal_guard_v1.json",
    "content/identity_view_holdout_v1.json",
    "content/clone_promotion_bundle_policy_v1.json",
    "content/clone_duration_gate_policy_v1.json",
    "content/storage_config.json",
]


def result(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
            gate = load_json(gate_path)
            gate_ok = (
                isinstance(gate, dict)
                and bool(gate)
                and gate.get("manual_approval_required") is True
                and gate.get("auto_promote_to_master") is False
            )
            gate_detail = "valid and manual-promotion locked" if gate_ok else "invalid or promotion policy weakened"
        except Exception as exc:
            gate_detail = f"invalid JSON: {type(exc).__name__}"
    checks.append(result("quality_gate", gate_ok, gate_detail))

    release_ok = False
    release_detail = "missing"
    release_path = ROOT / "content/clone_release_policy_v1.json"
    if release_path.is_file():
        try:
            release = load_json(release_path)
            release_ok = (
                float(release.get("identity_regression_tolerance", 1)) == 0.0
                and release.get("manual_promotion_required") is True
                and release.get("auto_promote") is False
            )
            release_detail = "zero identity regression and manual promotion enforced" if release_ok else "release policy weakened"
        except Exception as exc:
            release_detail = f"invalid JSON: {type(exc).__name__}"
    checks.append(result("release_identity_policy", release_ok, release_detail))

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

    readiness_ok = False
    readiness_detail = "clone_v2_readiness.py missing"
    readiness_script = ROOT / "runpod/clone_v2_readiness.py"
    if readiness_script.is_file():
        try:
            proc = subprocess.run(
                [sys.executable, str(readiness_script)],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            parsed = json.loads(proc.stdout) if proc.stdout.strip() else {}
            readiness_ok = (
                proc.returncode == 0
                and parsed.get("ready_for_paid_gpu_consideration") is True
                and parsed.get("paid_gpu_started") is False
                and parsed.get("manual_budget_approval_required_before_gpu_start") is True
                and parsed.get("first_test_gate_seconds") == [8, 15]
            )
            readiness_detail = "static Clone v2 readiness PASS; no GPU started" if readiness_ok else "static Clone v2 readiness failed or safety contract changed"
        except Exception as exc:
            readiness_detail = f"readiness check error: {type(exc).__name__}"
    checks.append(result("clone_v2_static_readiness", readiness_ok, readiness_detail))

    failed = [item for item in checks if not item["ok"]]
    report = {
        "service": "zaskaleta-ai-clone",
        "preflight": "pass" if not failed else "fail",
        "paid_gpu_started": False,
        "manual_budget_approval_required_before_gpu_start": True,
        "first_test_gate_seconds": [8, 15],
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
