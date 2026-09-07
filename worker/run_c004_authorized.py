#!/usr/bin/env python3
"""C004 runtime entrypoint.

This is a candidate-only wrapper. It never changes the stable MASTER CLONE package and it never
selects legacy primary behavior as a fallback. A verified C004 source/QA attestation is mandatory.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_004'
DERIVED_NAME = 'C004_APPROVED_SEGMENT.mp4'


def run(cmd, **kwargs):
    cmd = [str(x) for x in cmd]
    print('▶', ' '.join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def probe_duration(path: Path) -> float:
    return float(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nw=1:nk=1', str(path)
    ], text=True).strip())


def main():
    ap = argparse.ArgumentParser(description='Run C004 only from an explicitly approved source segment')
    ap.add_argument('--root', required=True)
    ap.add_argument('--mydrive', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--approval', required=True)
    ap.add_argument('--qa', required=True)
    ap.add_argument('--prior-inventory', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--seconds', type=float, default=8.0)
    ap.add_argument('--voice-preset', default='conversational')
    ap.add_argument('--text', default='Я говорю спокійно і природно. Рухи обличчя та голови мають залишатися живими, стабільними й природними.')
    ap.add_argument('--preflight-only', action='store_true')
    ap.add_argument('--source-cpu-only', action='store_true',
                    help='Collect preparation evidence before QA exists; requires --preflight-only')
    args = ap.parse_args()
    if args.source_cpu_only and not args.preflight_only:
        ap.error('--source-cpu-only requires --preflight-only; rendering is forbidden')

    root = Path(args.root).resolve(strict=True)
    storage = Path(args.mydrive).resolve(strict=True)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    worker = root / 'worker'

    sys.path.insert(0, str(worker))
    from c004_runtime_guard import validate as validate_guard

    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    approval = json.loads(Path(args.approval).read_text(encoding='utf-8'))
    if args.source_cpu_only:
        from validate_c004_source import validate as validate_source
        guard = validate_source(manifest, approval, storage)
        expected_decision = 'SOURCE_APPROVED_FOR_CPU_QA'
    else:
        qa = json.loads(Path(args.qa).read_text(encoding='utf-8'))
        priors = json.loads(Path(args.prior_inventory).read_text(encoding='utf-8'))
        guard = validate_guard(manifest, approval, storage, qa, priors)
        expected_decision = 'C004_PREFLIGHT_VERIFIED'
    if guard.get('decision') != expected_decision or guard.get('candidate_id') != CANDIDATE:
        raise SystemExit('C004 source preflight did not verify')

    source = Path(guard['source_path']).resolve(strict=True)
    source_duration = probe_duration(source)
    start = float(guard['segment_start_seconds'])
    end = float(guard['segment_end_seconds'])
    if not 0 <= start < end <= source_duration + 0.05:
        raise SystemExit('Approved segment is outside source duration')
    segment_duration = end - start
    if not 8.0 <= segment_duration <= 15.0:
        raise SystemExit('C004 gate segment must be 8-15 seconds')
    seconds = float(args.seconds)
    if not 8.0 <= seconds <= min(15.0, segment_duration):
        raise SystemExit('Requested C004 duration must fit the approved 8-15s segment')

    derived_dir = storage / '_c004_runtime_only'
    derived_dir.mkdir(parents=True, exist_ok=True)
    derived = derived_dir / DERIVED_NAME
    candidate_root = out / '_c004_candidate_root'
    try:
        run([
            'ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{start:.6f}', '-i', source,
            '-t', f'{segment_duration:.6f}', '-an', '-r', '25', '-c:v', 'libx264',
            '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', derived,
        ])
        if not derived.is_file() or probe_duration(derived) < 7.95:
            raise RuntimeError('Derived C004 behavior segment is invalid')

        shutil.copytree(root / 'content', candidate_root / 'content', dirs_exist_ok=True)
        os.symlink(worker, candidate_root / 'worker', target_is_directory=True)
        package_path = candidate_root / 'content' / 'master_clone_package.json'
        package = json.loads(package_path.read_text(encoding='utf-8'))
        motion = package['components']['motion']
        motion['primary_reference'] = DERIVED_NAME
        motion['candidate_runtime_lineage'] = {
            'candidate_id': CANDIDATE,
            'source_segment_id': guard['source_segment_id'],
            'source_roles': guard['approved_roles'],
            'stable_package_modified': False,
            'fallback_to_legacy_primary_allowed': False,
        }
        package['version'] = 'v1+c004-candidate-runtime'
        package['status'] = 'candidate_runtime_only'
        package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        ready = {
            'schema': 'zaskaleta-c004-runtime-readiness-v1',
            'candidate_id': CANDIDATE,
            'decision': 'C004_CPU_PREPARATION_VERIFIED' if args.source_cpu_only else 'C004_RUNTIME_READY',
            'gpu_launch_allowed': False,
            'source_segment_id': guard['source_segment_id'],
            'derived_behavior_name': DERIVED_NAME,
            'segment_duration_seconds': segment_duration,
            'requested_duration_seconds': seconds,
            'fallback_to_c003_allowed': False,
            'fallback_to_legacy_primary_allowed': False,
            'stable_clone_modified': False,
            'automatic_master_promotion': False,
        }
        (out / 'C004_RUNTIME_READINESS.json').write_text(json.dumps(ready, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.preflight_only:
            print('C004_RUNTIME_PREFLIGHT_ONLY_VERIFIED')
            return 0

        cmd = [
            sys.executable, candidate_root / 'worker' / 'run_clone_v2_test.py',
            '--root', candidate_root, '--mydrive', storage, '--candidate-id', CANDIDATE,
            '--seconds', f'{seconds:.6f}', '--voice-preset', args.voice_preset,
            '--text', args.text, '--output-dir', out,
        ]
        env = os.environ.copy()
        env['AI_TWIN_PYTHON'] = os.environ.get('AI_TWIN_PYTHON', sys.executable)
        run(cmd, env=env)
        final = out / 'CLONE_V2_TALKING_TEST.mp4'
        evaluation = out / 'CLONE_V2_EVALUATION.json'
        if not final.is_file() or not evaluation.is_file():
            raise RuntimeError('C004 renderer did not produce mandatory review outputs')
        ev = json.loads(evaluation.read_text(encoding='utf-8'))
        if ev.get('candidate_id') != CANDIDATE or ev.get('reference_behavior') != DERIVED_NAME:
            raise RuntimeError('C004 output is not bound to the approved derived behavior')
        print('C004_RENDER_READY_FOR_MANUAL_REVIEW')
        return 0
    finally:
        shutil.rmtree(candidate_root, ignore_errors=True)
        shutil.rmtree(derived_dir, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
