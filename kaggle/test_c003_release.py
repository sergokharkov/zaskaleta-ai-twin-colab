#!/usr/bin/env python3
"""CPU-only C003 regression tests. No private data, model loading, network, or GPU."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

renderer = load('c003_renderer_test', 'kaggle/first_gate_alignment_render.py')
autopilot = load('c003_autopilot_test', 'kaggle/autopilot_kernel_c003.py')
entry = load('c003_entry_test', 'kaggle/verified_c003_entry.py')
recovery = load('c003_recovery_test', 'kaggle/recover_c003.py')

class C003Contracts(unittest.TestCase):
    def test_status_safety_cannot_be_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(autopilot, 'STATUS', Path(tmp) / 'status.json'), mock.patch.object(autopilot, 'mounted_input_dirs', return_value=[]):
                for state in ('WAITING_FOR_PRIVATE_ASSETS', 'FAILED_CLOSED', 'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW'):
                    autopilot.write_status(state=state, auto_promote=True, promotion_allowed=True, stable_release_modified=True)
                    data = json.loads((Path(tmp) / 'status.json').read_text())
                    self.assertIs(data['auto_promote'], False)
                    self.assertIs(data['promotion_allowed'], False)
                    self.assertIs(data['stable_release_modified'], False)
                    self.assertEqual(data['first_gate_candidate_id'], recovery.CANDIDATE)
                    self.assertIs(data['render_completed'], False)

    def test_reference_fps_and_no_repetition(self):
        meta = {'format': {'duration': '12.0'}, 'streams': [{'codec_type': 'video', 'avg_frame_rate': '25/1'}]}
        policy = {'preserve_reference_fps': 25, 'do_not_repeat_reference_motion': True, 'do_not_loop_audio': True}
        self.assertEqual(renderer.enforce_reference_policy(meta, 10.0, policy), 25)
        bad = copy.deepcopy(meta)
        bad['streams'][0]['avg_frame_rate'] = '30/1'
        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(bad, 10.0, policy)
        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(meta, 13.0, policy)
        with self.assertRaises(RuntimeError): renderer.enforce_reference_policy(meta, 10.0, {**policy, 'do_not_repeat_reference_motion': False})

    def test_final_audio_and_video_timing(self):
        video = {'codec_type': 'video', 'codec_name': 'h264', 'avg_frame_rate': '25/1', 'width': 1080, 'height': 1920}
        audio = {'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '24000'}
        meta = {'format': {'duration': '10.02'}, 'streams': [video, audio]}
        intermediate = {'streams': [video]}
        self.assertAlmostEqual(renderer.validate_final(meta, 10.0, 24000, 25, intermediate), 10.02)
        for changed in ({'sample_rate': '22050'}, {'sample_rate': '16000'}):
            bad = copy.deepcopy(meta)
            bad['streams'][1].update(changed)
            with self.assertRaises(RuntimeError): renderer.validate_final(bad, 10.0, 24000, 25, intermediate)
        bad = copy.deepcopy(meta)
        bad['format']['duration'] = '9.0'
        with self.assertRaises(RuntimeError): renderer.validate_final(bad, 10.0, 24000, 25, intermediate)
        bad = copy.deepcopy(meta)
        bad['streams'][0]['avg_frame_rate'] = '30/1'
        with self.assertRaises(RuntimeError): renderer.validate_final(bad, 10.0, 24000, 25, intermediate)

    def test_archive_rejects_traversal_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('safe/file.py', 'pass')
            with zipfile.ZipFile(path) as z:
                self.assertIn('safe/file.py', entry.safe_archive_members(z))
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('../escape.py', 'pass')
            with zipfile.ZipFile(path) as z:
                with self.assertRaises(RuntimeError): entry.safe_archive_members(z)
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('same.py', 'a')
                z.writestr('same.py', 'b')
            with zipfile.ZipFile(path) as z:
                with self.assertRaises(RuntimeError): entry.safe_archive_members(z)

    def test_exact_candidate_and_run_identity(self):
        manifest = {'run_token': '123-1', 'source_sha': 'a' * 40}
        status = {'schema': 'zaskaleta-kaggle-autopilot-status-v6', 'run_token': '123-1', 'source_sha': 'a'*40, 'promotion_allowed': False, 'auto_promote': False, 'stable_release_modified': False, 'state': 'FIRST_GATE_CANDIDATE_003_READY_FOR_MANUAL_REVIEW', 'first_gate_candidate_id': recovery.CANDIDATE, 'render_completed': True}
        evidence = {'run_token': '123-1', 'source_sha': 'a'*40, 'promotion_allowed': False, 'auto_promote': False, 'stable_release_modified': False, 'candidate_id': recovery.CANDIDATE, 'technical_gate_pass': True, 'single_component_change': 'audio_alignment', 'lipsync_sample_rate': 16000, 'final_audio_sample_rate': 24000, 'render_duration_seconds': 10, 'subjective_identity_review': 'PENDING_MANUAL_REVIEW'}
        recovery.validate(status, evidence, manifest)
        for target, key, value in ((status, 'first_gate_candidate_id', 'MASTER_CLONE_GATE_08_15_CANDIDATE_002'), (status, 'run_token', 'old-1'), (evidence, 'auto_promote', True), (evidence, 'final_audio_sample_rate', 22050), (evidence, 'render_duration_seconds', 16)):
            bad_status, bad_evidence = copy.deepcopy(status), copy.deepcopy(evidence)
            (bad_status if target is status else bad_evidence)[key] = value
            with self.assertRaises(RuntimeError): recovery.validate(bad_status, bad_evidence, manifest)

    def test_complete_archive_and_dirty_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'kaggle').mkdir()
            (root / 'kaggle/verified_c003_entry.py').write_text('print("test")\n')
            (root / 'sample.py').write_text('value = 1\n')
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            git('add', '.')
            git('commit', '-qm', 'fixture')
            source = git('rev-parse', 'HEAD')
            original_root, original_files = recovery.ROOT, recovery.SOURCE_FILES
            try:
                recovery.ROOT = root
                recovery.SOURCE_FILES = ['kaggle/verified_c003_entry.py', 'sample.py']
                manifest = recovery.build(root / '.package', source, '123-1', 'test/c003', 'dry-run/dataset')
                self.assertEqual(set(manifest['source_hashes']), set(recovery.SOURCE_FILES))
                with self.assertRaises(RuntimeError): recovery.build(root / '.wrong', '0'*40, '123-1', 'test/c003', 'dry-run/dataset')
                (root / 'sample.py').write_text('value = 2\n')
                with self.assertRaises(RuntimeError): recovery.build(root / '.dirty', source, '123-1', 'test/c003', 'dry-run/dataset')
            finally:
                recovery.ROOT, recovery.SOURCE_FILES = original_root, original_files

if __name__ == '__main__':
    unittest.main(verbosity=2)
