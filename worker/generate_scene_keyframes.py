import argparse
import gc
import json
import os
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from diffusers import StableDiffusionXLPipeline, DPMSolverMultistepScheduler
from transformers import CLIPVisionModelWithProjection

NEGATIVE = (
    "identity drift, different person, face swap, changed face shape, changed eyes, changed nose, changed lips, "
    "changed beard length, changed beard color, clean shaven, changed hairstyle, changed hairline, changed age, "
    "changed skin tone, deformed face, asymmetrical eyes, bad anatomy, deformed hands, extra fingers, missing fingers, "
    "duplicate person, twin, collage, split face, merged bodies, plastic skin, beauty filter, cartoon, illustration, "
    "low quality, blurry, watermark, text"
)

IDENTITY_SCALE = 1.12
DEFAULT_ATTEMPTS = 3
MIN_FACE_AREA_RATIO = 0.012
MAX_FACE_AREA_RATIO = 0.32
MIN_FACE_SHARPNESS = 18.0

# Colab/mobile connections can briefly interrupt large Hugging Face downloads.
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


def _fallback_square(image: Image.Image):
    w, h = image.size
    side = min(w, h)
    left = max(0, (w - side) // 2)
    top = max(0, min(h - side, int((h - side) * 0.18)))
    return image.crop((left, top, left + side, top + side))


def _face_detector():
    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def open_reference(path: str):
    """Create a stable face-centric identity anchor from the canonical MASTER photo."""
    image = Image.open(path).convert("RGB")
    arr = np.asarray(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    faces = _face_detector().detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(64, 64))
    if len(faces):
        x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        cx, cy = x + w / 2.0, y + h / 2.0
        side = max(w, h) * 2.15
        left = max(0, int(cx - side / 2))
        top = max(0, int(cy - side * 0.43))
        right = min(image.width, int(left + side))
        bottom = min(image.height, int(top + side))
        side2 = min(right - left, bottom - top)
        crop = image.crop((left, top, left + side2, top + side2))
        print(f"🎯 Identity anchor: detected canonical face ({w}x{h})")
    else:
        crop = _fallback_square(image)
        print("⚠️ Identity anchor: face detector fallback crop")
    return crop.resize((768, 768), Image.Resampling.LANCZOS)


def compact_prompt(scene_prompt: str, focus: str):
    text = " ".join(str(scene_prompt).split())
    if len(text) > 420:
        text = text[:420].rsplit(" ", 1)[0]
    return (
        focus
        + text
        + " Photorealistic cinematic still, natural skin pores, realistic anatomy, authentic German environment, "
          "35mm eye-level camera, coherent natural lighting, realistic fabric, single coherent frame, vertical 9:16."
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
        guidance_scale=4.6,
        generator=generator,
    ).images[0]


def inspect_visible_face(image: Image.Image):
    """Cheap render-time guard: require one detectable, usable face before a keyframe may be saved.

    This is deliberately not an identity classifier. Identity promotion is still controlled by the
    downstream stable-vs-challenger quality gate. Here we prevent obviously unusable render outputs
    (missing/tiny/soft face) from becoming scene keyframes in the first place.
    """
    arr = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    faces = _face_detector().detectMultiScale(gray, scaleFactor=1.07, minNeighbors=5, minSize=(48, 48))
    if not len(faces):
        return {"passed": False, "reason": "no_face_detected", "score": -1.0, "face_count": 0}

    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    area_ratio = float(w * h) / float(arr.shape[0] * arr.shape[1])
    face_gray = gray[y:y + h, x:x + w]
    sharpness = float(cv2.Laplacian(face_gray, cv2.CV_64F).var()) if face_gray.size else 0.0
    area_ok = MIN_FACE_AREA_RATIO <= area_ratio <= MAX_FACE_AREA_RATIO
    sharp_ok = sharpness >= MIN_FACE_SHARPNESS
    passed = bool(area_ok and sharp_ok)
    score = (min(area_ratio / 0.05, 1.5) * 0.45) + (min(sharpness / 100.0, 2.0) * 0.55)
    reason = "ok" if passed else ("face_size_out_of_range" if not area_ok else "face_too_soft")
    return {
        "passed": passed,
        "reason": reason,
        "score": round(score, 6),
        "face_count": int(len(faces)),
        "face_area_ratio": round(area_ratio, 6),
        "face_sharpness": round(sharpness, 3),
        "face_box": [int(x), int(y), int(w), int(h)],
    }


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
            "identity_lock": f"face-crop-ip-adapter-{IDENTITY_SCALE:.2f}" if not secondary_only else "off-camera",
            "render_guard": "face-presence-size-sharpness-v1" if not secondary_only else "not-required",
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
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS, help="Deterministic render candidates per AI-clone scene")
    ap.add_argument("--scene", type=int, default=None, help="Render only one scene, then exit to fully release GPU memory")
    args = ap.parse_args()

    if args.attempts < 1 or args.attempts > 6:
        raise SystemExit("--attempts must be between 1 and 6")

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
        audit_path = out / f"scene_{n:02d}.render.json"
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
            focus = (
                "Reverse-shot on a clearly different supporting speaker; canonical AI clone is fully off-camera. "
                "Do not create a lookalike, twin, duplicate or partial face of the AI clone. "
            )
        else:
            pipe.set_ip_adapter_scale(IDENTITY_SCALE)
            focus = (
                "CANONICAL IDENTITY LOCK. The visible main subject must be exactly the person in the reference image. "
                "Preserve the same facial geometry, natural asymmetry, eye spacing, eye shape, nose, lips, jaw, beard shape "
                "and density, beard color, hairline, hairstyle, age and skin tone. Preserve identity across camera angle changes. "
                "No reinterpretation, no face redesign, no beautification, no rejuvenation, no age shift. One coherent person only. "
            )

        prompt = compact_prompt(scene["prompt"], focus)
        free_memory()
        chosen = None
        chosen_guard = None
        attempts_log = []
        attempt_count = 1 if secondary_only else args.attempts

        for attempt in range(attempt_count):
            candidate_seed = args.seed + n * 101 + attempt * 1009
            try:
                image = render_one(pipe, prompt, ref, candidate_seed, args.steps, 704, 1216)
                render_mode = "704x1216"
            except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
                msg = str(e).lower()
                if "out of memory" not in msg and "cuda" not in msg:
                    raise
                print(f"⚠️ Scene {n:02d}: GPU pressure, retrying attempt {attempt + 1} at fallback size")
                free_memory()
                image = render_one(pipe, prompt, ref, candidate_seed, min(args.steps, 20), 640, 1088)
                render_mode = "640x1088-fallback"

            guard = {"passed": True, "reason": "secondary_only", "score": 0.0} if secondary_only else inspect_visible_face(image)
            attempts_log.append({"attempt": attempt + 1, "seed": candidate_seed, "render_mode": render_mode, "guard": guard})
            print(f"🔎 Scene {n:02d} attempt {attempt + 1}/{attempt_count}: {guard['reason']} score={guard.get('score')}")

            if chosen is None or guard.get("score", -1.0) > chosen_guard.get("score", -1.0):
                if chosen is not None:
                    del chosen
                chosen = image
                chosen_guard = guard
            else:
                del image

            if guard.get("passed"):
                break
            free_memory()

        if chosen is None:
            raise RuntimeError(f"Scene {n:02d}: no render candidate produced")
        if not secondary_only and not chosen_guard.get("passed"):
            audit = {
                "scene": n,
                "status": "REJECTED_BEFORE_SAVE",
                "reason": "render_time_face_guard_failed",
                "identity_anchor": Path(primary_photo).name,
                "identity_scale": IDENTITY_SCALE,
                "attempts": attempts_log,
            }
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            del chosen, ref, pipe
            free_memory()
            raise RuntimeError(f"Scene {n:02d}: all AI-clone render attempts failed face guard; stable keyframe not written")

        chosen.save(path, quality=95)
        audit = {
            "scene": n,
            "status": "SAVED_CANDIDATE_KEYFRAME",
            "focus": focus_name,
            "identity_anchor": None if secondary_only else Path(primary_photo).name,
            "identity_scale": 0.0 if secondary_only else IDENTITY_SCALE,
            "identity_regression_policy": "zero-tolerance-at-release-gate" if not secondary_only else "not-applicable",
            "render_guard": chosen_guard,
            "attempts": attempts_log,
            "note": "Render guard prevents missing/tiny/soft faces; identity similarity is still enforced by downstream clone quality and stable-vs-challenger gates.",
        }
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"✅ Scene {n:02d}: {path.name} | focus={focus_name} | identity_anchor={Path(primary_photo).name} "
            f"| identity_scale={'0.0' if secondary_only else f'{IDENTITY_SCALE:.2f}'} | guard={chosen_guard['reason']}"
        )
        del chosen, ref, pipe
        free_memory()
        rebuild_index(manifest, primary_photo, out)

    print("✅ Requested keyframe generation complete")


if __name__ == "__main__":
    main()
