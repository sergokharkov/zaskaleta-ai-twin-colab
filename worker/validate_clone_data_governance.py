#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    return json.loads((ROOT / 'content' / name).read_text(encoding='utf-8'))


def main() -> int:
    failures: list[str] = []
    storage = load('storage_config.json')
    migration = load('storage_migration_policy_v1.json')
    memory = load('clone_memory_policy_v1.json')
    restore = load('storage_restore_policy_v1.json')
    retention = load('storage_retention_policy_v1.json')
    cutover = load('storage_cutover_policy_v1.json')

    if storage.get('canonical_storage', {}).get('provider') != 's3_compatible':
        failures.append('canonical_storage_not_s3')
    if storage.get('canonical_storage', {}).get('required_region_policy') != 'EU_ONLY':
        failures.append('eu_only_missing')
    if storage.get('encryption', {}).get('client_side_encryption_required') is not True:
        failures.append('client_side_encryption_not_required')
    if storage.get('legacy_canonical_migration', {}).get('production_dependency') is not False:
        failures.append('google_drive_still_production_dependency')

    mrules = migration.get('rules') or {}
    for key in ('dry_run_default', 'manual_cutover_required', 'automatic_source_deletion_forbidden',
                'automatic_google_drive_cutover_forbidden', 'source_and_destination_sha256_must_match_after_decryption',
                'encryption_key_must_not_be_written_to_manifest', 'secret_values_must_not_be_logged',
                'partial_migration_must_not_mark_complete', 'failed_objects_must_remain_retryable'):
        if mrules.get(key) is not True:
            failures.append('migration_rule_weakened:' + key)

    mem_rules = memory.get('rules') or {}
    for key in ('stable_memory_entries_immutable', 'append_only_event_log_required',
                'manual_promotion_required', 'automatic_self_training_forbidden',
                'raw_biometric_payloads_forbidden_in_memory_json', 'secrets_forbidden_in_memory'):
        if mem_rules.get(key) is not True:
            failures.append('memory_rule_weakened:' + key)
    if 'failure_memory' not in (memory.get('domains') or []):
        failures.append('failure_memory_missing')

    rrules = restore.get('rules') or {}
    for key in ('restore_test_required', 'restore_to_isolated_staging_first',
                'decrypted_sha256_must_match_original', 'production_overwrite_forbidden',
                'manual_cutover_required_after_restore'):
        if rrules.get(key) is not True:
            failures.append('restore_rule_weakened:' + key)

    keep = retention.get('rules') or {}
    for key in ('stable_releases_never_auto_delete', 'approved_identity_assets_never_auto_delete',
                'master_voice_never_auto_delete', 'canonical_identity_never_auto_delete',
                'automatic_deletion_of_biometric_source_assets_forbidden'):
        if keep.get(key) is not True:
            failures.append('retention_rule_weakened:' + key)
    for klass in ('APPROVED', 'VERSIONS', 'RELEASES', 'FINAL', 'MEMORY'):
        if (retention.get('classes') or {}).get(klass, {}).get('auto_delete') is not False:
            failures.append('protected_retention_class_auto_delete:' + klass)

    crules = cutover.get('rules') or {}
    for key in ('manual_cutover_required', 'automatic_cutover_forbidden', 'all_objects_must_be_verified',
                'source_and_decrypted_destination_sha256_must_match', 'restore_test_must_pass',
                'backup_copy_must_be_verified', 'source_deletion_forbidden_during_cutover',
                'rollback_plan_required'):
        if crules.get(key) is not True:
            failures.append('cutover_rule_weakened:' + key)

    report = {
        'schema': 'zaskaleta-clone-data-governance-evaluation-v1',
        'policy_valid': not failures,
        'google_drive_required_for_production': False,
        'automatic_cutover_allowed': False,
        'automatic_self_training_allowed': False,
        'automatic_biometric_deletion_allowed': False,
        'failures': failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
