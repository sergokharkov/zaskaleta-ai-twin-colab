#!/usr/bin/env python3
"""Execute C004 from an immutable run-scoped source bundle."""
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

WORK = Path('/kaggle/working')
REPO = WORK / 'zaskaleta-ai-twin-colab'
EXPECTED = 'MASTER_CLONE_GATE_08_15_CANDIDATE_004'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    here = Path(__file__).resolve().parent
    identity = json.loads((here / 'run_identity.json').read_text(encoding='utf-8'))
    token = identity['run_token']
    source_sha = identity['source_sha']
    if identity.get('candidate_id') != EXPECTED or not token:
        raise RuntimeError('invalid C004 run identity')
    if len(source_sha) != 40 or any(c not in '0123456789abcdef' for c in source_sha):
        raise RuntimeError('invalid source SHA')
    bundle = here / 'source_bundle.zip'
    if digest(bundle) != identity['bundle_sha256']:
        raise RuntimeError('C004 source bundle digest mismatch')
    if REPO.exists():
        raise RuntimeError('refusing to overwrite existing C004 workspace')
    REPO.mkdir(parents=True)
    with zipfile.ZipFile(bundle) as zf:
        for member in zf.infolist():
            p = Path(member.filename)
            if p.is_absolute() or '..' in p.parts:
                raise RuntimeError('unsafe source bundle path')
        zf.extractall(REPO)
    for name, expected in identity['critical_source_hashes'].items():
        path = REPO / name
        if not path.is_file() or digest(path) != expected:
            raise RuntimeError('pinned C004 source mismatch: ' + name)
    spec = importlib.util.spec_from_file_location('c004_kernel', REPO / 'kaggle' / 'autopilot_kernel_c004.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def pinned_repo():
        for name, expected in identity['critical_source_hashes'].items():
            if digest(REPO / name) != expected:
                raise RuntimeError('C004 source changed during execution: ' + name)
        print('PINNED_C004_SOURCE_SHA=' + source_sha, flush=True)

    module.ensure_repo = pinned_repo
    original_status = module.write_status

    def write_status(**kwargs):
        kwargs.update(run_token=token, source_sha=source_sha, expected_candidate_id=EXPECTED)
        original_status(**kwargs)

    module.write_status = write_status
    rc = module.main()
    if rc != 0:
        return rc
    status = json.loads((WORK / 'zaskaleta_c004_status.json').read_text(encoding='utf-8'))
    if status.get('candidate_id') != EXPECTED or status.get('state') != 'C004_RENDER_READY_FOR_MANUAL_REVIEW':
        raise RuntimeError('C004 terminal status mismatch')
    if status.get('render_completed') is not True or status.get('auto_promote') is not False or status.get('stable_release_modified') is not False:
        raise RuntimeError('C004 safety/terminal status mismatch')
    if status.get('subjective_identity_review') != 'PENDING_MANUAL_REVIEW':
        raise RuntimeError('C004 bypassed output identity review')
    print('VERIFIED_C004_RENDER_READY_FOR_MANUAL_REVIEW', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('FAILED_CLOSED: ' + repr(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
