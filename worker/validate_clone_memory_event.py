#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate one append-only Clone Memory v1 event')
    ap.add_argument('--event', required=True)
    args = ap.parse_args()

    doc = json.loads(Path(args.event).read_text(encoding='utf-8'))
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
    if doc.get('raw_biometric_payload') is not None:
        failures.append('raw_biometric_payload_forbidden')
    forbidden_keys = {'password', 'secret', 'secret_key', 'access_key', 'encryption_key', 'token'}
    if forbidden_keys.intersection(doc):
        failures.append('secret_field_forbidden')
    if doc.get('decision') == 'APPROVED' and doc.get('manual_approval') is not True:
        failures.append('approved_memory_requires_manual_approval')
    if doc.get('decision') == 'REJECTED' and doc.get('eligible_for_training') is True:
        failures.append('rejected_memory_cannot_train')
    if doc.get('automatic_self_training') is True:
        failures.append('automatic_self_training_forbidden')

    report = {
        'schema': 'zaskaleta-clone-memory-event-evaluation-v1',
        'valid': not failures,
        'failures': failures,
        'decision': 'ACCEPT_APPEND_ONLY_EVENT' if not failures else 'REJECT_MEMORY_EVENT'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
