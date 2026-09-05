#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def sha_ok(value) -> bool:
    text = str(value or '')
    return len(text) == 64 and all(c in '0123456789abcdefABCDEF' for c in text)


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate evidence required before manual clone storage cutover')
    ap.add_argument('--evidence', required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.evidence).read_text(encoding='utf-8'))
    failures: list[str] = []
    required = [
        'migration_manifest_sha256', 'expected_object_count', 'verified_object_count',
        'failed_object_count', 'restore_evaluation_sha256', 'backup_verification',
        'runtime_storage_readiness', 'rollback_target', 'decision'
    ]
    for key in required:
        if key not in doc:
            failures.append('missing:' + key)

    if not sha_ok(doc.get('migration_manifest_sha256')):
        failures.append('migration_manifest_sha256_invalid')
    if not sha_ok(doc.get('restore_evaluation_sha256')):
        failures.append('restore_evaluation_sha256_invalid')

    try:
        expected = int(doc.get('expected_object_count', -1))
        verified = int(doc.get('verified_object_count', -1))
        failed = int(doc.get('failed_object_count', -1))
        if expected <= 0 or verified != expected or failed != 0:
            failures.append('migration_object_counts_not_exact')
    except Exception:
        failures.append('migration_object_counts_invalid')

    backup = doc.get('backup_verification') or {}
    if backup.get('verified') is not True or backup.get('independent_credentials') is not True:
        failures.append('backup_not_independently_verified')

    runtime = doc.get('runtime_storage_readiness') or {}
    for key in ('credentials_complete', 'client_side_encryption_enabled', 'versioning_enabled', 'eu_region_policy_passed'):
        if runtime.get(key) is not True:
            failures.append('runtime_not_ready:' + key)

    if not str(doc.get('rollback_target') or '').strip():
        failures.append('rollback_target_missing')
    if doc.get('source_deleted') is True:
        failures.append('source_deletion_forbidden_during_cutover')
    if doc.get('automatic_cutover_performed') is True:
        failures.append('automatic_cutover_forbidden')
    if doc.get('decision') != 'PASS_TO_MANUAL_CUTOVER':
        failures.append('decision_not_pass')

    report = {
        'schema': 'zaskaleta-storage-cutover-evaluation-v1',
        'eligible_for_manual_cutover': not failures,
        'automatic_cutover_allowed': False,
        'source_deletion_allowed': False,
        'failures': failures,
        'decision': 'PASS_TO_MANUAL_CUTOVER' if not failures else 'BLOCK_CUTOVER',
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
