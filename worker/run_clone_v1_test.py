import argparse
import json
import subprocess
from pathlib import Path


def run(cmd):
    cmd = [str(x) for x in cmd]
    print('▶', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(description='Short Clone v1 talking test using the approved real behavior video')
    ap.add_argument('--root', required=True)
    ap.add_argument('--mydrive', required=True)
    ap.add_argument('--seconds', type=float, default=7.0)
    ap.add_argument('--text', default='Сьогодні новий день. Без зайвих слів. Просто рухаюсь далі.')
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

    master_voice = assets['master_voice']
    photos = assets['master_photos']
    if not photos:
        raise SystemExit('No master photo available')

    # Keep the test deliberately simple: one real reference, one camera, one short line.
    ref_video = out / 'clone_v1_behavior_reference_7s.mp4'
    run([
        'ffmpeg', '-y', '-loglevel', 'error', '-ss', '0.4', '-i', behavior,
        '-t', str(args.seconds), '-an', '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
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
            'seconds': args.seconds,
            'prompt': 'Single talking portrait test. Eye-level camera. Standing naturally. No bed, no bedroom, no reclining pose.',
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

    final = out / 'CLONE_V1_TALKING_TEST_7s.mp4'
    run([
        python_bin, worker / 'lipsync_musetalk.py',
        '--photo', fallback_photo,
        '--reference-video', ref_video,
        '--audio', audio,
        '--output', final,
    ])

    if not final.is_file() or final.stat().st_size < 50_000:
        raise RuntimeError('Clone v1 talking test did not produce a valid MP4')

    print('\n✅ CLONE V1 TALKING TEST READY')
    print('REFERENCE:', behavior)
    print('TEXT:', args.text)
    print('FINAL_PATH=' + str(final))


if __name__ == '__main__':
    main()
