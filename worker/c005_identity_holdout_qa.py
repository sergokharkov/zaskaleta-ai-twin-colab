#!/usr/bin/env python3
import argparse, json, math, sys
from pathlib import Path

import cv2
import numpy as np

ANCHOR_PHOTOS = ['53665.jpg','53666.jpg','53668.jpg']
HOLDOUT_PHOTO = '53669.jpg'
VIDEOS = ['54193.mp4','54194.mp4','54197.mp4']

# OpenCV SFace reference threshold is around 0.363 cosine on LFW.
# Keep a small safety margin while allowing moderate pose/lighting variation.
MIN_COSINE = 0.40
MIN_VIDEO_PASSES = 2
SAMPLE_FRACTIONS = (0.20, 0.50, 0.80)


def detect_largest(detector, image):
    h, w = image.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        return None
    return max(faces, key=lambda f: float(f[2] * f[3]))


def embedding(detector, recognizer, image):
    face = detect_largest(detector, image)
    if face is None:
        return None
    aligned = recognizer.alignCrop(image, face)
    feat = recognizer.feature(aligned).reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(feat))
    if not math.isfinite(norm) or norm <= 0:
        return None
    return feat / norm


def cosine(a, b):
    return float(np.dot(a, b))


def sample_video(path, fractions):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f'cannot open {path.name}')
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        raise RuntimeError(f'no frame count for {path.name}')
    frames = []
    for frac in fractions:
        idx = max(0, min(frame_count - 1, int(round((frame_count - 1) * frac))))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append((idx, frame))
    cap.release()
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--detector-model', required=True)
    ap.add_argument('--recognizer-model', required=True)
    ap.add_argument('--source-sha', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    root = Path(args.root)
    detector = cv2.FaceDetectorYN.create(args.detector_model, '', (320, 320), 0.8, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create(args.recognizer_model, '')

    anchor_embeddings = []
    anchor_status = {}
    for name in ANCHOR_PHOTOS:
        img = cv2.imread(str(root / name))
        if img is None:
            raise SystemExit(f'anchor image unreadable: {name}')
        emb = embedding(detector, recognizer, img)
        if emb is None:
            raise SystemExit(f'anchor face missing: {name}')
        anchor_embeddings.append(emb)
        anchor_status[name] = 'PASS'

    centroid = np.mean(np.stack(anchor_embeddings), axis=0)
    centroid /= np.linalg.norm(centroid)

    holdout_img = cv2.imread(str(root / HOLDOUT_PHOTO))
    if holdout_img is None:
        raise SystemExit('holdout image unreadable')
    holdout_emb = embedding(detector, recognizer, holdout_img)
    if holdout_emb is None:
        raise SystemExit('holdout face missing')
    holdout_pass = cosine(centroid, holdout_emb) >= MIN_COSINE
    if not holdout_pass:
        raise SystemExit('identity holdout photo mismatch')

    video_results = {}
    for name in VIDEOS:
        samples = sample_video(root / name, SAMPLE_FRACTIONS)
        detected = 0
        passed = 0
        for _, frame in samples:
            emb = embedding(detector, recognizer, frame)
            if emb is None:
                continue
            detected += 1
            if cosine(centroid, emb) >= MIN_COSINE:
                passed += 1
        ok = detected >= MIN_VIDEO_PASSES and passed >= MIN_VIDEO_PASSES
        video_results[name] = {
            'sample_count': len(samples),
            'face_detected_count': detected,
            'identity_pass_count': passed,
            'decision': 'PASS' if ok else 'FAIL',
        }
        if not ok:
            raise SystemExit(f'identity holdout video mismatch: {name}')

    # Public-safe evidence only: no face embeddings, media hashes, or raw similarity values.
    out = {
        'schema': 'zaskaleta-c005-identity-holdout-qa-v1',
        'candidate_id': 'MASTER_CLONE_CANDIDATE_005',
        'source_sha': args.source_sha,
        'decision': 'IDENTITY_HOLDOUT_PASS',
        'anchor_count': len(ANCHOR_PHOTOS),
        'holdout_photo': {'alias': 'C005_IDENTITY_PHOTO_04', 'decision': 'PASS'},
        'videos': {
            'C005_BEHAVIOR_FRONT_16S': video_results['54193.mp4'],
            'C005_BEHAVIOR_FRONT_36S': video_results['54194.mp4'],
            'C005_BEHAVIOR_LONG_119S': video_results['54197.mp4'],
        },
        'raw_biometric_media_exported': False,
        'face_embeddings_exported': False,
        'similarity_values_exported': False,
        'gpu_launch_allowed': False,
        'stable_release_modified': False,
        'next_stage': 'FINAL_EXACT_SHA_PREFLIGHT',
    }
    Path(args.output).write_text(json.dumps(out, indent=2) + '\n', encoding='utf-8')
    print('C005_IDENTITY_HOLDOUT_PASS')
    print('GPU_LAUNCH_ALLOWED=false')
    print('NEXT_STAGE=FINAL_EXACT_SHA_PREFLIGHT')


if __name__ == '__main__':
    main()
