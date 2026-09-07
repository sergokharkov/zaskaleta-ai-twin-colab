#!/usr/bin/env python3
"""Execute only the exact C003 source archive in an isolated workspace."""
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path

WORK = Path('/kaggle/working')
REPO = WORK / 'zaskaleta-ai-twin-colab'
EXPECTED = 'MASTER_CLONE_GATE_08_15_CANDIDATE_003'

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def safe_archive_members(archive):
    names = set()
    for member in archive.infolist():
        name = member.filename
        path = Path(name)
        require(not path.is_absolute() and '..' not in path.parts and '\\' not in name, 'Unsafe source archive path')
        require(name not in names, 'Duplicate source archive member')
        names.add(name)
        mode = member.external_attr >> 16
        require(not stat.S_ISLNK(mode), 'Source archive symlink forbidden')
        require(member.file_size <= 50 * 1024 * 1024, 'Unexpectedly large source file')
    return names

def main():
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / 'run_identity.json').read_text())
    token, source = manifest['run_token'], manifest['source_sha']
    require(bool(re.fullmatch(r'[0-9]+-[0-9]+', token)), 'Invalid run token')
    require(bool(re.fullmatch(r'[0-9a-f]{40}', source)), 'Invalid source SHA')
    require(manifest['candidate_id'] == EXPECTED, 'Wrong candidate identity')
    bundle = here / 'source_bundle.zip'
    require(digest(bundle) == manifest['bundle_sha256'], 'Source bundle SHA mismatch')
    source_root = WORK / ('c003-source-' + token)
    require(not source_root.exists(), 'Refusing to overwrite an existing source workspace')
    with zipfile.ZipFile(bundle) as z:
        names = safe_archive_members(z)
        actual_files = {n for n in names if not n.endswith('/')}
        require(actual_files == set(manifest['source_hashes']), 'Source manifest does not cover every archived file')
        source_root.mkdir(parents=True)
        z.extractall(source_root)
    for name, expected in manifest['source_hashes'].items():
        require(digest(source_root / name) == expected, 'Pinned source hash mismatch: ' + name)
    spec = importlib.util.spec_from_file_location('candidate003', source_root / 'kaggle/autopilot_kernel_c003.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO = source_root
    def pinned_repo():
        for name, expected in manifest['source_hashes'].items():
            require(digest(source_root / name) == expected, 'Source changed during execution: ' + name)
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
    require(status['run_token'] == token and status['source_sha'] == source, 'Wrong status run identity')
    require(status['first_gate_candidate_id'] == EXPECTED and status['render_completed'] is True, 'Wrong or incomplete candidate')
    require(status['state'] == 'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW', 'Wrong final status')
    require(evidence['candidate_id'] == EXPECTED and evidence['technical_gate_pass'] is True, 'Technical evidence failed')
    require(evidence['single_component_change'] == 'audio_alignment', 'Wrong component change')
    require(evidence['lipsync_sample_rate'] == 16000 and evidence['final_audio_sample_rate'] == 24000, 'Audio alignment mismatch')
    require(evidence['render_sha256'] == digest(video), 'Final video SHA mismatch')
    require(evidence['provenance_sha256'] == digest(provenance), 'Final provenance SHA mismatch')
    prov = json.loads(provenance.read_text())
    require(prov.get('final_render_sha256') == digest(video), 'Provenance identifies the wrong MP4')
    require(prov.get('candidate_id') == EXPECTED, 'Wrong provenance candidate')
    for obj in (status, evidence, prov):
        require(obj.get('promotion_allowed') is False and obj.get('auto_promote') is False, 'Promotion safety violation')
        require(obj.get('stable_release_modified') is False, 'Stable release safety violation')
    require(evidence['subjective_identity_review'] == 'PENDING_MANUAL_REVIEW', 'Manual identity review bypassed')
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
