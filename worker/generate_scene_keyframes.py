import argparse
import gc
import json
import os
import shutil
import time
from pathlib import Path

import torch
from PIL import Image
from diffusers import StableDiffusionXLPipeline, DPMSolverMultistepScheduler
from transformers import CLIPVisionModelWithProjection

NEGATIVE = (
    "identity drift, different person, changed face, changed beard, changed hairstyle, deformed face, asymmetrical eyes, bad anatomy, deformed hands, "
    "extra fingers, missing fingers, duplicate person, plastic skin, beauty filter, cartoon, illustration, "
    "low quality, blurry, watermark, text"
)

# Colab/mobile connections can briefly interrupt large Hugging Face downloads.
# Give the hub more time and disable Xet so ordinary resumable HTTP downloads are used.
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "240")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "90")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

HF_CACHE = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def _clear_repo_cache(repo_id: str):
    repo_dir = HF_CACHE / ("models--" + repo_id.replace("/", "--"))
    if repo_dir.exists():
        print(f"🧹 Clearing damaged Hugging Face cache: {repo_dir.name}")
        shutil.rmtree(repo_dir, ignore_errors=True)


def _retry_load(label, fn, repo_id=None, attempts=5):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (OSError, ConnectionError, TimeoutError) as e:
            last = e
            text = str(e).lower()
            if repo_id and ("consistency check failed" in text or "file should be of size" in text):
                _clear_repo_cache(repo_id)
            if attempt >= attempts:
                raise
            wait = min(12 * attempt, 45)
            print(f"⚠️ {label}: transient download/cache error ({attempt}/{attempts}); retry in {wait}s")
            print(f"   {type(e).__name__}: {str(e)[:260]}")
            time.sleep(wait)
    raise last


def load_pipe():
    dtype = torch.float16
    image_encoder = _retry_load(
        "IP-Adapter image encoder",
        lambda: CLIPVisionModelWithProjection.from_pretrained(
            "h94/IP-Adapter", subfolder="models/image_encoder", torch_dtype=dtype
        ),
        repo_id="h94/IP-Adapter",
    )
    pipe = _retry_load(
        "SDXL base model",
        lambda: StableDiffusionXLPipeline.from_pretrained(
            "stabilityai/stable-diffusion-xl-base-1.0",
            image_encoder=image_encoder,
            torch_dtype=dtype,
            variant="fp16",
            use_safetensors=True,
        ),
        repo_id="stabilityai/stable-diffusion-xl-base-1.0",
    )
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    _retry_load(
        "IP-Adapter face weights",
        lambda: pipe.load_ip_adapter(
            "h94/IP-Adapter",
            subfolder="sdxl_models",
            weight_name="ip-adapter-plus-face_sdxl_vit-h.safetensors",
        ),
        repo_id="h94/IP-Adapter",
    )
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()
    return pipe


def open_reference(path: str):
    image = Image.open(path).convert("RGB")
    w, h = image.size
    side = min(w, h)
    left = max(0, (w - side) // 2)
    top = max(0, min(h - side, int((h - side) * 0.28)))
    return image.crop((left, top, left + side, top + side)).resize((768, 768))


def compact_prompt(scene_prompt: str, focus: str):
    text = " ".join(str(scene_prompt).split())
    if len(text) > 700:
        text = text[:700].rsplit(" ", 1)[0]
    return (
        focus + text
        + " Photorealistic European cinema still, same recognizable man in every scene, natural pores and facial asymmetry, "
          "realistic anatomy, authentic German environment, coherent motivated lighting, 35mm lens, cinematic composition, "
          "realistic clothing fabric, vertical 9:16."
    )


def render_one(pipe, prompt, ref, seed, steps, width, height):
    generator = torch.Generator(device="cpu").manual_seed(seed)
    return pipe(
        prompt=prompt,
        negative_prompt=NEGATIVE,
        ip_adapter_image=ref,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=5.2,
        generator=generator,
    ).images[0]


def free_memory():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass


def rebuild_index(manifest, primary_photo, out):
    rows = []
    for scene in manifest["scenes"]:
        n = int(scene["n"])
        path = out / f"scene_{n:02d}.png"
        if not path.is_file() or path.stat().st_size <= 10_000:
            continue
        lines = scene.get("dialogue", [])
        secondary_only = bool(lines) and all(x.get("speaker") != "AI_CLONE" for x in lines)
        rows.append({
            "scene": n,
            "image": str(path),
            "reference": None if secondary_only else primary_photo,
            "focus": "SECONDARY" if secondary_only else "AI_CLONE",
        })
    (out / "keyframes.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def choose_primary(photos):
    return photos[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--photos", nargs="+", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--seed", type=int, default=9969)
    ap.add_argument("--steps", type=int, default=22)
    ap.add_argument("--scene", type=int, default=None, help="Render only one scene, then exit to fully release GPU memory")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    photos = [p for p in args.photos if Path(p).is_file()]
    if not photos:
        raise SystemExit("No valid MASTER_PHOTOS")
    primary_photo = choose_primary(photos)

    scenes = manifest["scenes"]
    if args.scene is not None:
        scenes = [s for s in scenes if int(s["n"]) == args.scene]
        if not scenes:
            raise SystemExit(f"Scene {args.scene} not found in manifest")

    for scene in scenes:
        n = int(scene["n"])
        path = out / f"scene_{n:02d}.png"
        if path.is_file() and path.stat().st_size > 10_000:
            print(f"↪ Scene {n:02d}: existing keyframe, resume")
            rebuild_index(manifest, primary_photo, out)
            continue

        ref = open_reference(primary_photo)
        lines = scene.get("dialogue", [])
        secondary_only = bool(lines) and all(x.get("speaker") != "AI_CLONE" for x in lines)
        focus_name = "SECONDARY" if secondary_only else "AI_CLONE"

        pipe = load_pipe()
        if secondary_only:
            pipe.set_ip_adapter_scale(0.0)
            focus = "Reverse-shot on supporting speaker; AI clone off-camera. Visible speaker must not resemble AI clone. "
        else:
            pipe.set_ip_adapter_scale(0.88)
            focus = "Persistent AI clone is the visible main subject. Preserve the canonical identity exactly: same face shape, eyes, nose, lips, beard, hairstyle, hairline, age and skin tone. "

        prompt = compact_prompt(scene["prompt"], focus)
        free_memory()
        try:
            image = render_one(pipe, prompt, ref, args.seed + n * 101, args.steps, 704, 1216)
            render_mode = "704x1216"
        except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
            msg = str(e).lower()
            if "out of memory" not in msg and "cuda" not in msg:
                raise
            print(f"⚠️ Scene {n:02d}: GPU pressure, retrying at fallback size")
            free_memory()
            image = render_one(pipe, prompt, ref, args.seed + n * 101, min(args.steps, 18), 640, 1088)
            render_mode = "640x1088-fallback"

        image.save(path, quality=95)
        print(f"✅ Scene {n:02d}: {path.name} | focus={focus_name} | identity_anchor={Path(primary_photo).name} | {render_mode}")
        del image, ref, pipe
        free_memory()
        rebuild_index(manifest, primary_photo, out)

    print("✅ Requested keyframe generation complete")


if __name__ == "__main__":
    main()
