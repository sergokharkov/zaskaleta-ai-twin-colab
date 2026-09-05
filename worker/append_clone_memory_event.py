#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'content' / 'clone_memory_policy_v1.json'
VALIDATOR = ROOT / 'worker' / 'validate_clone_memory_event.py'


def fail(msg: str) -> None:
    raise SystemExit(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description='Validate and append one Clone Memory v1 event to local persistent storage')
    ap.add_argument('--event', required=True)
    ap.add_argument('--storage-root', default=os.environ.get('AI_TWIN_STORAGE_MOUNT', '/workspace/zaskaleta-storage'))
    args = ap.parse_args()

    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    if policy.get('mode') != 'append_only_versioned':
        fail('Clone Memory policy is not append-only/versioned')

    event_path = Path(args.event).resolve()
    event = json.loads(event_path.read_text(encoding='utf-8'))

    # Validate with the canonical validator before any write.
    result = subprocess.run([sys.executable, str(VALIDATOR), '--event', str(event_path)], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        fail('Clone Memory event validation failed; refusing append')

    memory_root = Path(args.storage_root).resolve() / policy['root']
    memory_root.mkdir(parents=True, exist_ok=True)
    log_path = memory_root / 'events_v1.jsonl'

    memory_id = str(event.get('memory_id') or '').strip()
    if not memory_id:
        fail('memory_id required')

    # Prevent accidental duplicate append by memory_id.
    if log_path.exists():
        with log_path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    fail('Existing Clone Memory log is corrupted; refusing append')
                if existing.get('memory_id') == memory_id:
                    fail(f'duplicate_memory_id:{memory_id}')

    event.setdefault('recorded_at', datetime.now(timezone.utc).isoformat())
    event['append_only'] = True
    event['automatic_self_training'] = False

    encoded = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
    with log_path.open('a', encoding='utf-8') as fh:
        fh.write(encoded + '\n')
        fh.flush()
        os.fsync(fh.fileno())

    print(json.dumps({
        'schema': 'zaskaleta-clone-memory-append-result-v1',
        'appended': True,
        'memory_id': memory_id,
        'domain': event.get('domain'),
        'decision': event.get('decision'),
        'log_path': str(log_path),
        'training_auto_started': False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
