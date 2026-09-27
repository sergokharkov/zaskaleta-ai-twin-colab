#!/usr/bin/env python3
"""Redacted runtime attestation for the AI Clone production runtime.

Default execution performs no network calls. It reports only non-secret booleans,
provider classification, namespace policy, runtime mount state, revision metadata,
and whether forbidden Google Drive runtime variables are present.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.clone_isolation import assert_clone_s3_scope
from worker.materialize_clone_runtime_from_s3 import s3_client

CONFIG = ROOT / 'content' / 'storage_config.json'
CANONICAL_NAMESPACE = 'MASTER_CLONE/'
PROBE_PREFIX = 'MASTER_CLONE/TESTS/ISOLATION_PROBES'
PROBE_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
DRIVE_RUNTIME_ENV_NAMES = (
    'AI_TWIN_DRIVE_SYNC',
    'AI_TWIN_DRIVE_FOLDER_ID',
    'GOOGLE_APPLICATION_CREDENTIALS',
)


def classify_provider(endpoint: str) -> str:
    value = (endpoint or '').strip().lower()
    if not value:
        return 'NOT_VERIFIED'
    if 'r2.cloudflarestorage.com' in value:
        return 'cloudflare_r2'
    if 'your-objectstorage.com' in value:
        return 'hetzner_object_storage'
    return 's3_compatible_unknown'


def _env_present(env: Mapping[str, str], name: str | None) -> bool:
    return bool(isinstance(name, str) and name and (env.get(name) or '').strip())


def detect_revision() -> str:
    for name in ('AI_TWIN_RUNTIME_REVISION', 'GITHUB_SHA'):
        value = os.environ.get(name, '').strip()
        if value:
            return value
    try:
        proc = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 'NOT_VERIFIED'
    value = proc.stdout.strip() if proc.returncode == 0 else ''
    return value or 'NOT_VERIFIED'


def build_static_attestation(
    cfg: dict,
    env: Mapping[str, str],
    *,
    mount_path: Path,
    revision: str | None = None,
) -> dict:
    canonical = cfg.get('canonical_storage') or {}
    runtime = cfg.get('runtime') or {}

    env_names = {
        'bucket': canonical.get('bucket_env'),
        'endpoint': canonical.get('endpoint_env'),
        'region': canonical.get('region_env'),
        'access_key': canonical.get('access_key_env'),
        'secret_key': canonical.get('secret_key_env'),
    }
    configured = {name: _env_present(env, env_name) for name, env_name in env_names.items()}
    endpoint_name = env_names['endpoint']
    endpoint_value = env.get(endpoint_name, '') if isinstance(endpoint_name, str) else ''

    active_drive_env_names = [
        name for name in DRIVE_RUNTIME_ENV_NAMES if (env.get(name) or '').strip()
    ]

    mount = Path(mount_path)
    report = {
        'schema': 'zaskaleta-clone-runtime-provider-attestation-v1',
        'provider': classify_provider(endpoint_value),
        'canonical_namespace': CANONICAL_NAMESPACE,
        'canonical_bucket_configured': configured['bucket'],
        'runtime_credentials_complete': all(configured.values()),
        'configured_fields': configured,
        'runtime_credential_identity_verified': False,
        'runtime_revision': (revision or '').strip() or 'NOT_VERIFIED',
        'runtime_mount_expected': runtime.get('local_mount'),
        'runtime_mount_exists': mount.exists(),
        'runtime_mount_is_directory': mount.is_dir(),
        'runtime_mount_writable': os.access(mount, os.W_OK) if mount.exists() else False,
        'drive_runtime_env_clear': not active_drive_env_names,
        'active_drive_env_names': active_drive_env_names,
        'provider_read_verified': False,
        'provider_write_inside_namespace_verified': False,
        'foreign_bucket_negative_verified': False,
        'foreign_prefix_negative_verified': False,
        'outside_write_negative_verified': False,
        'network_action_performed': False,
        'secret_values_exposed': False,
        'hard_isolation_verified': False,
        'note': (
            'Static runtime attestation only. Provider authorization is NOT VERIFIED '
            'until the same production identity completes explicit provider probes.'
        ),
    }
    return report



def probe_canonical_read(client, bucket: str, manifest_key: str) -> dict:
    key = assert_clone_s3_scope(bucket, manifest_key)
    client.head_object(Bucket=bucket, Key=key)
    return {
        'provider_read_verified': True,
        'network_action_performed': True,
        'secret_values_exposed': False,
    }


def probe_controlled_write_inside(client, bucket: str, *, probe_id: str) -> dict:
    if not isinstance(probe_id, str) or not PROBE_ID_RE.fullmatch(probe_id):
        raise ValueError('probe_id must match [A-Za-z0-9._-]{1,128}')
    key = f'{PROBE_PREFIX}/{probe_id}.json'
    assert_clone_s3_scope(bucket, key)
    body = json.dumps(
        {
            'schema': 'zaskaleta-clone-isolation-probe-v1',
            'probe_id': probe_id,
            'non_secret': True,
        },
        sort_keys=True,
    ).encode('utf-8')
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType='application/json',
    )
    return {
        'provider_write_inside_namespace_verified': True,
        'probe_key': key,
        'delete_performed': False,
        'network_action_performed': True,
        'secret_values_exposed': False,
    }

def main() -> int:
    ap = argparse.ArgumentParser(
        description='Emit redacted AI Clone runtime/provider attestation'
    )
    ap.add_argument('--probe-read', action='store_true')
    ap.add_argument('--probe-write-inside', action='store_true')
    ap.add_argument('--probe-id', default='')
    ap.add_argument('--require-drive-free', action='store_true')
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    canonical = cfg.get('canonical_storage') or {}
    runtime = cfg.get('runtime') or {}
    mount_path = Path(
        os.environ.get('AI_TWIN_STORAGE', '').strip()
        or runtime.get('local_mount')
        or '/workspace/zaskaleta-storage'
    )
    report = build_static_attestation(
        cfg,
        os.environ,
        mount_path=mount_path,
        revision=detect_revision(),
    )

    if args.require_drive_free and not report['drive_runtime_env_clear']:
        report['runtime_attestation_error'] = 'active_drive_runtime_environment'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 4

    wants_network_probe = args.probe_read or args.probe_write_inside
    if not wants_network_probe:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    report['network_action_attempted'] = True
    if not report['runtime_credentials_complete']:
        report['provider_probe_error'] = 'runtime_storage_env_incomplete'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    if args.probe_write_inside and not args.probe_id:
        report['provider_probe_error'] = 'probe_id_required_for_controlled_write'
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    bucket_env = canonical.get('bucket_env')
    manifest_key = canonical.get('migration_manifest_key')
    bucket = os.environ.get(bucket_env, '').strip() if isinstance(bucket_env, str) else ''

    try:
        client = s3_client(cfg)
        if args.probe_read:
            report.update(probe_canonical_read(client, bucket, manifest_key))
        if args.probe_write_inside:
            report.update(
                probe_controlled_write_inside(
                    client,
                    bucket,
                    probe_id=args.probe_id,
                )
            )
    except Exception as exc:
        report['provider_probe_error_class'] = type(exc).__name__
        report['provider_probe_error_redacted'] = True
        report['secret_values_exposed'] = False
        report['hard_isolation_verified'] = False
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3

    report['note'] = (
        'Requested in-scope provider probes completed. Hard isolation remains '
        'NOT VERIFIED until provider identity and all required negative '
        'authorization checks are independently proven.'
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
