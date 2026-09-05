#!/usr/bin/env python3
"""Encrypt, persist, verify and restore MASTER CLONE job artifacts in canonical S3 storage.

No operation runs implicitly. `persist` encrypts every regular job artifact except job.json and
job-scoped runtime inputs, uploads encrypted objects, downloads and decrypt-verifies them, then
publishes a non-secret manifest. `restore` restores one artifact from that manifest and verifies
SHA-256/size. Secret values are only read from environment variables and never printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'content' / 'storage_config.json'
CHUNK_SIZE = 16 * 1024 * 1024
SAFE_ID = re.compile(r'^[A-Za-z0-9._-]{1,80}$')


def load_cfg() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    canonical = cfg.get('canonical_storage') or {}
    runtime = cfg.get('runtime') or {}
    if cfg.get('schema') != 'zaskaleta-storage-v2':
        raise RuntimeError('Storage v2 required')
    if canonical.get('provider') != 's3_compatible' or canonical.get('required_region_policy') != 'EU_ONLY':
        raise RuntimeError('Canonical storage must remain EU-only S3-compatible')
    if runtime.get('delete_temporary_plaintext_after_job') is not True:
        raise RuntimeError('Plaintext cleanup policy must remain enabled')
    return cfg


def env_map(cfg: dict) -> dict[str, str]:
    c = cfg['canonical_storage']; e = cfg['encryption']
    return {
        'bucket': c['bucket_env'], 'endpoint': c['endpoint_env'], 'region': c['region_env'],
        'access_key': c['access_key_env'], 'secret_key': c['secret_key_env'], 'key': e['key_env'],
    }


def require_env(cfg: dict) -> dict[str, str]:
    mapping = env_map(cfg)
    values = {}
    missing = []
    for name, var in mapping.items():
        value = os.environ.get(var, '').strip()
        if not value: missing.append(name)
        else: values[name] = value
    if missing:
        raise RuntimeError('Missing runtime storage configuration: ' + ', '.join(missing))
    return values


def strict_key(raw: str) -> bytes:
    try: key = base64.b64decode(raw, validate=True)
    except Exception as exc: raise RuntimeError('Encryption key must be strict base64') from exc
    if len(key) != 32: raise RuntimeError('Encryption key must decode to 32 bytes')
    return key


def client(cfg: dict, values: dict):
    try: import boto3
    except ImportError as exc: raise RuntimeError('boto3 required') from exc
    return boto3.client('s3', endpoint_url=values['endpoint'], region_name=values['region'], aws_access_key_id=values['access_key'], aws_secret_access_key=values['secret_key'])


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def encrypt_file(src: Path, dst: Path, key: bytes) -> tuple[str, str, int]:
    try: from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc: raise RuntimeError('cryptography required') from exc
    nonce = os.urandom(12)
    enc = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    size = 0
    with src.open('rb') as fin, dst.open('wb') as fout:
        for chunk in iter(lambda: fin.read(CHUNK_SIZE), b''):
            out = enc.update(chunk)
            if out: fout.write(out); size += len(out)
        out = enc.finalize()
        if out: fout.write(out); size += len(out)
        fout.flush(); os.fsync(fout.fileno())
    return base64.b64encode(nonce).decode(), base64.b64encode(enc.tag).decode(), size


def decrypt_file(src: Path, dst: Path, key: bytes, nonce_b64: str, tag_b64: str) -> tuple[str, int]:
    try: from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc: raise RuntimeError('cryptography required') from exc
    nonce = base64.b64decode(nonce_b64, validate=True); tag = base64.b64decode(tag_b64, validate=True)
    if len(nonce) != 12 or len(tag) != 16: raise RuntimeError('Invalid AES-GCM nonce/tag')
    dec = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    h = hashlib.sha256(); size = 0
    with src.open('rb') as fin, dst.open('wb') as fout:
        for chunk in iter(lambda: fin.read(CHUNK_SIZE), b''):
            out = dec.update(chunk)
            if out: fout.write(out); h.update(out); size += len(out)
        out = dec.finalize()
        if out: fout.write(out); h.update(out); size += len(out)
        fout.flush(); os.fsync(fout.fileno())
    return h.hexdigest(), size


def validate_id(value: str) -> str:
    value = value.strip()
    if not SAFE_ID.fullmatch(value): raise RuntimeError('Invalid candidate id')
    return value


def safe_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def artifact_files(job_dir: Path) -> list[Path]:
    files = []
    for p in sorted(job_dir.rglob('*')):
        if not p.is_file(): continue
        rel = safe_relative(job_dir, p)
        if rel == 'job.json' or rel.startswith('_runtime_assets/'):
            continue
        files.append(p)
    return files


def persist(job_dir: Path, candidate_id: str, manifest_out: Path) -> int:
    cfg = load_cfg(); values = require_env(cfg); key = strict_key(values['key']); s3 = client(cfg, values)
    bucket = values['bucket']; prefix = cfg['canonical_storage']['job_artifact_prefix'].rstrip('/') + '/' + candidate_id
    files = artifact_files(job_dir)
    if not files: raise RuntimeError('No job artifacts to persist')
    records = []
    for src in files:
        rel = safe_relative(job_dir, src)
        object_key = prefix + '/' + rel + '.enc'
        cipher_tmp = verify_tmp = plain_tmp = None
        try:
            with tempfile.NamedTemporaryFile(prefix='clone-job-enc-', delete=False) as tf: cipher_tmp = Path(tf.name)
            nonce, tag, enc_size = encrypt_file(src, cipher_tmp, key)
            source_sha = sha256_file(src); source_size = src.stat().st_size
            s3.upload_file(str(cipher_tmp), bucket, object_key, ExtraArgs={'Metadata': {'source-sha256': source_sha, 'source-size': str(source_size), 'encryption': 'AES-256-GCM'}})
            with tempfile.NamedTemporaryFile(prefix='clone-job-verify-', delete=False) as tf: verify_tmp = Path(tf.name)
            s3.download_file(bucket, object_key, str(verify_tmp))
            with tempfile.NamedTemporaryFile(prefix='clone-job-plain-', delete=False) as tf: plain_tmp = Path(tf.name)
            actual_sha, actual_size = decrypt_file(verify_tmp, plain_tmp, key, nonce, tag)
            if actual_sha != source_sha or actual_size != source_size: raise RuntimeError('Persisted artifact verification failed: ' + rel)
            records.append({'relative_path': rel, 'object_key': object_key, 'source_sha256': source_sha, 'source_size_bytes': source_size, 'encrypted_size_bytes': enc_size, 'nonce_b64': nonce, 'tag_b64': tag, 'verify_status': 'VERIFIED_DECRYPTED_SHA256'})
        finally:
            for tmp in (cipher_tmp, verify_tmp, plain_tmp):
                if tmp is not None and tmp.exists(): tmp.unlink()
    manifest = {'schema': 'zaskaleta-clone-job-artifact-manifest-v1', 'candidate_id': candidate_id, 'created_at': datetime.now(timezone.utc).isoformat(), 'storage_provider': 's3_compatible', 'artifact_count': len(records), 'all_artifacts_verified': True, 'client_side_encryption': 'AES-256-GCM', 'secret_values_exposed': False, 'artifacts': records}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    manifest_key = prefix + '/artifact_manifest_v1.json'
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=manifest_bytes, ContentType='application/json', Metadata={'manifest-sha256': manifest_sha, 'secret-values-exposed': 'false'})
    head = s3.head_object(Bucket=bucket, Key=manifest_key)
    if (head.get('Metadata') or {}).get('manifest-sha256') != manifest_sha: raise RuntimeError('Job artifact manifest verification failed')
    manifest_out.parent.mkdir(parents=True, exist_ok=True); manifest_out.write_bytes(manifest_bytes)
    print(json.dumps({'persisted': True, 'candidate_id': candidate_id, 'artifact_count': len(records), 'manifest_key': manifest_key, 'manifest_sha256': manifest_sha, 'secret_values_exposed': False}, ensure_ascii=False))
    return 0


def restore(candidate_id: str, relative_path: str, output: Path) -> int:
    cfg = load_cfg(); values = require_env(cfg); key = strict_key(values['key']); s3 = client(cfg, values); bucket = values['bucket']
    prefix = cfg['canonical_storage']['job_artifact_prefix'].rstrip('/') + '/' + candidate_id
    manifest_key = prefix + '/artifact_manifest_v1.json'
    body = s3.get_object(Bucket=bucket, Key=manifest_key)['Body'].read(); manifest = json.loads(body.decode())
    if manifest.get('schema') != 'zaskaleta-clone-job-artifact-manifest-v1' or manifest.get('candidate_id') != candidate_id or manifest.get('all_artifacts_verified') is not True: raise RuntimeError('Invalid job artifact manifest')
    rows = [x for x in manifest.get('artifacts', []) if x.get('relative_path') == relative_path]
    if len(rows) != 1: raise RuntimeError('Requested artifact is missing or ambiguous')
    row = rows[0]
    cipher_tmp = None
    try:
        with tempfile.NamedTemporaryFile(prefix='clone-job-restore-', delete=False) as tf: cipher_tmp = Path(tf.name)
        s3.download_file(bucket, row['object_key'], str(cipher_tmp))
        output.parent.mkdir(parents=True, exist_ok=True)
        actual_sha, actual_size = decrypt_file(cipher_tmp, output, key, row['nonce_b64'], row['tag_b64'])
        if actual_sha != row['source_sha256'] or actual_size != row['source_size_bytes']:
            output.unlink(missing_ok=True); raise RuntimeError('Restored artifact verification failed')
        print(json.dumps({'restored': True, 'candidate_id': candidate_id, 'relative_path': relative_path, 'sha256': actual_sha, 'size_bytes': actual_size, 'secret_values_exposed': False}, ensure_ascii=False))
        return 0
    finally:
        if cipher_tmp is not None and cipher_tmp.exists(): cipher_tmp.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('persist'); p.add_argument('--job-dir', required=True); p.add_argument('--candidate-id', required=True); p.add_argument('--manifest-out', required=True)
    r = sub.add_parser('restore'); r.add_argument('--candidate-id', required=True); r.add_argument('--relative-path', required=True); r.add_argument('--output', required=True)
    args = ap.parse_args(); candidate_id = validate_id(args.candidate_id)
    if args.command == 'persist': return persist(Path(args.job_dir).resolve(), candidate_id, Path(args.manifest_out).resolve())
    return restore(candidate_id, args.relative_path, Path(args.output).resolve())


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'secret_values_exposed': False}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)
