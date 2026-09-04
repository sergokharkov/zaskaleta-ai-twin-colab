import argparse
import os
import pathlib
import shutil
import subprocess
import sys
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
    job = output.parent
    results_root = job / 'musetalk-results'
    results_root.mkdir(parents=True, exist_ok=True)

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

    for attempt_no, (label, media) in enumerate(media_candidates, start=1):
        attempt_results = results_root / f'{output.stem}-{attempt_no}'
        if attempt_results.exists():
            shutil.rmtree(attempt_results, ignore_errors=True)
        attempt_results.mkdir(parents=True, exist_ok=True)

        config = job / f'musetalk-task-{output.stem}-{attempt_no}.yaml'
        write_config(config, media, audio)

        cmd = [
            sys.executable, '-m', 'scripts.inference',
            '--inference_config', str(config),
            '--result_dir', str(attempt_results),
            '--unet_model_path', str(model_dir / 'unet.pth'),
            '--unet_config', str(model_dir / 'musetalk.json'),
            '--whisper_dir', str(root / 'models' / 'whisper'),
            '--version', 'v15',
            '--use_float16',
            '--batch_size', '4',
        ]

        print(f'🎭 MuseTalk attempt {attempt_no}: {label} -> {media}')
        started = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=3600,
            )
        except Exception as exc:
            failures.append(f'{label}: subprocess error: {exc}')
            print(f'⚠️ {label} failed to start: {exc}')
            continue

        rendered = newest_mp4(attempt_results, started)
        if result.returncode == 0 and rendered is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rendered, output)
            print(f'✅ MuseTalk output ({label}): {output}')
            return

        tail = ((result.stdout or '') + '\n' + (result.stderr or ''))[-7000:]
        failures.append(f'{label}: returncode={result.returncode}\n{tail}')
        print(f'⚠️ MuseTalk {label} did not produce MP4; trying fallback if available.')
        print(tail[-2500:])

    raise RuntimeError(
        'MuseTalk failed for all media candidates. Log tail:\n' +
        '\n\n===== NEXT ATTEMPT =====\n\n'.join(failures)[-12000:]
    )


if __name__ == '__main__':
    main()
