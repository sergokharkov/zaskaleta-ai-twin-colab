#!/usr/bin/env python3
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

CANDIDATE = "MASTER_CLONE_CANDIDATE_005"
SCHEMA = "zaskaleta-c005-render-output-validation-v1"


def _num(value, name):
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"missing_or_invalid_{name}")
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"missing_or_invalid_{name}")
    return x


def _ratio(value, name):
    if not isinstance(value, str) or "/" not in value:
        raise ValueError(f"invalid_{name}")
    a, b = value.split("/", 1)
    den = _num(b, name + "_den")
    if den == 0:
        raise ValueError(f"invalid_{name}")
    return _num(a, name + "_num") / den


def probe_video(path: Path):
    if not path.is_file():
        raise ValueError("video_file_missing")
    cmd = [
        "ffprobe", "-v", "error", "-count_frames",
        "-show_streams", "-show_format", "-of", "json", str(path)
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ValueError("ffprobe_not_available") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("ffprobe_failed: " + (exc.stderr or "unknown").strip()) from exc
    return json.loads(proc.stdout)


def validate_probe(data, *, min_video_seconds=5.0, duration_tolerance=0.35, av_tolerance=0.35):
    streams = data.get("streams")
    fmt = data.get("format")
    if not isinstance(streams, list) or not isinstance(fmt, dict):
        raise ValueError("invalid_ffprobe_payload")

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video_streams) != 1:
        raise ValueError(f"expected_one_video_stream_got_{len(video_streams)}")
    if len(audio_streams) < 1:
        raise ValueError("audio_stream_missing")

    v = video_streams[0]
    a = audio_streams[0]
    container_duration = _num(fmt.get("duration"), "container_duration")
    video_duration = _num(v.get("duration"), "video_duration")
    audio_duration = _num(a.get("duration"), "audio_duration")
    fps = _ratio(v.get("avg_frame_rate") or v.get("r_frame_rate"), "fps")
    if fps <= 0:
        raise ValueError("invalid_fps")

    frame_value = v.get("nb_read_frames") or v.get("nb_frames")
    try:
        frames = int(frame_value)
    except (TypeError, ValueError):
        raise ValueError("video_frame_count_missing")
    if frames <= 0:
        raise ValueError("video_frame_count_invalid")

    if video_duration < min_video_seconds:
        raise ValueError(f"video_stream_too_short_{video_duration:.3f}s")
    if abs(video_duration - container_duration) > duration_tolerance:
        raise ValueError(
            f"video_container_duration_mismatch_video_{video_duration:.3f}_container_{container_duration:.3f}"
        )
    if abs(video_duration - audio_duration) > av_tolerance:
        raise ValueError(
            f"audio_video_duration_mismatch_video_{video_duration:.3f}_audio_{audio_duration:.3f}"
        )

    expected_frames = video_duration * fps
    frame_ratio = frames / expected_frames if expected_frames else 0.0
    if frame_ratio < 0.92 or frame_ratio > 1.08:
        raise ValueError(
            f"frame_duration_inconsistent_frames_{frames}_expected_{expected_frames:.1f}_ratio_{frame_ratio:.3f}"
        )

    width = int(v.get("width") or 0)
    height = int(v.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("invalid_video_dimensions")

    return {
        "schema": SCHEMA,
        "candidate_id": CANDIDATE,
        "decision": "C005_RENDER_STREAM_INTEGRITY_PASS",
        "video_duration_seconds": round(video_duration, 6),
        "audio_duration_seconds": round(audio_duration, 6),
        "container_duration_seconds": round(container_duration, 6),
        "fps": round(fps, 6),
        "video_frames": frames,
        "frame_duration_ratio": round(frame_ratio, 6),
        "resolution": f"{width}x{height}",
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "stable_release_modified": False,
        "promotion_allowed": False,
    }


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--video")
    src.add_argument("--probe-json", help="Pre-recorded ffprobe JSON, intended for deterministic QA tests")
    ap.add_argument("--output", required=True)
    ap.add_argument("--min-video-seconds", type=float, default=5.0)
    ap.add_argument("--duration-tolerance", type=float, default=0.35)
    ap.add_argument("--av-tolerance", type=float, default=0.35)
    args = ap.parse_args()

    try:
        if args.video:
            data = probe_video(Path(args.video))
        else:
            data = json.loads(Path(args.probe_json).read_text(encoding="utf-8"))
        result = validate_probe(
            data,
            min_video_seconds=args.min_video_seconds,
            duration_tolerance=args.duration_tolerance,
            av_tolerance=args.av_tolerance,
        )
        code = 0
    except Exception as exc:
        result = {
            "schema": SCHEMA,
            "candidate_id": CANDIDATE,
            "decision": "C005_RENDER_STREAM_INTEGRITY_FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "stable_release_modified": False,
            "promotion_allowed": False,
        }
        code = 3

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
