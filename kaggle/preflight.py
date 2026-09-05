#!/usr/bin/env python3
"""Fail-closed Kaggle preflight for MASTER CLONE.

No model render, no paid provisioning, no storage mutation, no clone promotion.
Checks only the local Kaggle runtime and repository safety prerequisites.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUSETALK = Path(os.environ.get("MUSETALK_ROOT", "/kaggle/working/MuseTalk")).resolve()


def check(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def main() -> int:
    checks: list[dict] = []

    py_ok = sys.version_info[:2] == (3, 11)
    checks.append(check("python_3_11", py_ok, sys.version.split()[0]))

    try:
        import torch
        torch_ok = torch.__version__ == "2.0.1+cu118"
        checks.append(check("torch_version", torch_ok, torch.__version__))
        cuda_ok = torch.cuda.is_available()
        checks.append(check("cuda_available", cuda_ok, str(cuda_ok)))
        gpu_count = torch.cuda.device_count()
        checks.append(check("gpu_count", gpu_count >= 1, str(gpu_count)))
        gpu_names = [torch.cuda.get_device_name(i) for i in range(gpu_count)]
        checks.append(check("gpu_model", bool(gpu_names), ", ".join(gpu_names) if gpu_names else "none"))
    except Exception as exc:
        checks.append(check("torch_import", False, type(exc).__name__))

    versions = {
        "numpy": "1.23.5",
        "cv2": "4.9.0",
        "diffusers": "0.30.2",
        "transformers": "4.39.2",
        "huggingface_hub": "0.30.2",
        "mmengine": "0.10.7",
        "mmcv": "2.0.1",
        "mmdet": "3.1.0",
        "mmpose": "1.1.0",
    }
    for mod_name, expected in versions.items():
        try:
            mod = __import__(mod_name)
            actual = getattr(mod, "__version__", "unknown")
            checks.append(check(f"version:{mod_name}", actual == expected, str(actual)))
        except Exception as exc:
            checks.append(check(f"version:{mod_name}", False, type(exc).__name__))

    try:
        from openvoice.api import ToneColorConverter  # noqa: F401
        checks.append(check("openvoice_import", True, "ToneColorConverter available"))
    except Exception as exc:
        checks.append(check("openvoice_import", False, type(exc).__name__))

    inference = MUSETALK / "scripts" / "inference.py"
    checks.append(check("musetalk_inference", inference.is_file(), str(inference)))

    required_repo = [
        "worker/lipsync_musetalk.py",
        "worker/generate_scene_speech.py",
        "worker/voice_mms_openvoice.py",
        "worker/evaluate_clone_release.py",
        "content/master_clone_package.json",
        "content/clone_quality_gate_v1.json",
        "content/clone_release_policy_v1.json",
        "content/clone_duration_gate_policy_v1.json",
        "content/identity_view_holdout_v1.json",
    ]
    missing = [p for p in required_repo if not (ROOT / p).is_file()]
    checks.append(check("required_repo_files", not missing, "all present" if not missing else ", ".join(missing)))

    try:
        gate = json.loads((ROOT / "content/clone_quality_gate_v1.json").read_text(encoding="utf-8"))
        gate_ok = gate.get("manual_approval_required") is True and gate.get("auto_promote_to_master") is False
        checks.append(check("manual_promotion_lock", gate_ok, "manual approval required; auto-promote disabled" if gate_ok else "policy weakened"))
    except Exception as exc:
        checks.append(check("manual_promotion_lock", False, type(exc).__name__))

    try:
        release = json.loads((ROOT / "content/clone_release_policy_v1.json").read_text(encoding="utf-8"))
        ident_ok = float(release.get("identity_regression_tolerance", 1)) == 0.0
        checks.append(check("zero_identity_regression", ident_ok, str(release.get("identity_regression_tolerance"))))
    except Exception as exc:
        checks.append(check("zero_identity_regression", False, type(exc).__name__))

    try:
        duration = json.loads((ROOT / "content/clone_duration_gate_policy_v1.json").read_text(encoding="utf-8"))
        ordered = duration.get("ordered_gates")
        rules = duration.get("rules") or {}
        first = ordered[0] if isinstance(ordered, list) and ordered else {}
        first_ok = (
            first.get("id") == "gate_08_15"
            and first.get("min_seconds") == 8
            and first.get("max_seconds") == 15
            and rules.get("start_at_first_gate") is True
            and rules.get("sequential_only") is True
            and rules.get("skip_gate_allowed") is False
            and rules.get("manual_override_allowed") is False
            and float(rules.get("identity_regression_tolerance", 1)) == 0.0
        )
        detail = "8-15 first gate present; sequential/no-skip/zero-regression enforced" if first_ok else "first duration gate policy invalid"
        checks.append(check("first_duration_gate_8_15", first_ok, detail))
    except Exception as exc:
        checks.append(check("first_duration_gate_8_15", False, type(exc).__name__))

    # Static adapter syntax only; does not render or download private data.
    adapter_ok = True
    adapter_detail = "syntax ok"
    for rel in ("worker/lipsync_musetalk.py", "worker/generate_scene_speech.py", "worker/voice_mms_openvoice.py"):
        proc = subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / rel)], capture_output=True, text=True)
        if proc.returncode != 0:
            adapter_ok = False
            adapter_detail = f"syntax failed: {rel}"
            break
    checks.append(check("clone_adapter_syntax", adapter_ok, adapter_detail))

    failed = [c for c in checks if not c["ok"]]
    report = {
        "schema": "zaskaleta-kaggle-preflight-v1",
        "ready_for_model_download_and_private_asset_mount": not failed,
        "ready_for_render": False,
        "render_not_started": True,
        "paid_gpu_provisioned_by_preflight": False,
        "private_biometric_media_touched": False,
        "auto_promote": False,
        "manual_release_gate_required": True,
        "first_test_gate_seconds": [8, 15],
        "checks": checks,
        "failures": [c["check"] for c in failed],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
