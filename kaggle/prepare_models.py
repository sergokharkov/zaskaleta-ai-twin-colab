#!/usr/bin/env python3
"""Prepare public model weights for MASTER CLONE on Kaggle.

Downloads only public model checkpoints needed by MuseTalk 1.5 and OpenVoice V2.
Does not touch private biometric media, does not render, and does not promote any clone.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

WORK = Path(os.environ.get("KAGGLE_WORKING", "/kaggle/working")).resolve()
MUSETALK = Path(os.environ.get("MUSETALK_ROOT", WORK / "MuseTalk")).resolve()
OPENVOICE = Path(os.environ.get("OPENVOICE_ROOT", WORK / "OpenVoice")).resolve()
MODELS = MUSETALK / "models"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> None:
    print("$", " ".join(str(x) for x in cmd))
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def require_file(path: Path, *, min_bytes: int = 64) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size < min_bytes:
        raise RuntimeError(f"Model file is unexpectedly small: {path} ({size} bytes)")
    return {"path": str(path), "size_bytes": size, "sha256": sha256_file(path)}


def download_hf(repo_id: str, filename: str, local_dir: Path) -> Path:
    local_dir.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(local_dir))
    return Path(path)


def prepare_musetalk() -> list[dict]:
    if not (MUSETALK / "scripts" / "inference.py").is_file():
        raise RuntimeError(f"MuseTalk repository missing at {MUSETALK}")

    files: list[dict] = []
    files.append(require_file(download_hf("TMElyralab/MuseTalk", "musetalkV15/musetalk.json", MODELS), min_bytes=100))
    files.append(require_file(download_hf("TMElyralab/MuseTalk", "musetalkV15/unet.pth", MODELS), min_bytes=1024 * 1024))

    files.append(require_file(download_hf("stabilityai/sd-vae-ft-mse", "config.json", MODELS / "sd-vae"), min_bytes=100))
    files.append(require_file(download_hf("stabilityai/sd-vae-ft-mse", "diffusion_pytorch_model.bin", MODELS / "sd-vae"), min_bytes=1024 * 1024))

    for name in ("config.json", "pytorch_model.bin", "preprocessor_config.json"):
        min_bytes = 1024 * 1024 if name.endswith(".bin") else 100
        files.append(require_file(download_hf("openai/whisper-tiny", name, MODELS / "whisper"), min_bytes=min_bytes))

    files.append(require_file(download_hf("yzd-v/DWPose", "dw-ll_ucoco_384.pth", MODELS / "dwpose"), min_bytes=1024 * 1024))
    files.append(require_file(download_hf("ByteDance/LatentSync", "latentsync_syncnet.pt", MODELS / "syncnet"), min_bytes=1024 * 1024))

    face_dir = MODELS / "face-parse-bisent"
    face_dir.mkdir(parents=True, exist_ok=True)
    resnet = face_dir / "resnet18-5c106cde.pth"
    if not resnet.is_file() or resnet.stat().st_size < 1024 * 1024:
        run(["curl", "-fL", "https://download.pytorch.org/models/resnet18-5c106cde.pth", "-o", str(resnet)])
    files.append(require_file(resnet, min_bytes=1024 * 1024))

    face_parse = face_dir / "79999_iter.pth"
    if not face_parse.is_file() or face_parse.stat().st_size < 1024 * 1024:
        py = shutil.which("python") or sys.executable
        try:
            import gdown  # noqa: F401
        except Exception:
            run([py, "-m", "pip", "install", "-q", "gdown"])
        run([py, "-m", "gdown", "--id", "154JgKpzCPW82qINcVieuPH3fZ2e0P812", "-O", str(face_parse)])
    files.append(require_file(face_parse, min_bytes=1024 * 1024))
    return files


def prepare_openvoice() -> list[dict]:
    if not (OPENVOICE / "openvoice" / "api.py").is_file():
        raise RuntimeError(f"OpenVoice repository missing at {OPENVOICE}")
    ckpt = OPENVOICE / "checkpoints_v2"
    snapshot_download(
        repo_id="myshell-ai/OpenVoiceV2",
        allow_patterns=["converter/config.json", "converter/checkpoint.pth"],
        local_dir=str(ckpt),
    )
    return [
        require_file(ckpt / "converter" / "config.json", min_bytes=100),
        require_file(ckpt / "converter" / "checkpoint.pth", min_bytes=1024 * 1024),
    ]


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found in PATH")

    muse = prepare_musetalk()
    voice = prepare_openvoice()
    report = {
        "schema": "zaskaleta-kaggle-model-prepare-v1",
        "musetalk_root": str(MUSETALK),
        "openvoice_root": str(OPENVOICE),
        "ffmpeg": ffmpeg,
        "musetalk_files": muse,
        "openvoice_files": voice,
        "private_biometric_media_touched": False,
        "render_started": False,
        "auto_promote": False,
        "ready_for_private_asset_mount": True,
    }
    out = WORK / "zaskaleta_model_prepare_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"MODEL_PREPARE_REPORT={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
