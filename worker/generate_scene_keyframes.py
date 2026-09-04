import argparse
import gc
import json
from pathlib import Path

import torch
from PIL import Image
from diffusers import StableDiffusionXLPipeline, DPMSolverMultistepScheduler
from transformers import CLIPVisionModelWithProjection

NEGATIVE = (
    "identity drift, changed face, deformed face, asymmetrical eyes, bad anatomy, deformed hands, "
    "extra fingers, missing fingers, duplicate person, plastic skin, beauty filter, cartoon, illustration, "
    "low quality, blurry, watermark, text"
)


def load_pipe():
    dtype = torch.float16
    image_encoder = CLIPVisionModelWithProjection.from_pretrained(
        "h94/IP-Adapter", subfolder="models/image_encoder", torch_dtype=dtype
    )
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "stabilityai/stable-diffusion-xl-base-1.0",
        image_encoder=image_encoder,
        torch_dtype=dtype,
        variant="fp16",
        use_safetensors=True,
    )
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.load_ip_adapter(
        "h94/IP-Adapter",
        subfolder="sdxl_models",
        weight_name="ip-adapter-plus-face_sdxl_vit-h.safetensors",
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
        + " Photorealistic European cinema still, natural skin, realistic anatomy, authentic German environment, "
          "35mm lens, cinematic composition, realistic clothing, vertical 9:16."
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
        guidance_scale=5.5,
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


def rebuild_index(manifest, photos, out):
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
            "reference": None if secondary_only else photos[(n - 1) % len(photos)],
            "focus": "SECONDARY" if secondary_only else "AI_CLONE",
        })
    (out / "keyframes.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


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
            rebuild_index(manifest, photos, out)
            continue

        ref_path = photos[(n - 1) % len(photos)]
        ref = open_reference(ref_path)
        lines = scene.get("dialogue", [])
        secondary_only = bool(lines) and all(x.get("speaker") != "AI_CLONE" for x in lines)
        focus_name = "SECONDARY" if secondary_only else "AI_CLONE"

        pipe = load_pipe()
        if secondary_only:
            pipe.set_ip_adapter_scale(0.0)
            focus = "Reverse-shot on supporting speaker; AI clone off-camera. Visible speaker must not resemble AI clone. "
        else:
            pipe.set_ip_adapter_scale(0.82)
            focus = "Persistent AI clone is the visible main subject; preserve recognizable facial identity. "

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
        print(f"✅ Scene {n:02d}: {path.name} | focus={focus_name} | {render_mode}")
        del image, ref, pipe
        free_memory()
        rebuild_index(manifest, photos, out)

    print("✅ Requested keyframe generation complete")


if __name__ == "__main__":
    main()
