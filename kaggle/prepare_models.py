#!/usr/bin/env python3
"""Prepare or verify public model weights for MASTER CLONE on Kaggle.

Downloads only public model checkpoints needed by MuseTalk 1.5 and OpenVoice V2.
Does not touch private biometric media, does not render, and does not promote any clone.
"""
from __future__ import annotations

import argparse
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
FACE_PARSE_SHA256 = "468e13ca13a9b43cc0881a9f99083a430e9c0a38abd935431d1c28ee94b26567"


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


def expected_musetalk_paths() -> list[tuple[Path, int]]:
    return [
        (MODELS / "musetalkV15" / "musetalk.json", 100),
        (MODELS / "musetalkV15" / "unet.pth", 1024 * 1024),
        (MODELS / "sd-vae" / "config.json", 100),
        (MODELS / "sd-vae" / "diffusion_pytorch_model.bin", 1024 * 1024),
        (MODELS / "whisper" / "config.json", 100),
        (MODELS / "whisper" / "pytorch_model.bin", 1024 * 1024),
        (MODELS / "whisper" / "preprocessor_config.json", 100),
        (MODELS / "dwpose" / "dw-ll_ucoco_384.pth", 1024 * 1024),
        (MODELS / "syncnet" / "latentsync_syncnet.pt", 1024 * 1024),
        (MODELS / "face-parse-bisent" / "79999_iter.pth", 1024 * 1024),
        (MODELS / "face-parse-bisent" / "resnet18-5c106cde.pth", 1024 * 1024),
    ]


def expected_openvoice_paths() -> list[tuple[Path, int]]:
    ckpt = OPENVOICE / "checkpoints_v2"
    return [
        (ckpt / "converter" / "config.json", 100),
        (ckpt / "converter" / "checkpoint.pth", 1024 * 1024),
    ]


def verify_existing() -> tuple[list[dict], list[dict]]:
    muse = [require_file(path, min_bytes=min_bytes) for path, min_bytes in expected_musetalk_paths()]
    voice = [require_file(path, min_bytes=min_bytes) for path, min_bytes in expected_openvoice_paths()]
    return muse, voice


def ensure_face_parse_checkpoint(face_parse: Path) -> None:
    if face_parse.is_file() and face_parse.stat().st_size >= 1024 * 1024:
        digest = sha256_file(face_parse)
        if digest == FACE_PARSE_SHA256:
            return
        face_parse.unlink(missing_ok=True)

    try:
        try:
            import gdown  # noqa: F401
        except Exception:
            run([sys.executable, "-m", "pip", "install", "-q", "gdown"])
        run([sys.executable, "-m", "gdown", "--id", "154JgKpzCPW82qINcVieuPH3fZ2e0P812", "-O", str(face_parse)])
    except subprocess.CalledProcessError as exc:
        print(f"Primary gdown source failed (exit={exc.returncode}); using verified Hugging Face mirror")
        face_parse.unlink(missing_ok=True)
        mirror = download_hf("ManyOtherFunctions/face-parse-bisent", "79999_iter.pth", face_parse.parent)
        if mirror.resolve() != face_parse.resolve():
            shutil.copy2(mirror, face_parse)

    info = require_file(face_parse, min_bytes=1024 * 1024)
    if info["sha256"] != FACE_PARSE_SHA256:
        face_parse.unlink(missing_ok=True)
        raise RuntimeError(
            "Face parser checkpoint SHA-256 mismatch: "
            f"expected {FACE_PARSE_SHA256}, got {info['sha256']}"
        )


def prepare_musetalk() -> list[dict]:
    if not (MUSETALK / "scripts" / "inference.py").is_file():
        raise RuntimeError(f"MuseTalk repository missing at {MUSETALK}")

    download_hf("TMElyralab/MuseTalk", "musetalkV15/musetalk.json", MODELS)
    download_hf("TMElyralab/MuseTalk", "musetalkV15/unet.pth", MODELS)
    download_hf("stabilityai/sd-vae-ft-mse", "config.json", MODELS / "sd-vae")
    download_hf("stabilityai/sd-vae-ft-mse", "diffusion_pytorch_model.bin", MODELS / "sd-vae")

    for name in ("config.json", "pytorch_model.bin", "preprocessor_config.json"):
        download_hf("openai/whisper-tiny", name, MODELS / "whisper")

    download_hf("yzd-v/DWPose", "dw-ll_ucoco_384.pth", MODELS / "dwpose")
    download_hf("ByteDance/LatentSync", "latentsync_syncnet.pt", MODELS / "syncnet")

    face_dir = MODELS / "face-parse-bisent"
    face_dir.mkdir(parents=True, exist_ok=True)
    resnet = face_dir / "resnet18-5c106cde.pth"
    if not resnet.is_file() or resnet.stat().st_size < 1024 * 1024:
        run(["curl", "-fL", "https://download.pytorch.org/models/resnet18-5c106cde.pth", "-o", str(resnet)])

    face_parse = face_dir / "79999_iter.pth"
    ensure_face_parse_checkpoint(face_parse)

    return [require_file(path, min_bytes=min_bytes) for path, min_bytes in expected_musetalk_paths()]


def prepare_openvoice() -> list[dict]:
    if not (OPENVOICE / "openvoice" / "api.py").is_file():
        raise RuntimeError(f"OpenVoice repository missing at {OPENVOICE}")
    ckpt = OPENVOICE / "checkpoints_v2"
    snapshot_download(
        repo_id="myshell-ai/OpenVoiceV2",
        allow_patterns=["converter/config.json", "converter/checkpoint.pth"],
        local_dir=str(ckpt),
    )
    return [require_file(path, min_bytes=min_bytes) for path, min_bytes in expected_openvoice_paths()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare public MASTER CLONE model weights")
    ap.add_argument("--verify-only", action="store_true", help="Verify existing public weights without network downloads")
    args = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found in PATH")

    if not (MUSETALK / "scripts" / "inference.py").is_file():
        raise RuntimeError(f"MuseTalk repository missing at {MUSETALK}")
    if not (OPENVOICE / "openvoice" / "api.py").is_file():
        raise RuntimeError(f"OpenVoice repository missing at {OPENVOICE}")

    if args.verify_only:
        muse, voice = verify_existing()
    else:
        muse = prepare_musetalk()
        voice = prepare_openvoice()

    report = {
        "schema": "zaskaleta-kaggle-model-prepare-v1",
        "mode": "verify_only" if args.verify_only else "download_and_verify",
        "musetalk_root": str(MUSETALK),
        "openvoice_root": str(OPENVOICE),
        "ffmpeg": ffmpeg,
        "musetalk_files": muse,
        "openvoice_files": voice,
        "required_public_model_files": len(muse) + len(voice),
        "all_required_public_models_verified": True,
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
