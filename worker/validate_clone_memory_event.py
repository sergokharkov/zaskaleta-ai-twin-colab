#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOMAINS = {
    'identity_memory', 'voice_memory', 'motion_memory', 'talking_memory',
    'failure_memory', 'release_history', 'quality_history', 'provenance'
}
DECISIONS = {'CANDIDATE', 'APPROVED', 'REJECTED', 'SUPERSEDED'}
REQUIRED = {
    'memory_id', 'domain', 'version', 'source_type', 'source_ref',
    'source_sha256', 'created_at', 'decision', 'notes'
}
SENSITIVE_KEYS = {
    'password', 'passwd', 'secret', 'secret_key', 'access_key', 'access_key_id',
    'secret_access_key', 'encryption_key', 'api_key', 'token', 'bearer_token',
    'private_key', 'credential', 'credentials', 'client_secret'
}
RAW_BIOMETRIC_KEYS = {
    'raw_biometric_payload', 'raw_identity_payload', 'raw_voice_payload',
    'raw_motion_payload', 'raw_talking_payload'
}
SECRET_VALUE_PATTERNS = {
    'private_key_material': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'aws_access_key_like': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'github_pat_like': re.compile(r'\bghp_[A-Za-z0-9]{36}\b'),
    'github_fine_grained_pat_like': re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'),
}


def normalize_key(key: object) -> str:
    return str(key).strip().lower().replace('-', '_').replace(' ', '_')


def walk(value: object, path: str = '$'):
    if isinstance(value, dict):
        for key, child in value.items():
            key_norm = normalize_key(key)
            child_path = f'{path}.{key}'
            yield child_path, key_norm, child
            yield from walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f'{path}[{index}]')


def scan_nested_safety(doc: dict) -> list[str]:
    failures: list[str] = []
    seen: set[str] = set()
    for path, key_norm, value in walk(doc):
        if key_norm in RAW_BIOMETRIC_KEYS and value is not None:
            marker = f'raw_biometric_payload_forbidden:{path}'
            if marker not in seen:
                failures.append(marker); seen.add(marker)
        if key_norm in SENSITIVE_KEYS:
            marker = f'secret_field_forbidden:{path}'
            if marker not in seen:
                failures.append(marker); seen.add(marker)
        if isinstance(value, str):
            for pattern_name, pattern in SECRET_VALUE_PATTERNS.items():
                if pattern.search(value):
                    marker = f'secret_value_forbidden:{pattern_name}:{path}'
                    if marker not in seen:
                        failures.append(marker); seen.add(marker)
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate one append-only Clone Memory v1 event')
    ap.add_argument('--event', required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.event).read_text(encoding='utf-8'))
    if not isinstance(doc, dict):
        raise SystemExit('Clone Memory event root must be a JSON object')

    failures: list[str] = []
    missing = sorted(REQUIRED - set(doc))
    if missing:
        failures.append('missing_fields:' + ','.join(missing))
    if doc.get('domain') not in DOMAINS:
        failures.append('invalid_domain')
    if doc.get('decision') not in DECISIONS:
        failures.append('invalid_decision')
    sha = str(doc.get('source_sha256') or '')
    if len(sha) != 64 or any(c not in '0123456789abcdefABCDEF' for c in sha):
        failures.append('invalid_source_sha256')

    failures.extend(scan_nested_safety(doc))

    if doc.get('decision') == 'APPROVED' and doc.get('manual_approval') is not True:
        failures.append('approved_memory_requires_manual_approval')
    if doc.get('decision') == 'REJECTED' and doc.get('eligible_for_training') is True:
        failures.append('rejected_memory_cannot_train')
    if doc.get('automatic_self_training') is True:
        failures.append('automatic_self_training_forbidden')

    report = {
        'schema': 'zaskaleta-clone-memory-event-evaluation-v2',
        'valid': not failures,
        'recursive_secret_scan_enforced': True,
        'recursive_raw_biometric_scan_enforced': True,
        'secret_value_signature_scan_enforced': True,
        'failures': failures,
        'decision': 'ACCEPT_APPEND_ONLY_EVENT' if not failures else 'REJECT_MEMORY_EVENT'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
