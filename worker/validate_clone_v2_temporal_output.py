import argparse
import json
import math
from pathlib import Path

import cv2


def load_policy(path: Path) -> dict:
    policy = json.loads(path.read_text(encoding='utf-8'))
    required = [
        'sample_count',
        'minimum_pass_ratio',
        'max_face_count',
        'minimum_face_sharpness',
        'minimum_face_area_ratio',
        'maximum_face_area_ratio',
        'maximum_normalized_center_jump',
        'maximum_adjacent_area_ratio_multiplier',
    ]
    missing = [key for key in required if key not in policy]
    if missing:
        raise SystemExit('Temporal policy missing required fields: ' + ', '.join(missing))
    return policy


def sample_indices(frame_count: int, count: int):
    count = max(1, min(int(count), frame_count))
    return sorted({int(round(i * (frame_count - 1) / max(count - 1, 1))) for i in range(count)})


def normalized_center(box, width: int, height: int):
    x, y, w, h = box
    return ((x + w / 2.0) / float(width), (y + h / 2.0) / float(height))


def center_distance(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def validate(video_path: Path, policy: dict) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {'passed': False, 'reason': 'video_open_failed', 'samples': []}

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if frame_count <= 0 or width <= 0 or height <= 0:
        cap.release()
        return {'passed': False, 'reason': 'invalid_video_geometry', 'samples': []}

    detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    indices = sample_indices(frame_count, int(policy['sample_count']))
    samples = []
    passed_samples = 0
    continuity_failures = []
    previous_valid = None

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        sample = {'frame': idx, 'time_seconds': round(idx / fps, 3) if fps > 0 else None}
        if not ok or frame is None:
            sample.update({'passed': False, 'reason': 'frame_read_failed'})
            samples.append(sample)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, scaleFactor=1.07, minNeighbors=5, minSize=(48, 48))
        face_count = int(len(faces))
        sample['face_count'] = face_count

        if face_count == 0:
            sample.update({'passed': False, 'reason': 'no_face_detected'})
            samples.append(sample)
            continue
        if face_count > int(policy['max_face_count']):
            sample.update({'passed': False, 'reason': 'multiple_faces_detected'})
            samples.append(sample)
            continue

        x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
        area_ratio = float(w * h) / float(width * height)
        face_gray = gray[y:y + h, x:x + w]
        sharpness = float(cv2.Laplacian(face_gray, cv2.CV_64F).var()) if face_gray.size else 0.0
        center = normalized_center((x, y, w, h), width, height)

        area_ok = float(policy['minimum_face_area_ratio']) <= area_ratio <= float(policy['maximum_face_area_ratio'])
        sharp_ok = sharpness >= float(policy['minimum_face_sharpness'])
        structural_pass = bool(area_ok and sharp_ok)
        sample.update({
            'passed': structural_pass,
            'reason': 'ok' if structural_pass else ('face_size_out_of_range' if not area_ok else 'face_too_soft'),
            'face_area_ratio': round(area_ratio, 6),
            'face_sharpness': round(sharpness, 3),
            'face_box': [int(x), int(y), int(w), int(h)],
            'normalized_center': [round(center[0], 6), round(center[1], 6)],
        })

        if structural_pass:
            passed_samples += 1
            if previous_valid is not None:
                jump = center_distance(previous_valid['center'], center)
                smaller = max(min(previous_valid['area_ratio'], area_ratio), 1e-9)
                larger = max(previous_valid['area_ratio'], area_ratio)
                area_multiplier = larger / smaller
                continuity = {
                    'from_frame': previous_valid['frame'],
                    'to_frame': idx,
                    'normalized_center_jump': round(jump, 6),
                    'area_ratio_multiplier': round(area_multiplier, 6),
                }
                if jump > float(policy['maximum_normalized_center_jump']):
                    continuity['reason'] = 'face_center_jump_exceeded'
                    continuity_failures.append(continuity)
                elif area_multiplier > float(policy['maximum_adjacent_area_ratio_multiplier']):
                    continuity['reason'] = 'face_scale_jump_exceeded'
                    continuity_failures.append(continuity)
            previous_valid = {'frame': idx, 'center': center, 'area_ratio': area_ratio}

        samples.append(sample)

    cap.release()
    total = len(samples)
    pass_ratio = float(passed_samples) / float(total) if total else 0.0
    structural_ok = total > 0 and pass_ratio >= float(policy['minimum_pass_ratio'])
    continuity_ok = not continuity_failures
    passed = bool(structural_ok and continuity_ok)
    reason = 'ok' if passed else ('temporal_geometry_instability' if continuity_failures else 'temporal_face_instability')

    return {
        'schema': 'zaskaleta-clone-v2-temporal-output-evaluation-v1',
        'passed': passed,
        'reason': reason,
        'video': video_path.name,
        'video_geometry': {'frames': frame_count, 'fps': fps, 'width': width, 'height': height},
        'sample_count': total,
        'passed_samples': passed_samples,
        'pass_ratio': round(pass_ratio, 6),
        'required_pass_ratio': float(policy['minimum_pass_ratio']),
        'continuity_failures': continuity_failures,
        'samples': samples,
        'identity_note': 'Structural temporal validation only. It does not identify the person and cannot replace canonical identity similarity or manual review.',
    }


def main():
    ap = argparse.ArgumentParser(description='Validate Clone v2 talking output against temporal face policy')
    ap.add_argument('--video', required=True)
    ap.add_argument('--policy', default='content/talking_temporal_guard_v1.json')
    ap.add_argument('--output', default=None)
    args = ap.parse_args()

    video = Path(args.video)
    policy_path = Path(args.policy)
    if not video.is_file():
        raise SystemExit(f'Video not found: {video}')
    if not policy_path.is_file():
        raise SystemExit(f'Policy not found: {policy_path}')

    result = validate(video, load_policy(policy_path))
    output = Path(args.output) if args.output else video.with_suffix('.temporal-evaluation.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': result['passed'], 'reason': result['reason'], 'output': str(output)}, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
