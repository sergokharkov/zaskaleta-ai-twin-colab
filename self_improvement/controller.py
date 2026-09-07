#!/usr/bin/env python3
"""Guarded dry-run controller for MASTER CLONE candidate improvement.

The controller never edits the stable MASTER clone, never submits GPU work, and never
promotes a candidate. It only evaluates technical evidence and known failure memory to
produce a bounded improvement plan for the next candidate iteration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def classify_failure(text: str, known: list[dict]) -> dict:
    low = text.lower()
    for record in known:
        symptom = str(record.get('symptom', '')).lower()
        tokens = [t for t in symptom.replace('/', ' ').replace('_', ' ').split() if len(t) >= 5]
        score = sum(1 for t in tokens if t in low)
        if score >= 2:
            return {
                'matched_failure_id': record.get('id'),
                'failure_class': record.get('class', 'unknown'),
                'known_fix': record.get('fix'),
                'known_status': record.get('status'),
            }
    if '404' in low and 'dataset' in low:
        cls = 'private_dataset_propagation'
    elif 'filenotfounderror' in low or 'no such file' in low:
        cls = 'private_asset_resolution'
    elif 'kernelworkerstatus' in low or 'workflow' in low:
        cls = 'orchestration'
    elif 'cuda' in low or 'torch' in low or 'dependency' in low:
        cls = 'runtime_dependency'
    elif 'musetalk' in low or 'render' in low:
        cls = 'render'
    elif 'output' in low or 'artifact' in low:
        cls = 'output_retrieval'
    else:
        cls = 'unknown'
    return {'matched_failure_id': None, 'failure_class': cls, 'known_fix': None, 'known_status': None}


def compare_metrics(baseline: dict, challenger: dict) -> dict:
    tracked = {
        'temporal_pass_ratio': 'higher',
        'lip_sync_score': 'higher',
        'face_stability_score': 'higher',
        'render_completion_rate': 'higher',
        'identity_regression': 'lower',
    }
    changes = []
    regression = False
    improvement = False
    for key, direction in tracked.items():
        if key not in baseline or key not in challenger:
            continue
        try:
            b = float(baseline[key]); c = float(challenger[key])
        except (TypeError, ValueError):
            continue
        delta = c - b
        better = delta > 0 if direction == 'higher' else delta < 0
        worse = delta < 0 if direction == 'higher' else delta > 0
        changes.append({'metric': key, 'baseline': b, 'challenger': c, 'delta': delta, 'direction': direction, 'better': better})
        improvement = improvement or better
        regression = regression or worse
    return {'changes': changes, 'has_improvement': improvement, 'has_regression': regression}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--policy', default='self_improvement/policy_v1.json')
    ap.add_argument('--memory', default='self_improvement/failure_memory_v1.json')
    ap.add_argument('--failure-log')
    ap.add_argument('--baseline-metrics')
    ap.add_argument('--challenger-metrics')
    ap.add_argument('--output', default='self_improvement/improvement_report.json')
    args = ap.parse_args()

    policy = load(Path(args.policy))
    memory = load(Path(args.memory))
    if policy.get('mode') != 'dry_run_only':
        raise SystemExit('Self-improvement controller must remain dry-run only')
    hard = policy.get('hard_rules') or {}
    required_true = [
        'never_retry_same_known_failure_without_a_recorded_fix',
        'never_replace_stable_master_automatically',
        'never_weaken_identity_or_source_gates',
        'never_export_raw_private_media_or_biometric_hashes',
        'never_auto_submit_paid_gpu',
        'require_manual_review_before_promotion',
    ]
    if any(hard.get(k) is not True for k in required_true):
        raise SystemExit('Self-improvement hard safety rules are incomplete')

    report = {
        'schema': 'zaskaleta-master-clone-improvement-report-v1',
        'mode': 'DRY_RUN_ONLY',
        'stable_master_modified': False,
        'gpu_submitted': False,
        'auto_promote': False,
        'manual_identity_review_required': True,
        'failure_analysis': None,
        'metric_comparison': None,
        'decision': 'NO_CHANGE_RECOMMENDED',
        'next_actions': [],
    }

    if args.failure_log:
        text = Path(args.failure_log).read_text(encoding='utf-8', errors='replace')
        analysis = classify_failure(text, memory.get('records') or [])
        report['failure_analysis'] = analysis
        if analysis.get('matched_failure_id') and not analysis.get('known_fix'):
            report['decision'] = 'BLOCK_REPEAT_UNTIL_FIX_RECORDED'
        elif analysis.get('known_fix'):
            report['decision'] = 'VERIFY_KNOWN_FIX_BEFORE_NEXT_CANDIDATE'
            report['next_actions'].append(analysis['known_fix'])
        else:
            report['decision'] = 'NEW_FAILURE_REQUIRES_ROOT_CAUSE_AND_CPU_REPRODUCTION'
            report['next_actions'].append('Record a reproducible root cause and CPU-safe regression test before another candidate GPU attempt.')

    if args.baseline_metrics and args.challenger_metrics:
        comparison = compare_metrics(load(Path(args.baseline_metrics)), load(Path(args.challenger_metrics)))
        report['metric_comparison'] = comparison
        if comparison['has_regression']:
            report['decision'] = 'REJECT_CHALLENGER_TECHNICAL_REGRESSION'
        elif comparison['has_improvement']:
            report['decision'] = 'CHALLENGER_TECHNICALLY_BETTER_MANUAL_REVIEW_REQUIRED'
            report['next_actions'].append('Run all identity/source/temporal gates and require manual identity review; do not auto-promote.')

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
