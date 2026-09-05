#!/usr/bin/env python3
"""Materialize verified MASTER CLONE assets from encrypted S3 into a job-scoped runtime directory.

This tool is intentionally network-capable but never runs unless invoked by the runtime.
It requires complete storage credentials and a strict base64 AES-256 key in environment variables,
downloads the previously verified migration manifest, decrypts each object, verifies SHA-256 and size,
and writes a non-secret runtime attestation. Destination must not be inside the Git repository.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'content' / 'storage_config.json'
CHUNK_SIZE = 16 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def strict_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except Exception as exc:
        raise RuntimeError('AI_TWIN_DATA_ENCRYPTION_KEY must be strict base64') from exc
    if len(key) != 32:
        raise RuntimeError('AI_TWIN_DATA_ENCRYPTION_KEY must decode to exactly 32 bytes')
    return key


def s3_client(cfg: dict):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError('boto3 is required for runtime materialization') from exc
    canonical = cfg['canonical_storage']
    return boto3.client(
        's3',
        endpoint_url=os.environ[canonical['endpoint_env']],
        region_name=os.environ[canonical['region_env']],
        aws_access_key_id=os.environ[canonical['access_key_env']],
        aws_secret_access_key=os.environ[canonical['secret_key_env']],
    )


def decrypt_stream(src: Path, dst: Path, key: bytes, nonce_b64: str, tag_b64: str) -> tuple[str, int]:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError('cryptography is required for runtime materialization') from exc
    nonce = base64.b64decode(nonce_b64, validate=True)
    tag = base64.b64decode(tag_b64, validate=True)
    if len(nonce) != 12 or len(tag) != 16:
        raise RuntimeError('Invalid AES-GCM nonce/tag length')
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    h = hashlib.sha256()
    size = 0
    with src.open('rb') as fin, dst.open('wb') as fout:
        for chunk in iter(lambda: fin.read(CHUNK_SIZE), b''):
            plain = decryptor.update(chunk)
            if plain:
                fout.write(plain)
                h.update(plain)
                size += len(plain)
        final = decryptor.finalize()
        if final:
            fout.write(final)
            h.update(final)
            size += len(final)
        fout.flush()
        os.fsync(fout.fileno())
    return h.hexdigest(), size


def safe_target(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith('/'):
        raise RuntimeError('Invalid relative path in migration manifest')
    target = (root / relative_path).resolve()
    target.relative_to(root.resolve())
    return target


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def required_env(cfg: dict) -> dict[str, str]:
    c = cfg['canonical_storage']
    e = cfg['encryption']
    return {
        'bucket': c['bucket_env'],
        'endpoint': c['endpoint_env'],
        'region': c['region_env'],
        'access_key': c['access_key_env'],
        'secret_key': c['secret_key_env'],
        'encryption_key': e['key_env'],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='Materialize encrypted MASTER CLONE assets for one runtime job')
    ap.add_argument('--destination', required=True)
    ap.add_argument('--manifest-key', default='')
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    if cfg.get('schema') != 'zaskaleta-storage-v2':
        raise SystemExit('Storage v2 configuration required')
    canonical = cfg.get('canonical_storage') or {}
    runtime = cfg.get('runtime') or {}
    if canonical.get('provider') != 's3_compatible' or canonical.get('required_region_policy') != 'EU_ONLY':
        raise SystemExit('Canonical storage must remain EU-only S3-compatible')
    if runtime.get('job_scoped_plaintext_materialization_required') is not True:
        raise SystemExit('Job-scoped materialization policy is not enabled')
    if runtime.get('delete_temporary_plaintext_after_job') is not True:
        raise SystemExit('Plaintext cleanup policy must remain enabled')

    destination = Path(args.destination).resolve()
    if inside_repo(destination):
        raise SystemExit('Refusing to materialize private clone assets inside the Git repository')
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit('Destination must be empty before materialization')
    destination.mkdir(parents=True, exist_ok=True)

    env_map = required_env(cfg)
    missing = [name for name, env_name in env_map.items() if not os.environ.get(env_name, '').strip()]
    if missing:
        raise SystemExit('Runtime materialization blocked; missing configuration: ' + ', '.join(missing))

    bucket = os.environ[env_map['bucket']]
    manifest_key = args.manifest_key.strip() or canonical.get('migration_manifest_key')
    if not isinstance(manifest_key, str) or not manifest_key:
        raise SystemExit('migration_manifest_key is not configured')

    client = s3_client(cfg)
    manifest_response = client.get_object(Bucket=bucket, Key=manifest_key)
    manifest_bytes = manifest_response['Body'].read()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes.decode('utf-8'))

    if manifest.get('schema') != 'zaskaleta-storage-migration-manifest-v1':
        raise RuntimeError('Invalid canonical storage migration manifest schema')
    if manifest.get('mode') != 'EXECUTE' or manifest.get('migration_complete') is not True:
        raise RuntimeError('Canonical migration manifest is not complete')
    if manifest.get('destination_verified_after_decryption') is not True:
        raise RuntimeError('Canonical migration was not verified after decryption')
    if manifest.get('secret_values_exposed') is not False:
        raise RuntimeError('Migration manifest secret-safety invariant failed')

    objects = manifest.get('objects') or []
    if not objects or manifest.get('object_count') != len(objects):
        raise RuntimeError('Canonical migration manifest object count mismatch')

    key = strict_key(os.environ[env_map['encryption_key']])
    verified = []
    try:
        for obj in objects:
            if obj.get('verify_status') != 'VERIFIED_DECRYPTED_SHA256':
                raise RuntimeError('Object lacks verified decrypted migration status')
            relative = obj.get('relative_path')
            encrypted_key = obj.get('encrypted_object_key')
            expected_sha = obj.get('source_sha256')
            expected_size = obj.get('size_bytes')
            nonce_b64 = obj.get('encryption_nonce_b64')
            tag_b64 = obj.get('encryption_tag_b64')
            if not isinstance(encrypted_key, str) or not encrypted_key:
                raise RuntimeError('Encrypted object key missing')
            if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                raise RuntimeError('Source SHA-256 missing or invalid')
            if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
                raise RuntimeError('Source size missing or invalid')

            target = safe_target(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_cipher = None
            tmp_plain = None
            try:
                with tempfile.NamedTemporaryFile(prefix='clone-runtime-cipher-', delete=False) as tf:
                    tmp_cipher = Path(tf.name)
                client.download_file(bucket, encrypted_key, str(tmp_cipher))
                with tempfile.NamedTemporaryFile(prefix='clone-runtime-plain-', dir=target.parent, delete=False) as tf:
                    tmp_plain = Path(tf.name)
                actual_sha, actual_size = decrypt_stream(tmp_cipher, tmp_plain, key, nonce_b64, tag_b64)
                if actual_sha != expected_sha or actual_size != expected_size:
                    raise RuntimeError(f'Decrypted runtime verification failed for {relative}')
                os.replace(tmp_plain, target)
                tmp_plain = None
                verified.append({'relative_path': relative, 'sha256': actual_sha, 'size_bytes': actual_size})
            finally:
                for tmp in (tmp_cipher, tmp_plain):
                    if tmp is not None and tmp.exists():
                        tmp.unlink()

        attestation = {
            'schema': 'zaskaleta-clone-runtime-materialization-attestation-v1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source_provider': 's3_compatible',
            'manifest_key': manifest_key,
            'manifest_sha256': manifest_sha,
            'object_count': len(verified),
            'all_objects_verified': len(verified) == len(objects),
            'job_scoped_plaintext': True,
            'cleanup_required_after_job': True,
            'secret_values_exposed': False,
            'objects': verified,
        }
        (destination / '.clone_runtime_attestation.json').write_text(
            json.dumps(attestation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
        )
        print(json.dumps({
            'materialized': True,
            'object_count': len(verified),
            'manifest_sha256': manifest_sha,
            'attestation': str(destination / '.clone_runtime_attestation.json'),
            'secret_values_exposed': False,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


if __name__ == '__main__':
    sys.exit(main())
