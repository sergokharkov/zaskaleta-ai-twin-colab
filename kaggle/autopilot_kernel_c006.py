#!/usr/bin/env python3
"""Fail-closed Kaggle renderer for MASTER_CLONE_CANDIDATE_006.

C006 is candidate-only. Canonical identity is frozen by the C006 private source
contract. Runtime uses only the explicitly allowlisted profile + asset locator.
No stable package or release is modified.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORK = Path("/kaggle/working")
INPUT_ROOT = Path("/kaggle/input")
REPO = WORK / "zaskaleta-ai-twin-colab"
PY = WORK / "clone311" / "bin" / "python"
STATUS = WORK / "zaskaleta_c006_status.json"
OUT = WORK / "c006_first_gate"
CANDIDATE = "MASTER_CLONE_CANDIDATE_006"
CANONICAL = "MVIMG_20260929_094501.jpg"
PRIMARY_BEHAVIOR = "VID_20260901_175350.mp4"
MASTER_VOICE = "Zaskaleta_AI_Voice_Master.mp3"
PROFILE = REPO / "content" / "c006_clone_reference_profile_v1.json"
LOCATOR = REPO / "worker" / "locate_c006_assets.py"


def run(cmd, *, cwd=None, timeout=7200):
    print("$", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(
        [str(x) for x in cmd],
        cwd=str(cwd) if cwd else None,
        check=True,
        timeout=timeout,
    )


def find_matches(root: Path, name: str):
    return [p for p in root.rglob(name) if p.is_file()]


def resolve_private_root() -> Path:
    preferred = Path(os.environ.get("ZASKALETA_PRIVATE_ASSET_ROOT", "/kaggle/input"))
    roots = [preferred] if preferred.is_dir() else []
    if INPUT_ROOT.is_dir() and INPUT_ROOT not in roots:
        roots.append(INPUT_ROOT)

    required = (MASTER_VOICE, CANONICAL, PRIMARY_BEHAVIOR)
    for root in roots:
        if all(len(find_matches(root, name)) == 1 for name in required):
            return root
    raise RuntimeError("private C006 dataset with canonical clone assets is not uniquely mounted")


def ffprobe_streams(video: Path) -> dict:
    raw = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=index,codec_type,duration,nb_frames,avg_frame_rate",
            "-of", "json", str(video),
        ],
        text=True,
    )
    data = json.loads(raw)
    fmt = float((data.get("format") or {}).get("duration") or 0)
    streams = data.get("streams") or []
    vids = [s for s in streams if s.get("codec_type") == "video"]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    if len(vids) != 1 or not auds:
        raise RuntimeError("C006 output must contain exactly one video stream and at least one audio stream")
    vd = float(vids[0].get("duration") or fmt or 0)
    ad = float(auds[0].get("duration") or fmt or 0)
    frames = int(vids[0].get("nb_frames") or 0)
    if not (7.5 <= vd <= 15.5):
        raise RuntimeError(f"C006 video-stream duration invalid: {vd:.3f}s")
    if frames and frames < 150:
        raise RuntimeError(f"C006 output has too few video frames: {frames}")
    if abs(vd - ad) > 0.40:
        raise RuntimeError(f"C006 A/V duration mismatch: video={vd:.3f}s audio={ad:.3f}s")
    if abs(fmt - vd) > 0.40:
        raise RuntimeError(f"C006 container/video duration mismatch: container={fmt:.3f}s video={vd:.3f}s")
    return {
        "container_duration": round(fmt, 6),
        "video_duration": round(vd, 6),
        "audio_duration": round(ad, 6),
        "frames": frames,
    }


def write_status(**extra):
    data = {
        "schema": "zaskaleta-c006-kaggle-status-v1",
        "candidate_id": CANDIDATE,
        "auto_promote": False,
        "stable_release_modified": False,
        "raw_private_media_exported_to_github": False,
        "private_hashes_exported": False,
        "face_embeddings_exported": False,
        **extra,
    }
    STATUS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    render_started = False
    try:
        private_root = resolve_private_root()

        run([sys.executable, REPO / "kaggle" / "auto_prepare.py"], cwd=REPO)
        if not PY.is_file():
            raise RuntimeError("clone311 Python missing after preparation")

        run([PY, REPO / "kaggle" / "prepare_models.py", "--verify-only"], cwd=REPO)
        run([PY, REPO / "kaggle" / "preflight.py"], cwd=REPO)

        if not PROFILE.is_file() or not LOCATOR.is_file():
            raise RuntimeError("C006 isolated profile/locator missing")

        # Resolve the full private allowlist before GPU render. This is fail-closed:
        # ambiguous duplicate names, pending use, or missing approved sources stop here.
        asset_map = WORK / "c006_runtime_assets.json"
        run(
            [
                PY, LOCATOR,
                "--private-root", private_root,
                "--profile", PROFILE,
                "--output", asset_map,
            ],
            cwd=REPO,
        )
        assets = json.loads(asset_map.read_text(encoding="utf-8"))
        if assets.get("candidate_id") != CANDIDATE:
            raise RuntimeError("C006 asset binding failed")
        if assets.get("pending_ingestion_allowed") is not False:
            raise RuntimeError("C006 pending isolation weakened")
        if assets.get("auto_promote") is not False or assets.get("stable_release_modified") is not False:
            raise RuntimeError("C006 asset resolver promotion safety weakened")

        OUT.mkdir(parents=True, exist_ok=True)
        render_started = True

        run(
            [
                PY, REPO / "worker" / "run_clone_v2_test.py",
                "--root", REPO,
                "--mydrive", private_root,
                "--output-dir", OUT,
                "--candidate-id", CANDIDATE,
                "--seconds", "8.0",
                "--voice-preset", "conversational",
                "--profile-path", PROFILE,
                "--asset-locator", LOCATOR,
            ],
            cwd=REPO,
            timeout=7200,
        )

        video = OUT / "CLONE_V2_TALKING_TEST.mp4"
        evaluation = OUT / "CLONE_V2_EVALUATION.json"
        if not video.is_file() or not evaluation.is_file():
            raise RuntimeError("C006 mandatory review output missing")

        ev = json.loads(evaluation.read_text(encoding="utf-8"))
        if ev.get("candidate_id") != CANDIDATE:
            raise RuntimeError("C006 output candidate binding failed")

        stream_qa = ffprobe_streams(video)
        write_status(
            state="C006_RENDER_READY_FOR_MANUAL_REVIEW",
            render_started=True,
            render_completed=True,
            promotion_allowed=False,
            subjective_identity_review="PENDING_MANUAL_REVIEW",
            canonical_identity=CANONICAL,
            behavior_reference=PRIMARY_BEHAVIOR,
            output_path=str(video),
            stream_qa=stream_qa,
        )
        return 0

    except Exception as exc:
        write_status(
            state="FAILED_CLOSED",
            render_started=render_started,
            render_completed=False,
            promotion_allowed=False,
            subjective_identity_review="NOT_REACHED",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
