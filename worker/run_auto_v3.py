import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(cmd, env=None):
    cmd = [str(x) for x in cmd]
    print('▶', ' '.join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    lines = []
    for line in proc.stdout:
        print(line, end='')
        lines.append(line)
    code = proc.wait()
    if code != 0:
        tail = ''.join(lines[-120:])
        raise RuntimeError(
            f"Command failed with exit code {code}: {' '.join(cmd)}\n"
            f"----- child process log tail -----\n{tail}"
        )


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


def valid_file(path: Path, min_size=1):
    return path.is_file() and path.stat().st_size >= min_size


def load_state(path: Path):
    if not path.is_file():
        return {'stages': {}, 'updated_at': None}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'stages': {}, 'updated_at': None}


def mark(state_path: Path, stage: str, value=True, extra=None):
    state = load_state(state_path)
    state.setdefault('stages', {})[stage] = value
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    if extra is not None:
        state.setdefault('details', {})[stage] = extra
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='Zaskaleta AI Twin AUTO — persistent resumable one-click production')
    ap.add_argument('--root', required=True)
    ap.add_argument('--mydrive', default='/content/drive/MyDrive')
    ap.add_argument('--day', type=int, default=None)
    ap.add_argument('--sync-folder-id', default=None, help='Fixed Google Drive source folder ID used to persist checkpoints')
    args = ap.parse_args()

    root = Path(args.root)
    worker = root / 'worker'
    content = root / 'content'
    plan = content / 'monthly_plan_30_days.json'
    dialogues = content / 'dialogue_overrides.json'
    outfits = content / 'outfit_profiles.json'
    clone_profile = content / 'clone_reference_profile.json'
    python_bin = Path('/content/ai-twin-py311/bin/python')

    def sync_progress():
        if not args.sync_folder_id:
            return
        try:
            run([
                '/usr/bin/python3', worker / 'fixed_drive_folder_sync.py', 'push',
                '--folder-id', args.sync_folder_id,
                '--local-dir', args.mydrive,
            ])
        except Exception as e:
            print('⚠️ Progress sync failed; local runtime continues:', e)

    asset_map = Path('/content/clone_assets_v3.json')
    run([python_bin, worker / 'locate_clone_assets.py', '--mydrive', args.mydrive, '--profile', clone_profile, '--output', asset_map])
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
    state_path = daydir / 'checkpoint.json'
    final = daydir / f'Day_{day:02d}_FINAL_9x16.mp4'

    print(f'\n🎬 AUTO DAY: {day:02d}')
    print('💾 Resume state:', state_path)
    if valid_file(final, 100_000):
        mark(state_path, 'final', True, str(final))
        sync_progress()
        print('✅ Final already exists:', final)
        print('FINAL_PATH=' + str(final))
        return

    episode_json = daydir / 'episode.json'
    scene_prompts = daydir / 'scene_prompts.json'
    if valid_file(episode_json, 100) and valid_file(scene_prompts, 100):
        print('↪ Episode preparation: existing, resume')
        mark(state_path, 'episode_prepared')
    else:
        run([python_bin, worker / 'prepare_daily_episode.py', '--plan', plan, '--day', day, '--output-dir', daydir, '--dialogues', dialogues, '--outfits', outfits, '--clone-profile', clone_profile])
        mark(state_path, 'episode_prepared')
    sync_progress()

    episode = json.loads(episode_json.read_text(encoding='utf-8'))
    print('🎞️', episode.get('title'))
    print('📍', episode.get('city'), '—', episode.get('location'))
    print('👕', episode.get('outfit'))
    print('🗣️ Dialogue:', 'YES' if episode.get('dialogue') else 'NO')
    print('🎭 REALISM LOCK: ON')

    dialogue_dir = daydir / 'dialogue_audio'
    dm = dialogue_dir / 'dialogue_audio_manifest.json'
    if episode.get('dialogue') and not valid_file(dm, 100):
        run([python_bin, worker / 'generate_dialogue_audio.py', '--episode', episode_json, '--master-voice', voice, '--worker-dir', worker, '--python-bin', python_bin, '--output-dir', dialogue_dir])
    else:
        dialogue_dir.mkdir(parents=True, exist_ok=True)
        print('↪ Dialogue audio stage: skip/resume')
    mark(state_path, 'dialogue_audio')

    speech_dir = daydir / 'scene_speech'
    speech_manifest = speech_dir / 'scene_speech_manifest.json'
    if not valid_file(speech_manifest, 100):
        run([python_bin, worker / 'generate_scene_speech.py', '--episode', episode_json, '--manifest', scene_prompts, '--master-voice', voice, '--output-dir', speech_dir])
    else:
        print('↪ Main speech stage: resume existing')
    mark(state_path, 'scene_speech')
    sync_progress()

    keyframes = daydir / 'keyframes'
    keyframes.mkdir(parents=True, exist_ok=True)
    for scene_no in range(1, 9):
        kf = keyframes / f'scene_{scene_no:02d}.png'
        if valid_file(kf, 10_000):
            print(f'↪ Scene {scene_no:02d}: existing keyframe, resume')
            continue
        print(f'🎨 Rendering Scene {scene_no:02d} in isolated process')
        run([python_bin, worker / 'generate_scene_keyframes.py', '--manifest', scene_prompts, '--photos', *photos, '--output-dir', keyframes, '--seed', '9969', '--scene', str(scene_no)])
        mark(state_path, f'keyframe_{scene_no:02d}')
    if count_files(keyframes, 'scene_*.png') < 8:
        raise RuntimeError('Not all 8 keyframes are ready')
    mark(state_path, 'keyframes_all')
    print('✅ Keyframes: 8/8 ready')
    sync_progress()

    animated = daydir / 'animated'
    animated.mkdir(parents=True, exist_ok=True)
    if count_files(animated, 'scene_*_silent.mp4') < 8:
        run([python_bin, worker / 'animate_scene_keyframes.py', '--manifest', scene_prompts, '--image-dir', keyframes, '--output-dir', animated])
    else:
        print('↪ Animation: 8 existing, resume')
    if count_files(animated, 'scene_*_silent.mp4') < 8:
        raise RuntimeError('Not all 8 animated scenes are ready')
    mark(state_path, 'animation_all')
    sync_progress()

    for scene_no in range(1, 9):
        scene_target = daydir / f'scene_{scene_no:02d}.mp4'
        if valid_file(scene_target, 50_000):
            print(f'↪ Scene {scene_no:02d}: existing final clip, resume')
            mark(state_path, f'final_scene_{scene_no:02d}')
            continue
        print(f'🎭 Rendering Scene {scene_no:02d} final clip in isolated process')
        run([python_bin, worker / 'render_scene_clips.py', '--manifest', scene_prompts, '--keyframes', keyframes, '--scene-speech', speech_manifest, '--dialogue-audio-dir', dialogue_dir, '--animated-dir', animated, '--worker-dir', worker, '--python-bin', python_bin, '--output-dir', daydir, '--scene', str(scene_no)])
        mark(state_path, f'final_scene_{scene_no:02d}')
        sync_progress()
    if count_files(daydir, 'scene_??.mp4') < 8:
        raise RuntimeError('Not all 8 final scene clips are ready')
    mark(state_path, 'scene_render_all')
    print('✅ Scene render: 8/8 ready')
    sync_progress()

    if not valid_file(final, 100_000):
        run([python_bin, worker / 'assemble_daily_episode.py', '--episode-dir', daydir, '--output', final])
    else:
        print('↪ Final assembly: existing, resume')

    if not valid_file(final, 100_000):
        raise RuntimeError('Final render did not appear')
    mark(state_path, 'final', True, str(final))
    sync_progress()
    print('\n✅ READY:', final)
    print('FINAL_PATH=' + str(final))


if __name__ == '__main__':
    main()
