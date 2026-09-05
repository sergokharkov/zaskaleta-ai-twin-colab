#!/usr/bin/env python3
"""Safe Google-Drive/source-folder -> encrypted S3-compatible migration for MASTER CLONE.

Defaults to dry-run. It never deletes source data, never changes storage_config.json,
and never performs cutover. Actual upload requires --execute plus complete runtime
credentials. Biometric/private assets are encrypted client-side with AES-256-GCM.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "content" / "storage_migration_policy_v1.json"
STORAGE = ROOT / "content" / "storage_config.json"

PRIVATE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".m4a", ".mp4", ".mov", ".m4v", ".webm"}


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


def derive_aes_key(raw: str) -> bytes:
    # Accept either 32-byte base64 key or a passphrase-like secret and derive a stable 32-byte key.
    try:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_bytes(data: bytes, key: bytes) -> tuple[bytes, str]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError("cryptography package is required for --execute migration") from exc
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return ciphertext, base64.b64encode(nonce).decode("ascii")


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
    ap.add_argument("--execute", action="store_true", help="Actually encrypt and upload. Default is dry-run.")
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
            "upload_status": "PENDING" if args.execute else "DRY_RUN",
            "verify_status": "PENDING" if args.execute else "NOT_RUN",
        })

    client = None
    bucket = None
    aes_key = None
    if args.execute:
        client = s3_client(storage)
        bucket = os.environ[env_map["bucket"]]
        aes_key = derive_aes_key(os.environ[env_map["encryption_key"]])

        for obj in objects:
            src = source_root / obj["relative_path"]
            data = src.read_bytes()
            encrypted, nonce_b64 = encrypt_bytes(data, aes_key)
            obj["encryption_nonce_b64"] = nonce_b64
            client.put_object(
                Bucket=bucket,
                Key=obj["encrypted_object_key"],
                Body=encrypted,
                Metadata={"source-sha256": obj["source_sha256"], "encryption": "AES-256-GCM"},
            )
            head = client.head_object(Bucket=bucket, Key=obj["encrypted_object_key"])
            remote_sha = (head.get("Metadata") or {}).get("source-sha256")
            if remote_sha != obj["source_sha256"]:
                obj["upload_status"] = "UPLOADED"
                obj["verify_status"] = "FAILED_METADATA_SHA256"
                raise RuntimeError(f"Destination verification failed for {obj['relative_path']}")
            obj["upload_status"] = "UPLOADED"
            obj["verify_status"] = "VERIFIED_METADATA_SHA256"

    complete = bool(objects) and all(o["verify_status"] == "VERIFIED_METADATA_SHA256" for o in objects) if args.execute else False
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
        "object_count": len(objects),
        "migration_complete": complete,
        "objects": objects,
        "note": "Cutover is intentionally separate. Google Drive/source data is never deleted automatically."
    }
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": manifest["mode"],
        "object_count": manifest["object_count"],
        "migration_complete": manifest["migration_complete"],
        "source_deleted": False,
        "cutover_performed": False,
        "manifest": str(manifest_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
