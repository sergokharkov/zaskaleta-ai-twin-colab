# MASTER CLONE — External Connection Checklist

This checklist is the operational handoff from static readiness to real infrastructure connection.
It does **not** authorize paid GPU provisioning and must never contain real credentials or biometric payloads.

## 1. Object storage

- Create an S3-compatible bucket in an EU region.
- Enable bucket versioning before canonical migration.
- Prefer object lock/immutability when supported.
- Create least-privilege credentials limited to the MASTER CLONE bucket/prefixes.
- Configure a second independent encrypted backup before production cutover.
- Keep the AES-256 client-side encryption key separate from storage credentials and objects.

Required runtime variable names are in `runpod/runtime.env.example`. Real values belong only in the external provider secret manager/runtime environment.

## 2. Canonical migration

Mount the legacy/source folder read-only and run the migration worker in execute mode only after storage credentials are installed in the runtime:

```bash
python worker/migrate_clone_storage.py \
  --source-root /path/to/read-only/source \
  --manifest-out /secure/runtime/storage_migration_manifest_v1.json \
  --execute
```

Continue only when every object has `VERIFIED_DECRYPTED_SHA256`, `migration_complete=true`, and `destination_verified_after_decryption=true`. The source is not deleted and storage cutover remains manual.

## 3. Runtime preflight

Install the seven secret/runtime variables named in `runpod/runtime.env.example`, then run:

```bash
python runpod/connection_readiness.py --require-runtime-env
```

The API startup script performs this gate automatically and fails closed:

```bash
bash runpod/start_api.sh
```

## 4. Acceptance before GPU render

Verify `/health` reports the S3 contract/runtime as ready, no Google Drive production dependency, encrypted artifact persistence/state recovery enabled, and one active render maximum per worker.

Do not start a paid GPU until an explicit budget cap is approved. The first real GPU test is limited to the 8–15 second gate.

## 5. First render acceptance

For the first 8–15 second render, require all of the following before manual review:

- canonical identity and approved motion references are resolved;
- cloned speech provenance is present;
- final postprocessed MP4 provenance passes;
- candidate ID is consistent across evidence;
- temporal face guard passes;
- encrypted job artifacts are persisted and can be restored;
- encrypted job state can be restored after local state removal;
- job-scoped plaintext/runtime files are cleaned;
- no automatic MASTER CLONE promotion occurs.

Then perform the manual identity/quality review. Only a passing manual review may unlock the next duration gate.

## 6. Duration progression

Progress strictly in this order:

`8–15 → 15–30 → 30–45 → 45–60 → 60–90 seconds`

No gate may be skipped. Stable MASTER CLONE remains immutable; challenger promotion remains manual with rollback evidence.

## Stop conditions

Stop connection or testing immediately if S3 verification, encrypted restore, provenance, identity, temporal stability, cleanup, or secret-safety checks fail. Do not weaken gates as a workaround.
