import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd, env=None):
    print('▶', ' '.join(map(str, cmd)))
    subprocess.run([str(x) for x in cmd], check=True, env=env)


def count_files(folder: Path, pattern: str):
    return len(list(folder.glob(pattern))) if folder.is_dir() else 0


def choose_day(base: Path, requested=None):
    if requested:
        return int(requested)
    episodes = base / 'episodes'
    for day in range(1, 31):
        final = episodes / f'Day_{day:02d}' / f'Day_{day:02d}_FINAL_9x16.mp4'
        if not final.is_file():
            return day
    return 30


def main():
    ap = argparse.ArgumentParser(description='Zaskaleta AI Twin AUTO v3 — resumable one-click production')
    ap.add_argument('--root', required=True)
    ap.add_argument('--mydrive', default='/content/drive/MyDrive')
    ap.add_argument('--day', type=int, default=None)
    args = ap.parse_args()

    root = Path(args.root)
    worker = root / 'worker'
    content = root / 'content'
    plan = content / 'monthly_plan_30_days.json'
    dialogues = content / 'dialogue_overrides.json'
    outfits = content / 'outfit_profiles.json'
    clone_profile = content / 'clone_reference_profile.json'
    python_bin = Path('/content/ai-twin-py311/bin/python')

    asset_map = Path('/content/clone_assets_v3.json')
    run([
        python_bin, worker / 'locate_clone_assets.py',
        '--mydrive', args.mydrive,
        '--profile', clone_profile,
        '--output', asset_map,
    ])
    assets = json.loads(asset_map.read_text(encoding='utf-8'))
    base = Path(assets['base_dir'])
    photos = assets['master_photos']
    voice = assets['master_voice']
    behavior = [x['path'] for x in assets.get('behavior_videos', [])]

    print('\n✅ CLONE SET')
    print('📁', base)
    print('🖼️ Photos:', len(photos))
    print('🎙️ Voice:', Path(voice).name)
    print('🎥 Behavior videos:', len(behavior))

    if len(photos) < 5:
        raise RuntimeError('Need at least 5 master photos')
    if not Path(voice).is_file():
        raise RuntimeError('Master voice was not found')

    day = choose_day(base, args.day)
    daydir = base / 'episodes' / f'Day_{day:02d}'
    daydir.mkdir(parents=True, exist_ok=True)
    final = daydir / f'Day_{day:02d}_FINAL_9x16.mp4'

    print(f'\n🎬 AUTO DAY: {day:02d}')
    if final.is_file():
        print('✅ Final already exists:', final)
        print('FINAL_PATH=' + str(final))
        return

    run([
        python_bin, worker / 'prepare_daily_episode.py',
        '--plan', plan, '--day', day,
        '--output-dir', daydir,
        '--dialogues', dialogues,
        '--outfits', outfits,
        '--clone-profile', clone_profile,
    ])

    episode = json.loads((daydir / 'episode.json').read_text(encoding='utf-8'))
    print('🎞️', episode.get('title'))
    print('📍', episode.get('city'), '—', episode.get('location'))
    print('👕', episode.get('outfit'))
    print('🗣️ Dialogue:', 'YES' if episode.get('dialogue') else 'NO')
    print('🎭 REALISM LOCK: ON')

    dialogue_dir = daydir / 'dialogue_audio'
    dm = dialogue_dir / 'dialogue_audio_manifest.json'
    if episode.get('dialogue') and not dm.is_file():
        run([
            python_bin, worker / 'generate_dialogue_audio.py',
            '--episode', daydir / 'episode.json',
            '--master-voice', voice,
            '--worker-dir', worker,
            '--python-bin', python_bin,
            '--output-dir', dialogue_dir,
        ])
    else:
        dialogue_dir.mkdir(parents=True, exist_ok=True)
        print('↪ Dialogue audio stage: skip/resume')

    speech_dir = daydir / 'scene_speech'
    speech_manifest = speech_dir / 'scene_speech_manifest.json'
    if not speech_manifest.is_file():
        run([
            python_bin, worker / 'generate_scene_speech.py',
            '--episode', daydir / 'episode.json',
            '--manifest', daydir / 'scene_prompts.json',
            '--master-voice', voice,
            '--output-dir', speech_dir,
        ])
    else:
        print('↪ Main speech stage: resume existing')

    keyframes = daydir / 'keyframes'
    keyframes.mkdir(parents=True, exist_ok=True)
    for scene_no in range(1, 9):
        kf = keyframes / f'scene_{scene_no:02d}.png'
        if kf.is_file() and kf.stat().st_size > 10_000:
            print(f'↪ Scene {scene_no:02d}: existing keyframe, resume')
            continue
        print(f'🎨 Rendering Scene {scene_no:02d} in isolated process')
        run([
            python_bin, worker / 'generate_scene_keyframes.py',
            '--manifest', daydir / 'scene_prompts.json',
            '--photos', *photos,
            '--output-dir', keyframes,
            '--seed', '9969',
            '--scene', str(scene_no),
        ])
    if count_files(keyframes, 'scene_*.png') < 8:
        raise RuntimeError('Not all 8 keyframes are ready')
    print('✅ Keyframes: 8/8 ready')

    animated = daydir / 'animated'
    if count_files(animated, 'scene_*_silent.mp4') < 8:
        run([
            python_bin, worker / 'animate_scene_keyframes.py',
            '--manifest', daydir / 'scene_prompts.json',
            '--image-dir', keyframes,
            '--output-dir', animated,
        ])
    else:
        print('↪ Animation: 8 existing, resume')

    # Render each final scene in its own process. This prevents MuseTalk/Whisper/UNet VRAM
    # from accumulating across scenes and makes the stage fully resumable.
    for scene_no in range(1, 9):
        scene_target = daydir / f'scene_{scene_no:02d}.mp4'
        if scene_target.is_file() and scene_target.stat().st_size > 50_000:
            print(f'↪ Scene {scene_no:02d}: existing final clip, resume')
            continue
        print(f'🎭 Rendering Scene {scene_no:02d} final clip in isolated process')
        run([
            python_bin, worker / 'render_scene_clips.py',
            '--manifest', daydir / 'scene_prompts.json',
            '--keyframes', keyframes,
            '--scene-speech', speech_manifest,
            '--dialogue-audio-dir', dialogue_dir,
            '--animated-dir', animated,
            '--worker-dir', worker,
            '--python-bin', python_bin,
            '--output-dir', daydir,
            '--scene', str(scene_no),
        ])
    if count_files(daydir, 'scene_??.mp4') < 8:
        raise RuntimeError('Not all 8 final scene clips are ready')
    print('✅ Scene render: 8/8 ready')

    run([
        python_bin, worker / 'assemble_daily_episode.py',
        '--episode-dir', daydir,
        '--output', final,
    ])

    if not final.is_file():
        raise RuntimeError('Final render did not appear')
    print('\n✅ READY:', final)
    print('FINAL_PATH=' + str(final))


if __name__ == '__main__':
    main()
