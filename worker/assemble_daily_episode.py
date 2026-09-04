import argparse
import json
import subprocess
from pathlib import Path


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-4000:])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--episode-dir', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--voice', default=None, help='Deprecated: scene clips now carry their own synchronized audio')
    args = p.parse_args()

    root = Path(args.episode_dir)
    manifest = json.loads((root / 'scene_prompts.json').read_text(encoding='utf-8'))
    clips = []
    normalized = root / '_normalized'
    normalized.mkdir(exist_ok=True)

    # One restrained grade across all scenes to make separately generated shots feel like one film.
    # Keep it subtle: slight contrast, saturation restraint, mild sharpening and very fine grain.
    vf = (
        'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,'
        'eq=contrast=1.035:saturation=0.96:brightness=-0.005:gamma=0.995,'
        'unsharp=5:5:0.25:5:5:0.0,'
        'noise=alls=1.2:allf=t+u,'
        'format=yuv420p'
    )
    af = 'aresample=48000:async=1:first_pts=0,loudnorm=I=-16:TP=-1.5:LRA=9'

    for scene in manifest['scenes']:
        src = root / scene['expected_clip']
        if not src.is_file():
            raise FileNotFoundError(f'Missing scene clip: {src}')
        dst = normalized / scene['expected_clip']
        run([
            'ffmpeg','-y','-i',str(src),'-vf',vf,
            '-af',af,
            '-c:v','libx264','-preset','veryfast','-crf','19','-pix_fmt','yuv420p',
            '-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart',str(dst)
        ])
        clips.append(dst)

    concat_file = normalized / 'concat.txt'
    concat_file.write_text(''.join(f"file '{c.as_posix()}'\n" for c in clips), encoding='utf-8')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        'ffmpeg','-y','-f','concat','-safe','0','-i',str(concat_file),
        '-c:v','copy','-c:a','copy','-movflags','+faststart',str(output)
    ])
    print(f'✅ Final cinematic-grade synchronized episode: {output}')


if __name__ == '__main__':
    main()
