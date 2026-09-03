import argparse
import json
import pathlib
import subprocess

import soundfile as sf
import torch
from transformers import AutoTokenizer, VitsModel


def synthesize_mms(text, output, tokenizer, model, device):
    inputs = tokenizer(text, return_tensors='pt')
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        wav = model(**inputs).waveform.squeeze().detach().cpu().float().numpy()
    sf.write(output, wav, model.config.sampling_rate)


def transform_voice(src, dst, speaker):
    speaker = speaker.upper()
    # Role differentiation from the neutral Ukrainian MMS voice.
    # atempo compensates for asetrate pitch-shift so sentence timing remains natural.
    if speaker in {'WOMAN', 'GIRL'}:
        factor = 1.16
        extra = 'highpass=f=120,lowpass=f=14500'
    elif speaker in {'FRIEND', 'MAN', 'COLLEAGUE'}:
        factor = 0.94
        extra = 'highpass=f=75,lowpass=f=13000'
    elif speaker in {'AUDIENCE', 'CROWD', 'PERSON_FROM_CROWD'}:
        factor = 1.03
        extra = 'aecho=0.8:0.25:35:0.12,highpass=f=90,lowpass=f=14000'
    else:
        factor = 1.00
        extra = 'highpass=f=90,lowpass=f=14000'

    tempo = 1.0 / factor
    af = f'asetrate=16000*{factor},aresample=16000,atempo={tempo},{extra},loudnorm=I=-18:TP=-2:LRA=7'
    cmd = ['ffmpeg', '-y', '-i', str(src), '-af', af, '-ar', '16000', '-ac', '1', str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or 'ffmpeg failed')[-2000:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--episode', required=True)
    ap.add_argument('--master-voice', required=True)
    ap.add_argument('--worker-dir', required=True)
    ap.add_argument('--python-bin', required=True)
    ap.add_argument('--output-dir', required=True)
    args = ap.parse_args()

    episode = json.loads(pathlib.Path(args.episode).read_text(encoding='utf-8'))
    dialogue = episode.get('dialogue')
    out = pathlib.Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if not dialogue:
        print('ℹ️ This is not a dialogue episode. No dialogue audio generated.')
        return

    lines = dialogue.get('dialogues', [])
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    tokenizer = None
    model = None

    index = []
    for i, line in enumerate(lines, 1):
        scene = int(line['scene'])
        speaker = str(line['speaker']).upper()
        text = str(line['text']).strip()
        stem = f'scene_{scene:02d}_line_{i:02d}_{speaker.lower()}'
        txt = out / f'{stem}.txt'
        wav = out / f'{stem}.wav'
        txt.write_text(text + '\n', encoding='utf-8')

        if speaker == 'AI_CLONE':
            cmd = [
                args.python_bin,
                str(pathlib.Path(args.worker_dir) / 'voice_mms_openvoice.py'),
                '--script', str(txt),
                '--voice', args.master_voice,
                '--output', str(wav),
                '--language', 'uk',
            ]
            subprocess.run(cmd, check=True)
            profile = 'MASTER_VOICE'
        else:
            if tokenizer is None:
                tokenizer = AutoTokenizer.from_pretrained('facebook/mms-tts-ukr')
                model = VitsModel.from_pretrained('facebook/mms-tts-ukr').to(device)
                model.eval()
            raw = out / f'{stem}_raw.wav'
            synthesize_mms(text, raw, tokenizer, model, device)
            transform_voice(raw, wav, speaker)
            raw.unlink(missing_ok=True)
            profile = speaker

        index.append({
            'scene': scene,
            'speaker': speaker,
            'text': text,
            'audio': wav.name,
            'voice_profile': profile,
        })
        print(f'✅ Scene {scene:02d} | {speaker}: {wav.name}')

    (out / 'dialogue_audio_manifest.json').write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print('✅ Dialogue audio pack ready:', out)


if __name__ == '__main__':
    main()
