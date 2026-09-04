#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/content/zaskaleta-ai-twin-colab/worker}"
MUSETALK_ROOT="${MUSETALK_ROOT:-/content/MuseTalk}"
VENV_DIR="${VENV_DIR:-/content/ai-twin-py311}"
OPENVOICE_ROOT="${OPENVOICE_ROOT:-/content/OpenVoice}"

step() { echo; echo "========== $1 =========="; }

step "System packages"
apt-get update
apt-get install -y --no-install-recommends ffmpeg git curl
rm -rf /var/lib/apt/lists/*

step "Python 3.11 environment"
python3 -m pip install --upgrade pip uv
if [ -x "${VENV_DIR}/bin/python" ]; then
  echo "Reusing existing venv: ${VENV_DIR}"
else
  rm -rf "${VENV_DIR}"
  python3 -m uv venv --python 3.11 --seed "${VENV_DIR}"
fi
PYTHON_BIN="${VENV_DIR}/bin/python"
export PATH="${VENV_DIR}/bin:${PATH}"
"${PYTHON_BIN}" -m pip install --upgrade pip wheel
"${PYTHON_BIN}" -m pip install --upgrade "setuptools==80.9.0"
"${PYTHON_BIN}" -c "import pkg_resources, setuptools; print('setuptools:', setuptools.__version__); print('pkg_resources: OK')"

step "MuseTalk-compatible PyTorch stack"
# MuseTalk/OpenMMLab 1.x is most reliable with Torch 2.0.1 + CUDA 11.8.
# CUDA 11.8 wheels run correctly on current Colab NVIDIA drivers.
"${PYTHON_BIN}" -m pip uninstall -y torch torchvision torchaudio mmcv mmcv-lite >/dev/null 2>&1 || true
"${PYTHON_BIN}" -m pip install --no-cache-dir \
  torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 \
  --index-url https://download.pytorch.org/whl/cu118
"${PYTHON_BIN}" -c "import torch, torchvision, torchaudio; print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available(), 'CUDA build:', torch.version.cuda); print('TorchVision:', torchvision.__version__); print('TorchAudio:', torchaudio.__version__)"

step "Clone MuseTalk"
if [ ! -d "${MUSETALK_ROOT}/.git" ]; then
  git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git "${MUSETALK_ROOT}"
fi

step "MuseTalk dependencies"
"${PYTHON_BIN}" -m pip install -r "${MUSETALK_ROOT}/requirements.txt"

step "AI Twin runtime dependencies"
"${PYTHON_BIN}" -m pip install -r "${APP_DIR}/requirements.txt"
"${PYTHON_BIN}" -m pip install -r "${APP_DIR}/requirements-gpu.txt"

step "MuseTalk OpenMMLab dependencies"
# IMPORTANT: use FULL mmcv, not mmcv-lite. MMPose/MMDDetection import mmcv.ops,
# which requires the compiled mmcv._ext extension.
"${PYTHON_BIN}" -m pip uninstall -y mmcv mmcv-lite >/dev/null 2>&1 || true
"${PYTHON_BIN}" -m pip install --no-cache-dir "mmengine>=0.8,<1.0"
"${PYTHON_BIN}" -m pip install --no-cache-dir "mmcv==2.0.1" \
  -f https://download.openmmlab.com/mmcv/dist/cu118/torch2.0.0/index.html
"${PYTHON_BIN}" -m pip install --no-cache-dir "mmdet==3.1.0"

# mmpose 1.1.0 depends on legacy chumpy 0.70. Install the patched source first.
if ! "${PYTHON_BIN}" -m pip show chumpy >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m pip install --no-build-isolation \
    "git+https://github.com/mattloper/chumpy.git@4228d703b622e172e843438fe0fada102979361a"
fi
"${PYTHON_BIN}" -m pip install --no-cache-dir "mmpose==1.1.0"

step "Restore MuseTalk scientific stack"
# OpenMMLab can upgrade NumPy/Matplotlib. Restore versions compatible with
# MuseTalk 1.5 and TensorFlow 2.12 without removing the full MMCV extension.
"${PYTHON_BIN}" -m pip install --force-reinstall --no-cache-dir \
  "numpy==1.23.5" "opencv-python==4.9.0.80"
"${PYTHON_BIN}" -m pip install --force-reinstall --no-deps \
  "contourpy==1.1.1" "matplotlib==3.7.5"

step "OpenMMLab deep sanity check"
"${PYTHON_BIN}" - <<'PY'
import pkg_resources
import torch, numpy, cv2, matplotlib
import mmengine, mmcv, mmdet, mmpose
from mmcv.ops import nms
from mmpose.apis import inference_topdown, init_model
print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available(), 'build:', torch.version.cuda)
print('NumPy:', numpy.__version__)
print('OpenCV:', cv2.__version__)
print('Matplotlib:', matplotlib.__version__)
print('MMEngine:', mmengine.__version__)
print('MMCV:', mmcv.__version__, 'mmcv.ops: OK')
print('MMDet:', mmdet.__version__)
print('MMPose:', mmpose.__version__, 'apis: OK')
PY

step "OpenVoice package without legacy pins"
if [ ! -d "${OPENVOICE_ROOT}/.git" ]; then
  git clone --depth 1 https://github.com/myshell-ai/OpenVoice.git "${OPENVOICE_ROOT}"
fi
"${PYTHON_BIN}" -m pip install --no-deps -e "${OPENVOICE_ROOT}"

step "Hugging Face tools"
"${PYTHON_BIN}" -m pip install --upgrade "huggingface_hub[cli]==0.30.2" gdown

step "MuseTalk model folders"
mkdir -p \
  "${MUSETALK_ROOT}/models/musetalkV15" \
  "${MUSETALK_ROOT}/models/syncnet" \
  "${MUSETALK_ROOT}/models/dwpose" \
  "${MUSETALK_ROOT}/models/face-parse-bisent" \
  "${MUSETALK_ROOT}/models/sd-vae" \
  "${MUSETALK_ROOT}/models/whisper"

step "Download MuseTalk models"
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

"${VENV_DIR}/bin/gdown" 154JgKpzCPW82qINcVieuPH3fZ2e0P812 \
  -O "${MUSETALK_ROOT}/models/face-parse-bisent/79999_iter.pth"

curl -L https://download.pytorch.org/models/resnet18-5c106cde.pth \
  -o "${MUSETALK_ROOT}/models/face-parse-bisent/resnet18-5c106cde.pth"

step "Sanity check"
"${PYTHON_BIN}" -c "import sys, pkg_resources, setuptools, torch, torchvision, torchaudio, cv2, numpy, matplotlib, openvoice, transformers, huggingface_hub, mmengine, mmcv, mmdet, mmpose; from mmcv.ops import nms; from mmpose.apis import inference_topdown, init_model; print('Python:', sys.version); print('setuptools:', setuptools.__version__); print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available(), 'build:', torch.version.cuda); print('TorchVision:', torchvision.__version__); print('TorchAudio:', torchaudio.__version__); print('NumPy:', numpy.__version__); print('OpenCV:', cv2.__version__); print('Matplotlib:', matplotlib.__version__); print('Transformers:', transformers.__version__); print('Hugging Face Hub:', huggingface_hub.__version__); print('MMEngine:', mmengine.__version__); print('MMCV:', mmcv.__version__, 'ops: OK'); print('MMDet:', mmdet.__version__); print('MMPose:', mmpose.__version__)"
echo "Zaskaleta AI Twin GPU engines installed with ${PYTHON_BIN}."
