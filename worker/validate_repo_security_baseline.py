#!/usr/bin/env python3
"""Static repository safety scan for MASTER CLONE.

Scans tracked text files for common credential/private-key signatures and blocks tracked raw
biometric/media extensions. Performs no network calls and never prints secret values.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.mp3', '.wav', '.m4a', '.mp4', '.mov', '.m4v', '.webm'}
PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'aws_access_key': re.compile(r'AKIA[0-9A-Z]{16}'),
    'google_api_key': re.compile(r'AIza[0-9A-Za-z_-]{35}'),
    'github_pat': re.compile(r'ghp_[A-Za-z0-9]{36}'),
    'github_fine_grained_pat': re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
}


def tracked_files() -> list[Path]:
    out = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    return [ROOT / p.decode('utf-8') for p in out.split(b'\0') if p]


def main() -> int:
    failures: list[str] = []
    scanned = 0
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix.lower() in MEDIA_EXTS:
            failures.append(f'tracked_private_media:{rel}')
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b'\x00' in data[:4096]:
            continue
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
        scanned += 1
        for name, pattern in PATTERNS.items():
            if pattern.search(text):
                failures.append(f'credential_signature:{name}:{rel}')

    print({
        'schema': 'zaskaleta-repo-security-baseline-v1',
        'passed': not failures,
        'tracked_text_files_scanned': scanned,
        'secret_values_exposed': False,
        'network_action_performed': False,
        'failures': failures,
    })
    return 0 if not failures else 2


if __name__ == '__main__':
    sys.exit(main())
