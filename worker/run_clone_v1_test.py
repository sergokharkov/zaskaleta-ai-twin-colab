import argparse
import json
import subprocess
from pathlib import Path


def run(cmd):
    cmd = [str(x) for x in cmd]
    print('▶', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)
    ], text=True).strip()
    return float(out)


def main():
    ap = argparse.ArgumentParser(description='Clone v1 talking test using the full approved real behavior video')
    ap.add_argument('--root', required=True)
    ap.add_argument('--mydrive', required=True)
    ap.add_argument('--seconds', type=float, default=0.0, help='0 = keep the full source duration')
    ap.add_argument('--text', default='Сьогодні новий день. Я не поспішаю і не намагаюся комусь щось довести. Просто рухаюсь уперед, крок за кроком. Бо головне для мене — не зупинятися.')
    ap.add_argument('--output-dir', default=None)
    args = ap.parse_args()

    root = Path(args.root)
    worker = root / 'worker'
    content = root / 'content'
    python_bin = Path('/content/ai-twin-py311/bin/python')
    profile = content / 'clone_reference_profile.json'

    out = Path(args.output_dir) if args.output_dir else Path(args.mydrive) / 'clone_v1_tests'
    out.mkdir(parents=True, exist_ok=True)

    asset_map = out / 'clone_assets_test.json'
    run([python_bin, worker / 'locate_clone_assets.py', '--mydrive', args.mydrive, '--profile', profile, '--output', asset_map])
    assets = json.loads(asset_map.read_text(encoding='utf-8'))

    behavior = assets.get('primary_behavior')
    if not behavior:
        videos = assets.get('behavior_videos', [])
        behavior = videos[0]['path'] if videos else None
    if not behavior or not Path(behavior).is_file():
        raise SystemExit('No behavior video available for Clone v1 test')

    behavior = Path(behavior)
    source_duration = probe_duration(behavior)
    seconds = source_duration if args.seconds <= 0 else min(args.seconds, source_duration)
    print(f'🎞️ Source duration: {source_duration:.2f}s | test duration: {seconds:.2f}s')

    master_voice = assets['master_voice']
    photos = assets['master_photos']
    if not photos:
        raise SystemExit('No master photo available')

    # Preserve the entire approved behavior performance. Only normalize codec/FPS here.
    ref_video = out / 'clone_v1_behavior_reference_full.mp4'
    run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', str(behavior),
        '-t', f'{seconds:.3f}', '-an',
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
        '-r', '25', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
        str(ref_video)
    ])

    fallback_photo = out / 'clone_v1_reference_frame.png'
    run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', '1.0', '-i', str(ref_video), '-frames:v', '1', str(fallback_photo)])

    episode = out / 'test_episode.json'
    manifest = out / 'test_scene_prompts.json'
    episode.write_text(json.dumps({'voiceover': args.text}, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest.write_text(json.dumps({
        'scenes': [{
            'n': 1,
            'seconds': seconds,
            'prompt': 'Single talking portrait test. Preserve real body motion and gaze. No bed, no bedroom, no reclining pose.',
            'dialogue': []
        }]
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    speech_dir = out / 'speech'
    run([
        python_bin, worker / 'generate_scene_speech.py',
        '--episode', episode,
        '--manifest', manifest,
        '--master-voice', master_voice,
        '--output-dir', speech_dir,
    ])

    audio = speech_dir / 'scene_01_clone.wav'
    if not audio.is_file():
        raise RuntimeError('Talking audio was not generated')

    # Make the audio track exactly as long as the reference clip. This prevents MuseTalk
    # from shortening the final video when speech ends before the real behavior clip.
    full_audio = speech_dir / 'scene_01_clone_full_duration.wav'
    run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', str(audio),
        '-af', f'apad=pad_dur={seconds:.3f}', '-t', f'{seconds:.3f}',
        '-ar', '24000', '-ac', '1', str(full_audio)
    ])

    raw = out / 'CLONE_V1_TALKING_RAW.mp4'
    run([
        python_bin, worker / 'lipsync_musetalk.py',
        '--photo', fallback_photo,
        '--reference-video', ref_video,
        '--audio', full_audio,
        '--output', raw,
    ])

    if not raw.is_file() or raw.stat().st_size < 50_000:
        raise RuntimeError('Clone v1 talking render did not produce a valid MP4')

    # The approved behavior clip contains old baked-in caption/watermark areas.
    # A controlled 9:16 crop removes them while keeping the person and full duration.
    final = out / 'CLONE_V1_TALKING_TEST_FULL.mp4'
    run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', str(raw),
        '-vf', 'crop=910:1618:170:210,scale=1080:1920:flags=lanczos,format=yuv420p',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(final)
    ])

    final_duration = probe_duration(final)
    if not final.is_file() or final.stat().st_size < 50_000:
        raise RuntimeError('Clone v1 final test did not produce a valid MP4')
    if final_duration < seconds - 0.35:
        raise RuntimeError(f'Final duration shortened unexpectedly: {final_duration:.2f}s vs {seconds:.2f}s')

    print('\n✅ CLONE V1 FULL-LENGTH TALKING TEST READY')
    print('REFERENCE:', behavior)
    print('TEXT:', args.text)
    print(f'DURATION={final_duration:.2f}')
    print('FINAL_PATH=' + str(final))


if __name__ == '__main__':
    main()
