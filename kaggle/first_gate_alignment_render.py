#!/usr/bin/env python3
"""Render MASTER CLONE first-gate challenger CANDIDATE_003.

Single-component change versus CANDIDATE_002:
- enforce talking_profile_v2 audio alignment: 16 kHz lipsync input and 24 kHz final audio.

Safety invariants: 8–15 seconds, candidate-only, no auto-promotion, no stable overwrite,
manual identity review required. Motion reference remains the approved supporting reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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
    cp = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True, timeout=120)
    return json.loads(cp.stdout or "{}")


def duration_seconds(meta: dict) -> float:
    try:
        return float((meta.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        raise RuntimeError("ffprobe did not return a valid duration")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--output-dir", default="/kaggle/working/first_gate_alignment")
    args = ap.parse_args()

    private_root = Path(args.root).resolve()
    repo = Path(args.repo).resolve()
    py = Path(args.python)
    if not py.is_file():
        raise RuntimeError(f"requested worker Python does not exist: {py}")
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    profile = json.loads((repo / "content" / "clone_reference_profile.json").read_text(encoding="utf-8"))
    package = json.loads((repo / "content" / "master_clone_package.json").read_text(encoding="utf-8"))
    talking = json.loads((repo / "content" / "talking_profile_v2.json").read_text(encoding="utf-8"))
    gate_policy = json.loads((repo / "content" / "clone_duration_gate_policy_v1.json").read_text(encoding="utf-8"))

    first_gate = gate_policy["ordered_gates"][0]
    if first_gate.get("id") != "gate_08_15" or first_gate.get("min_seconds") != 8 or first_gate.get("max_seconds") != 15:
        raise RuntimeError("first duration gate policy changed unexpectedly")
    if gate_policy.get("rules", {}).get("manual_promotion_required") is not True:
        raise RuntimeError("manual promotion safety rule is not enabled")

    align = talking["audio_alignment"]
    lip_sr = int(align["lipsync_sample_rate"])
    final_sr = int(align["final_audio_sample_rate"])
    if lip_sr != 16000 or final_sr != 24000 or align.get("pad_end_only") is not True:
        raise RuntimeError("talking_profile_v2 audio alignment changed unexpectedly")

    canonical_name = profile["canonical_identity_photo"]
    voice_name = profile["master_voice_filename"]
    motion_name = package["components"]["motion"]["supporting_reference"]
    canonical = unique_exact(private_root, canonical_name)
    master_voice = unique_exact(private_root, voice_name)
    motion = unique_exact(private_root, motion_name)

    script = out / "gate_script_uk.txt"
    script.write_text(
        "Це третій контрольний тест. Говорю спокійно і природно. Перевіряємо точність губ, паузи, погляд і стабільність обличчя.",
        encoding="utf-8",
    )

    raw_audio = out / "gate_voice_raw.wav"
    lipsync_audio = out / "gate_voice_lipsync_16k.wav"
    final_audio = out / "gate_voice_final_24k.wav"
    musetalk_render = out / "candidate003_musetalk_raw.mp4"
    render = out / "MASTER_CLONE_GATE_08_15_CANDIDATE_003.mp4"
    provenance = out / "MASTER_CLONE_GATE_08_15_CANDIDATE_003.provenance.json"
    evidence_path = out / "MASTER_CLONE_GATE_08_15_CANDIDATE_003.evidence.json"
    candidate_id = "MASTER_CLONE_GATE_08_15_CANDIDATE_003"

    run([
        str(py), str(repo / "worker" / "voice_mms_openvoice.py"),
        "--script", str(script), "--voice", str(master_voice),
        "--output", str(raw_audio), "--language", "uk",
    ], cwd=repo, timeout=3600)

    raw_duration = duration_seconds(probe(raw_audio))
    if raw_duration > 15.0:
        raise RuntimeError(f"generated gate speech exceeds 15s: {raw_duration:.3f}s")
    target = max(8.25, raw_duration)
    pad = max(0.0, target - raw_duration)

    # Identical timing, two sample-rate representations: 16 kHz for MuseTalk analysis,
    # 24 kHz for final delivery. Only end padding is allowed.
    run(["ffmpeg", "-y", "-i", str(raw_audio), "-af", f"apad=pad_dur={pad:.3f}",
         "-t", f"{target:.3f}", "-ac", "1", "-ar", str(lip_sr), str(lipsync_audio)], timeout=300)
    run(["ffmpeg", "-y", "-i", str(raw_audio), "-af", f"apad=pad_dur={pad:.3f}",
         "-t", f"{target:.3f}", "-ac", "1", "-ar", str(final_sr), str(final_audio)], timeout=300)

    lip_duration = duration_seconds(probe(lipsync_audio))
    final_audio_duration = duration_seconds(probe(final_audio))
    if not (8.0 <= lip_duration <= 15.0 and abs(lip_duration - final_audio_duration) <= 0.03):
        raise RuntimeError("aligned audio durations invalid")

    run([
        str(py), str(repo / "worker" / "lipsync_musetalk.py"),
        "--photo", str(canonical), "--reference-video", str(motion),
        "--audio", str(lipsync_audio), "--output", str(musetalk_render),
        "--provenance-output", str(provenance), "--candidate-id", candidate_id,
    ], cwd=repo, timeout=7200)

    # Preserve generated video frames exactly; replace only the audio stream with the
    # 24 kHz aligned master to satisfy the talking profile without re-encoding video.
    run(["ffmpeg", "-y", "-i", str(musetalk_render), "-i", str(final_audio),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", str(final_sr),
         "-ac", "1", "-shortest", str(render)], timeout=600)

    render_meta = probe(render)
    render_duration = duration_seconds(render_meta)
    streams = render_meta.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not has_video or not audio_streams or not 8.0 <= render_duration <= 15.0:
        raise RuntimeError("candidate 003 render streams/duration invalid")
    if int(audio_streams[0].get("sample_rate") or 0) != final_sr:
        raise RuntimeError("candidate 003 final audio sample rate is not 24 kHz")

    evidence = {
        "schema": "zaskaleta-first-gate-alignment-evidence-v1",
        "candidate_id": candidate_id,
        "baseline_candidate_id": "MASTER_CLONE_GATE_08_15_CANDIDATE_002",
        "gate": "gate_08_15",
        "gate_range_seconds": [8, 15],
        "technical_gate_pass": True,
        "single_component_change": "audio_alignment",
        "motion_reference_unchanged": motion_name,
        "lipsync_sample_rate": lip_sr,
        "final_audio_sample_rate": final_sr,
        "pad_end_only": True,
        "render_duration_seconds": round(render_duration, 3),
        "render_sha256": sha256_file(render),
        "provenance_sha256": sha256_file(provenance),
        "subjective_identity_review": "PENDING_MANUAL_REVIEW",
        "lip_sync_review": "PENDING_MANUAL_REVIEW",
        "mouth_beard_edge_stability_review": "PENDING_MANUAL_REVIEW",
        "blink_eye_behavior_review": "PENDING_MANUAL_REVIEW",
        "motion_naturalness_review": "PENDING_MANUAL_REVIEW",
        "identity_regression_decision": "PENDING_MANUAL_REVIEW",
        "promotion_allowed": False,
        "auto_promote": False,
        "stable_release_modified": False,
        "next_state": "FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW",
    }
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_id": candidate_id, "technical_gate_pass": True,
                      "render_duration_seconds": evidence["render_duration_seconds"],
                      "promotion_allowed": False, "state": evidence["next_state"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
