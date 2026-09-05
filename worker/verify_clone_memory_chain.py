#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ZERO_HASH = '0' * 64


def canonical_event_payload(event: dict) -> bytes:
    payload = dict(event)
    payload.pop('event_hash', None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def compute_hash(previous_hash: str, event: dict) -> str:
    h = hashlib.sha256()
    h.update(previous_hash.encode('ascii'))
    h.update(b'\n')
    h.update(canonical_event_payload(event))
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description='Verify tamper-evident Clone Memory v1 JSONL hash chain')
    ap.add_argument('--log', required=True)
    args = ap.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(json.dumps({'schema':'zaskaleta-clone-memory-chain-evaluation-v1','valid':True,'entries':0,'head_hash':ZERO_HASH,'failures':[]}, indent=2))
        return 0

    previous = ZERO_HASH
    failures: list[str] = []
    count = 0
    seen_ids: set[str] = set()

    with path.open('r', encoding='utf-8') as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            count += 1
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                failures.append(f'line_{lineno}:invalid_json')
                break

            memory_id = str(event.get('memory_id') or '')
            if not memory_id:
                failures.append(f'line_{lineno}:missing_memory_id')
            elif memory_id in seen_ids:
                failures.append(f'line_{lineno}:duplicate_memory_id:{memory_id}')
            else:
                seen_ids.add(memory_id)

            declared_prev = str(event.get('previous_event_hash') or '')
            if declared_prev != previous:
                failures.append(f'line_{lineno}:previous_hash_mismatch')

            declared_hash = str(event.get('event_hash') or '')
            expected_hash = compute_hash(previous, event)
            if declared_hash != expected_hash:
                failures.append(f'line_{lineno}:event_hash_mismatch')

            previous = declared_hash if len(declared_hash) == 64 else expected_hash

    report = {
        'schema': 'zaskaleta-clone-memory-chain-evaluation-v1',
        'valid': not failures,
        'entries': count,
        'head_hash': previous,
        'failures': failures,
        'decision': 'CHAIN_VALID' if not failures else 'CHAIN_INVALID'
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
