#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED = {
    'relative_path', 'size_bytes', 'source_sha256', 'encrypted_object_key',
    'encryption_nonce_b64', 'upload_status', 'verify_status'
}


def main() -> int:
    ap = argparse.ArgumentParser(description='Fail-closed offline validator for clone storage migration manifests')
    ap.add_argument('--manifest', required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    objects = doc.get('objects') or []
    failures: list[str] = []
    seen_paths = set()
    seen_keys = set()

    if not isinstance(objects, list) or not objects:
        failures.append('manifest_has_no_objects')
    for i, row in enumerate(objects):
        missing = sorted(REQUIRED - set(row))
        if missing:
            failures.append(f'object_{i}_missing_fields:' + ','.join(missing))
            continue
        rel = row.get('relative_path')
        key = row.get('encrypted_object_key')
        sha = str(row.get('source_sha256') or '')
        if not rel or rel in seen_paths:
            failures.append(f'object_{i}_relative_path_invalid_or_duplicate')
        seen_paths.add(rel)
        if not key or key in seen_keys:
            failures.append(f'object_{i}_encrypted_key_invalid_or_duplicate')
        seen_keys.add(key)
        if len(sha) != 64 or any(c not in '0123456789abcdefABCDEF' for c in sha):
            failures.append(f'object_{i}_source_sha256_invalid')
        if int(row.get('size_bytes') or 0) <= 0:
            failures.append(f'object_{i}_size_invalid')
        if row.get('upload_status') not in {'UPLOADED', 'VERIFIED'}:
            failures.append(f'object_{i}_upload_not_complete')
        if row.get('verify_status') != 'VERIFIED':
            failures.append(f'object_{i}_not_verified')
        for forbidden in ('encryption_key', 'secret_key', 'access_key', 'password'):
            if forbidden in row:
                failures.append(f'object_{i}_secret_field_forbidden:{forbidden}')

    expected = doc.get('expected_object_count')
    if expected is not None and int(expected) != len(objects):
        failures.append('expected_object_count_mismatch')

    report = {
        'schema': 'zaskaleta-storage-migration-manifest-evaluation-v1',
        'eligible_for_restore_test': not failures,
        'object_count': len(objects),
        'failures': failures,
        'decision': 'PASS_TO_RESTORE_TEST' if not failures else 'BLOCK_MIGRATION',
        'note': 'Offline manifest validation only; no network calls and no cutover side effects.'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
