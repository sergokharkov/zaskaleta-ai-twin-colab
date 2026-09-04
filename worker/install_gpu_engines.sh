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
# MMPose/MMEngine still import pkg_resources. Newer setuptools releases can
# remove that legacy module, so keep a compatible setuptools in this runtime.
"${PYTHON_BIN}" -m pip install --upgrade "setuptools==80.9.0"
"${PYTHON_BIN}" -c "import pkg_resources, setuptools; print('setuptools:', setuptools.__version__); print('pkg_resources: OK')"

step "Clone MuseTalk"
if [ ! -d "${MUSETALK_ROOT}/.git" ]; then
  git clone --depth 1 https://github.com/TMElyralab/MuseTalk.git "${MUSETALK_ROOT}"
fi

step "MuseTalk dependencies"
"${PYTHON_BIN}" -m pip install -r "${MUSETALK_ROOT}/requirements.txt"

step "AI Twin runtime dependencies"
"${PYTHON_BIN}" -m pip install -r "${APP_DIR}/requirements.txt"
"${PYTHON_BIN}" -m pip install -r "${APP_DIR}/requirements-gpu.txt"

step "MuseTalk OpenMMLab dependencies (Colab-safe)"
# Full mmcv==2.0.1 may require a local C++/CUDA build on Python 3.11/Torch 2.4.
# MuseTalk's preprocessing path only needs the Python APIs, so use mmcv-lite.
"${PYTHON_BIN}" -m pip install --upgrade "mmengine>=0.8,<1.0"
"${PYTHON_BIN}" -m pip install --upgrade "mmcv-lite==2.0.1"
"${PYTHON_BIN}" -m pip install --upgrade "mmdet==3.1.0"

# mmpose 1.1.0 depends on legacy chumpy 0.70. The PyPI sdist fails under modern
# PEP-517 build isolation, so install a patched Git commit first without isolation.
if ! "${PYTHON_BIN}" -m pip show chumpy >/dev/null 2>&1; then
  "${PYTHON_BIN}" -m pip install --no-build-isolation \
    "git+https://github.com/mattloper/chumpy.git@4228d703b622e172e843438fe0fada102979361a"
fi

"${PYTHON_BIN}" -m pip install --upgrade "mmpose==1.1.0"

step "Restore MuseTalk NumPy/OpenCV/Matplotlib ABI"
# OpenMMLab can pull NumPy 2.x and a very new Matplotlib. MuseTalk 1.5 pins
# NumPy 1.23.5, so restore a mutually compatible scientific stack afterwards.
"${PYTHON_BIN}" -m pip install --force-reinstall --no-cache-dir \
  "numpy==1.23.5" "opencv-python==4.9.0.80"
# Install versions whose wheels support NumPy 1.23.5. --no-deps prevents pip
# from upgrading NumPy again while repairing the Matplotlib dependency chain.
"${PYTHON_BIN}" -m pip install --force-reinstall --no-deps \
  "contourpy==1.1.1" "matplotlib==3.7.5"
"${PYTHON_BIN}" -c "import pkg_resources, numpy, cv2, matplotlib, contourpy; print('pkg_resources: OK'); print('NumPy:', numpy.__version__); print('OpenCV:', cv2.__version__); print('Matplotlib:', matplotlib.__version__); print('ContourPy:', contourpy.__version__)"
"${PYTHON_BIN}" -c "import mmengine, mmcv, mmdet, mmpose; from mmpose.apis import inference_topdown, init_model; print('MMLab OK:', mmengine.__version__, mmcv.__version__, mmdet.__version__, mmpose.__version__)"

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
"${PYTHON_BIN}" -c "import sys, pkg_resources, setuptools, torch, cv2, numpy, matplotlib, openvoice, transformers, huggingface_hub, mmengine, mmcv, mmdet, mmpose; from mmpose.apis import inference_topdown, init_model; print('Python:', sys.version); print('setuptools:', setuptools.__version__); print('pkg_resources: OK'); print('Torch:', torch.__version__, 'CUDA:', torch.cuda.is_available()); print('NumPy:', numpy.__version__); print('OpenCV:', cv2.__version__); print('Matplotlib:', matplotlib.__version__); print('Transformers:', transformers.__version__); print('Hugging Face Hub:', huggingface_hub.__version__); print('MMEngine:', mmengine.__version__); print('MMCV:', mmcv.__version__); print('MMDet:', mmdet.__version__); print('MMPose:', mmpose.__version__)"
echo "Zaskaleta AI Twin GPU engines installed with ${PYTHON_BIN}."
