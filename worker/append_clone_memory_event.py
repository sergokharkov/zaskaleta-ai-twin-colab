#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'content' / 'clone_memory_policy_v1.json'
VALIDATOR = ROOT / 'worker' / 'validate_clone_memory_event.py'
CHAIN_VERIFIER = ROOT / 'worker' / 'verify_clone_memory_chain.py'
ZERO_HASH = '0' * 64


def fail(msg: str) -> None:
    raise SystemExit(msg)


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
    ap = argparse.ArgumentParser(description='Validate and append one Clone Memory v1 event to local persistent storage')
    ap.add_argument('--event', required=True)
    ap.add_argument('--storage-root', default=os.environ.get('AI_TWIN_STORAGE_MOUNT', '/workspace/zaskaleta-storage'))
    args = ap.parse_args()

    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    if policy.get('mode') != 'append_only_versioned':
        fail('Clone Memory policy is not append-only/versioned')

    event_path = Path(args.event).resolve()
    event = json.loads(event_path.read_text(encoding='utf-8'))

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

    previous_hash = ZERO_HASH
    if log_path.exists():
        chain = subprocess.run([sys.executable, str(CHAIN_VERIFIER), '--log', str(log_path)], capture_output=True, text=True)
        if chain.returncode != 0:
            print(chain.stdout)
            print(chain.stderr, file=sys.stderr)
            fail('Existing Clone Memory hash chain is invalid; refusing append')
        try:
            chain_doc = json.loads(chain.stdout)
            previous_hash = str(chain_doc.get('head_hash') or ZERO_HASH)
        except Exception:
            fail('Could not parse Clone Memory chain verification result')

        with log_path.open('r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                existing = json.loads(line)
                if existing.get('memory_id') == memory_id:
                    fail(f'duplicate_memory_id:{memory_id}')

    event.setdefault('recorded_at', datetime.now(timezone.utc).isoformat())
    event['append_only'] = True
    event['automatic_self_training'] = False
    event['previous_event_hash'] = previous_hash
    event['event_hash'] = compute_hash(previous_hash, event)

    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with log_path.open('a', encoding='utf-8') as fh:
        fh.write(encoded + '\n')
        fh.flush()
        os.fsync(fh.fileno())

    post = subprocess.run([sys.executable, str(CHAIN_VERIFIER), '--log', str(log_path)], capture_output=True, text=True)
    if post.returncode != 0:
        print(post.stdout)
        print(post.stderr, file=sys.stderr)
        fail('Post-append Clone Memory chain verification failed')

    print(json.dumps({
        'schema': 'zaskaleta-clone-memory-append-result-v2',
        'appended': True,
        'memory_id': memory_id,
        'domain': event.get('domain'),
        'decision': event.get('decision'),
        'previous_event_hash': previous_hash,
        'event_hash': event['event_hash'],
        'log_path': str(log_path),
        'chain_verified_after_append': True,
        'training_auto_started': False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
