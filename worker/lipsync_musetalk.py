import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_config(path: pathlib.Path, media: pathlib.Path, audio: pathlib.Path):
    def q(value: pathlib.Path):
        return str(value).replace('\\', '/').replace('"', '\\"')
    path.write_text(
        'task_0:\n'
        f' video_path: "{q(media)}"\n'
        f' audio_path: "{q(audio)}"\n',
        encoding='utf-8',
    )


def newest_mp4(root: pathlib.Path, started_at: float):
    candidates = [
        p for p in root.rglob('*.mp4')
        if p.is_file() and p.stat().st_mtime >= started_at - 2
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def copy_local(src: pathlib.Path, dst_dir: pathlib.Path, stem: str):
    suffix = src.suffix.lower() or '.bin'
    dst = dst_dir / f'{stem}{suffix}'
    shutil.copy2(src, dst)
    return dst


def run_inference(root, model_dir, config, attempt_results, batch_size):
    cmd = [
        sys.executable, '-m', 'scripts.inference',
        '--inference_config', str(config),
        '--result_dir', str(attempt_results),
        '--unet_model_path', str(model_dir / 'unet.pth'),
        '--unet_config', str(model_dir / 'musetalk.json'),
        '--whisper_dir', str(root / 'models' / 'whisper'),
        '--version', 'v15',
        '--use_float16',
        '--batch_size', str(batch_size),
    ]
    started = time.time()
    result = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    rendered = newest_mp4(attempt_results, started)
    return result, rendered


def main():
    parser = argparse.ArgumentParser(description='MuseTalk 1.5 adapter for Zaskaleta AI Twin')
    parser.add_argument('--photo', required=True)
    parser.add_argument('--reference-video', default='')
    parser.add_argument('--audio', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--provenance-output', default='', help='Optional JSON output for immutable render provenance evidence.')
    parser.add_argument('--candidate-id', default='')
    parser.add_argument(
        '--allow-photo-fallback',
        action='store_true',
        help='Explicitly allow a static-photo fallback if the approved motion reference fails. Disabled by default for MASTER CLONE safety.',
    )
    args = parser.parse_args()

    root = pathlib.Path(os.environ.get('MUSETALK_ROOT', '/content/MuseTalk')).resolve()
    if not (root / 'scripts' / 'inference.py').exists():
        raise SystemExit(f'MuseTalk not found at {root}')

    photo = pathlib.Path(args.photo).resolve()
    reference = pathlib.Path(args.reference_video).resolve() if args.reference_video else None
    audio = pathlib.Path(args.audio).resolve()
    output = pathlib.Path(args.output).resolve()
    provenance_output = pathlib.Path(args.provenance_output).resolve() if args.provenance_output else output.with_suffix(output.suffix + '.provenance.json')

    if not photo.is_file():
        raise FileNotFoundError(photo)
    if not audio.is_file():
        raise FileNotFoundError(audio)
    if reference is None or not reference.is_file():
        raise FileNotFoundError(
            f'Approved reference video is required for MASTER CLONE lipsync: {reference}'
        )

    source_hashes = {
        'canonical_photo_sha256': sha256_file(photo),
        'approved_reference_video_sha256': sha256_file(reference),
        'speech_audio_sha256': sha256_file(audio),
    }

    media_candidates = [('approved-animated-reference', reference)]
    if args.allow_photo_fallback:
        media_candidates.append(('explicit-keyframe-fallback', photo))

    model_dir = root / 'models' / 'musetalkV15'
    failures = []

    with tempfile.TemporaryDirectory(prefix='zaskaleta_musetalk_', dir='/content') as tmp_name:
        tmp = pathlib.Path(tmp_name)
        local_audio = copy_local(audio, tmp, 'audio')

        for attempt_no, (label, media) in enumerate(media_candidates, start=1):
            attempt_dir = tmp / f'attempt_{attempt_no}'
            attempt_dir.mkdir(parents=True, exist_ok=True)
            local_media = copy_local(media, attempt_dir, 'input')
            config = attempt_dir / 'task.yaml'
            write_config(config, local_media, local_audio)

            for batch_size in (8, 4):
                attempt_results = attempt_dir / f'results_b{batch_size}'
                attempt_results.mkdir(parents=True, exist_ok=True)
                print(f'🎭 MuseTalk attempt {attempt_no}: {label}, batch={batch_size}')
                try:
                    result, rendered = run_inference(
                        root, model_dir, config, attempt_results, batch_size
                    )
                except Exception as exc:
                    failures.append(f'{label}/batch{batch_size}: subprocess error: {exc}')
                    print(f'⚠️ {label} batch={batch_size} failed to start: {exc}')
                    continue

                if result.returncode == 0 and rendered is not None:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(rendered, output)
                    if not output.is_file() or output.stat().st_size <= 0:
                        raise RuntimeError('MuseTalk reported success but final output is missing or empty')

                    render_sha = sha256_file(output)
                    evidence = {
                        'schema': 'zaskaleta-lipsync-render-provenance-v1',
                        'candidate_id': args.candidate_id.strip() or None,
                        'engine': 'MuseTalk',
                        'engine_version': '1.5',
                        'render_mode': label,
                        'batch_size': batch_size,
                        'photo_fallback_allowed': bool(args.allow_photo_fallback),
                        'photo_fallback_used': label == 'explicit-keyframe-fallback',
                        'approved_motion_reference_required': True,
                        'source_hashes': source_hashes,
                        'output': {
                            'path': str(output),
                            'sha256': render_sha,
                            'size_bytes': output.stat().st_size,
                        },
                        'provenance_complete': True,
                        'auto_promote': False,
                    }
                    provenance_output.parent.mkdir(parents=True, exist_ok=True)
                    provenance_output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                    print(f'✅ MuseTalk output ({label}, batch={batch_size}): {output}')
                    print(f'🔐 Provenance: {provenance_output}')
                    return

                tail = ((result.stdout or '') + '\n' + (result.stderr or ''))[-7000:]
                failures.append(f'{label}/batch{batch_size}: returncode={result.returncode}\n{tail}')
                if batch_size == 8:
                    print('⚠️ Batch 8 failed; retrying batch 4 with the same approved reference.')
                else:
                    print(f'⚠️ MuseTalk {label} did not produce MP4.')
                print(tail[-2500:])

    fallback_note = (
        ' Photo fallback was explicitly enabled.' if args.allow_photo_fallback
        else ' Static-photo fallback is disabled; refusing to replace approved motion implicitly.'
    )
    raise RuntimeError(
        'MuseTalk failed for all allowed media candidates.' + fallback_note + '\nLog tail:\n' +
        '\n\n===== NEXT ATTEMPT =====\n\n'.join(failures)[-12000:]
    )


if __name__ == '__main__':
    main()
