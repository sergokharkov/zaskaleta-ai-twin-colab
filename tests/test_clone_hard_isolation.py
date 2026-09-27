import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]

class StaticIsolationTests(unittest.TestCase):
    def test_no_account_wide_kaggle_enumeration(self):
        p = ROOT / '.github/workflows/c004-kaggle-source-discovery.yml'
        text = p.read_text(encoding='utf-8')
        self.assertNotIn('dataset_list(user=', text)
        self.assertNotIn('while page <=', text)
        self.assertIn('AI_CLONE_KAGGLE_DATASET', text)

    def test_isolation_policy_exists(self):
        self.assertTrue((ROOT / 'worker/clone_isolation.py').is_file())

    def test_runpod_startup_requires_drive_free_runtime_attestation(self):
        attestation = (ROOT / 'runpod/runtime_attestation.py').read_text(encoding='utf-8')
        start_api = (ROOT / 'runpod/start_api.sh').read_text(encoding='utf-8')
        readiness = (ROOT / 'runpod/connection_readiness.py').read_text(encoding='utf-8')

        self.assertIn("add_argument('--require-drive-free'", attestation)
        self.assertIn("runtime_attestation.py\" --require-drive-free", start_api)
        self.assertIn("'runpod/runtime_attestation.py'", readiness)

    def test_runtime_attestation_cli_requires_explicit_provider_probes(self):
        p = ROOT / 'runpod/runtime_attestation.py'
        text = p.read_text(encoding='utf-8')
        self.assertIn("add_argument('--probe-read'", text)
        self.assertIn("add_argument('--probe-write-inside'", text)
        self.assertIn("add_argument('--probe-id'", text)
        self.assertIn('args.probe_read', text)
        self.assertIn('args.probe_write_inside', text)
        self.assertIn('s3_client(cfg)', text)
        self.assertNotIn('delete_object(', text)

    def test_runtime_materializer_enforces_canonical_s3_scope(self):
        p = ROOT / 'worker/materialize_clone_runtime_from_s3.py'
        text = p.read_text(encoding='utf-8')
        self.assertIn('assert_clone_s3_scope(bucket, manifest_key)', text)
        self.assertIn('assert_clone_s3_scope(bucket, encrypted_key)', text)

class RuntimeNegativeAccessTests(unittest.TestCase):
    def setUp(self):
        from worker.clone_isolation import IsolationError, assert_clone_resource
        self.IsolationError = IsolationError
        self.assert_clone_resource = assert_clone_resource

    def test_allows_canonical_s3_bucket_resource(self):
        with mock.patch.dict(os.environ, {'AI_TWIN_STORAGE_BUCKET':'ai-clone-private'}, clear=True):
            self.assertEqual(
                self.assert_clone_resource('s3', 'ai-clone-private'),
                'ai-clone-private',
            )

    def test_rejects_foreign_s3_bucket(self):
        with mock.patch.dict(os.environ, {'AI_TWIN_STORAGE_BUCKET':'ai-clone-private'}, clear=False):
            with self.assertRaises(self.IsolationError):
                self.assert_clone_resource('s3', 'other-project')

    def test_rejects_foreign_drive_folder(self):
        with mock.patch.dict(os.environ, {'AI_CLONE_DRIVE_FOLDER_ID':'clone-folder'}, clear=False):
            with self.assertRaises(self.IsolationError):
                self.assert_clone_resource('drive', 'foreign-folder')

    def test_rejects_foreign_kaggle_dataset(self):
        with mock.patch.dict(os.environ, {'AI_CLONE_KAGGLE_DATASET':'owner/ai-clone-private'}, clear=False):
            with self.assertRaises(self.IsolationError):
                self.assert_clone_resource('kaggle', 'owner/other-project')

    def test_rejects_foreign_runpod_namespace(self):
        with mock.patch.dict(os.environ, {'AI_CLONE_RUNPOD_NAMESPACE':'ai-clone'}, clear=False):
            with self.assertRaises(self.IsolationError):
                self.assert_clone_resource('runpod', 'telegram-bot')

    def test_fail_closed_when_scope_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(self.IsolationError):
                self.assert_clone_resource('s3', 'anything')

    def test_canonical_s3_scope_uses_runtime_contract(self):
        from worker.clone_isolation import assert_clone_s3_scope

        with mock.patch.dict(os.environ, {'AI_TWIN_STORAGE_BUCKET': 'canonical-bucket'}, clear=True):
            self.assertEqual(
                assert_clone_s3_scope('canonical-bucket', 'MASTER_CLONE/MEMORY/object.bin'),
                'MASTER_CLONE/MEMORY/object.bin',
            )
            with self.assertRaises(self.IsolationError):
                assert_clone_s3_scope('other-bucket', 'MASTER_CLONE/MEMORY/object.bin')
            with self.assertRaises(self.IsolationError):
                assert_clone_s3_scope('canonical-bucket', 'OTHER_PROJECT/object.bin')


class RuntimeAttestationTests(unittest.TestCase):
    def test_static_attestation_classifies_provider_without_exposing_secrets(self):
        from runpod.runtime_attestation import build_static_attestation

        cfg = json.loads((ROOT / 'content/storage_config.json').read_text(encoding='utf-8'))
        fake_env = {
            'AI_TWIN_STORAGE_BUCKET': 'private-canonical-bucket',
            'AI_TWIN_STORAGE_ENDPOINT': 'https://account.r2.cloudflarestorage.com',
            'AI_TWIN_STORAGE_REGION': 'eu',
            'AI_TWIN_STORAGE_ACCESS_KEY_ID': 'private-access-id',
            'AI_TWIN_STORAGE_SECRET_ACCESS_KEY': 'private-secret-key',
        }
        with tempfile.TemporaryDirectory() as td:
            report = build_static_attestation(
                cfg,
                fake_env,
                mount_path=pathlib.Path(td),
                revision='deadbeef',
            )

        self.assertEqual(report['provider'], 'cloudflare_r2')
        self.assertEqual(report['canonical_namespace'], 'MASTER_CLONE/')
        self.assertTrue(report['runtime_credentials_complete'])
        self.assertTrue(report['drive_runtime_env_clear'])
        self.assertFalse(report['runtime_credential_identity_verified'])
        self.assertFalse(report['network_action_performed'])
        self.assertFalse(report['secret_values_exposed'])

        serialized = json.dumps(report, sort_keys=True)
        for secret in (
            fake_env['AI_TWIN_STORAGE_BUCKET'],
            fake_env['AI_TWIN_STORAGE_ENDPOINT'],
            fake_env['AI_TWIN_STORAGE_ACCESS_KEY_ID'],
            fake_env['AI_TWIN_STORAGE_SECRET_ACCESS_KEY'],
        ):
            self.assertNotIn(secret, serialized)


    def test_provider_probes_are_scoped_and_non_destructive(self):
        from runpod.runtime_attestation import probe_canonical_read, probe_controlled_write_inside

        class FakeS3:
            def __init__(self):
                self.head_calls = []
                self.put_calls = []

            def head_object(self, **kwargs):
                self.head_calls.append(kwargs)
                return {'ContentLength': 1}

            def put_object(self, **kwargs):
                self.put_calls.append(kwargs)
                return {'ETag': '"test"'}

        client = FakeS3()
        with mock.patch.dict(os.environ, {'AI_TWIN_STORAGE_BUCKET': 'canonical-bucket'}, clear=True):
            read_result = probe_canonical_read(
                client,
                'canonical-bucket',
                'MASTER_CLONE/MEMORY/storage_migration_manifest_v1.json',
            )
            write_result = probe_controlled_write_inside(
                client,
                'canonical-bucket',
                probe_id='unit-test-probe',
            )

        self.assertTrue(read_result['provider_read_verified'])
        self.assertEqual(
            client.head_calls[0]['Key'],
            'MASTER_CLONE/MEMORY/storage_migration_manifest_v1.json',
        )
        self.assertTrue(write_result['provider_write_inside_namespace_verified'])
        self.assertEqual(
            client.put_calls[0]['Key'],
            'MASTER_CLONE/TESTS/ISOLATION_PROBES/unit-test-probe.json',
        )
        self.assertFalse(write_result['delete_performed'])

if __name__ == '__main__':
    unittest.main()
