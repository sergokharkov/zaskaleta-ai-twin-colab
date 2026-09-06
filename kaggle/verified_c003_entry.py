#!/usr/bin/env python3
"""Execute the existing C003 renderer from an immutable, run-scoped source bundle."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

WORK = Path('/kaggle/working')
REPO = WORK / 'zaskaleta-ai-twin-colab'
EXPECTED = 'MASTER_CLONE_GATE_08_15_CANDIDATE_003'

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / 'run_identity.json').read_text())
    token, source = manifest['run_token'], manifest['source_sha']
    assert token and len(source) == 40 and all(c in '0123456789abcdef' for c in source)
    assert manifest['candidate_id'] == EXPECTED
    bundle = here / 'source_bundle.zip'
    assert digest(bundle) == manifest['bundle_sha256']
    if REPO.exists():
        raise RuntimeError('Refusing to overwrite an existing repository workspace')
    REPO.mkdir(parents=True)
    with zipfile.ZipFile(bundle) as z:
        for member in z.infolist():
            path = Path(member.filename)
            if path.is_absolute() or '..' in path.parts or member.is_dir() and not member.filename.endswith('/'):
                raise RuntimeError('Unsafe source bundle path')
        z.extractall(REPO)
    for name, expected in manifest['source_hashes'].items():
        if digest(REPO / name) != expected:
            raise RuntimeError('Pinned source hash mismatch: ' + name)
    spec = importlib.util.spec_from_file_location('candidate003', REPO / 'kaggle' / 'autopilot_kernel_c003.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def pinned_repo():
        for name, expected in manifest['source_hashes'].items():
            if digest(REPO / name) != expected:
                raise RuntimeError('Source changed during execution: ' + name)
        print('PINNED_SOURCE_SHA=' + source, flush=True)
    module.ensure_repo = pinned_repo
    original_write = module.write_status
    def write_status(*args, **kwargs):
        kwargs.update(run_token=token, source_sha=source, expected_candidate_id=EXPECTED)
        original_write(*args, **kwargs)
    module.write_status = write_status
    rc = module.main()
    if rc != 0:
        return rc
    status_path = WORK / 'zaskaleta_autopilot_status.json'
    status = json.loads(status_path.read_text())
    out = WORK / 'first_gate_alignment'
    video = out / (EXPECTED + '.mp4')
    provenance = out / (EXPECTED + '.provenance.json')
    evidence_path = out / (EXPECTED + '.evidence.json')
    evidence = json.loads(evidence_path.read_text())
    assert status['run_token'] == token and status['source_sha'] == source
    assert status['first_gate_candidate_id'] == EXPECTED and status['render_completed'] is True
    assert status['state'] == 'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW'
    assert evidence['candidate_id'] == EXPECTED and evidence['technical_gate_pass'] is True
    assert evidence['single_component_change'] == 'audio_alignment'
    assert evidence['lipsync_sample_rate'] == 16000 and evidence['final_audio_sample_rate'] == 24000
    assert evidence['render_sha256'] == digest(video)
    assert evidence['provenance_sha256'] == digest(provenance)
    assert evidence['promotion_allowed'] is False and evidence['auto_promote'] is False
    assert evidence['stable_release_modified'] is False
    assert evidence['subjective_identity_review'] == 'PENDING_MANUAL_REVIEW'
    for obj in (status, evidence):
        obj.update(run_token=token, source_sha=source, expected_candidate_id=EXPECTED)
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False) + '\n')
    evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
    print('VERIFIED_C003_RENDER=' + json.dumps({'run_token':token,'candidate_id':EXPECTED,'render_sha256':digest(video),'source_sha':source}), flush=True)
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('FAILED_CLOSED: ' + repr(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
