import argparse
import json
import re
from pathlib import Path

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


def synth(text, tokenizer, tts, converter, target_se, work, out_path, device):
    base = work / (out_path.stem + '_base.wav')
    inputs = tokenizer(text, return_tensors='pt')
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        waveform = tts(**inputs).waveform.squeeze().detach().cpu().float().numpy()
    sf.write(base, waveform, tts.config.sampling_rate)
    src_se = converter.extract_se(str(base))
    converter.convert(
        audio_src_path=str(base), src_se=src_se, tgt_se=target_se,
        output_path=str(out_path), tau=0.3, message='ZaskaletaAITwin'
    )
    base.unlink(missing_ok=True)


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

    # Four strong on-camera beats per ordinary episode. Remaining narration is still
    # spoken by the main character, but as voiceover over cinematic cutaways.
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
        wav = out / f'scene_{n:02d}_clone.wav'
        synth(text, tokenizer, tts, converter, target_se, out, wav, device)
        talking = n in preferred_talking or n in dialogue_clone
        mode = 'talking' if talking else 'voiceover'
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
    print('✅ 4 speaking beats + main-character voiceover cutaways prepared')


if __name__ == '__main__':
    main()
