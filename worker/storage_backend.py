#!/usr/bin/env python3
"""Provider-agnostic MASTER CLONE storage configuration validator.

No network calls, uploads, downloads, paid resources, or secret values are emitted.
The purpose is to validate the storage contract before a runtime uses S3-compatible
storage such as Hetzner Object Storage, Cloudflare R2, or MinIO.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'content' / 'storage_config.json'


def env_present(name: str) -> bool:
    return bool(os.environ.get(name, '').strip())


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    failures: list[str] = []

    if cfg.get('schema') != 'zaskaleta-storage-v2':
        failures.append('storage_schema_not_v2')

    canonical = cfg.get('canonical_storage') or {}
    if canonical.get('provider') != 's3_compatible':
        failures.append('canonical_storage_not_s3_compatible')
    if canonical.get('required_region_policy') != 'EU_ONLY':
        failures.append('eu_only_policy_missing')
    if canonical.get('versioning_required') is not True:
        failures.append('versioning_not_required')
    if canonical.get('required_client_side_encryption_for_biometrics') is not True:
        failures.append('client_side_encryption_not_required')

    encryption = cfg.get('encryption') or {}
    if encryption.get('client_side_encryption_required') is not True:
        failures.append('encryption_policy_weakened')
    if encryption.get('key_material_must_not_be_stored_with_objects') is not True:
        failures.append('key_separation_policy_weakened')
    if encryption.get('never_commit_keys') is not True:
        failures.append('key_commit_protection_missing')

    runtime = cfg.get('runtime') or {}
    if runtime.get('delete_temporary_plaintext_after_job') is not True:
        failures.append('plaintext_cleanup_not_required')
    if runtime.get('never_commit_private_biometric_media') is not True:
        failures.append('biometric_git_protection_missing')

    legacy = cfg.get('legacy_source_import') or {}
    migration = cfg.get('legacy_canonical_migration') or {}
    if legacy.get('production_dependency') is not False:
        failures.append('legacy_drive_still_production_dependency')
    if migration.get('production_dependency') is not False:
        failures.append('google_drive_still_production_dependency')

    env_names = {
        'bucket': canonical.get('bucket_env'),
        'endpoint': canonical.get('endpoint_env'),
        'region': canonical.get('region_env'),
        'access_key': canonical.get('access_key_env'),
        'secret_key': canonical.get('secret_key_env'),
        'encryption_key': encryption.get('key_env'),
    }
    configured = {k: bool(name and env_present(name)) for k, name in env_names.items()}

    report = {
        'schema': 'zaskaleta-storage-runtime-readiness-v1',
        'policy_valid': not failures,
        'runtime_credentials_complete': all(configured.values()),
        'configured_fields': configured,
        'secret_values_exposed': False,
        'network_action_performed': False,
        'google_drive_production_dependency': False,
        'failures': failures,
        'note': 'Policy validation only. This script never connects to an object-storage provider and never prints secret values.'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
