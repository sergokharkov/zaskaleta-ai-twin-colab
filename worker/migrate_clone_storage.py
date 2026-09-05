#!/usr/bin/env python3
"""Safe mounted-source -> encrypted S3-compatible migration for MASTER CLONE.

Defaults to dry-run. It never deletes source data, never changes storage_config.json,
and never performs cutover. Actual upload requires --execute plus complete runtime
credentials. Private assets are encrypted client-side with streaming AES-256-GCM.
Destination verification downloads the encrypted object, decrypts it locally, and
compares the decrypted SHA-256 and byte length with the source before marking success.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "content" / "storage_migration_policy_v1.json"
STORAGE = ROOT / "content" / "storage_config.json"

PRIVATE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".m4a", ".mp4", ".mov", ".m4v", ".webm"}
CHUNK_SIZE = 16 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_aes256_key(raw: str) -> bytes:
    """Require an actual 256-bit key encoded as strict base64; never derive from a passphrase."""
    try:
        decoded = base64.b64decode(raw.strip(), validate=True)
    except Exception as exc:
        raise RuntimeError("Encryption key must be strict base64 for exactly 32 raw bytes") from exc
    if len(decoded) != 32:
        raise RuntimeError("Encryption key must decode to exactly 32 bytes (AES-256)")
    return decoded


def crypto_primitives():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise RuntimeError("cryptography package is required for --execute migration") from exc
    return Cipher, algorithms, modes


def encrypt_file_streaming(src: Path, dst: Path, key: bytes) -> tuple[str, str, int]:
    Cipher, algorithms, modes = crypto_primitives()
    nonce = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    written = 0
    with src.open("rb") as fin, dst.open("wb") as fout:
        for chunk in iter(lambda: fin.read(CHUNK_SIZE), b""):
            encrypted = encryptor.update(chunk)
            if encrypted:
                fout.write(encrypted)
                written += len(encrypted)
        final = encryptor.finalize()
        if final:
            fout.write(final)
            written += len(final)
        fout.flush()
        os.fsync(fout.fileno())
    return (
        base64.b64encode(nonce).decode("ascii"),
        base64.b64encode(encryptor.tag).decode("ascii"),
        written,
    )


def verify_downloaded_ciphertext(
    encrypted_path: Path,
    key: bytes,
    nonce_b64: str,
    tag_b64: str,
    expected_sha256: str,
    expected_size: int,
) -> tuple[bool, str, int]:
    Cipher, algorithms, modes = crypto_primitives()
    nonce = base64.b64decode(nonce_b64, validate=True)
    tag = base64.b64decode(tag_b64, validate=True)
    if len(nonce) != 12 or len(tag) != 16:
        raise RuntimeError("Invalid AES-GCM nonce/tag length in migration state")
    decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
    h = hashlib.sha256()
    plain_size = 0
    with encrypted_path.open("rb") as fin:
        for chunk in iter(lambda: fin.read(CHUNK_SIZE), b""):
            plain = decryptor.update(chunk)
            if plain:
                h.update(plain)
                plain_size += len(plain)
        final = decryptor.finalize()
        if final:
            h.update(final)
            plain_size += len(final)
    actual_sha = h.hexdigest()
    return actual_sha == expected_sha256 and plain_size == expected_size, actual_sha, plain_size


def s3_client(cfg: dict):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 package is required for --execute migration") from exc
    c = cfg["canonical_storage"]
    return boto3.client(
        "s3",
        endpoint_url=os.environ[c["endpoint_env"]],
        region_name=os.environ[c["region_env"]],
        aws_access_key_id=os.environ[c["access_key_env"]],
        aws_secret_access_key=os.environ[c["secret_key_env"]],
    )


def required_env(cfg: dict) -> dict[str, str]:
    c = cfg["canonical_storage"]
    e = cfg["encryption"]
    return {
        "bucket": c["bucket_env"],
        "endpoint": c["endpoint_env"],
        "region": c["region_env"],
        "access_key": c["access_key_env"],
        "secret_key": c["secret_key_env"],
        "encryption_key": e["key_env"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", required=True, help="Read-only mounted source folder containing MASTER_CLONE")
    ap.add_argument("--manifest-out", required=True)
    ap.add_argument("--execute", action="store_true", help="Actually encrypt, upload, download and verify. Default is dry-run.")
    args = ap.parse_args()

    source_root = Path(args.source_root).resolve()
    manifest_out = Path(args.manifest_out).resolve()
    policy = load_json(POLICY)
    storage = load_json(STORAGE)

    if policy.get("schema") != "zaskaleta-storage-migration-policy-v1":
        raise SystemExit("Invalid migration policy")
    if storage.get("schema") != "zaskaleta-storage-v2":
        raise SystemExit("Storage v2 configuration required")
    if not source_root.is_dir():
        raise SystemExit(f"Source root not found: {source_root}")

    rules = policy.get("rules") or {}
    if rules.get("source_and_destination_sha256_must_match_after_decryption") is not True:
        raise SystemExit("Migration policy must require decrypted destination SHA-256 verification")

    env_map = required_env(storage)
    configured = {name: bool(os.environ.get(env_name, "").strip()) for name, env_name in env_map.items()}
    if args.execute and not all(configured.values()):
        missing = [name for name, ok in configured.items() if not ok]
        raise SystemExit("Execution blocked; missing runtime configuration: " + ", ".join(missing))

    files = sorted(p for p in source_root.rglob("*") if p.is_file())
    objects = []
    for p in files:
        rel = safe_rel(source_root, p)
        objects.append({
            "relative_path": rel,
            "size_bytes": p.stat().st_size,
            "source_sha256": sha256_file(p),
            "private_asset": p.suffix.lower() in PRIVATE_EXTS,
            "encrypted_object_key": "MASTER_CLONE_ENCRYPTED/" + rel + ".enc",
            "encryption_nonce_b64": None,
            "encryption_tag_b64": None,
            "encrypted_size_bytes": None,
            "upload_status": "PENDING" if args.execute else "DRY_RUN",
            "verify_status": "PENDING" if args.execute else "NOT_RUN",
            "verified_decrypted_sha256": None,
            "verified_decrypted_size_bytes": None,
        })

    if args.execute:
        client = s3_client(storage)
        bucket = os.environ[env_map["bucket"]]
        aes_key = parse_aes256_key(os.environ[env_map["encryption_key"]])

        for obj in objects:
            src = source_root / obj["relative_path"]
            encrypted_tmp = None
            downloaded_tmp = None
            try:
                with tempfile.NamedTemporaryFile(prefix="zaskaleta-enc-", suffix=".bin", delete=False) as tf:
                    encrypted_tmp = Path(tf.name)
                nonce_b64, tag_b64, encrypted_size = encrypt_file_streaming(src, encrypted_tmp, aes_key)
                obj["encryption_nonce_b64"] = nonce_b64
                obj["encryption_tag_b64"] = tag_b64
                obj["encrypted_size_bytes"] = encrypted_size

                client.upload_file(
                    str(encrypted_tmp),
                    bucket,
                    obj["encrypted_object_key"],
                    ExtraArgs={
                        "Metadata": {
                            "source-sha256": obj["source_sha256"],
                            "source-size": str(obj["size_bytes"]),
                            "encryption": "AES-256-GCM",
                        }
                    },
                )
                obj["upload_status"] = "UPLOADED"

                head = client.head_object(Bucket=bucket, Key=obj["encrypted_object_key"])
                remote_size = int(head.get("ContentLength", -1))
                metadata = head.get("Metadata") or {}
                if remote_size != encrypted_size:
                    obj["verify_status"] = "FAILED_ENCRYPTED_SIZE"
                    raise RuntimeError(f"Destination encrypted size mismatch for {obj['relative_path']}")
                if metadata.get("source-sha256") != obj["source_sha256"] or metadata.get("source-size") != str(obj["size_bytes"]):
                    obj["verify_status"] = "FAILED_METADATA"
                    raise RuntimeError(f"Destination metadata mismatch for {obj['relative_path']}")

                with tempfile.NamedTemporaryFile(prefix="zaskaleta-download-", suffix=".bin", delete=False) as tf:
                    downloaded_tmp = Path(tf.name)
                client.download_file(bucket, obj["encrypted_object_key"], str(downloaded_tmp))

                ok, verified_sha, verified_size = verify_downloaded_ciphertext(
                    downloaded_tmp,
                    aes_key,
                    nonce_b64,
                    tag_b64,
                    obj["source_sha256"],
                    int(obj["size_bytes"]),
                )
                obj["verified_decrypted_sha256"] = verified_sha
                obj["verified_decrypted_size_bytes"] = verified_size
                if not ok:
                    obj["verify_status"] = "FAILED_DECRYPTED_SHA256_OR_SIZE"
                    raise RuntimeError(f"Destination decrypted verification failed for {obj['relative_path']}")
                obj["verify_status"] = "VERIFIED_DECRYPTED_SHA256"
            finally:
                for tmp_path in (encrypted_tmp, downloaded_tmp):
                    if tmp_path is not None and tmp_path.exists():
                        os.remove(tmp_path)

    complete = bool(objects) and all(o["verify_status"] == "VERIFIED_DECRYPTED_SHA256" for o in objects) if args.execute else False
    manifest = {
        "schema": "zaskaleta-storage-migration-manifest-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "EXECUTE" if args.execute else "DRY_RUN",
        "source_provider": "google_drive_or_mounted_source",
        "destination_provider": "s3_compatible",
        "source_deleted": False,
        "cutover_performed": False,
        "manual_cutover_required": True,
        "secret_values_exposed": False,
        "plaintext_temp_files_created": False,
        "destination_verified_after_decryption": bool(args.execute and complete),
        "object_count": len(objects),
        "migration_complete": complete,
        "objects": objects,
        "note": "Cutover is intentionally separate. Source data is never deleted automatically; migration completes only after downloaded ciphertext decrypts to the exact source SHA-256 and size."
    }
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": manifest["mode"],
        "object_count": manifest["object_count"],
        "migration_complete": manifest["migration_complete"],
        "destination_verified_after_decryption": manifest["destination_verified_after_decryption"],
        "source_deleted": False,
        "cutover_performed": False,
        "secret_values_exposed": False,
        "manifest": str(manifest_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
