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
    media = reference if reference and reference.exists() else photo
    audio = pathlib.Path(args.audio).resolve()
    output = pathlib.Path(args.output).resolve()
    job = output.parent
    config = job / f'musetalk-task-{output.stem}.yaml'
    results = job / 'musetalk-results'
    results.mkdir(parents=True, exist_ok=True)
    write_config(config, media, audio)

    model_dir = root / 'models' / 'musetalkV15'
    cmd = [
        sys.executable, '-m', 'scripts.inference',
        '--inference_config', str(config),
        '--result_dir', str(results),
        '--unet_model_path', str(model_dir / 'unet.pth'),
        '--unet_config', str(model_dir / 'musetalk.json'),
        '--whisper_dir', str(root / 'models' / 'whisper'),
        '--version', 'v15',
        '--use_float16',
        '--batch_size', '4',
    ]
    started = time.time()
    result = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or 'MuseTalk inference failed')[-6000:])

    rendered = newest_mp4(results, started)
    if not rendered:
        tail = ((result.stdout or '') + '\n' + (result.stderr or ''))[-6000:]
        raise RuntimeError('MuseTalk finished but no MP4 was produced. Log tail:\n' + tail)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rendered, output)
    print(f'✅ MuseTalk output: {output}')


if __name__ == '__main__':
    main()
