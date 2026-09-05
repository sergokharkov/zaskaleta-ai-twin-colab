#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'content' / 'clone_speech_lineage_policy_v1.json'

HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')
SECRET_KEYS = re.compile(r'(secret|password|passwd|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)', re.I)
RAW_BIOMETRIC_KEYS = re.compile(r'(raw.*(voice|audio|biometric)|voice.*payload|biometric.*payload)', re.I)
SECRET_VALUES = ('BEGIN PRIVATE KEY', 'BEGIN OPENSSH PRIVATE KEY', 'aws_secret_access_key=', 'AI_TWIN_DATA_ENCRYPTION_KEY=')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sha_ok(value: object) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))


def scan_forbidden(value: object, path: str = '$') -> list[str]:
    failures: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f'{path}.{key_text}'
            if SECRET_KEYS.search(key_text):
                failures.append('secret_field_forbidden:' + child_path)
            if RAW_BIOMETRIC_KEYS.search(key_text):
                failures.append('raw_biometric_payload_forbidden:' + child_path)
            failures.extend(scan_forbidden(child, child_path))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            failures.extend(scan_forbidden(child, f'{path}[{idx}]'))
    elif isinstance(value, str):
        for marker in SECRET_VALUES:
            if marker in value:
                failures.append('secret_value_forbidden:' + path)
                break
    return failures


def wav_contract(path: Path) -> tuple[int, int] | None:
    try:
        with wave.open(str(path), 'rb') as wf:
            return int(wf.getframerate()), int(wf.getnchannels())
    except (wave.Error, EOFError, OSError):
        return None


def resolve_audio(manifest_path: Path, raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (manifest_path.parent / p).resolve()


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate MASTER CLONE speech manifest lineage and generated WAV integrity')
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--master-voice', required=True)
    args = ap.parse_args()

    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    manifest_path = Path(args.manifest).resolve()
    master_voice = Path(args.master_voice).resolve()
    failures: list[str] = []

    try:
        doc = json.loads(manifest_path.read_text(encoding='utf-8'))
    except Exception as exc:
        print(json.dumps({'schema': 'zaskaleta-clone-speech-lineage-evaluation-v1', 'valid': False,
                          'failures': [f'manifest_unreadable:{type(exc).__name__}'], 'decision': 'BLOCK_SPEECH_LINEAGE'}, indent=2))
        return 2

    if not isinstance(doc, dict):
        failures.append('manifest_root_must_be_object')
        doc = {}

    failures.extend(scan_forbidden(doc))
    if policy.get('schema') != 'zaskaleta-clone-speech-lineage-policy-v1':
        failures.append('policy_schema_invalid')
    if doc.get('schema') != policy.get('manifest_schema'):
        failures.append('manifest_schema_invalid')

    canonical_name = policy.get('canonical_master_voice_filename')
    if not master_voice.is_file():
        failures.append('master_voice_file_missing')
        runtime_master_sha = None
    else:
        if master_voice.name != canonical_name:
            failures.append('master_voice_filename_not_canonical')
        runtime_master_sha = sha256_file(master_voice)

    manifest_master_sha = doc.get('master_voice_sha256')
    if not sha_ok(manifest_master_sha):
        failures.append('master_voice_sha256_invalid')
    elif runtime_master_sha is not None and manifest_master_sha.lower() != runtime_master_sha.lower():
        failures.append('master_voice_sha256_runtime_mismatch')

    normalized_sha = doc.get('normalized_reference_sha256')
    if normalized_sha is not None and not sha_ok(normalized_sha):
        failures.append('normalized_reference_sha256_invalid')

    expected_audio = policy.get('required_final_audio') or {}
    contract = doc.get('final_audio_contract')
    if not isinstance(contract, dict):
        failures.append('final_audio_contract_invalid')
    else:
        if contract.get('sample_rate_hz') != expected_audio.get('sample_rate_hz'):
            failures.append('final_audio_sample_rate_contract_mismatch')
        if contract.get('channels') != expected_audio.get('channels'):
            failures.append('final_audio_channels_contract_mismatch')
        if contract.get('loop_or_repeat_allowed') is not False:
            failures.append('loop_or_repeat_contract_must_be_false')

    scenes = doc.get('scenes')
    if not isinstance(scenes, list):
        failures.append('scenes_must_be_array')
        scenes = []

    allowed_speakers = set(policy.get('allowed_speakers') or [])
    allowed_modes = set(policy.get('allowed_modes') or [])
    allowed_presets = set(policy.get('allowed_voice_presets') or [])
    seen_scene_ids: set[int] = set()

    for idx, row in enumerate(scenes):
        prefix = f'scene[{idx}]'
        if not isinstance(row, dict):
            failures.append(prefix + ':must_be_object')
            continue
        scene_id = row.get('scene')
        if not isinstance(scene_id, int) or isinstance(scene_id, bool) or scene_id <= 0:
            failures.append(prefix + ':scene_id_invalid')
        elif scene_id in seen_scene_ids:
            failures.append(prefix + ':duplicate_scene_id')
        else:
            seen_scene_ids.add(scene_id)

        if row.get('speaker') not in allowed_speakers:
            failures.append(prefix + ':speaker_not_allowed')
        if row.get('mode') not in allowed_modes:
            failures.append(prefix + ':mode_not_allowed')
        if row.get('voice_preset') not in allowed_presets:
            failures.append(prefix + ':voice_preset_not_allowed')
        if row.get('loop_or_repeat_used') is not False:
            failures.append(prefix + ':loop_or_repeat_forbidden')
        if row.get('master_voice_sha256') != manifest_master_sha:
            failures.append(prefix + ':master_voice_sha256_mismatch')

        mode = row.get('mode')
        audio = row.get('audio')
        audio_sha = row.get('audio_sha256')
        if mode == 'silent':
            if audio is not None or audio_sha is not None:
                failures.append(prefix + ':silent_scene_must_not_reference_audio')
            if row.get('talking') is not False:
                failures.append(prefix + ':silent_scene_talking_must_be_false')
            continue

        if not isinstance(audio, str) or not audio.strip():
            failures.append(prefix + ':audio_path_missing')
            continue
        if not sha_ok(audio_sha):
            failures.append(prefix + ':audio_sha256_invalid')
        audio_path = resolve_audio(manifest_path, audio)
        if not audio_path.is_file():
            failures.append(prefix + ':audio_file_missing')
            continue
        runtime_audio_sha = sha256_file(audio_path)
        if sha_ok(audio_sha) and runtime_audio_sha.lower() != str(audio_sha).lower():
            failures.append(prefix + ':audio_sha256_runtime_mismatch')

        actual = wav_contract(audio_path)
        if actual is None:
            failures.append(prefix + ':audio_not_valid_wav')
        else:
            rate, channels = actual
            if rate != expected_audio.get('sample_rate_hz'):
                failures.append(prefix + ':audio_sample_rate_invalid')
            if channels != expected_audio.get('channels'):
                failures.append(prefix + ':audio_channels_invalid')
            if row.get('sample_rate_hz') != rate:
                failures.append(prefix + ':manifest_sample_rate_mismatch')
            if row.get('channels') != channels:
                failures.append(prefix + ':manifest_channels_mismatch')

        if mode == 'talking' and row.get('talking') is not True:
            failures.append(prefix + ':talking_flag_mismatch')
        if mode == 'voiceover' and row.get('talking') is not False:
            failures.append(prefix + ':voiceover_talking_flag_mismatch')

    report = {
        'schema': 'zaskaleta-clone-speech-lineage-evaluation-v1',
        'valid': not failures,
        'canonical_master_voice_filename': canonical_name,
        'canonical_master_voice_source_ref': policy.get('canonical_master_voice_source_ref'),
        'master_voice_runtime_hash_verified': runtime_master_sha is not None and manifest_master_sha == runtime_master_sha,
        'scene_count': len(scenes),
        'perceptual_voice_similarity_verified': False,
        'manual_release_gate_still_required': True,
        'failures': failures,
        'decision': 'PASS_SPEECH_LINEAGE' if not failures else 'BLOCK_SPEECH_LINEAGE',
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
