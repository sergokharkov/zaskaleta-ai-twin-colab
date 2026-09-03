import argparse
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
    pipe.enable_attention_slicing()
    return pipe


def open_reference(path: str):
    image = Image.open(path).convert("RGB")
    w, h = image.size
    side = min(w, h)
    left = max(0, (w - side) // 2)
    top = max(0, min(h - side, int((h - side) * 0.28)))
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
    if not photos:
        raise SystemExit("No valid MASTER_PHOTOS")

    pipe = load_pipe()
    results = []

    for scene in manifest["scenes"]:
        n = int(scene["n"])
        ref_path = photos[(n - 1) % len(photos)]
        ref = open_reference(ref_path)
        lines = scene.get("dialogue", [])
        secondary_only = bool(lines) and all(x.get("speaker") != "AI_CLONE" for x in lines)

        if secondary_only:
            pipe.set_ip_adapter_scale(0.0)
            focus = (
                "This is a reverse-shot focused on the supporting speaker. The AI clone is off-camera or only "
                "a blurred shoulder in the foreground. The visible speaker must look like the supporting character "
                "described in the scene, not like the AI clone. "
            )
        else:
            pipe.set_ip_adapter_scale(0.82)
            focus = (
                "The persistent AI clone is the clearly visible main subject. Preserve his recognizable facial identity. "
            )

        prompt = (
            focus + scene["prompt"]
            + " Photorealistic European cinema still, natural skin texture, 35mm lens, cinematic composition, "
              "authentic clothing fabric, realistic German environment, subtle film grain, high detail, vertical 9:16."
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
        results.append({
            "scene": n,
            "image": str(path),
            "reference": None if secondary_only else ref_path,
            "focus": "SECONDARY" if secondary_only else "AI_CLONE",
        })
        print(f"✅ Scene {n:02d}: {path.name} | focus={results[-1]['focus']}")

    (out / "keyframes.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ All cinematic scene keyframes generated")


if __name__ == "__main__":
    main()
