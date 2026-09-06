import argparse
import pathlib
import subprocess

import soundfile as sf
import torch
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer, VitsModel
from openvoice.api import OpenVoiceBaseClass, ToneColorConverter


def normalize_reference(src: pathlib.Path, dest: pathlib.Path):
    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-t", "60", "-ac", "1", "-ar", "22050",
        "-af", "highpass=f=80,lowpass=f=14000,loudnorm=I=-18:TP=-2:LRA=7", str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "ffmpeg reference normalization failed")[-2000:])


def synthesize_ukrainian(text: str, output_path: pathlib.Path, device: str):
    model_id = "facebook/mms-tts-ukr"
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = VitsModel.from_pretrained(model_id).to(device)
    model.eval()
    inputs = tokenizer(text, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        waveform = model(**inputs).waveform.squeeze().detach().cpu().float().numpy()
    sf.write(output_path, waveform, model.config.sampling_rate)


def build_converter_without_watermark(config_path: pathlib.Path, device: str) -> ToneColorConverter:
    """Initialize current OpenVoice V2 safely without triggering its broken kwargs path.

    Upstream ToneColorConverter.__init__ forwards enable_watermark to
    OpenVoiceBaseClass.__init__, which does not accept that keyword. Calling the
    base initializer directly keeps the official converter model/state while
    deliberately disabling watermarking for this private MASTER CLONE test.
    """
    converter = ToneColorConverter.__new__(ToneColorConverter)
    OpenVoiceBaseClass.__init__(converter, str(config_path), device=device)
    converter.watermark_model = None
    converter.version = getattr(converter.hps, "_version_", "v1")
    return converter


def clone_tone(base_audio: pathlib.Path, reference_audio: pathlib.Path, output_path: pathlib.Path, device: str):
    repo = snapshot_download(
        repo_id="myshell-ai/OpenVoiceV2",
        allow_patterns=["converter/*"],
    )
    converter_dir = pathlib.Path(repo) / "converter"
    converter = build_converter_without_watermark(converter_dir / "config.json", device)
    converter.load_ckpt(str(converter_dir / "checkpoint.pth"))
    src_se = converter.extract_se(str(base_audio))
    tgt_se = converter.extract_se(str(reference_audio))
    converter.convert(
        audio_src_path=str(base_audio),
        src_se=src_se,
        tgt_se=tgt_se,
        output_path=str(output_path),
        tau=0.3,
        message="ZaskaletaAITwin",
    )


def main():
    parser = argparse.ArgumentParser(description="Ukrainian MMS TTS + OpenVoice V2 tone cloning")
    parser.add_argument("--script", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="uk")
    args = parser.parse_args()

    if args.language not in {"uk", "ukr"}:
        raise SystemExit("This adapter is configured for Ukrainian only.")

    script_path = pathlib.Path(args.script)
    voice_path = pathlib.Path(args.voice)
    output_path = pathlib.Path(args.output)
    work = output_path.parent
    text = script_path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("Script is empty")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    base_audio = work / "mms-ukrainian-base.wav"
    reference_wav = work / "master-voice-normalized.wav"

    synthesize_ukrainian(text, base_audio, device)
    normalize_reference(voice_path, reference_wav)
    clone_tone(base_audio, reference_wav, output_path, device)

    base_audio.unlink(missing_ok=True)
    reference_wav.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
