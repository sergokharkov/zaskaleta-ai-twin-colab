import argparse
import hashlib
import json
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
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(proc.stdout.strip())


def dhash_at(path, timestamp):
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{timestamp:.6f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"
        ],
        check=True,
        capture_output=True,
    )
    raw = proc.stdout
    if len(raw) != 72:
        raise RuntimeError(f"unexpected decoded frame size: {len(raw)}")
    bits = 0
    bit_index = 0
    for y in range(8):
        row = raw[y * 9:(y + 1) * 9]
        for x in range(8):
            if row[x] > row[x + 1]:
                bits |= 1 << bit_index
            bit_index += 1
    return bits


def hamming(a, b):
    return (a ^ b).bit_count()


def sample_times(duration, count):
    if count < 2:
        raise ValueError("sample_count must be >= 2")
    start = min(0.25, duration * 0.05)
    end = max(start, duration - min(0.25, duration * 0.05))
    if end <= start:
        return [duration / 2.0] * count
    return [start + (end - start) * i / (count - 1) for i in range(count)]


def compare(candidate, prior, policy):
    candidate_sha = sha256_file(candidate)
    prior_sha = sha256_file(prior)
    exact = candidate_sha == prior_sha

    c_duration = duration_seconds(candidate)
    p_duration = duration_seconds(prior)
    duration_delta = abs(c_duration - p_duration)
    common_duration = min(c_duration, p_duration)
    common_duration_ratio = common_duration / max(c_duration, p_duration)

    count = int(policy["sample_count"])
    max_dist = int(policy["per_frame_dhash_distance_max"])
    min_ratio = float(policy["near_duplicate_matching_frame_ratio_min"])
    max_duration_delta = float(policy["duration_delta_seconds_max"])
    containment_min_ratio = float(policy["containment_matching_frame_ratio_min"])
    containment_common_min = float(policy["containment_common_duration_ratio_min"])

    times = sample_times(common_duration, count)
    distances = []
    matching = 0
    for t in times:
        d = hamming(dhash_at(candidate, t), dhash_at(prior, t))
        distances.append(d)
        if d <= max_dist:
            matching += 1
    ratio = matching / len(times)

    near_duplicate = duration_delta <= max_duration_delta and ratio >= min_ratio
    contained_duplicate = ratio >= containment_min_ratio and common_duration_ratio >= containment_common_min
    blocked = exact or near_duplicate or contained_duplicate

    return {
        "prior": str(prior),
        "exact_sha256_match": exact,
        "candidate_sha256": candidate_sha,
        "prior_sha256": prior_sha,
        "candidate_duration": c_duration,
        "prior_duration": p_duration,
        "duration_delta": duration_delta,
        "common_duration_ratio": common_duration_ratio,
        "frame_hamming_distances": distances,
        "matching_frame_ratio": ratio,
        "near_duplicate": near_duplicate,
        "contained_duplicate": contained_duplicate,
        "blocked": blocked,
    }


def main():
    ap = argparse.ArgumentParser(description="Fail-closed clone duplicate gate")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--prior", action="append", required=True, help="Prior candidate video; repeat for multiple files")
    ap.add_argument("--policy", default="content/clone_duplicate_gate_v1.json")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    policy = load_json(args.policy)
    candidate = Path(args.candidate)
    priors = [Path(p) for p in args.prior]
    if not candidate.is_file():
        raise SystemExit("candidate_missing")
    missing = [str(p) for p in priors if not p.is_file()]
    if missing:
        raise SystemExit("prior_missing:" + ",".join(missing))

    results = [compare(candidate, prior, policy) for prior in priors]
    blocked = any(r["blocked"] for r in results)
    report = {
        "schema": "zaskaleta-clone-duplicate-report-v1",
        "candidate": str(candidate),
        "decision": "DUPLICATE_CANDIDATE" if blocked else "UNIQUE_ENOUGH",
        "hard_fail_flag": policy["hard_fail_flag"] if blocked else None,
        "manual_override_allowed": False,
        "comparisons": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report["decision"])
    if blocked:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
