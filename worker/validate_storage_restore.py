#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate clone storage restore evidence before any production cutover')
    ap.add_argument('--evidence', required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.evidence).read_text(encoding='utf-8'))
    failures: list[str] = []
    required = [
        'manifest_sha256', 'object_count_expected', 'object_count_restored',
        'objects_verified', 'objects_failed', 'restore_started_at',
        'restore_completed_at', 'staging_location', 'decision'
    ]
    for key in required:
        if key not in doc:
            failures.append('missing:' + key)

    try:
        expected = int(doc.get('object_count_expected', -1))
        restored = int(doc.get('object_count_restored', -1))
        verified = int(doc.get('objects_verified', -1))
        failed = int(doc.get('objects_failed', -1))
        if expected <= 0 or restored != expected or verified != expected or failed != 0:
            failures.append('restore_counts_not_exact')
    except Exception:
        failures.append('restore_counts_invalid')

    manifest_sha = str(doc.get('manifest_sha256') or '')
    if len(manifest_sha) != 64 or any(c not in '0123456789abcdefABCDEF' for c in manifest_sha):
        failures.append('manifest_sha256_invalid')
    staging = str(doc.get('staging_location') or '')
    if not staging or 'production' in staging.casefold():
        failures.append('restore_not_isolated_staging')
    if doc.get('production_overwritten') is True:
        failures.append('production_overwrite_forbidden')
    if doc.get('all_decrypted_sha256_match') is not True:
        failures.append('decrypted_hash_verification_failed')
    if doc.get('decision') != 'PASS_TO_MANUAL_CUTOVER_REVIEW':
        failures.append('restore_decision_not_pass')

    report = {
        'schema': 'zaskaleta-storage-restore-evaluation-v1',
        'eligible_for_manual_cutover_review': not failures,
        'failures': failures,
        'decision': 'PASS_TO_MANUAL_CUTOVER_REVIEW' if not failures else 'BLOCK_RESTORE',
        'automatic_cutover_performed': False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
