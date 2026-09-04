import argparse
import json
import shutil
import subprocess
from pathlib import Path


def run(cmd):
    r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(((r.stdout or '') + '\n' + (r.stderr or ''))[-8000:])
    if r.stdout:
        print(r.stdout[-3000:])


def probe_duration(path: Path):
    r = subprocess.run([
        'ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)
    ], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def concat_audio(files, out):
    if len(files) == 1:
        shutil.copy2(files[0], out)
        return
    inputs = []
    filters = []
    labels = []
    for i, f in enumerate(files):
        inputs += ['-i', str(f)]
        filters.append(f'[{i}:a]aresample=16000,apad=pad_dur=0.35[a{i}]')
        labels.append(f'[a{i}]')
    filters.append(''.join(labels) + f'concat=n={len(files)}:v=0:a=1[outa]')
    run(['ffmpeg','-y',*inputs,'-filter_complex',';'.join(filters),'-map','[outa]','-ar','16000','-ac','1',str(out)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--keyframes', required=True)
    ap.add_argument('--scene-speech', required=True)
    ap.add_argument('--dialogue-audio-dir', required=True)
    ap.add_argument('--animated-dir', required=True)
    ap.add_argument('--worker-dir', required=True)
    ap.add_argument('--python-bin', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--scene', type=int, default=None)
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    speech_rows = json.loads(Path(args.scene_speech).read_text(encoding='utf-8'))
    speech = {int(x['scene']): x for x in speech_rows}
    dialogue_dir = Path(args.dialogue_audio_dir)
    dialogue_manifest = []
    dm = dialogue_dir / 'dialogue_audio_manifest.json'
    if dm.is_file():
        dialogue_manifest = json.loads(dm.read_text(encoding='utf-8'))

    dialogue_by_scene = {}
    for row in dialogue_manifest:
        dialogue_by_scene.setdefault(int(row['scene']), []).append(row)

    keyframes = Path(args.keyframes)
    animated = Path(args.animated_dir)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    worker = Path(args.worker_dir)

    scenes = manifest['scenes']
    if args.scene is not None:
        scenes = [s for s in scenes if int(s['n']) == args.scene]
        if not scenes:
            raise SystemExit(f'Scene {args.scene} not found')

    for scene in scenes:
        n = int(scene['n'])
        target = out / f'scene_{n:02d}.mp4'
        if target.is_file() and target.stat().st_size > 50_000:
            print(f'↪ Scene {n:02d}: existing final clip, resume')
            continue

        image = keyframes / f'scene_{n:02d}.png'
        silent = animated / f'scene_{n:02d}_silent.mp4'
        scene_dialogue = dialogue_by_scene.get(n, [])

        chosen_audio = None
        if scene_dialogue:
            parts = [dialogue_dir / x['audio'] for x in scene_dialogue if (dialogue_dir / x['audio']).is_file()]
            if parts:
                chosen_audio = out / f'_scene_{n:02d}_dialogue.wav'
                concat_audio(parts, chosen_audio)
        elif speech.get(n, {}).get('audio'):
            p = Path(speech[n]['audio'])
            if p.is_file():
                chosen_audio = p

        if chosen_audio and (silent.is_file() or image.is_file()):
            raw = out / f'_scene_{n:02d}_talk_raw.mp4'
            cmd = [
                args.python_bin, str(worker / 'lipsync_musetalk.py'),
                '--photo', str(image), '--audio', str(chosen_audio), '--output', str(raw)
            ]
            if silent.is_file():
                cmd += ['--reference-video', str(silent)]
            run(cmd)
            duration = max(float(scene.get('seconds', 8)), probe_duration(chosen_audio) + 0.4)
            # veryfast materially reduces CPU re-encode time while keeping the same
            # 1080x1920 delivery resolution and CRF quality target.
            run([
                'ffmpeg','-y','-i',str(raw),
                '-vf',f'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,tpad=stop_mode=clone:stop_duration={duration}',
                '-af',f'apad=pad_dur={duration}','-t',str(duration),
                '-c:v','libx264','-preset','veryfast','-crf','19','-c:a','aac','-b:a','192k','-movflags','+faststart',str(target)
            ])
            raw.unlink(missing_ok=True)
            print(f'🗣️ Scene {n:02d}: lip-sync render from animated reference')
        else:
            if not silent.is_file():
                raise FileNotFoundError(silent)
            duration = float(scene.get('seconds', 8))
            run([
                'ffmpeg','-y','-i',str(silent),'-f','lavfi','-t',str(duration),'-i','anullsrc=channel_layout=stereo:sample_rate=48000',
                '-map','0:v:0','-map','1:a:0','-t',str(duration),'-c:v','copy','-c:a','aac','-b:a','128k','-shortest',str(target)
            ])
            print(f'🎬 Scene {n:02d}: cinematic cutaway')

    print('✅ Requested scene clip(s) ready')


if __name__ == '__main__':
    main()
