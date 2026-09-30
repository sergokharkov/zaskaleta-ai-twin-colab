#!/usr/bin/env python3
import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

PRIMARY = [
    "MVIMG_20260830_144457.jpg",
    "MVIMG_20260830_144606.jpg",
    "MVIMG_20260830_144834.jpg",
    "MVIMG_20260830_144843.jpg",
    "MVIMG_20260929_094501.jpg",
    "MVIMG_20260929_094504.jpg",
    "MVIMG_20260929_094509.jpg",
    "MVIMG_20260929_095053.jpg",
    "MVIMG_20260929_095058.jpg",
    "MVIMG_20260929_095109.jpg",
    "MVIMG_20260929_095114.jpg",
    "MVIMG_20260929_095123.jpg",
]
SECONDARY = [
    "image-1788277947699.jpg",
    "image-1788277957517.jpg",
]
FACE_MOTION = [
    "VID_20260901_175254.mp4",
    "VID_20260901_175350.mp4",
    "VID_20260929_095146.mp4",
]

MIN_COSINE = 0.40
MIN_PRIMARY_DETECTED = 10
MIN_PRIMARY_PASS_RATIO = 0.89
VIDEO_SAMPLE_FRACTIONS = (0.10, 0.30, 0.50, 0.70, 0.90)
MIN_VIDEO_DETECTIONS = 3
MIN_VIDEO_IDENTITY_PASSES = 3


def detect_largest(detector, image):
    h, w = image.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        return None, 0
    return max(faces, key=lambda f: float(f[2] * f[3])), len(faces)


def embedding(detector, recognizer, image):
    face, count = detect_largest(detector, image)
    if face is None:
        return None, count
    aligned = recognizer.alignCrop(image, face)
    feat = recognizer.feature(aligned).reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(feat))
    if not math.isfinite(norm) or norm <= 0:
        return None, count
    return feat / norm, count


def cosine(a, b):
    return float(np.dot(a, b))


def sample_video(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {path.name}")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        raise RuntimeError(f"no frame count for {path.name}")
    frames = []
    for frac in VIDEO_SAMPLE_FRACTIONS:
        idx = max(0, min(frame_count - 1, int(round((frame_count - 1) * frac))))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append((idx, frame))
    cap.release()
    return frames


def normalize_centroid(features):
    centroid = np.mean(np.stack(features), axis=0)
    norm = float(np.linalg.norm(centroid))
    if not math.isfinite(norm) or norm <= 0:
        raise RuntimeError("invalid identity centroid")
    return centroid / norm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--detector-model", required=True)
    ap.add_argument("--recognizer-model", required=True)
    ap.add_argument("--source-sha", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    root = Path(args.root)
    detector = cv2.FaceDetectorYN.create(
        args.detector_model, "", (320, 320), 0.8, 0.3, 5000
    )
    recognizer = cv2.FaceRecognizerSF.create(args.recognizer_model, "")

    primary_embeddings = {}
    primary_face_counts = {}
    for name in PRIMARY:
        image = cv2.imread(str(root / name))
        if image is None:
            raise SystemExit(f"primary image unreadable: {name}")
        emb, face_count = embedding(detector, recognizer, image)
        primary_face_counts[name] = face_count
        if emb is not None:
            primary_embeddings[name] = emb

    detected_count = len(primary_embeddings)
    if detected_count < MIN_PRIMARY_DETECTED:
        raise SystemExit(
            f"insufficient primary face detections: {detected_count}/{len(PRIMARY)}"
        )

    # Leave-one-out consistency among detected primary references.
    primary_passes = 0
    for name, emb in primary_embeddings.items():
        others = [v for k, v in primary_embeddings.items() if k != name]
        if len(others) < MIN_PRIMARY_DETECTED - 1:
            raise SystemExit("insufficient leave-one-out anchor set")
        centroid = normalize_centroid(others)
        if cosine(centroid, emb) >= MIN_COSINE:
            primary_passes += 1

    primary_pass_ratio = primary_passes / detected_count
    if primary_pass_ratio < MIN_PRIMARY_PASS_RATIO:
        raise SystemExit(
            f"primary identity consistency below threshold: {primary_passes}/{detected_count}"
        )

    centroid = normalize_centroid(list(primary_embeddings.values()))

    secondary_detected = 0
    secondary_passed = 0
    for name in SECONDARY:
        image = cv2.imread(str(root / name))
        if image is None:
            continue
        emb, _ = embedding(detector, recognizer, image)
        if emb is None:
            continue
        secondary_detected += 1
        if cosine(centroid, emb) >= MIN_COSINE:
            secondary_passed += 1

    video_results = {}
    for name in FACE_MOTION:
        samples = sample_video(root / name)
        detected = 0
        passed = 0
        multiface_samples = 0
        for _, frame in samples:
            emb, face_count = embedding(detector, recognizer, frame)
            if face_count > 1:
                multiface_samples += 1
            if emb is None:
                continue
            detected += 1
            if cosine(centroid, emb) >= MIN_COSINE:
                passed += 1
        ok = (
            detected >= MIN_VIDEO_DETECTIONS
            and passed >= MIN_VIDEO_IDENTITY_PASSES
            and multiface_samples <= 1
        )
        video_results[name] = {
            "sample_count": len(samples),
            "face_detected_count": detected,
            "identity_pass_count": passed,
            "multiface_sample_count": multiface_samples,
            "decision": "PASS" if ok else "FAIL",
        }
        if not ok:
            raise SystemExit(f"face-motion identity holdout mismatch: {name}")

    out = {
        "schema": "zaskaleta-c006-identity-holdout-qa-v1",
        "candidate_id": "MASTER_CLONE_CANDIDATE_006",
        "source_sha": args.source_sha,
        "decision": "IDENTITY_HOLDOUT_PASS",
        "primary_registered": len(PRIMARY),
        "primary_detected": detected_count,
        "primary_identity_passed": primary_passes,
        "primary_machine_pass_ratio": round(primary_pass_ratio, 6),
        "manual_profile_view_review_still_required": True,
        "secondary": {
            "registered": len(SECONDARY),
            "detected": secondary_detected,
            "identity_passed": secondary_passed,
            "hard_gate": False,
        },
        "face_motion": {
            "C006_FACE_MOTION_01": video_results["VID_20260901_175254.mp4"],
            "C006_FACE_MOTION_02": video_results["VID_20260901_175350.mp4"],
            "C006_FACE_MOTION_03": video_results["VID_20260929_095146.mp4"],
        },
        "raw_biometric_media_exported": False,
        "face_embeddings_exported": False,
        "raw_similarity_values_exported": False,
        "private_hashes_exported": False,
        "gpu_launch_allowed": False,
        "auto_promote": False,
        "stable_release_modified": False,
        "next_stage": "BEHAVIOR_AND_VOICE_ROLE_QA",
    }

    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("C006_IDENTITY_HOLDOUT_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
