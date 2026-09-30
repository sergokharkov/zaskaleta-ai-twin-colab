#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
from PIL import Image

EXPECTED_COUNTS = {
    "IDENTITY_PRIMARY": 12,
    "IDENTITY_SECONDARY": 2,
    "FACE_MOTION_PRIMARY": 3,
    "TALKING_BEHAVIOR": 4,
    "BODY_MOVEMENT": 1,
    "VOICE_PRIMARY": 1,
    "VOICE_SOURCE_BACKUP": 3,
    "STYLE_REFERENCE_ONLY": 1,
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe(path: Path) -> dict:
    raw = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels,duration,nb_frames",
            "-of", "json", str(path),
        ],
        text=True,
    )
    return json.loads(raw)


def sample_video(path: Path) -> int:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"{path.name}: cannot open video")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        raise RuntimeError(f"{path.name}: no frame count")
    readable = 0
    for idx in (0, max(0, frame_count // 2), max(0, frame_count - 2)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            readable += 1
    cap.release()
    if readable != 3:
        raise RuntimeError(f"{path.name}: sample frame readability {readable}/3")
    return frame_count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--pass-id", required=True)
    ap.add_argument("--source-sha", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))

    if registry.get("schema") != "zaskaleta-c006-private-integrity-registry-v1":
        raise SystemExit("wrong registry schema")
    if registry.get("candidate_id") != "MASTER_CLONE_CANDIDATE_006":
        raise SystemExit("wrong candidate")

    assets = registry.get("assets") or []
    counts = {}
    seen_sha = {}
    safe_details = {}

    for item in assets:
        role = item["role"]
        counts[role] = counts.get(role, 0) + 1
        name = item["filename"]
        path = root / name
        if not path.is_file():
            raise SystemExit(f"{name}: missing")
        if path.stat().st_size != int(item["size_bytes"]):
            raise SystemExit(f"{name}: size mismatch")

        digest = sha256(path)
        if digest != item["sha256"]:
            raise SystemExit(f"{name}: sha mismatch")
        if digest in seen_sha:
            raise SystemExit(f"{name}: exact duplicate of {seen_sha[digest]}")
        seen_sha[digest] = name

        media_type = item["media_type"]
        if media_type == "image":
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
            if width != int(item["width"]) or height != int(item["height"]):
                raise SystemExit(f"{name}: image dimensions mismatch")
            if min(width, height) < 512:
                raise SystemExit(f"{name}: image too small")
            safe_details[name] = {
                "media_type": "image",
                "decode": "PASS",
                "width": width,
                "height": height,
            }

        elif media_type == "video":
            meta = ffprobe(path)
            streams = meta.get("streams") or []
            video_streams = [s for s in streams if s.get("codec_type") == "video"]
            if len(video_streams) != 1:
                raise SystemExit(f"{name}: expected one video stream")
            duration = float((meta.get("format") or {}).get("duration") or 0)
            if duration <= 0:
                raise SystemExit(f"{name}: invalid duration")
            frame_count = sample_video(path)
            safe_details[name] = {
                "media_type": "video",
                "decode": "PASS",
                "duration_seconds": round(duration, 6),
                "sample_frames_readable": 3,
                "reported_frame_count": frame_count,
            }

        elif media_type == "audio":
            meta = ffprobe(path)
            streams = meta.get("streams") or []
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            if not audio_streams:
                raise SystemExit(f"{name}: no audio stream")
            duration = float((meta.get("format") or {}).get("duration") or 0)
            if duration <= 0:
                raise SystemExit(f"{name}: invalid audio duration")
            safe_details[name] = {
                "media_type": "audio",
                "decode": "PASS",
                "duration_seconds": round(duration, 6),
                "audio_streams": len(audio_streams),
            }
        else:
            raise SystemExit(f"{name}: unsupported media type {media_type}")

    if counts != EXPECTED_COUNTS:
        raise SystemExit(f"role counts mismatch: {counts}")
    if registry.get("pending_excluded") != ["VID_20260901_183436.mp4"]:
        raise SystemExit("pending exclusion mismatch")

    out = {
        "schema": "zaskaleta-c006-private-cpu-qa-v1",
        "candidate_id": "MASTER_CLONE_CANDIDATE_006",
        "source_sha": args.source_sha,
        "pass_id": args.pass_id,
        "decision": "CPU_QA_PASS",
        "complete": True,
        "private_asset_count": len(assets),
        "role_counts": counts,
        "exact_duplicate_count": 0,
        "pending_consumed": False,
        "raw_biometric_media_exported": False,
        "private_hashes_exported": False,
        "face_embeddings_exported": False,
        "gpu_launch_allowed": False,
        "auto_promote": False,
        "stable_release_modified": False,
        "details": safe_details,
    }

    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"C006_PRIVATE_CPU_QA_PASS={args.pass_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
