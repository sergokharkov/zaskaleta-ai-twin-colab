#!/usr/bin/env python3
"""One-command Kaggle bootstrap for MASTER CLONE.

Designed for Kaggle GPU notebooks. It is idempotent, never prints secret values,
never uploads biometric media, never promotes a clone, and never provisions paid GPU.
It prepares a Python 3.11 environment compatible with the validated clone stack,
installs the pinned CUDA/Torch/OpenMMLab dependencies, clones OpenVoice/MuseTalk,
and runs a fail-closed preflight.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("KAGGLE_WORKING", "/kaggle/working")).resolve()
VENV = WORK / "clone311"
PY = VENV / "bin" / "python"
MUSETALK = WORK / "MuseTalk"
OPENVOICE = WORK / "OpenVoice"


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> None:
    safe = [str(x) for x in cmd]
    print("$", " ".join(safe))
    subprocess.run(safe, cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def output(cmd: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.check_output([str(x) for x in cmd], cwd=str(cwd) if cwd else None, text=True).strip()


def ensure_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv
    run([sys.executable, "-m", "pip", "install", "-q", "uv"])
    uv = shutil.which("uv") or "/root/.local/bin/uv"
    if not Path(uv).exists():
        raise RuntimeError("uv installation completed but executable was not found")
    return uv


def ensure_python311(uv: str) -> None:
    if PY.is_file():
        return
    run([uv, "python", "install", "3.11"])
    run([uv, "venv", "--python", "3.11", str(VENV)])


def ensure_repo(url: str, path: Path) -> None:
    if (path / ".git").is_dir():
        run(["git", "-C", str(path), "fetch", "--depth", "1", "origin", "HEAD"])
        return
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite non-git path: {path}")
    run(["git", "clone", "--depth", "1", url, str(path)])


def install_core(uv: str) -> None:
    run([
        uv, "pip", "install", "--python", str(PY), "--link-mode=copy",
        "torch==2.0.1+cu118", "torchvision==0.15.2+cu118", "torchaudio==2.0.2+cu118",
        "--index-url", "https://download.pytorch.org/whl/cu118",
    ])
    run([
        uv, "pip", "install", "--python", str(PY), "--link-mode=copy",
        "numpy==1.23.5", "opencv-python==4.9.0.80", "matplotlib==3.7.5",
        "diffusers==0.30.2", "transformers==4.39.2", "huggingface_hub==0.30.2",
        "mmengine==0.10.7", "scipy==1.10.1", "soundfile==0.12.1",
        "librosa==0.10.1", "einops==0.7.0", "omegaconf==2.3.0", "tqdm==4.66.5",
        "accelerate==0.28.0", "gdown", "requests", "imageio[ffmpeg]",
        "ffmpeg-python", "moviepy",
        "pip==24.0", "setuptools==68.2.2", "wheel",
    ])
    run([
        str(PY), "-m", "pip", "install", "mmcv==2.0.1",
        "-f", "https://download.openmmlab.com/mmcv/dist/cu118/torch2.0/index.html",
    ])
    run([str(PY), "-m", "pip", "install", "mmdet==3.1.0", "mmpose==1.1.0"])


def install_openvoice() -> None:
    ensure_repo("https://github.com/myshell-ai/OpenVoice.git", OPENVOICE)
    # OpenVoice upstream pins an old faster-whisper/av stack that source-builds
    # av==10 on Kaggle Python 3.11. MASTER CLONE only needs ToneColorConverter,
    # so install the package without upstream extras and add the pure/runtime
    # dependencies required by openvoice.api. Watermarking is disabled by our
    # worker adapter, so wavmark/faster-whisper/gradio are intentionally omitted.
    run([
        str(PY), "-m", "pip", "install",
        "eng_to_ipa==0.0.2", "inflect==7.0.0", "unidecode==1.3.7",
        "pypinyin==0.50.0", "cn2an==0.5.22", "jieba==0.42.1", "langid==1.1.6",
    ])
    run([str(PY), "-m", "pip", "install", "--no-deps", "-e", str(OPENVOICE)])
    run([str(PY), "-c", "from openvoice.api import ToneColorConverter; print('OpenVoice ToneColorConverter: OK')"])


def install_musetalk() -> None:
    ensure_repo("https://github.com/TMElyralab/MuseTalk.git", MUSETALK)


def run_preflight() -> int:
    env = os.environ.copy()
    env["MUSETALK_ROOT"] = str(MUSETALK)
    proc = subprocess.run(
        [str(PY), str(ROOT / "kaggle" / "preflight.py")],
        cwd=str(ROOT), env=env, text=True,
    )
    return int(proc.returncode)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare Kaggle for Zaskaleta MASTER CLONE")
    ap.add_argument("--skip-install", action="store_true", help="Only run preflight on an existing environment")
    args = ap.parse_args()

    if not ROOT.is_dir():
        raise SystemExit("Repository root missing")
    WORK.mkdir(parents=True, exist_ok=True)

    if not args.skip_install:
        uv = ensure_uv()
        ensure_python311(uv)
        install_core(uv)
        install_openvoice()
        install_musetalk()

    rc = run_preflight()
    print(json.dumps({
        "schema": "zaskaleta-kaggle-bootstrap-result-v1",
        "repo": str(ROOT),
        "python": str(PY),
        "musetalk_root": str(MUSETALK),
        "openvoice_root": str(OPENVOICE),
        "paid_gpu_started_by_bootstrap": False,
        "biometric_media_uploaded_by_bootstrap": False,
        "auto_promote": False,
        "preflight_passed": rc == 0,
    }, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
