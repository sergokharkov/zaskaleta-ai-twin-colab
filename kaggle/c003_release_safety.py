#!/usr/bin/env python3
"""Apply narrowly scoped C003 fixes, without importing models or using GPU."""
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def replace(path, old, new):
    p = ROOT / path
    text = p.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError('Expected unique source contract missing: ' + path)
    text = text.replace(old, new, 1)
    compile(text, str(p), 'exec')
    p.write_text(text, encoding='utf-8')

def apply():
    renderer = 'kaggle/first_gate_alignment_render.py'
    replace(renderer, "'reference_fps':fps,'render_duration_seconds'", "'reference_fps':int(fps),'render_duration_seconds'")
    replace(renderer, "'reference_fps':fps,'do_not_repeat_reference_motion'", "'reference_fps':int(fps),'do_not_repeat_reference_motion'")
    recovery = 'kaggle/recover_c003.py'
    replace(recovery, "    shutil.copy2(provenance, review / (CANDIDATE + '.provenance.json'))", """    private_provenance = json.loads(provenance.read_text(encoding='utf-8'))
    if private_provenance.get('final_render_sha256') != sha(video):
        raise RuntimeError('Private provenance does not identify the final MP4')
    safe_provenance = {
        'schema': 'zaskaleta-c003-public-review-v1',
        'candidate_id': CANDIDATE,
        'source_sha': source,
        'run_token': token,
        'final_render_sha256': sha(video),
        'render_duration_seconds': evidence['render_duration_seconds'],
        'lipsync_sample_rate': 16000,
        'final_audio_sample_rate': 24000,
        'single_component_change': 'audio_alignment',
        'subjective_identity_review': 'PENDING_MANUAL_REVIEW',
        'promotion_allowed': False,
        'auto_promote': False,
        'stable_release_modified': False,
    }
    safe_path = review / (CANDIDATE + '.provenance.json')
    safe_path.write_text(json.dumps(safe_provenance, indent=2) + chr(10), encoding='utf-8')""")
    replace(recovery, "'provenance_sha256': sha(provenance), 'manual_review'", "'provenance_sha256': sha(safe_path), 'manual_review'")
    replace(recovery, "    source_hashes = {}\n    with zipfile.ZipFile(bundle) as z:", "    source_hashes = {}\n    with zipfile.ZipFile(bundle) as z:\n        if len(z.namelist()) != len(set(z.namelist())):\n            raise RuntimeError('Duplicate source archive member')")
    autopilot = 'kaggle/autopilot_kernel_c003.py'
    replace(autopilot, "        renderer=REPO/'kaggle'/'first_gate_alignment_render.py'", """        second_manifest_path = WORK/'private_asset_manifest_second.json'
        run([str(PY),str(validator),'--root',str(private_root),'--profile',str(REPO/'content'/'clone_reference_profile.json'),'--package',str(REPO/'content'/'master_clone_package.json'),'--output',str(second_manifest_path)],cwd=REPO,timeout=3600)
        second_manifest = json.loads(second_manifest_path.read_text(encoding='utf-8'))
        if manifest != second_manifest or second_manifest.get('validated') is not True:
            raise RuntimeError('Double private asset preflight mismatch')
        run([str(PY),str(REPO/'kaggle'/'prepare_models.py'),'--verify-only'],cwd=REPO)
        run([str(PY),str(REPO/'kaggle'/'preflight.py')],cwd=REPO)
        renderer=REPO/'kaggle'/'first_gate_alignment_render.py'""")
    print('C003_RELEASE_PATCH_APPLIED: double preflight and safe export enforced')

def verify():
    renderer = (ROOT/'kaggle/first_gate_alignment_render.py').read_text()
    recovery = (ROOT/'kaggle/recover_c003.py').read_text()
    autopilot = (ROOT/'kaggle/autopilot_kernel_c003.py').read_text()
    for p in (renderer, recovery, autopilot):
        ast.parse(p)
    assert "'reference_fps':int(fps)" in renderer
    assert 'safe_provenance = {' in recovery
    assert 'safe_path.write_text(json.dumps(safe_provenance, indent=2) + chr(10)' in recovery
    assert 'Double private asset preflight mismatch' in autopilot
    assert 'private_asset_manifest_second.json' in autopilot
    assert 'Duplicate source archive member' in recovery
    print('C003_RELEASE_SAFETY_VERIFIED')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()
    if args.apply: apply()
    if args.verify: verify()
