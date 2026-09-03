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
    p.add_argument('--voice', required=True, help='Final narration audio WAV/MP3')
    p.add_argument('--output', required=True)
    args = p.parse_args()

    root = Path(args.episode_dir)
    manifest = json.loads((root / 'scene_prompts.json').read_text(encoding='utf-8'))
    clips = []
    normalized = root / '_normalized'
    normalized.mkdir(exist_ok=True)

    for scene in manifest['scenes']:
        src = root / scene['expected_clip']
        if not src.is_file():
            raise FileNotFoundError(f'Missing scene clip: {src}')
        dst = normalized / scene['expected_clip']
        vf = (
            'scale=1080:1920:force_original_aspect_ratio=increase,'
            'crop=1080:1920,setsar=1,fps=30'
        )
        run([
            'ffmpeg','-y','-i',str(src),'-t',str(scene['seconds']),
            '-vf',vf,'-an','-c:v','libx264','-preset','medium','-crf','20',str(dst)
        ])
        clips.append(dst)

    concat_file = normalized / 'concat.txt'
    concat_file.write_text(''.join(f"file '{c.as_posix()}'\n" for c in clips), encoding='utf-8')
    silent = normalized / 'silent.mp4'
    run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(concat_file),'-c','copy',str(silent)])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        'ffmpeg','-y','-i',str(silent),'-i',str(args.voice),
        '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','192k',
        '-shortest','-movflags','+faststart',str(output)
    ])
    print(f'✅ Final episode: {output}')


if __name__ == '__main__':
    main()
