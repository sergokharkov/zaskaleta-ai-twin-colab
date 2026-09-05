#!/usr/bin/env python3
"""Persist and restore encrypted clone job state in canonical S3 storage.

Job state contains no raw biometric payloads, but it is still encrypted client-side so API job
status survives worker restarts without widening metadata exposure. Every write is downloaded,
decrypted and SHA/size verified before success is reported. Secret values never leave env vars.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'content' / 'storage_config.json'
SAFE_ID = re.compile(r'^[0-9a-f]{32}$')


def cfg_and_values():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    c = cfg.get('canonical_storage') or {}; e = cfg.get('encryption') or {}
    if cfg.get('schema') != 'zaskaleta-storage-v2' or c.get('provider') != 's3_compatible' or c.get('required_region_policy') != 'EU_ONLY':
        raise RuntimeError('Valid EU S3 storage v2 contract required')
    mapping = {'bucket': c['bucket_env'], 'endpoint': c['endpoint_env'], 'region': c['region_env'], 'access': c['access_key_env'], 'secret': c['secret_key_env'], 'key': e['key_env']}
    values = {k: os.environ.get(v, '').strip() for k, v in mapping.items()}
    missing = [k for k, v in values.items() if not v]
    if missing: raise RuntimeError('Missing runtime storage configuration: ' + ', '.join(missing))
    return cfg, values


def strict_key(raw: str) -> bytes:
    try: key = base64.b64decode(raw, validate=True)
    except Exception as exc: raise RuntimeError('Encryption key must be strict base64') from exc
    if len(key) != 32: raise RuntimeError('Encryption key must decode to 32 bytes')
    return key


def s3(values: dict):
    try: import boto3
    except ImportError as exc: raise RuntimeError('boto3 required') from exc
    return boto3.client('s3', endpoint_url=values['endpoint'], region_name=values['region'], aws_access_key_id=values['access'], aws_secret_access_key=values['secret'])


def encrypt(data: bytes, key: bytes) -> tuple[bytes, str]:
    try: from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc: raise RuntimeError('cryptography required') from exc
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return ciphertext, base64.b64encode(nonce).decode('ascii')


def decrypt(data: bytes, key: bytes, nonce_b64: str) -> bytes:
    try: from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc: raise RuntimeError('cryptography required') from exc
    nonce = base64.b64decode(nonce_b64, validate=True)
    if len(nonce) != 12: raise RuntimeError('Invalid AES-GCM nonce')
    return AESGCM(key).decrypt(nonce, data, None)


def keys(cfg: dict, job_id: str) -> tuple[str, str]:
    prefix = cfg['canonical_storage']['job_artifact_prefix'].rstrip('/') + '/' + job_id
    return prefix + '/job_state_v1.json.enc', prefix + '/job_state_manifest_v1.json'


def persist(job_id: str, state_path: Path) -> int:
    cfg, values = cfg_and_values(); key = strict_key(values['key']); client = s3(values); bucket = values['bucket']
    raw = state_path.read_bytes(); parsed = json.loads(raw.decode('utf-8'))
    if parsed.get('job_id') != job_id: raise RuntimeError('Job state id mismatch')
    source_sha = hashlib.sha256(raw).hexdigest(); ciphertext, nonce = encrypt(raw, key)
    object_key, manifest_key = keys(cfg, job_id)
    client.put_object(Bucket=bucket, Key=object_key, Body=ciphertext, ContentType='application/octet-stream', Metadata={'encryption': 'AES-256-GCM'})
    downloaded = client.get_object(Bucket=bucket, Key=object_key)['Body'].read()
    verified = decrypt(downloaded, key, nonce)
    if hashlib.sha256(verified).hexdigest() != source_sha or len(verified) != len(raw):
        raise RuntimeError('Encrypted job state verification failed')
    manifest = {
        'schema': 'zaskaleta-clone-job-state-manifest-v1', 'candidate_id': job_id,
        'updated_at': datetime.now(timezone.utc).isoformat(), 'object_key': object_key,
        'source_sha256': source_sha, 'source_size_bytes': len(raw), 'nonce_b64': nonce,
        'verify_status': 'VERIFIED_DECRYPTED_SHA256', 'secret_values_exposed': False,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    client.put_object(Bucket=bucket, Key=manifest_key, Body=manifest_bytes, ContentType='application/json', Metadata={'manifest-sha256': manifest_sha, 'secret-values-exposed': 'false'})
    head = client.head_object(Bucket=bucket, Key=manifest_key)
    if (head.get('Metadata') or {}).get('manifest-sha256') != manifest_sha:
        raise RuntimeError('Job state manifest verification failed')
    print(json.dumps({'persisted': True, 'candidate_id': job_id, 'manifest_key': manifest_key, 'state_sha256': source_sha, 'secret_values_exposed': False}, ensure_ascii=False))
    return 0


def restore(job_id: str, output: Path) -> int:
    cfg, values = cfg_and_values(); key = strict_key(values['key']); client = s3(values); bucket = values['bucket']
    object_key, manifest_key = keys(cfg, job_id)
    manifest = json.loads(client.get_object(Bucket=bucket, Key=manifest_key)['Body'].read().decode('utf-8'))
    if manifest.get('schema') != 'zaskaleta-clone-job-state-manifest-v1' or manifest.get('candidate_id') != job_id or manifest.get('verify_status') != 'VERIFIED_DECRYPTED_SHA256':
        raise RuntimeError('Invalid job state manifest')
    ciphertext = client.get_object(Bucket=bucket, Key=object_key)['Body'].read()
    plain = decrypt(ciphertext, key, manifest['nonce_b64'])
    actual_sha = hashlib.sha256(plain).hexdigest()
    if actual_sha != manifest.get('source_sha256') or len(plain) != manifest.get('source_size_bytes'):
        raise RuntimeError('Restored job state verification failed')
    parsed = json.loads(plain.decode('utf-8'))
    if parsed.get('job_id') != job_id: raise RuntimeError('Restored job state id mismatch')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=output.parent, delete=False) as tf:
        tf.write(plain); tf.flush(); os.fsync(tf.fileno()); tmp = Path(tf.name)
    os.replace(tmp, output)
    print(json.dumps({'restored': True, 'candidate_id': job_id, 'state_sha256': actual_sha, 'secret_values_exposed': False}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('persist'); p.add_argument('--candidate-id', required=True); p.add_argument('--state-file', required=True)
    r = sub.add_parser('restore'); r.add_argument('--candidate-id', required=True); r.add_argument('--output', required=True)
    args = ap.parse_args(); job_id = args.candidate_id.strip()
    if not SAFE_ID.fullmatch(job_id): raise RuntimeError('Invalid candidate id')
    if args.command == 'persist': return persist(job_id, Path(args.state_file).resolve())
    return restore(job_id, Path(args.output).resolve())


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'secret_values_exposed': False}), file=sys.stderr)
        sys.exit(2)
