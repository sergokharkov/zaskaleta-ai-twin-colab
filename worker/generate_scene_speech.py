import argparse
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer, VitsModel
from openvoice.api import ToneColorConverter

from voice_mms_openvoice import normalize_reference


def split_sentences(text: str):
    parts = re.split(r'(?<=[.!?…])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def distribute(sentences, slots):
    buckets = {n: '' for n in slots}
    if not sentences or not slots:
        return buckets
    for i, sentence in enumerate(sentences):
        idx = min(len(slots) - 1, int(i * len(slots) / max(1, len(sentences))))
        n = slots[idx]
        buckets[n] = (buckets[n] + ' ' + sentence).strip()
    return buckets


def split_phrases(text: str):
    parts = re.findall(r'[^,;:—–.!?…]+[,;:—–.!?…]*', text.strip())
    return [p.strip() for p in parts if p.strip()]


def pause_for(phrase: str, mode: str):
    # Talking should feel conversational, not like a slogan read with exaggerated gaps.
    base = 0.11 if mode == 'talking' else 0.12
    if phrase.endswith('…'):
        return 0.38 if mode == 'talking' else 0.38
    if phrase.endswith(('!', '?')):
        return 0.28 if mode == 'talking' else 0.26
    if phrase.endswith(('.',)):
        return 0.24 if mode == 'talking' else 0.22
    if phrase.endswith(('—', '–')):
        return 0.22 if mode == 'talking' else 0.20
    if phrase.endswith((',', ';', ':')):
        return 0.15 if mode == 'talking' else 0.15
    return base


def load_models(device):
    model_id = 'facebook/mms-tts-ukr'
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    tts = VitsModel.from_pretrained(model_id).to(device)
    tts.eval()
    repo = snapshot_download(repo_id='myshell-ai/OpenVoiceV2', allow_patterns=['converter/*'])
    converter_dir = Path(repo) / 'converter'
    converter = ToneColorConverter(str(converter_dir / 'config.json'), device=device)
    converter.load_ckpt(str(converter_dir / 'checkpoint.pth'))
    return tokenizer, tts, converter


def tts_phrase(text, tokenizer, tts, device):
    inputs = tokenizer(text, return_tensors='pt')
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        wav = tts(**inputs).waveform.squeeze().detach().cpu().float().numpy()
    return wav.astype(np.float32), int(tts.config.sampling_rate)


def apply_voice_dynamics(src: Path, dst: Path, mode: str):
    # Talking: close to natural pace and gentler compression, preserving more expression.
    tempo = 0.96 if mode == 'talking' else 0.99
    if mode == 'talking':
        af = (
            f'atempo={tempo},'
            'highpass=f=65,'
            'acompressor=threshold=-20dB:ratio=1.8:attack=18:release=160:makeup=1.2,'
            'loudnorm=I=-16:TP=-1.5:LRA=10'
        )
    else:
        af = (
            f'atempo={tempo},'
            'highpass=f=70,'
            'acompressor=threshold=-18dB:ratio=2.0:attack=14:release=140:makeup=1.3,'
            'loudnorm=I=-16:TP=-1.5:LRA=9'
        )
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error', '-i', str(src),
        '-af', af, '-ar', '24000', '-ac', '1', str(dst)
    ], check=True)


def synth(text, tokenizer, tts, converter, target_se, work, out_path, device, mode='voiceover'):
    phrases = split_phrases(text)
    if not phrases:
        phrases = [text.strip()]

    assembled = []
    sample_rate = int(tts.config.sampling_rate)
    for phrase in phrases:
        clean = phrase.strip()
        if not clean:
            continue
        wav, sample_rate = tts_phrase(clean, tokenizer, tts, device)
        if wav.size:
            peak = float(np.max(np.abs(wav))) or 1.0
            mask = np.where(np.abs(wav) > peak * 0.008)[0]
            if mask.size:
                pad = int(sample_rate * 0.045)
                a = max(0, int(mask[0]) - pad)
                b = min(len(wav), int(mask[-1]) + pad)
                wav = wav[a:b]
        assembled.append(wav)
        assembled.append(np.zeros(int(sample_rate * pause_for(clean, mode)), dtype=np.float32))

    base = work / (out_path.stem + '_base.wav')
    joined = np.concatenate(assembled) if assembled else np.zeros(int(sample_rate * 0.2), dtype=np.float32)
    sf.write(base, joined, sample_rate)

    src_se = converter.extract_se(str(base))
    converted = work / (out_path.stem + '_converted.wav')
    converter.convert(
        audio_src_path=str(base), src_se=src_se, tgt_se=target_se,
        output_path=str(converted), tau=0.25, message='ZaskaletaAITwin'
    )
    apply_voice_dynamics(converted, out_path, mode)
    base.unlink(missing_ok=True)
    converted.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--episode', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--master-voice', required=True)
    ap.add_argument('--output-dir', required=True)
    args = ap.parse_args()

    episode = json.loads(Path(args.episode).read_text(encoding='utf-8'))
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    scenes = manifest['scenes']

    preferred_talking = [1, 3, 6, 8]
    dialogue_secondary = set()
    dialogue_clone = {}
    for scene in scenes:
        n = int(scene['n'])
        lines = scene.get('dialogue', [])
        if lines:
            if any(x.get('speaker') != 'AI_CLONE' for x in lines):
                dialogue_secondary.add(n)
            clone_lines = [x['text'] for x in lines if x.get('speaker') == 'AI_CLONE']
            if clone_lines:
                dialogue_clone[n] = ' '.join(clone_lines)

    narration_slots = [int(s['n']) for s in scenes if int(s['n']) not in dialogue_secondary and int(s['n']) not in dialogue_clone]
    narration_by_scene = distribute(split_sentences(episode.get('voiceover', '')), narration_slots)

    scene_texts = {}
    for scene in scenes:
        n = int(scene['n'])
        if n in dialogue_clone:
            scene_texts[n] = dialogue_clone[n]
        else:
            scene_texts[n] = narration_by_scene.get(n, '')

    if not any(scene_texts.values()):
        (out / 'scene_speech_manifest.json').write_text('[]', encoding='utf-8')
        print('✅ No main-character speech required')
        return

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    tokenizer, tts, converter = load_models(device)
    normalized = out / '_master_voice_normalized.wav'
    normalize_reference(Path(args.master_voice), normalized)
    target_se = converter.extract_se(str(normalized))

    audio_manifest = []
    for scene in scenes:
        n = int(scene['n'])
        text = scene_texts.get(n, '')
        if not text:
            audio_manifest.append({'scene': n, 'speaker': 'AI_CLONE', 'text': '', 'audio': None, 'talking': False, 'mode': 'silent'})
            continue
        talking = n in preferred_talking or n in dialogue_clone
        mode = 'talking' if talking else 'voiceover'
        wav = out / f'scene_{n:02d}_clone.wav'
        synth(text, tokenizer, tts, converter, target_se, out, wav, device, mode=mode)
        audio_manifest.append({
            'scene': n,
            'speaker': 'AI_CLONE',
            'text': text,
            'audio': str(wav),
            'talking': talking,
            'mode': mode,
        })
        icon = '🗣️' if talking else '🎙️'
        print(f'{icon} Scene {n:02d} {mode}: {text}')

    normalized.unlink(missing_ok=True)
    (out / 'scene_speech_manifest.json').write_text(json.dumps(audio_manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('✅ Natural talking cadence: shorter pauses + gentler dynamics + expressive range preserved')


if __name__ == '__main__':
    main()
