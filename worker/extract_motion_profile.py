import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from mmpose.apis import MMPoseInferencer


def pick_person(predictions):
    candidates = []
    if isinstance(predictions, list):
        for item in predictions:
            if isinstance(item, list):
                candidates.extend(item)
            elif isinstance(item, dict):
                candidates.append(item)
    elif isinstance(predictions, dict):
        candidates = [predictions]
    best = None
    best_score = -1.0
    for item in candidates:
        scores = np.asarray(item.get('keypoint_scores', []), dtype=np.float32)
        if scores.size == 0:
            continue
        score = float(np.nanmean(scores))
        if score > best_score:
            best = item
            best_score = score
    return best, best_score


def normalize_keypoints(keypoints, scores, threshold):
    pts = np.asarray(keypoints, dtype=np.float32)
    conf = np.asarray(scores, dtype=np.float32).reshape(-1)
    if pts.ndim != 2 or pts.shape[1] < 2 or len(conf) != len(pts):
        return None
    valid = conf >= threshold
    if int(valid.sum()) < 5:
        return None
    xy = pts[:, :2].copy()
    good = xy[valid]
    lo = good.min(axis=0)
    hi = good.max(axis=0)
    span = np.maximum(hi - lo, 1.0)
    xy = (xy - lo) / span
    return {
        'keypoints': xy.round(6).tolist(),
        'scores': conf.round(6).tolist(),
        'bbox_xyxy': [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])],
    }


def motion_metrics(frames):
    if len(frames) < 2:
        return {'mean_speed': 0.0, 'peak_speed': 0.0, 'mean_acceleration': 0.0}
    vectors = []
    for a, b in zip(frames, frames[1:]):
        ka = np.asarray(a['keypoints'], dtype=np.float32)
        kb = np.asarray(b['keypoints'], dtype=np.float32)
        sa = np.asarray(a['scores'], dtype=np.float32)
        sb = np.asarray(b['scores'], dtype=np.float32)
        valid = (sa >= 0.25) & (sb >= 0.25)
        if valid.any():
            vectors.append(float(np.linalg.norm(kb[valid] - ka[valid], axis=1).mean()))
    if not vectors:
        return {'mean_speed': 0.0, 'peak_speed': 0.0, 'mean_acceleration': 0.0}
    acc = np.diff(vectors) if len(vectors) > 1 else np.array([0.0])
    return {
        'mean_speed': float(np.mean(vectors)),
        'peak_speed': float(np.max(vectors)),
        'mean_acceleration': float(np.mean(np.abs(acc))),
    }


def main():
    ap = argparse.ArgumentParser(description='Extract identity-independent reusable motion from a video')
    ap.add_argument('--video', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--preset', default='custom')
    ap.add_argument('--sample-fps', type=float, default=8.0)
    ap.add_argument('--confidence', type=float, default=0.25)
    ap.add_argument('--pose2d', default='human')
    args = ap.parse_args()

    video = Path(args.video)
    if not video.is_file():
        raise FileNotFoundError(video)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f'Could not open video: {video}')

    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frame_count / source_fps if source_fps > 0 else 0.0
    sample_every = max(1, int(round(source_fps / max(0.5, args.sample_fps))))

    inferencer = MMPoseInferencer(pose2d=args.pose2d)
    extracted = []
    frame_index = 0
    low_confidence = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % sample_every != 0:
            frame_index += 1
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = next(inferencer(rgb, show=False, return_vis=False))
        person, score = pick_person(result.get('predictions', []))
        if person is None:
            low_confidence += 1
            frame_index += 1
            continue
        normalized = normalize_keypoints(
            person.get('keypoints', []), person.get('keypoint_scores', []), args.confidence
        )
        if normalized is None:
            low_confidence += 1
            frame_index += 1
            continue
        normalized.update({
            'frame': frame_index,
            'time': round(frame_index / source_fps, 4),
            'person_score': round(score, 6),
        })
        extracted.append(normalized)
        frame_index += 1

    cap.release()

    total_samples = len(extracted) + low_confidence
    detection_ratio = len(extracted) / total_samples if total_samples else 0.0
    package = {
        'schema': 'zaskaleta-motion-profile-v1',
        'status': 'extracted_candidate',
        'source': {
            'filename': video.name,
            'duration_seconds': round(duration, 4),
            'source_fps': source_fps,
            'width': width,
            'height': height,
        },
        'motion': {
            'preset': args.preset,
            'sample_fps_requested': args.sample_fps,
            'sample_every_frames': sample_every,
            'confidence_threshold': args.confidence,
            'samples_kept': len(extracted),
            'samples_rejected': low_confidence,
            'detection_ratio': round(detection_ratio, 6),
            'metrics': motion_metrics(extracted),
            'frames': extracted,
        },
        'privacy': {
            'identity_independent': True,
            'face_identity_stored': False,
            'voice_stored': False,
            'background_stored': False,
            'wardrobe_stored': False,
            'raw_frames_embedded': False,
        },
        'approval': {
            'manual_review_required': True,
            'approved_for_master_clone': False,
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
    print('✅ Motion candidate extracted')
    print('OUTPUT=' + str(out))
    print(f'DETECTION_RATIO={detection_ratio:.3f}')
    print(f'SAMPLES={len(extracted)}')


if __name__ == '__main__':
    main()
