import argparse
import json
import random
from pathlib import Path

import torch
from PIL import Image
from diffusers import StableDiffusionXLPipeline, DPMSolverMultistepScheduler
from transformers import CLIPVisionModelWithProjection


NEGATIVE = (
    "different person, identity drift, changed face, deformed face, asymmetrical eyes, "
    "bad anatomy, deformed hands, extra fingers, missing fingers, duplicate person, "
    "plastic skin, beauty filter, cartoon, illustration, low quality, blurry, watermark, text"
)


def load_pipe():
    dtype = torch.float16
    image_encoder = CLIPVisionModelWithProjection.from_pretrained(
        "h94/IP-Adapter",
        subfolder="models/image_encoder",
        torch_dtype=dtype,
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
    pipe.set_ip_adapter_scale(0.82)
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()
    pipe.enable_attention_slicing()
    return pipe


def open_reference(path: str):
    image = Image.open(path).convert("RGB")
    # IP-Adapter face variant benefits from a clean portrait reference.
    w, h = image.size
    side = min(w, h)
    left = max(0, (w - side) // 2)
    top = max(0, int((h - side) * 0.28))
    top = min(top, h - side)
    return image.crop((left, top, left + side, top + side)).resize((768, 768))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--photos", nargs="+", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--seed", type=int, default=9969)
    ap.add_argument("--steps", type=int, default=28)
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    photos = [p for p in args.photos if Path(p).is_file()]
    if len(photos) < 1:
        raise SystemExit("No valid MASTER_PHOTOS")

    pipe = load_pipe()
    rng = random.Random(args.seed)
    results = []

    for scene in manifest["scenes"]:
        n = int(scene["n"])
        ref_path = photos[(n - 1) % len(photos)]
        ref = open_reference(ref_path)
        prompt = (
            scene["prompt"]
            + " Photorealistic European cinema still, natural skin texture, realistic beard and hair, "
              "35mm lens, cinematic composition, authentic clothing fabric, realistic German architecture, "
              "subtle film grain, high detail, vertical social-video composition."
        )
        generator = torch.Generator(device="cpu").manual_seed(args.seed + n * 101)
        image = pipe(
            prompt=prompt,
            negative_prompt=NEGATIVE,
            ip_adapter_image=ref,
            width=768,
            height=1344,
            num_inference_steps=args.steps,
            guidance_scale=6.0,
            generator=generator,
        ).images[0]
        path = out / f"scene_{n:02d}.png"
        image.save(path, quality=96)
        results.append({"scene": n, "image": str(path), "reference": ref_path})
        print(f"✅ Scene {n:02d}: {path.name}")

    (out / "keyframes.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ All cinematic scene keyframes generated")


if __name__ == "__main__":
    main()
