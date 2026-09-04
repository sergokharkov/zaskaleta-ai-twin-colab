import argparse
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path


def run(cmd, check=True):
    r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(((r.stderr or '') + '\n' + (r.stdout or ''))[-6000:])
    return r


def probe(path: Path):
    r = run([
        'ffprobe','-v','error','-show_entries','format=duration:stream=index,codec_type,width,height',
        '-of','json',str(path)
    ])
    return json.loads(r.stdout or '{}')


def duration(path: Path):
    try:
        return float(probe(path).get('format', {}).get('duration', 0) or 0)
    except Exception:
        return 0.0


def quality_gate(path: Path, expected_seconds: float):
    if not path.is_file() or path.stat().st_size < 50_000:
        raise RuntimeError(f'QUALITY GATE: missing or tiny clip: {path}')
    data = probe(path)
    streams = data.get('streams', [])
    video = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    dur = float(data.get('format', {}).get('duration', 0) or 0)
    if not video:
        raise RuntimeError(f'QUALITY GATE: no video stream: {path.name}')
    if not audio:
        raise RuntimeError(f'QUALITY GATE: no audio stream: {path.name}')
    if int(video.get('width', 0) or 0) < 480 or int(video.get('height', 0) or 0) < 800:
        raise RuntimeError(f'QUALITY GATE: resolution too low: {path.name}')
    if dur < max(1.0, min(expected_seconds * 0.55, expected_seconds - 0.5)):
        raise RuntimeError(f'QUALITY GATE: clip too short: {path.name} ({dur:.2f}s)')
    b = run([
        'ffmpeg','-hide_banner','-i',str(path),'-vf','blackdetect=d=1.2:pix_th=0.015',
        '-an','-f','null','-'
    ], check=False)
    log = (b.stderr or '') + (b.stdout or '')
    if 'black_start:' in log:
        print(f'⚠️ QUALITY GATE warning: extended dark/black section detected in {path.name}')
    print(f'✅ QUALITY GATE: {path.name} | {dur:.2f}s | audio+video OK')


def ass_time(seconds: float):
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f'{h}:{m:02d}:{s:05.2f}'


def clean_text(text: str):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text.replace('{', '(').replace('}', ')')


def split_for_subtitles(text: str, max_chars=60):
    text = clean_text(text)
    if not text:
        return []
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    chunks = []
    current = ''
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) <= max_chars:
            current += ' ' + sentence
        elif not current and len(sentence) <= max_chars:
            current = sentence
        else:
            if current:
                chunks.append(current)
                current = ''
            words = sentence.split()
            buf = ''
            for word in words:
                if buf and len(buf) + 1 + len(word) > max_chars:
                    chunks.append(buf)
                    buf = word
                else:
                    buf = (buf + ' ' + word).strip()
            current = buf
    if current:
        chunks.append(current)
    return chunks


def wrap_ass(text: str, line_chars=30):
    lines = textwrap.wrap(clean_text(text), width=line_chars, break_long_words=False, break_on_hyphens=False)
    if len(lines) <= 2:
        return r'\N'.join(lines)
    words = clean_text(text).split()
    total = sum(len(w) for w in words) + max(0, len(words) - 1)
    target = total / 2
    left, right = [], []
    count = 0
    for w in words:
        if count < target or not left:
            left.append(w)
            count += len(w) + 1
        else:
            right.append(w)
    return ' '.join(left) + (r'\N' + ' '.join(right) if right else '')


def load_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def subtitle_text_for_scene(root: Path, scene: dict, speech_by_scene: dict):
    lines = scene.get('dialogue', []) or []
    if lines:
        return ' '.join(clean_text(x.get('text', '')) for x in lines if clean_text(x.get('text', '')))
    return clean_text(speech_by_scene.get(int(scene['n']), {}).get('text', ''))


def make_ass(root: Path, manifest: dict, clip_durations: dict, style: dict):
    speech_rows = load_json(root / 'scene_speech' / 'scene_speech_manifest.json', [])
    speech = {int(x.get('scene', 0)): x for x in speech_rows if x.get('scene') is not None}
    font = style.get('font', 'DejaVu Sans')
    size = int(style.get('font_size', 64))
    ml = int(style.get('margin_left', 110))
    mr = int(style.get('margin_right', 110))
    mv = int(style.get('margin_vertical', 235))
    outline = int(style.get('outline', 4))
    shadow = int(style.get('shadow', 1))
    bold = -1 if style.get('bold', True) else 0
    header = f'''[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Zaskaleta,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,{bold},0,0,0,100,100,0,0,1,{outline},{shadow},2,{ml},{mr},{mv},1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n'''
    events = []
    cursor = 0.0
    for scene in manifest['scenes']:
        n = int(scene['n'])
        dur = clip_durations.get(n, float(scene.get('seconds', 8)))
        text = subtitle_text_for_scene(root, scene, speech)
        chunks = split_for_subtitles(text, max_chars=60)
        if chunks:
            usable_start = cursor + 0.18
            usable_end = max(usable_start + 0.5, cursor + dur - 0.18)
            span = max(0.5, usable_end - usable_start)
            per = span / len(chunks)
            for i, chunk in enumerate(chunks):
                start = usable_start + i * per
                end = usable_start + (i + 1) * per
                display = wrap_ass(chunk, int(style.get('max_chars_per_line', 30)))
                events.append(f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Zaskaleta,,0,0,0,,{{\\fad(90,90)}}{display}')
        cursor += dur
    ass = root / '_normalized' / 'subtitles.ass'
    ass.write_text(header + '\n'.join(events) + '\n', encoding='utf-8')
    return ass, len(events)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--episode-dir', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--voice', default=None, help='Deprecated: scene clips now carry their own synchronized audio')
    args = p.parse_args()
    root = Path(args.episode_dir)
    manifest = json.loads((root / 'scene_prompts.json').read_text(encoding='utf-8'))
    repo_root = Path(__file__).resolve().parents[1]
    style = load_json(repo_root / 'content' / 'subtitle_style.json', {})
    clips = []
    clip_durations = {}
    normalized = root / '_normalized'
    normalized.mkdir(exist_ok=True)
    vf = (
        'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,'
        'eq=contrast=1.035:saturation=0.96:brightness=-0.005:gamma=0.995,'
        'unsharp=5:5:0.25:5:5:0.0,'
        'noise=alls=1.2:allf=t+u,'
        'format=yuv420p'
    )
    for scene in manifest['scenes']:
        n = int(scene['n'])
        src = root / scene['expected_clip']
        quality_gate(src, float(scene.get('seconds', 8)))
        src_dur = max(0.5, duration(src))
        clip_durations[n] = src_dur
        fade_out_start = max(0.0, src_dur - 0.10)
        af = (
            'aresample=48000:async=1:first_pts=0,'
            'loudnorm=I=-16:TP=-1.5:LRA=9,'
            f'afade=t=in:st=0:d=0.05,afade=t=out:st={fade_out_start:.3f}:d=0.10'
        )
        dst = normalized / scene['expected_clip']
        run([
            'ffmpeg','-y','-i',str(src),'-vf',vf,'-af',af,
            '-c:v','libx264','-preset','veryfast','-crf','19','-pix_fmt','yuv420p',
            '-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart',str(dst)
        ])
        clips.append(dst)
    concat_file = normalized / 'concat.txt'
    concat_file.write_text(''.join(f"file '{c.as_posix()}'\n" for c in clips), encoding='utf-8')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = normalized / 'episode_nosubs.mp4'
    run([
        'ffmpeg','-y','-f','concat','-safe','0','-i',str(concat_file),
        '-c:v','copy','-c:a','copy','-movflags','+faststart',str(temp)
    ])
    ass, event_count = make_ass(root, manifest, clip_durations, style)
    if event_count:
        safe_ass = Path('/content/zaskaleta_permanent_subtitles.ass')
        shutil.copy2(ass, safe_ass)
        run([
            'ffmpeg','-y','-i',str(temp),'-vf',f'ass={safe_ass}',
            '-c:v','libx264','-preset','veryfast','-crf','19','-pix_fmt','yuv420p',
            '-c:a','copy','-movflags','+faststart',str(output)
        ])
        print(f'✅ Permanent subtitle style burned in: {event_count} subtitle events')
    else:
        shutil.copy2(temp, output)
        print('↪ No spoken text — subtitle burn skipped')
    quality_gate(output, sum(float(s.get('seconds', 8)) for s in manifest['scenes']))
    print(f'✅ Final professional episode passed quality gate: {output}')


if __name__ == '__main__':
    main()
