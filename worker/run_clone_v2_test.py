import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def run(cmd):
    cmd = [str(x) for x in cmd]
    print('▶', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def probe_duration(path: Path) -> float:
    out = subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)
    ], text=True).strip()
    return float(out)


def clamp_tempo(audio_duration: float, target_duration: float, max_correction: float) -> float:
    if audio_duration <= 0 or target_duration <= 0:
        return 1.0
    raw = audio_duration / target_duration
    lo = 1.0 - max_correction
    hi = 1.0 + max_correction
    return max(lo, min(hi, raw))


def resolve_python() -> Path:
    configured = os.environ.get('AI_TWIN_PYTHON', '').strip()
    return Path(configured) if configured else Path(sys.executable)


def main():
    ap = argparse.ArgumentParser(description='Clone v2 talking realism test with gated duration and aligned lipsync audio')
    ap.add_argument('--root', required=True)
    ap.add_argument('--mydrive', required=True, help='Storage root. Can be Google Drive mount, RunPod volume, or synchronized workspace.')
    ap.add_argument('--seconds', type=float, default=12.0)
    ap.add_argument('--voice-preset', default='conversational', choices=['calm', 'confident', 'serious', 'warm', 'motivational', 'conversational'])
    ap.add_argument('--text', default='Я говорю спокійно і природно. Кожна пауза має виглядати так, ніби слова справді вимовляю я. Без поспіху, без зайвих рухів, просто жива розмова.')
    ap.add_argument('--output-dir', default=None)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    worker = root / 'worker'
    content = root / 'content'
    python_bin = resolve_python()
    profile_path = content / 'clone_reference_profile.json'
    profile_doc = json.loads(profile_path.read_text(encoding='utf-8'))
    talking_profile = json.loads((content / 'talking_profile_v2.json').read_text(encoding='utf-8'))

    out = Path(args.output_dir).resolve() if args.output_dir else Path(args.mydrive).resolve() / 'clone_v2_tests'
    out.mkdir(parents=True, exist_ok=True)

    print(f'🐍 Python: {python_bin}')
    print(f'📦 Root: {root}')
    print(f'💾 Storage: {Path(args.mydrive).resolve()}')

    asset_map = out / 'clone_assets_v2.json'
    run([python_bin, worker / 'locate_clone_assets.py', '--mydrive', args.mydrive, '--profile', profile_path, '--output', asset_map])
    assets = json.loads(asset_map.read_text(encoding='utf-8'))

    behavior = assets.get('primary_behavior')
    if not behavior:
        videos = assets.get('master_behavior_videos', [])
        behavior = videos[0] if videos else None
    if not behavior or not Path(behavior).is_file():
        raise SystemExit('No approved primary behavior video available for Clone v2 test')

    behavior = Path(behavior)
    source_duration = probe_duration(behavior)
    gate_min, gate_max = talking_profile['test_gate']['range_seconds']
    seconds = args.seconds
    if seconds <= 0:
        seconds = min(source_duration, talking_profile['test_gate']['duration_seconds'])
    seconds = max(gate_min, min(seconds, gate_max, source_duration))
    print(f'🎞️ Clone v2 gate: source={source_duration:.2f}s test={seconds:.2f}s preset={args.voice_preset}')

    master_voice = Path(assets['master_voice'])
    if not master_voice.is_file():
        raise SystemExit('Configured master voice is missing')

    photos = [Path(p) for p in assets.get('master_photos', []) if Path(p).is_file()]
    if not photos:
        raise SystemExit('No master photo available')

    expected_canonical = profile_doc.get('identity', {}).get('canonical_photo') or profile_doc.get('canonical_identity_photo')
    located_canonical = assets.get('profile', {}).get('canonical_identity_photo')
    canonical_name = expected_canonical or located_canonical
    if not canonical_name:
        raise SystemExit('Canonical identity photo is not explicitly configured; refusing fallback identity')
    if expected_canonical and located_canonical and expected_canonical != located_canonical:
        raise SystemExit(f'Canonical identity mismatch: profile={expected_canonical!r} assets={located_canonical!r}')

    canonical_matches = [p for p in photos if p.name == canonical_name]
    if len(canonical_matches) != 1:
        raise SystemExit(f'Canonical identity must resolve to exactly one approved photo: {canonical_name!r}; matches={len(canonical_matches)}')
    canonical = canonical_matches[0]

    canonical_sha = sha256_file(canonical)
    voice_sha = sha256_file(master_voice)
    behavior_sha = sha256_file(behavior)
    identity_preflight = {
        'schema': 'zaskaleta-clone-v2-identity-preflight-v1',
        'canonical_identity': canonical.name,
        'canonical_identity_sha256': canonical_sha,
        'master_voice': master_voice.name,
        'master_voice_sha256': voice_sha,
        'reference_behavior': behavior.name,
        'reference_behavior_sha256': behavior_sha,
        'fallback_identity_allowed': False,
        'status': 'PASS',
    }
    (out / 'CLONE_V2_IDENTITY_PREFLIGHT.json').write_text(json.dumps(identity_preflight, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'🔒 Canonical identity locked: {canonical.name} sha256={canonical_sha[:12]}…')

    ref_video = out / 'clone_v2_behavior_gate.mp4'
    run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', behavior,
        '-t', f'{seconds:.3f}', '-an',
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
        '-r', '25', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p',
        ref_video
    ])

    fallback_photo = out / 'clone_v2_identity_fallback.png'
    run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', canonical,
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2',
        '-frames:v', '1', fallback_photo
    ])

    episode = out / 'test_episode_v2.json'
    manifest = out / 'test_manifest_v2.json'
    episode.write_text(json.dumps({'voiceover': args.text}, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest.write_text(json.dumps({'scenes': [{'n': 1, 'seconds': seconds, 'prompt': 'MASTER CLONE talking test only. Preserve approved real motion and identity. No scene generation.', 'dialogue': []}]}, ensure_ascii=False, indent=2), encoding='utf-8')

    speech_dir = out / 'speech'
    run([python_bin, worker / 'generate_scene_speech.py', '--episode', episode, '--manifest', manifest, '--master-voice', master_voice, '--output-dir', speech_dir, '--voice-preset', args.voice_preset])

    audio = speech_dir / 'scene_01_clone.wav'
    if not audio.is_file():
        raise RuntimeError('Clone v2 speech was not generated')

    audio_duration = probe_duration(audio)
    target_speech_window = max(0.5, seconds - 0.35)
    max_correction = float(talking_profile['audio_alignment']['max_tempo_correction'])
    tempo = clamp_tempo(audio_duration, target_speech_window, max_correction)
    print(f'🎙️ speech={audio_duration:.2f}s target={target_speech_window:.2f}s tempo_correction={tempo:.4f}')

    lipsync_audio = speech_dir / 'scene_01_clone_lipsync_16k.wav'
    run(['ffmpeg', '-y', '-loglevel', 'error', '-i', audio, '-af', f'atempo={tempo:.6f},apad=pad_dur={seconds:.3f}', '-t', f'{seconds:.3f}', '-ar', '16000', '-ac', '1', lipsync_audio])

    raw = out / 'CLONE_V2_TALKING_RAW.mp4'
    run([python_bin, worker / 'lipsync_musetalk.py', '--photo', fallback_photo, '--reference-video', ref_video, '--audio', lipsync_audio, '--output', raw])

    if not raw.is_file() or raw.stat().st_size < 50_000:
        raise RuntimeError('Clone v2 MuseTalk render did not produce a valid MP4')

    final = out / 'CLONE_V2_TALKING_TEST.mp4'
    run(['ffmpeg', '-y', '-loglevel', 'error', '-i', raw, '-vf', 'crop=910:1618:170:210,scale=1080:1920:flags=lanczos,format=yuv420p', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', final])

    final_duration = probe_duration(final)
    if final_duration < seconds - 0.35:
        raise RuntimeError(f'Clone v2 duration gate failed: {final_duration:.2f}s vs requested {seconds:.2f}s')

    evaluation = {
        'clone_version': 'v2',
        'talking_profile': talking_profile['name'],
        'reference_behavior': behavior.name,
        'reference_behavior_sha256': behavior_sha,
        'canonical_identity': canonical.name,
        'canonical_identity_sha256': canonical_sha,
        'master_voice_sha256': voice_sha,
        'voice_preset': args.voice_preset,
        'requested_duration': seconds,
        'final_duration': final_duration,
        'speech_duration_before_alignment': audio_duration,
        'tempo_correction': tempo,
        'runtime': {'python': str(python_bin), 'root': str(root), 'storage': str(Path(args.mydrive).resolve())},
        'must_pass': talking_profile['test_gate']['must_pass'],
        'manual_review': {item: None for item in talking_profile['test_gate']['must_pass']},
        'approved_for_next_gate': False,
        'notes': 'Set every manual_review item to true before promoting this output to APPROVED or moving to the 15-30s gate.'
    }
    eval_path = out / 'CLONE_V2_EVALUATION.json'
    eval_path.write_text(json.dumps(evaluation, ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n✅ CLONE V2 TALKING TEST READY')
    print('FINAL_PATH=' + str(final))
    print('EVALUATION_PATH=' + str(eval_path))
    print(f'DURATION={final_duration:.2f}')
    print('⚠️ Do not add this generation to APPROVED until every manual gate is reviewed.')


if __name__ == '__main__':
    main()
