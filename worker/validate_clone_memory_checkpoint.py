#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'content' / 'clone_memory_checkpoint_policy_v1.json'
HEX64 = re.compile(r'^[0-9a-fA-F]{64}$')
FORBIDDEN_KEYS = {
    'password','passwd','secret','secret_key','access_key','access_key_id','secret_access_key',
    'encryption_key','api_key','token','bearer_token','private_key','credential','credentials','client_secret',
    'raw_biometric_payload','raw_identity_payload','raw_voice_payload','raw_motion_payload','raw_talking_payload'
}


def norm(key: object) -> str:
    return str(key).strip().lower().replace('-', '_').replace(' ', '_')


def walk(value: object, path: str = '$'):
    if isinstance(value, dict):
        for key, child in value.items():
            yield f'{path}.{key}', norm(key), child
            yield from walk(child, f'{path}.{key}')
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from walk(child, f'{path}[{i}]')


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate one externally anchored Clone Memory checkpoint record')
    ap.add_argument('--checkpoint', required=True)
    args = ap.parse_args()

    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    doc = json.loads(Path(args.checkpoint).read_text(encoding='utf-8'))
    failures: list[str] = []

    if policy.get('schema') != 'zaskaleta-clone-memory-checkpoint-policy-v1':
        failures.append('invalid_checkpoint_policy_schema')
    required = set(policy.get('required_fields') or [])
    missing = sorted(required - set(doc)) if isinstance(doc, dict) else sorted(required)
    if missing:
        failures.append('missing_fields:' + ','.join(missing))

    if not isinstance(doc, dict):
        failures.append('checkpoint_root_must_be_object')
    else:
        if not HEX64.fullmatch(str(doc.get('memory_chain_head_sha256') or '')):
            failures.append('invalid_memory_chain_head_sha256')
        if not HEX64.fullmatch(str(doc.get('source_log_sha256') or '')):
            failures.append('invalid_source_log_sha256')
        count = doc.get('event_count')
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            failures.append('invalid_event_count')
        if not isinstance(doc.get('storage_object_key'), str) or not doc.get('storage_object_key', '').strip():
            failures.append('storage_object_key_required')
        if not isinstance(doc.get('storage_version_id'), str) or not doc.get('storage_version_id', '').strip():
            failures.append('storage_version_id_required')
        if doc.get('immutable_retention_confirmed') is not True:
            failures.append('immutable_retention_not_confirmed')
        if doc.get('independent_backup_confirmed') is not True:
            failures.append('independent_backup_not_confirmed')
        if doc.get('manual_approval') is not True:
            failures.append('manual_checkpoint_approval_required')
        for path, key, value in walk(doc):
            if key in FORBIDDEN_KEYS and value is not None:
                failures.append('forbidden_sensitive_or_biometric_field:' + path)

    report = {
        'schema': 'zaskaleta-clone-memory-checkpoint-evaluation-v1',
        'valid': not failures,
        'trusted_external_anchor_eligible': not failures,
        'network_action_performed': False,
        'secret_values_exposed': False,
        'failures': failures,
        'decision': 'CHECKPOINT_VALID' if not failures else 'CHECKPOINT_REJECTED',
        'note': 'Validation alone does not prove the S3 version, immutable retention, or independent backup exist; those must be verified during real external connection.'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
