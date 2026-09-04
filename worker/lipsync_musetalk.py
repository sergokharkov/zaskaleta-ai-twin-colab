import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time


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
    args = parser.parse_args()

    root = pathlib.Path(os.environ.get('MUSETALK_ROOT', '/content/MuseTalk')).resolve()
    if not (root / 'scripts' / 'inference.py').exists():
        raise SystemExit(f'MuseTalk not found at {root}')

    photo = pathlib.Path(args.photo).resolve()
    reference = pathlib.Path(args.reference_video).resolve() if args.reference_video else None
    audio = pathlib.Path(args.audio).resolve()
    output = pathlib.Path(args.output).resolve()

    if not photo.is_file():
        raise FileNotFoundError(photo)
    if not audio.is_file():
        raise FileNotFoundError(audio)

    media_candidates = []
    if reference and reference.is_file():
        media_candidates.append(('animated-reference', reference))
    media_candidates.append(('keyframe-fallback', photo))

    model_dir = root / 'models' / 'musetalkV15'
    failures = []

    # MuseTalk internally invokes ffmpeg with shell-style command strings, so run
    # inference from simple local /content paths and copy only the finished MP4 back.
    with tempfile.TemporaryDirectory(prefix='zaskaleta_musetalk_', dir='/content') as tmp_name:
        tmp = pathlib.Path(tmp_name)
        local_audio = copy_local(audio, tmp, 'audio')

        for attempt_no, (label, media) in enumerate(media_candidates, start=1):
            attempt_dir = tmp / f'attempt_{attempt_no}'
            attempt_dir.mkdir(parents=True, exist_ok=True)
            local_media = copy_local(media, attempt_dir, 'input')
            config = attempt_dir / 'task.yaml'
            write_config(config, local_media, local_audio)

            # T4 normally benefits from batch 8. If VRAM is insufficient, retry the
            # same media automatically with batch 4 rather than failing the whole day.
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
                    print(f'✅ MuseTalk output ({label}, batch={batch_size}): {output}')
                    return

                tail = ((result.stdout or '') + '\n' + (result.stderr or ''))[-7000:]
                failures.append(f'{label}/batch{batch_size}: returncode={result.returncode}\n{tail}')
                if batch_size == 8:
                    print('⚠️ Batch 8 failed; retrying batch 4 automatically.')
                else:
                    print(f'⚠️ MuseTalk {label} did not produce MP4; trying fallback media if available.')
                print(tail[-2500:])

    raise RuntimeError(
        'MuseTalk failed for all media candidates. Log tail:\n' +
        '\n\n===== NEXT ATTEMPT =====\n\n'.join(failures)[-12000:]
    )


if __name__ == '__main__':
    main()
