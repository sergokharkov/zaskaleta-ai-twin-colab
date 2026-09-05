#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'content' / 'storage_cutover_policy_v1.json'


def sha_ok(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value)


def exact_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate evidence required before manual clone storage cutover')
    ap.add_argument('--evidence', required=True)
    args = ap.parse_args()

    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    doc = json.loads(Path(args.evidence).read_text(encoding='utf-8'))
    failures: list[str] = []

    if policy.get('schema') != 'zaskaleta-storage-cutover-policy-v1':
        failures.append('invalid_cutover_policy_schema')
    if not isinstance(doc, dict):
        failures.append('evidence_root_must_be_object')
        doc = {}

    required = list(policy.get('required_evidence') or [])
    for key in required:
        if key not in doc:
            failures.append('missing:' + key)

    if not sha_ok(doc.get('migration_manifest_sha256')):
        failures.append('migration_manifest_sha256_invalid')
    if not sha_ok(doc.get('restore_evaluation_sha256')):
        failures.append('restore_evaluation_sha256_invalid')

    expected = doc.get('expected_object_count')
    verified = doc.get('verified_object_count')
    failed = doc.get('failed_object_count')
    if not all(exact_nonnegative_int(v) for v in (expected, verified, failed)):
        failures.append('migration_object_counts_invalid_type')
    elif expected <= 0 or verified != expected or failed != 0:
        failures.append('migration_object_counts_not_exact')

    backup = doc.get('backup_verification')
    if not isinstance(backup, dict):
        failures.append('backup_verification_invalid')
    elif backup.get('verified') is not True or backup.get('independent_credentials') is not True:
        failures.append('backup_not_independently_verified')

    runtime = doc.get('runtime_storage_readiness')
    if not isinstance(runtime, dict):
        failures.append('runtime_storage_readiness_invalid')
    else:
        for key in ('credentials_complete', 'client_side_encryption_enabled', 'versioning_enabled', 'eu_region_policy_passed'):
            if runtime.get(key) is not True:
                failures.append('runtime_not_ready:' + key)

    checkpoint = doc.get('memory_checkpoint_verification')
    checkpoint_fields = list(policy.get('memory_checkpoint_verification_required_fields') or [])
    if not isinstance(checkpoint, dict):
        failures.append('memory_checkpoint_verification_invalid')
    else:
        for key in checkpoint_fields:
            if key not in checkpoint:
                failures.append('memory_checkpoint_missing:' + key)
        if not sha_ok(checkpoint.get('checkpoint_evaluation_sha256')):
            failures.append('memory_checkpoint_evaluation_sha256_invalid')
        strict_true = (
            'checkpoint_validation_passed',
            'storage_version_verified',
            'immutable_retention_verified',
            'independent_backup_verified',
            'manual_approval_verified',
        )
        for key in strict_true:
            if checkpoint.get(key) is not True:
                failures.append('memory_checkpoint_not_verified:' + key)

    if not isinstance(doc.get('rollback_target'), str) or not doc.get('rollback_target', '').strip():
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
        'trusted_clone_memory_checkpoint_required': True,
        'trusted_clone_memory_checkpoint_verified': not any(f.startswith('memory_checkpoint_') for f in failures),
        'automatic_cutover_allowed': False,
        'source_deletion_allowed': False,
        'failures': failures,
        'decision': 'PASS_TO_MANUAL_CUTOVER' if not failures else 'BLOCK_CUTOVER',
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
