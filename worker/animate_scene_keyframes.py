import argparse
import json
import subprocess
from pathlib import Path


def motion_filter(index: int, seconds: float, fps: int = 25):
    frames = max(1, int(seconds * fps))
    mode = (index - 1) % 4
    # Keep movement deliberately subtle. Large zooms/pans make a still frame look artificial.
    if mode == 0:
        z = "min(zoom+0.00022,1.025)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif mode == 1:
        z = "if(eq(on,1),1.025,max(zoom-0.00020,1.0))"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif mode == 2:
        z = "1.018"
        x = f"(iw-iw/zoom)*(0.35+0.30*on/{frames})"
        y = "ih/2-(ih/zoom/2)"
    else:
        z = "1.018"
        x = f"(iw-iw/zoom)*(0.65-0.30*on/{frames})"
        y = "ih/2-(ih/zoom/2)"
    return (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1080x1920:fps={fps},"
        "format=yuv420p"
    )


def valid_video(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 50_000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--image-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--fps", type=int, default=25)
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    image_dir = Path(args.image_dir)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for scene in manifest["scenes"]:
        n = int(scene["n"])
        seconds = float(scene.get("seconds", 8))
        src = image_dir / f"scene_{n:02d}.png"
        if not src.is_file():
            raise FileNotFoundError(src)
        dest = out / f"scene_{n:02d}_silent.mp4"
        if valid_video(dest):
            print(f"↪ Animated scene {n:02d}: existing, resume")
            continue
        vf = motion_filter(n, seconds, args.fps)
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(src), "-t", str(seconds),
            "-vf", vf, "-r", str(args.fps), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "19", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"✅ Animated scene {n:02d}: {dest.name}")


if __name__ == "__main__":
    main()
