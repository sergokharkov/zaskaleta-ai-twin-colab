#!/usr/bin/env python3
"""Render the first 8–15s MASTER CLONE gate candidate on Kaggle.

This stage is intentionally candidate-only:
- requires already validated private assets
- uses canonical identity + approved primary motion + MASTER VOICE
- produces technical/provenance evidence
- never promotes or overwrites a stable clone
- leaves subjective identity/quality approval for manual review
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> None:
    print("$", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)


def unique_exact(root: Path, filename: str) -> Path:
    matches = [p for p in root.rglob(filename) if p.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"asset {filename!r} must resolve uniquely; matches={len(matches)}")
    return matches[0]


def probe(path: Path) -> dict:
    cp = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels",
            "-of", "json", str(path),
        ],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return json.loads(cp.stdout or "{}")


def duration_seconds(meta: dict) -> float:
    try:
        return float((meta.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        raise RuntimeError("ffprobe did not return a valid duration")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Resolved private asset root")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--output-dir", default="/kaggle/working/first_gate")
    args = ap.parse_args()

    private_root = Path(args.root).resolve()
    repo = Path(args.repo).resolve()
    # Preserve the venv launcher path. Path.resolve() follows clone311/bin/python's
    # symlink to uv's base interpreter and drops the venv site-packages context.
    py = Path(args.python)
    if not py.is_file():
        raise RuntimeError(f"requested worker Python does not exist: {py}")
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    profile = json.loads((repo / "content" / "clone_reference_profile.json").read_text(encoding="utf-8"))
    package = json.loads((repo / "content" / "master_clone_package.json").read_text(encoding="utf-8"))
    gate_policy = json.loads((repo / "content" / "clone_duration_gate_policy_v1.json").read_text(encoding="utf-8"))

    first_gate = gate_policy["ordered_gates"][0]
    if first_gate.get("id") != "gate_08_15" or first_gate.get("min_seconds") != 8 or first_gate.get("max_seconds") != 15:
        raise RuntimeError("first duration gate policy changed unexpectedly")
    if gate_policy.get("rules", {}).get("manual_promotion_required") is not True:
        raise RuntimeError("manual promotion safety rule is not enabled")

    canonical_name = profile["canonical_identity_photo"]
    voice_name = profile["master_voice_filename"]
    motion_name = package["components"]["motion"]["primary_reference"]

    canonical = unique_exact(private_root, canonical_name)
    master_voice = unique_exact(private_root, voice_name)
    motion = unique_exact(private_root, motion_name)

    script = out / "gate_script_uk.txt"
    # Neutral, short, self-descriptive line. It is test content only and never enters reference pools automatically.
    script.write_text(
        "Це перший контрольний тест мого цифрового клону. Перевіряємо голос, міміку, рухи та стабільність обличчя.",
        encoding="utf-8",
    )

    raw_audio = out / "gate_voice_raw.wav"
    gate_audio = out / "gate_voice.wav"
    render = out / "MASTER_CLONE_GATE_08_15.mp4"
    provenance = out / "MASTER_CLONE_GATE_08_15.provenance.json"
    evidence_path = out / "MASTER_CLONE_GATE_08_15.evidence.json"

    run([
        str(py), str(repo / "worker" / "voice_mms_openvoice.py"),
        "--script", str(script), "--voice", str(master_voice),
        "--output", str(raw_audio), "--language", "uk",
    ], cwd=repo, timeout=3600)

    raw_meta = probe(raw_audio)
    raw_duration = duration_seconds(raw_meta)
    # Keep speech intact. If it is slightly short, pad silence to 8.25s. If over 15s, fail closed rather than truncate speech.
    if raw_duration > 15.0:
        raise RuntimeError(f"generated gate speech exceeds 15s: {raw_duration:.3f}s")
    target = max(8.25, raw_duration)
    run([
        "ffmpeg", "-y", "-i", str(raw_audio),
        "-af", f"apad=pad_dur={max(0.0, target - raw_duration):.3f}",
        "-t", f"{target:.3f}", "-ac", "1", "-ar", "22050", str(gate_audio),
    ], timeout=300)

    audio_meta = probe(gate_audio)
    audio_duration = duration_seconds(audio_meta)
    if not 8.0 <= audio_duration <= 15.0:
        raise RuntimeError(f"gate audio duration outside 8–15s: {audio_duration:.3f}s")

    run([
        str(py), str(repo / "worker" / "lipsync_musetalk.py"),
        "--photo", str(canonical),
        "--reference-video", str(motion),
        "--audio", str(gate_audio),
        "--output", str(render),
        "--provenance-output", str(provenance),
        "--candidate-id", "MASTER_CLONE_GATE_08_15_CANDIDATE_001",
    ], cwd=repo, timeout=7200)

    render_meta = probe(render)
    render_duration = duration_seconds(render_meta)
    streams = render_meta.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_video or not has_audio:
        raise RuntimeError(f"render streams invalid: video={has_video} audio={has_audio}")
    if not 8.0 <= render_duration <= 15.0:
        raise RuntimeError(f"render duration outside 8–15s: {render_duration:.3f}s")

    evidence = {
        "schema": "zaskaleta-first-gate-evidence-v1",
        "candidate_id": "MASTER_CLONE_GATE_08_15_CANDIDATE_001",
        "gate": "gate_08_15",
        "gate_range_seconds": [8, 15],
        "technical_gate_pass": True,
        "render_duration_seconds": round(render_duration, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "video_stream_present": has_video,
        "audio_stream_present": has_audio,
        "canonical_identity_filename": canonical_name,
        "approved_motion_filename": motion_name,
        "master_voice_filename": voice_name,
        "render_sha256": sha256_file(render),
        "provenance_sha256": sha256_file(provenance),
        "subjective_identity_review": "PENDING_MANUAL_REVIEW",
        "lip_sync_review": "PENDING_MANUAL_REVIEW",
        "motion_naturalness_review": "PENDING_MANUAL_REVIEW",
        "voice_naturalness_review": "PENDING_MANUAL_REVIEW",
        "identity_regression_decision": "PENDING_MANUAL_REVIEW",
        "promotion_allowed": False,
        "auto_promote": False,
        "stable_release_modified": False,
        "next_state": "FIRST_GATE_RENDER_READY_FOR_MANUAL_REVIEW",
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate_id": evidence["candidate_id"],
        "technical_gate_pass": True,
        "render_duration_seconds": evidence["render_duration_seconds"],
        "subjective_identity_review": evidence["subjective_identity_review"],
        "promotion_allowed": False,
        "state": evidence["next_state"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
