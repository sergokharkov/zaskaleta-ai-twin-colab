import os
import pathlib
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

    def test_rejects_foreign_s3_bucket(self):
        with mock.patch.dict(os.environ, {'AI_CLONE_S3_BUCKET':'ai-clone-private'}, clear=False):
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

if __name__ == '__main__':
    unittest.main()
