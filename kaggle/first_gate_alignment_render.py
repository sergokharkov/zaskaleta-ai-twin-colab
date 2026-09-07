#!/usr/bin/env python3
"""C003 audio-alignment challenger; no automatic promotion or stable overwrite."""
from __future__ import annotations
import argparse
from fractions import Fraction
import hashlib
import json
import subprocess
from pathlib import Path

CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_003'

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def run(cmd, cwd=None, timeout=3600):
    print('$', ' '.join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None, check=True, timeout=timeout)

def unique_exact(root, filename):
    matches = [p for p in root.rglob(filename) if p.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f'asset {filename!r} must resolve uniquely; matches={len(matches)}')
    return matches[0]

def probe(path):
    cp = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels,avg_frame_rate,nb_frames,start_time',
        '-of', 'json', str(path)], check=True, capture_output=True, text=True, timeout=120)
    return json.loads(cp.stdout or '{}')

def duration_seconds(meta):
    try:
        value = float(meta['format']['duration'])
        if not 0 < value < 86400:
            raise ValueError('invalid duration')
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('ffprobe did not return a valid duration') from exc

def video_stream(meta):
    streams = [s for s in meta.get('streams', []) if s.get('codec_type') == 'video']
    if len(streams) != 1:
        raise RuntimeError('Exactly one video stream is required')
    return streams[0]

def audio_stream(meta):
    streams = [s for s in meta.get('streams', []) if s.get('codec_type') == 'audio']
    if len(streams) != 1:
        raise RuntimeError('Exactly one audio stream is required')
    return streams[0]

def enforce_reference_policy(meta, target, align):
    preserve_reference_fps = int(align['preserve_reference_fps'])
    do_not_repeat_reference_motion = align['do_not_repeat_reference_motion'] is True
    if not do_not_repeat_reference_motion or align.get('do_not_loop_audio') is not True:
        raise RuntimeError('Reference/audio loop policy weakened')
    fps = Fraction(video_stream(meta)['avg_frame_rate'])
    if fps != preserve_reference_fps:
        raise RuntimeError(f'Approved reference FPS {fps} does not match required {preserve_reference_fps}; normalization requires a separate approved change')
    if duration_seconds(meta) + 0.001 < target:
        raise RuntimeError('Approved motion reference is shorter than speech; repetition is forbidden')
    return preserve_reference_fps

def validate_final(meta, audio_duration, final_sr, fps, intermediate):
    duration = duration_seconds(meta)
    video = video_stream(meta)
    audio = audio_stream(meta)
    if not 8 <= duration <= 15:
        raise RuntimeError('Final duration outside 8–15 seconds')
    if int(audio.get('sample_rate') or 0) != final_sr:
        raise RuntimeError('Final audio sample rate mismatch')
    if Fraction(video['avg_frame_rate']) != fps:
        raise RuntimeError('Final frame rate differs from approved reference')
    if video.get('codec_name') != video_stream(intermediate).get('codec_name'):
        raise RuntimeError('Video codec changed during audio-only remux')
    if duration + 0.04 < audio_duration or abs(duration - audio_duration) > 0.15:
        raise RuntimeError('Final video/audio duration mismatch or truncated speech')
    if not int(video.get('width') or 0) or not int(video.get('height') or 0):
        raise RuntimeError('Final video dimensions invalid')
    return duration

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--python', required=True)
    ap.add_argument('--output-dir', default='/kaggle/working/first_gate_alignment')
    args = ap.parse_args()
    private_root = Path(args.root).resolve()
    repo = Path(args.repo).resolve()
    py = Path(args.python)
    if not py.is_file():
        raise RuntimeError(f'Worker Python missing: {py}')
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    profile = json.loads((repo/'content/clone_reference_profile.json').read_text(encoding='utf-8'))
    package = json.loads((repo/'content/master_clone_package.json').read_text(encoding='utf-8'))
    talking = json.loads((repo/'content/talking_profile_v2.json').read_text(encoding='utf-8'))
    gate_policy = json.loads((repo/'content/clone_duration_gate_policy_v1.json').read_text(encoding='utf-8'))
    first_gate = gate_policy['ordered_gates'][0]
    if first_gate != {'id':'gate_08_15','min_seconds':8,'max_seconds':15}:
        raise RuntimeError('First duration gate policy changed')
    if gate_policy['rules'].get('manual_promotion_required') is not True:
        raise RuntimeError('Manual promotion policy weakened')
    align = talking['audio_alignment']
    lip_sr = int(align['lipsync_sample_rate'])
    final_sr = int(align['final_audio_sample_rate'])
    if lip_sr != 16000 or final_sr != 24000 or align.get('pad_end_only') is not True:
        raise RuntimeError('Audio alignment policy changed')
    canonical = unique_exact(private_root, profile['canonical_identity_photo'])
    master_voice = unique_exact(private_root, profile['master_voice_filename'])
    motion_name = package['components']['motion']['supporting_reference']
    if motion_name != talking['reference_policy']['supporting']:
        raise RuntimeError('Motion reference policy mismatch')
    motion = unique_exact(private_root, motion_name)
    motion_meta = probe(motion)
    script = out/'gate_script_uk.txt'
    script.write_text('Це третій контрольний тест. Говорю спокійно і природно. Перевіряємо точність губ, паузи, погляд і стабільність обличчя.', encoding='utf-8')
    raw_audio = out/'gate_voice_raw.wav'
    lipsync_audio = out/'gate_voice_lipsync_16k.wav'
    final_audio = out/'gate_voice_final_24k.wav'
    musetalk_render = out/'candidate003_musetalk_raw.mp4'
    intermediate_provenance = out/'candidate003_musetalk_raw.provenance.json'
    render = out/(CANDIDATE+'.mp4')
    provenance = out/(CANDIDATE+'.provenance.json')
    evidence_path = out/(CANDIDATE+'.evidence.json')
    run([str(py), str(repo/'worker/voice_mms_openvoice.py'), '--script', str(script),
         '--voice', str(master_voice), '--output', str(raw_audio), '--language', 'uk'], cwd=repo)
    raw_duration = duration_seconds(probe(raw_audio))
    if raw_duration > 15.0:
        raise RuntimeError(f'Generated speech exceeds 15s: {raw_duration:.3f}s')
    target = max(8.25, raw_duration)
    pad = max(0.0, target - raw_duration)
    fps = enforce_reference_policy(motion_meta, target, align)
    for rate, destination in ((lip_sr, lipsync_audio), (final_sr, final_audio)):
        run(['ffmpeg','-y','-i',str(raw_audio),'-af',f'apad=pad_dur={pad:.3f}',
             '-t',f'{target:.3f}','-ac','1','-ar',str(rate),str(destination)],timeout=300)
    lip_duration = duration_seconds(probe(lipsync_audio))
    final_audio_duration = duration_seconds(probe(final_audio))
    if not (8 <= lip_duration <= 15 and abs(lip_duration-final_audio_duration) <= 0.03):
        raise RuntimeError('Aligned audio durations invalid')
    run([str(py),str(repo/'worker/lipsync_musetalk.py'),'--photo',str(canonical),
         '--reference-video',str(motion),'--audio',str(lipsync_audio),
         '--output',str(musetalk_render),'--provenance-output',str(intermediate_provenance),
         '--candidate-id',CANDIDATE],cwd=repo,timeout=7200)
    intermediate_meta = probe(musetalk_render)
    if Fraction(video_stream(intermediate_meta)['avg_frame_rate']) != fps:
        raise RuntimeError('MuseTalk changed approved reference FPS')
    if duration_seconds(intermediate_meta) + 0.04 < final_audio_duration:
        raise RuntimeError('MuseTalk output is too short; refusing to truncate speech')
    # Copy the encoded video frames and replace only the audio stream.
    run(['ffmpeg','-y','-i',str(musetalk_render),'-i',str(final_audio),
         '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac',
         '-ar',str(final_sr),'-ac','1','-shortest',str(render)],timeout=600)
    render_meta = probe(render)
    render_duration = validate_final(render_meta, final_audio_duration, final_sr, fps, intermediate_meta)
    # Provenance describes the final remuxed artifact, not the intermediate MP4.
    intermediate = json.loads(intermediate_provenance.read_text(encoding='utf-8'))
    if intermediate.get('output',{}).get('sha256') != sha256_file(musetalk_render):
        raise RuntimeError('Intermediate provenance SHA mismatch')
    final_hash = sha256_file(render)
    final_provenance = {
        'schema':'zaskaleta-c003-final-provenance-v2',
        'candidate_id':CANDIDATE, 'single_component_change':'audio_alignment',
        'baseline_candidate_id':'MASTER_CLONE_GATE_08_15_CANDIDATE_002',
        'intermediate_render':intermediate,
        'intermediate_render_sha256':sha256_file(musetalk_render),
        'final_render_sha256':final_hash,
        'output':{'path':str(render),'sha256':final_hash,'size_bytes':render.stat().st_size},
        'audio_lineage':{'raw_sha256':sha256_file(raw_audio),
            'lipsync_16k_sha256':sha256_file(lipsync_audio),
            'final_24k_sha256':sha256_file(final_audio)},
        'motion_reference_sha256':sha256_file(motion),
        'reference_fps':int(fps),'render_duration_seconds':round(render_duration,3),
        'final_audio_sample_rate':final_sr,'lipsync_sample_rate':lip_sr,
        'pad_end_only':True,'do_not_repeat_reference_motion':True,
        'subjective_identity_review':'PENDING_MANUAL_REVIEW',
        'promotion_allowed':False,'auto_promote':False,'stable_release_modified':False,
    }
    provenance.write_text(json.dumps(final_provenance,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    evidence = {
        'schema':'zaskaleta-first-gate-alignment-evidence-v1',
        'candidate_id':CANDIDATE,'baseline_candidate_id':'MASTER_CLONE_GATE_08_15_CANDIDATE_002',
        'gate':'gate_08_15','gate_range_seconds':[8,15], 'technical_gate_pass':True,
        'single_component_change':'audio_alignment','motion_reference_unchanged':motion_name,
        'lipsync_sample_rate':lip_sr,'final_audio_sample_rate':final_sr,'pad_end_only':True,
        'reference_fps':int(fps),'do_not_repeat_reference_motion':True,
        'render_duration_seconds':round(render_duration,3),'render_sha256':final_hash,
        'provenance_sha256':sha256_file(provenance),
        'subjective_identity_review':'PENDING_MANUAL_REVIEW','lip_sync_review':'PENDING_MANUAL_REVIEW',
        'mouth_beard_edge_stability_review':'PENDING_MANUAL_REVIEW',
        'blink_eye_behavior_review':'PENDING_MANUAL_REVIEW','motion_naturalness_review':'PENDING_MANUAL_REVIEW',
        'identity_regression_decision':'PENDING_MANUAL_REVIEW',
        'promotion_allowed':False,'auto_promote':False,'stable_release_modified':False,
        'next_state':'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW',
    }
    evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'candidate_id':CANDIDATE,'technical_gate_pass':True,
        'render_duration_seconds':evidence['render_duration_seconds'],'promotion_allowed':False,
        'state':evidence['next_state']},ensure_ascii=False,indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
