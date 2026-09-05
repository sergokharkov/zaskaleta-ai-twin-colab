#!/usr/bin/env python3
"""Final non-provisioning readiness gate before external MASTER CLONE infrastructure is connected.

Default mode is fully static and performs no network calls, no GPU provisioning and no secret output.
--require-runtime-env additionally requires the names configured in storage_config.json and AI_TWIN_TOKEN
to be present in the environment, but still does not connect to storage or RunPod.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    '.gitignore','runpod/api_server.py','runpod/preflight.py','runpod/clone_v2_readiness.py',
    'runpod/start_api.sh','runpod/requirements-api.txt','worker/run_clone_v2_test.py',
    'worker/lipsync_musetalk.py','worker/validate_lipsync_render_provenance.py',
    'worker/materialize_clone_runtime_from_s3.py','worker/clone_job_artifact_store.py',
    'worker/migrate_clone_storage.py','worker/storage_backend.py','worker/validate_repo_security_baseline.py',
    'worker/evaluate_clone_release.py','worker/evaluate_identity_view_results.py','worker/compare_clone_challenger.py',
    'worker/validate_clone_promotion_bundle.py','worker/validate_clone_duration_progression.py',
    'worker/validate_clone_v2_temporal_output.py','worker/verify_clone_memory_chain.py',
    'content/storage_config.json','content/clone_quality_gate_v1.json','content/clone_release_policy_v1.json',
    'content/clone_duration_gate_policy_v1.json','content/clone_promotion_bundle_policy_v1.json',
    'content/clone_memory_policy_v1.json',
]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--require-runtime-env', action='store_true')
    args = ap.parse_args()

    failures: list[str] = []
    missing = [rel for rel in REQUIRED if not (ROOT / rel).is_file()]
    if missing:
        failures.extend('missing:' + rel for rel in missing)

    if not missing:
        cfg = json.loads(read('content/storage_config.json'))
        canonical = cfg.get('canonical_storage') or {}
        runtime = cfg.get('runtime') or {}
        encryption = cfg.get('encryption') or {}
        if cfg.get('schema') != 'zaskaleta-storage-v2': failures.append('storage_schema_not_v2')
        if canonical.get('provider') != 's3_compatible': failures.append('canonical_storage_not_s3')
        if canonical.get('required_region_policy') != 'EU_ONLY': failures.append('storage_not_eu_only')
        if canonical.get('versioning_required') is not True: failures.append('versioning_not_required')
        if not canonical.get('migration_manifest_key'): failures.append('runtime_manifest_key_missing')
        if not canonical.get('job_artifact_prefix'): failures.append('job_artifact_prefix_missing')
        if runtime.get('job_scoped_plaintext_materialization_required') is not True: failures.append('job_scoped_materialization_not_required')
        if runtime.get('delete_temporary_plaintext_after_job') is not True: failures.append('runtime_plaintext_cleanup_not_required')
        if runtime.get('runtime_attestation_required') is not True: failures.append('runtime_attestation_not_required')
        if encryption.get('client_side_encryption_required') is not True: failures.append('client_side_encryption_not_required')
        if (cfg.get('legacy_source_import') or {}).get('production_dependency') is not False: failures.append('legacy_drive_production_dependency')
        if (cfg.get('legacy_canonical_migration') or {}).get('production_dependency') is not False: failures.append('migration_drive_production_dependency')

        api = read('runpod/api_server.py')
        runner = read('worker/run_clone_v2_test.py')
        materializer = read('worker/materialize_clone_runtime_from_s3.py')
        artifact_store = read('worker/clone_job_artifact_store.py')
        provenance = read('worker/validate_lipsync_render_provenance.py')
        requirements = read('runpod/requirements-api.txt')
        ignore = read('.gitignore')
        promotion = json.loads(read('content/clone_promotion_bundle_policy_v1.json'))

        for forbidden in ('AI_TWIN_DRIVE_SYNC','AI_TWIN_DRIVE_FOLDER_ID','fixed_drive_folder_sync.py','GOOGLE_APPLICATION_CREDENTIALS'):
            if forbidden in api: failures.append('api_legacy_drive_token:' + forbidden)
        for token in ('--candidate-id','CLONE_V2_RENDER_PROVENANCE.json','CLONE_V2_RENDER_PROVENANCE_EVALUATION.json'):
            if token not in runner: failures.append('runner_missing:' + token)
        for token in ('zaskaleta-lipsync-render-provenance-v2','raw_output','identity_preflight_sha256','candidate_id_missing'):
            if token not in provenance: failures.append('provenance_validator_missing:' + token)
        for token in ('all_objects_verified','cleanup_required_after_job','Refusing to materialize private clone assets inside the Git repository'):
            if token not in materializer: failures.append('materializer_missing:' + token)
        for token in ('AES-256-GCM','VERIFIED_DECRYPTED_SHA256','artifact_manifest_v1.json',"sub.add_parser('persist')", "sub.add_parser('restore')",'strict_key'):
            if token not in artifact_store: failures.append('artifact_store_missing:' + token)
        for token in ('clone_job_artifact_store.py','artifacts_persisted_encrypted=True','cleanup_job_plaintext','BackgroundTask','JOB_ID_RE','ACTIVE_JOB_LOCK','reserve_render_slot','release_render_slot(job_id)',"status_code=429","'max_concurrent_render_jobs_per_worker': 1"):
            if token not in api: failures.append('api_runtime_safety_missing:' + token)
        for token in ('.env','*.pem','*.key','_runtime_assets/','MASTER_CLONE/','MASTER_CLONE_ENCRYPTED/'):
            if token not in ignore: failures.append('gitignore_missing:' + token)
        if 'boto3==' not in requirements or 'cryptography==' not in requirements: failures.append('runtime_storage_dependencies_missing')
        if 'google-auth' in requirements or 'google-api-python-client' in requirements: failures.append('legacy_google_dependencies_present')
        rules = promotion.get('rules') or {}
        if rules.get('candidate_id_required_on_all_artifacts') is not True: failures.append('candidate_id_not_required_on_all_promotion_artifacts')
        if promotion.get('auto_promote') is not False or promotion.get('manual_promotion_required') is not True: failures.append('promotion_policy_weakened')
        if float(promotion.get('identity_regression_tolerance', 1)) != 0.0: failures.append('identity_regression_tolerance_not_zero')

        for rel in ('runpod/clone_v2_readiness.py','worker/storage_backend.py','worker/validate_repo_security_baseline.py'):
            proc = subprocess.run([sys.executable, str(ROOT / rel)], cwd=ROOT, capture_output=True, text=True)
            if proc.returncode != 0: failures.append('static_check_failed:' + Path(rel).name)

        if args.require_runtime_env:
            env_names = [canonical.get('bucket_env'),canonical.get('endpoint_env'),canonical.get('region_env'),canonical.get('access_key_env'),canonical.get('secret_key_env'),encryption.get('key_env'),'AI_TWIN_TOKEN']
            for name in env_names:
                if not isinstance(name, str) or not name or not os.environ.get(name, '').strip():
                    failures.append('runtime_env_missing:' + str(name))

    report = {
        'schema': 'zaskaleta-clone-external-connection-readiness-v4',
        'static_ready_for_external_connection_setup': not failures if not args.require_runtime_env else None,
        'runtime_env_ready_for_connection': not failures if args.require_runtime_env else None,
        'repository_security_baseline_required': True,
        'encrypted_job_artifact_lifecycle_required': True,
        'max_concurrent_render_jobs_per_worker': 1,
        'external_connection_performed': False,
        'network_action_performed': False,
        'paid_gpu_started': False,
        'manual_budget_approval_required_before_paid_gpu': True,
        'secret_values_exposed': False,
        'first_gpu_gate_seconds': [8, 15],
        'failures': failures,
        'note': 'Passing this gate means repository/runtime wiring is ready for connection. It does not prove real S3 connectivity, GPU render quality, or production load capacity.',
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == '__main__':
    raise SystemExit(main())
