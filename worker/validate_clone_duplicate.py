import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def duration_seconds(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], check=True, capture_output=True, text=True)
    duration = float(proc.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("invalid_video_duration")
    return duration


def dhash_at(path, timestamp):
    proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{timestamp:.6f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True)
    raw = proc.stdout
    if len(raw) != 72:
        raise RuntimeError("unexpected_decoded_frame_size")
    bits = 0
    for y in range(8):
        for x in range(8):
            if raw[y * 9 + x] > raw[y * 9 + x + 1]:
                bits |= 1 << (y * 8 + x)
    return bits


def sample_times(duration, count):
    if count < 2:
        raise ValueError("sample_count must be >= 2")
    margin = min(0.25, duration * 0.05)
    return [margin + (duration - 2 * margin) * i / (count - 1) for i in range(count)]


def compare(candidate, prior, policy):
    c_sha, p_sha = sha256_file(candidate), sha256_file(prior)
    exact = c_sha == p_sha
    cd, pd = duration_seconds(candidate), duration_seconds(prior)
    short, long = (candidate, prior) if cd <= pd else (prior, candidate)
    sd, ld = min(cd, pd), max(cd, pd)
    count = int(policy["sample_count"])
    max_dist = int(policy["per_frame_dhash_distance_max"])
    min_ratio = float(policy["near_duplicate_matching_frame_ratio_min"])
    containment_ratio = float(policy["containment_matching_frame_ratio_min"])
    duration_ratio = sd / ld
    step = float(policy.get("alignment_step_seconds", 0.25))
    if step <= 0 or count < 2:
        raise ValueError("invalid_duplicate_policy")
    cache = {}
    def frame(path, t):
        key = (str(path), round(t, 6))
        if key not in cache:
            cache[key] = dhash_at(path, t)
        return cache[key]
    times = sample_times(sd, count)
    offsets = [0.0]
    if ld > sd:
        maximum = ld - sd
        offsets = [min(i * step, maximum) for i in range(math.ceil(maximum / step) + 1)]
        offsets.append(maximum)
    best = None
    for offset in sorted(set(round(x, 6) for x in offsets)):
        distances = [((frame(short, t) ^ frame(long, t + offset)).bit_count()) for t in times]
        ratio = sum(d <= max_dist for d in distances) / count
        if best is None or ratio > best["matching_frame_ratio"]:
            best = {"offset_seconds": offset, "frame_hamming_distances": distances, "matching_frame_ratio": ratio}
    near = abs(cd - pd) <= float(policy["duration_delta_seconds_max"]) and best["matching_frame_ratio"] >= min_ratio
    full_containment = best["matching_frame_ratio"] >= containment_ratio
    partial_containment = duration_ratio >= float(policy["containment_common_duration_ratio_min"]) and best["matching_frame_ratio"] >= containment_ratio
    contained = full_containment or partial_containment
    blocked = exact or near or contained
    return {"prior": str(prior), "exact_sha256_match": exact, "candidate_sha256": c_sha, "prior_sha256": p_sha, "candidate_duration": cd, "prior_duration": pd, "common_duration_ratio": duration_ratio, "best_alignment": best, "near_duplicate": near, "contained_duplicate": contained, "blocked": blocked}


def main():
    ap = argparse.ArgumentParser(description="Fail-closed clone duplicate gate")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--prior", action="append", required=True)
    ap.add_argument("--policy", default="content/clone_duplicate_gate_v1.json")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    policy = load_json(args.policy)
    candidate = Path(args.candidate)
    priors = [Path(p) for p in args.prior]
    if not candidate.is_file() or any(not p.is_file() for p in priors):
        raise SystemExit("candidate_or_prior_missing")
    try:
        results = [compare(candidate, p, policy) for p in priors]
        blocked = any(r["blocked"] for r in results)
        decision = "DUPLICATE_CANDIDATE" if blocked else "UNIQUE_ENOUGH"
        error = None
    except Exception as exc:
        results, blocked, decision, error = [], True, "VALIDATION_ERROR", type(exc).__name__ + ": " + str(exc)
    report = {"schema": "zaskaleta-clone-duplicate-report-v1", "candidate": str(candidate), "decision": decision, "hard_fail_flag": policy["hard_fail_flag"] if blocked else None, "manual_override_allowed": False, "comparisons": results, "error": error}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(decision)
    if blocked:
        raise SystemExit(2 if decision == "DUPLICATE_CANDIDATE" else 3)


if __name__ == "__main__":
    main()
