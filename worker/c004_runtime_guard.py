"""CPU-only C004 authorization boundary. Never provisions or renders."""
import argparse
import json
import math
import sys
from pathlib import Path

CANDIDATE = 'MASTER_CLONE_GATE_08_15_CANDIDATE_004'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, code):
    if not condition:
        raise ValueError(code)


def validate(manifest, approval, storage, qa, priors):
    from validate_c004_source import validate as validate_source
    source = validate_source(manifest, approval, storage)
    require(source['candidate_id'] == CANDIDATE and source['gpu_launch_allowed'] is False, 'invalid_source_decision')
    require(isinstance(qa, dict) and qa.get('candidate_id') == CANDIDATE, 'invalid_qa_candidate')
    require(qa.get('source_segment_id') == source['source_segment_id'], 'qa_source_mismatch')
    require(qa.get('decision') == 'CPU_QA_VERIFIED', 'cpu_qa_not_verified')
    passes = qa.get('passes')
    require(isinstance(passes, list) and len(passes) == 2, 'two_cpu_passes_required')
    require(all(isinstance(p, dict) and p.get('passed') is True and p.get('complete') is True for p in passes), 'incomplete_cpu_qa')
    require(isinstance(priors, list) and bool(priors) and len(set(priors)) == len(priors), 'invalid_prior_inventory')
    reports = qa.get('duplicate_reports')
    require(isinstance(reports, list) and len(reports) == len(priors), 'incomplete_duplicate_inventory')
    seen = set()
    for report in reports:
        require(isinstance(report, dict) and report.get('decision') == 'UNIQUE_ENOUGH', 'duplicate_gate_not_passed')
        require(report.get('candidate_id') == CANDIDATE and report.get('source_segment_id') == source['source_segment_id'], 'duplicate_source_mismatch')
        prior = report.get('prior_candidate_id')
        require(prior in priors and prior not in seen, 'duplicate_inventory_mismatch')
        seen.add(prior)
    require(seen == set(priors), 'incomplete_duplicate_inventory')
    require(qa.get('release_path_verified') is True and qa.get('recovery_path_verified') is True, 'runtime_paths_not_verified')
    return {'candidate_id': CANDIDATE, 'decision': 'C004_PREFLIGHT_VERIFIED', 'source_segment_id': source['source_segment_id'], 'source_path': source['source_path'], 'segment_start_seconds': source['segment_start_seconds'], 'segment_end_seconds': source['segment_end_seconds'], 'approved_roles': source['approved_roles'], 'gpu_launch_allowed': False, 'auto_promote': False}


def main():
    ap = argparse.ArgumentParser()
    for name in ('manifest', 'approval', 'storage-root', 'qa', 'prior-inventory', 'output'):
        ap.add_argument('--' + name, required=True)
    args = ap.parse_args()
    try:
        result = validate(load(args.manifest), load(args.approval), Path(args.storage_root), load(args.qa), load(args.prior_inventory))
        code = 0
    except Exception as exc:
        result = {'candidate_id': CANDIDATE, 'decision': 'C004_PREFLIGHT_BLOCKED', 'gpu_launch_allowed': False, 'auto_promote': False, 'error_code': type(exc).__name__}
        code = 3
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(result['decision'])
    return code


if __name__ == '__main__':
    sys.exit(main())
