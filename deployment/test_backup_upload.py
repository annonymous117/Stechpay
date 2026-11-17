"""Offline checks for backup transfer integrity and failed-upload reporting."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('upload_backup', Path(__file__).resolve().parents[1] / 'deployment/upload_backup.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupUploadTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='stechpay-upload-test-'))
        self.backup = self.root / 'test.dump'
        self.backup.write_bytes(b'PGDMP-test-archive')
        self.client = Mock()
        checksum = base64.b64encode(hashlib.md5(self.backup.read_bytes(), usedforsecurity=False).digest()).decode()
        self.client.put_object.return_value = SimpleNamespace(headers={'opc-content-md5': checksum})

    def tearDown(self):
        # This directory contains only files created by this test; no recursive deletion.
        for path in self.root.iterdir():
            path.unlink()
        self.root.rmdir()

    def test_verified_upload_records_restore_location_without_overwrite(self):
        receipt = module.upload_backup(self.backup, self.root, self.client, 'test-namespace', 'test-bucket')
        saved = json.loads(self.backup.with_suffix('.dump.upload.json').read_text())
        self.assertEqual(saved, receipt)
        self.assertEqual(receipt['sha256'], hashlib.sha256(self.backup.read_bytes()).hexdigest())
        self.assertEqual(self.client.put_object.call_args.kwargs['if_none_match'], '*')

    def test_checksum_mismatch_does_not_report_success(self):
        self.client.put_object.return_value.headers['opc-content-md5'] = 'wrong'
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            module.upload_backup(self.backup, self.root, self.client, 'test-namespace', 'test-bucket')
        self.assertFalse(self.backup.with_suffix('.dump.upload.json').exists())

    def test_permission_error_preserves_local_backup_without_success_receipt(self):
        self.client.put_object.side_effect = PermissionError('Upload denied')
        with self.assertRaises(PermissionError):
            module.upload_backup(self.backup, self.root, self.client, 'test-namespace', 'test-bucket')
        self.assertTrue(self.backup.exists())
        self.assertFalse(self.backup.with_suffix('.dump.upload.json').exists())

    def test_file_outside_backup_directory_is_rejected_before_upload(self):
        with self.assertRaises(ValueError):
            module.upload_backup(Path(__file__), self.root, self.client, 'test-namespace', 'test-bucket')
        self.client.put_object.assert_not_called()


if __name__ == '__main__':
    unittest.main()
