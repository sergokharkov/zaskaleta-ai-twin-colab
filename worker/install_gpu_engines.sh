#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/content/zaskaleta-ai-twin-colab/worker}"
MUSETALK_ROOT="${MUSETALK_ROOT:-/content/MuseTalk}"
VENV_DIR="${VENV_DIR:-/content/ai-twin-py311}"

apt-get update
apt-get install -y --no-install-recommends ffmpeg git curl
rm -rf /var/lib/apt/lists/*

python3 -m pip install --upgrade pip uv
python3 -m uv venv --python 3.11 --seed "${VENV_DIR}"
PYTHON_BIN="${VENV_DIR}/bin/python"
export PATH="${VENV_DIR}/bin:${PATH}"

"${PYTHON_BIN}" -m pip install --upgrade pip setuptools wheel
"${PYTHON_BIN}" -m pip install -r "${APP_DIR}/requirements.txt"
"${PYTHON_BIN}" -m pip install -r "${APP_DIR}/requirements-gpu.txt"

if [ ! -d "${MUSETALK_ROOT}/.git" ]; then
  git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git "${MUSETALK_ROOT}"
fi

"${PYTHON_BIN}" -m pip install -r "${MUSETALK_ROOT}/requirements.txt"
"${PYTHON_BIN}" -m pip install -U "huggingface_hub[cli]" gdown

mkdir -p \
  "${MUSETALK_ROOT}/models/musetalkV15" \
  "${MUSETALK_ROOT}/models/syncnet" \
  "${MUSETALK_ROOT}/models/dwpose" \
  "${MUSETALK_ROOT}/models/face-parse-bisent" \
  "${MUSETALK_ROOT}/models/sd-vae" \
  "${MUSETALK_ROOT}/models/whisper"

hf download TMElyralab/MuseTalk \
  --local-dir "${MUSETALK_ROOT}/models" \
  --include "musetalkV15/musetalk.json" "musetalkV15/unet.pth"

hf download stabilityai/sd-vae-ft-mse \
  --local-dir "${MUSETALK_ROOT}/models/sd-vae" \
  --include "config.json" "diffusion_pytorch_model.bin"

hf download openai/whisper-tiny \
  --local-dir "${MUSETALK_ROOT}/models/whisper" \
  --include "config.json" "pytorch_model.bin" "preprocessor_config.json"

hf download yzd-v/DWPose \
  --local-dir "${MUSETALK_ROOT}/models/dwpose" \
  --include "dw-ll_ucoco_384.pth"

hf download ByteDance/LatentSync \
  --local-dir "${MUSETALK_ROOT}/models/syncnet" \
  --include "latentsync_syncnet.pt"

gdown --id 154JgKpzCPW82qINcVieuPH3fZ2e0P812 \
  -O "${MUSETALK_ROOT}/models/face-parse-bisent/79999_iter.pth"

curl -L https://download.pytorch.org/models/resnet18-5c106cde.pth \
  -o "${MUSETALK_ROOT}/models/face-parse-bisent/resnet18-5c106cde.pth"

"${PYTHON_BIN}" -c "import sys, torch; print('Python:', sys.version); print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available())"
echo "Zaskaleta AI Twin GPU engines installed with ${PYTHON_BIN}."
