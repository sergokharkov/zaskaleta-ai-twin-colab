#!/usr/bin/env python3
"""Recover the exact C003 build; never follow a stale Kaggle kernel."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_003'
BASE = 'first_gate_alignment/' + CANDIDATE
SOURCE_FILES = [
    'kaggle/recover_c003.py', 'kaggle/recover_c003_readonly.py',
    'kaggle/verified_c003_entry.py', 'kaggle/autopilot_kernel_c003.py',
    'kaggle/first_gate_alignment_render.py', 'kaggle/auto_prepare.py',
    'kaggle/bootstrap.py', 'kaggle/preflight.py', 'kaggle/prepare_models.py',
    'kaggle/validate_private_assets.py', 'worker/lipsync_musetalk.py',
    'worker/voice_mms_openvoice.py', 'worker/generate_scene_speech.py',
    'worker/evaluate_clone_release.py', 'content/talking_profile_v2.json',
    'content/clone_reference_profile.json', 'content/master_clone_package.json',
    'content/clone_duration_gate_policy_v1.json', 'content/clone_quality_gate_v1.json',
    'content/clone_release_policy_v1.json', 'content/identity_view_holdout_v1.json',
]

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()

def command(args, timeout=120, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.stdout: print(p.stdout, flush=True)
    if p.stderr: print(p.stderr, flush=True)
    if check and p.returncode:
        raise RuntimeError('Command failed with exit code %s: %s' % (p.returncode, args[:3]))
    return p

def build(out, source, token, handle, dataset):
    if not re.fullmatch(r'[0-9a-f]{40}', source):
        raise RuntimeError('Invalid pinned source SHA')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if actual != source:
        raise RuntimeError('Checkout does not match requested source SHA')
    out.mkdir(parents=True, exist_ok=True)
    bundle = out / 'source_bundle.zip'
    with bundle.open('wb') as f:
        subprocess.run(['git', 'archive', '--format=zip', source], cwd=ROOT, stdout=f, check=True)
    # Hash the archived bytes rather than trusting a possibly dirty worktree.
    source_hashes = {}
    with zipfile.ZipFile(bundle) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise RuntimeError('Duplicate source archive member')
        for member in z.infolist():
            if member.is_dir():
                continue
            path = Path(member.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in member.filename:
                raise RuntimeError('Unsafe source archive path')
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('Symlinks are not permitted in the C003 source archive')
            source_hashes[member.filename] = hashlib.sha256(z.read(member)).hexdigest()
        missing = set(SOURCE_FILES) - set(source_hashes)
        if missing:
            raise RuntimeError('Missing pinned dependencies: ' + ', '.join(sorted(missing)))
        for name in SOURCE_FILES:
            if sha(ROOT / name) != source_hashes[name]:
                raise RuntimeError('Dirty or mismatched source file: ' + name)
    manifest = {
        'run_token': token, 'source_sha': source, 'candidate_id': CANDIDATE,
        'bundle_sha256': sha(bundle), 'source_hashes': source_hashes,
    }
    entry = (ROOT / 'kaggle/verified_c003_entry.py').read_bytes()
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('source_bundle.zip', bundle.read_bytes())
        z.writestr('run_identity.json', json.dumps(manifest))
        z.writestr('verified_c003_entry.py', entry)
    encoded = base64.b64encode(payload.getvalue()).decode('ascii')
    launcher = '''#!/usr/bin/env python3
import base64, hashlib, io, json, os, runpy, zipfile
from pathlib import Path
PAYLOAD = %r
EXPECTED_ENTRY_SHA = %r
here = Path(__file__).resolve().parent
with zipfile.ZipFile(io.BytesIO(base64.b64decode(PAYLOAD))) as z:
    for name in ('source_bundle.zip', 'run_identity.json', 'verified_c003_entry.py'):
        (here / name).write_bytes(z.read(name))
manifest = json.loads((here / 'run_identity.json').read_text())
if hashlib.sha256((here / 'source_bundle.zip').read_bytes()).hexdigest() != manifest['bundle_sha256']:
    raise RuntimeError('Source bundle SHA-256 mismatch')
if hashlib.sha256((here / 'verified_c003_entry.py').read_bytes()).hexdigest() != EXPECTED_ENTRY_SHA:
    raise RuntimeError('Entrypoint SHA-256 mismatch')
if os.environ.get('ZASKALETA_PACKAGING_TEST') == '1':
    print('C003_PACKAGE_SELF_TEST_OK', manifest['run_token'], flush=True)
else:
    runpy.run_path(str(here / 'verified_c003_entry.py'), run_name='__main__')
''' % (encoded, hashlib.sha256(entry).hexdigest())
    code = out / 'c003_runner.py'
    code.write_text(launcher)
    metadata = {
        'id': handle, 'title': 'Zaskaleta C003 recovery ' + token,
        'code_file': code.name, 'language': 'python', 'kernel_type': 'script',
        'is_private': True, 'enable_gpu': True, 'enable_internet': True,
        'dataset_sources': [dataset.strip()], 'competition_sources': [], 'kernel_sources': [],
    }
    (out / 'kernel-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(code)], cwd=tmp,
                       env={**os.environ, 'ZASKALETA_PACKAGING_TEST': '1'},
                       check=True, timeout=60)
    print('Prepared and self-tested', handle, 'source', source, flush=True)
    return manifest

def download(handle, name, out):
    import kagglehub
    p = Path(kagglehub.notebook_output_download(
        handle, path=name, output_dir=str(out), force_download=True))
    if p.is_dir(): p = p / name
    if not p.is_file(): raise FileNotFoundError(name)
    return p

def validate(status, evidence, manifest):
    token, source = manifest['run_token'], manifest['source_sha']
    def require(condition, message):
        if not condition: raise RuntimeError(message)
    require(status.get('schema') == 'zaskaleta-kaggle-autopilot-status-v6', 'Wrong status schema')
    for obj in (status, evidence):
        require(obj.get('run_token') == token and obj.get('source_sha') == source, 'Wrong run identity')
        require(obj.get('promotion_allowed') is False and obj.get('auto_promote') is False, 'Promotion safety violation')
        require(obj.get('stable_release_modified') is False, 'Stable release safety violation')
    require(status.get('state') == 'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW', 'Wrong final state')
    require(status.get('first_gate_candidate_id') == CANDIDATE and status.get('render_completed') is True, 'Wrong candidate')
    require(evidence.get('candidate_id') == CANDIDATE and evidence.get('technical_gate_pass') is True, 'Evidence gate failed')
    require(evidence.get('single_component_change') == 'audio_alignment', 'Wrong component change')
    require(evidence.get('lipsync_sample_rate') == 16000 and evidence.get('final_audio_sample_rate') == 24000, 'Audio alignment mismatch')
    require(8 <= evidence.get('render_duration_seconds', 0) <= 15, 'Duration gate failed')
    require(evidence.get('subjective_identity_review') == 'PENDING_MANUAL_REVIEW', 'Manual identity review bypassed')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output-dir', default='.kaggle-c003-recovery')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    source = os.environ['GITHUB_SHA']
    token = os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    handle = os.environ['KAGGLE_USERNAME'] + '/zaskaleta-c003-recovery-' + token
    out = ROOT / args.output_dir
    dataset = os.environ.get('KAGGLE_PRIVATE_DATASET', '')
    if not args.dry_run and (not os.environ.get('KAGGLE_API_TOKEN') or not dataset):
        raise RuntimeError('Missing required Kaggle credentials or private dataset')
    manifest = build(out, source, token, handle, dataset or 'dry-run/dataset')
    if args.dry_run: return 0
    p = command(['kaggle', 'kernels', 'push', '-p', str(out), '--timeout', '7200', '--accelerator', 'gpu'], timeout=180)
    m = re.search(r'Kernel version\s+(\d+)\s+successfully pushed', p.stdout, re.I)
    if not m: raise RuntimeError('Kaggle did not confirm a new version; no old kernel will be followed')
    version = m.group(1)
    print('ACCEPTED_C003_VERSION', version, handle, flush=True)
    review = ROOT / '.kaggle-review'; review.mkdir(exist_ok=True)
    deadline = time.monotonic() + 7200
    status = evidence = None
    while time.monotonic() < deadline:
        p = command(['kaggle', 'kernels', 'status', handle], timeout=60, check=False)
        message = p.stdout + '\n' + p.stderr
        if p.returncode:
            raise RuntimeError('Cannot read exact kernel status: ' + message[-1000:])
        if re.search(r'ERROR|CANCELLED|CANCELED|FAILED', message, re.I):
            command(['kaggle', 'kernels', 'logs', handle], timeout=90, check=False)
            raise RuntimeError('Exact C003 kernel failed: ' + message[-1000:])
        try:
            status = json.loads(download(handle, 'zaskaleta_autopilot_status.json', review).read_text())
            if status.get('run_token') != token or status.get('source_sha') != source:
                raise RuntimeError('Wrong run identity')
            if status.get('state') == 'FAILED_CLOSED':
                raise RuntimeError('C003 failed closed: ' + str(status.get('error')))
            if status.get('state') == 'WAITING_FOR_PRIVATE_ASSETS':
                raise RuntimeError('Private assets unavailable')
            if status.get('state') == 'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW':
                evidence = json.loads(download(handle, BASE + '.evidence.json', review).read_text())
                break
        except RuntimeError:
            raise
        except Exception as exc:
            code = getattr(getattr(exc, 'response', None), 'status_code', None)
            if code in (401, 403): raise RuntimeError('Kaggle API authentication or permission denied') from exc
            print('Output not yet available:', type(exc).__name__, str(exc)[:200], flush=True)
        if re.search(r'COMPLETE|SUCCESS', message, re.I) and evidence is None:
            command(['kaggle', 'kernels', 'logs', handle], timeout=90, check=False)
            raise RuntimeError('Kernel completed without exact C003 evidence')
        time.sleep(20)
    if evidence is None: raise RuntimeError('Timed out waiting for exact C003 evidence')
    validate(status, evidence, manifest)
    video = download(handle, BASE + '.mp4', review)
    provenance = download(handle, BASE + '.provenance.json', review)
    if sha(video) != evidence['render_sha256'] or sha(provenance) != evidence['provenance_sha256']:
        raise RuntimeError('Output SHA-256 mismatch')
    command(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,codec_name,width,height,sample_rate', '-of', 'json', str(video)], timeout=60)
    import shutil
    shutil.copy2(video, review / (CANDIDATE + '.mp4'))
    private_provenance = json.loads(provenance.read_text(encoding='utf-8'))
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
    safe_path.write_text(json.dumps(safe_provenance, indent=2) + chr(10), encoding='utf-8')
    (review / 'C003_verified_manifest.json').write_text(json.dumps({
        'kernel': handle, 'version': version, 'run_token': token, 'source_sha': source,
        'candidate_id': CANDIDATE, 'render_sha256': sha(video),
        'provenance_sha256': sha(safe_path), 'manual_review': 'PENDING_MANUAL_REVIEW',
        'auto_promote': False,
    }, indent=2) + '\n')
    print('VERIFIED_C003_RENDER', CANDIDATE, sha(video), flush=True)
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('FAILED_CLOSED:', repr(exc), file=sys.stderr, flush=True)
        raise SystemExit(1)
