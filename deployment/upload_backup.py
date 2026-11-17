#!/usr/bin/env python3
"""Upload one private PostgreSQL archive using the VM's Oracle identity."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


def upload_backup(path, backup_dir, client, namespace, bucket):
    path = Path(path)
    root = Path(backup_dir).resolve(strict=True)
    if path.is_symlink() or path.suffix != '.dump' or path.resolve(strict=True).parent != root:
        raise ValueError('Only a completed archive in the private backup directory can be uploaded')
    checksum = hashlib.md5(usedforsecurity=False)  # OCI transfer-integrity protocol.
    sha256 = hashlib.sha256()
    name = f'database/{path.stem}-{uuid.uuid4().hex}.dump'
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
            sha256.update(chunk)
        size = stream.tell()
        if not size or size > 100 * 1024 * 1024:
            raise ValueError('Archive is empty or exceeds the 100 MiB upload limit; review storage usage')
        stream.seek(0)
        expected = base64.b64encode(checksum.digest()).decode('ascii')
        response = client.put_object(
            namespace, bucket, name, stream, content_length=size,
            content_md5=expected, if_none_match='*',
            content_type='application/octet-stream',
            opc_meta={'sha256': sha256.hexdigest(), 'format': 'postgresql-custom'})
    if response.headers.get('opc-content-md5') != expected:
        raise RuntimeError('Oracle did not confirm the archive checksum; upload is not verified')
    receipt = {'bucket': bucket, 'namespace': namespace, 'object': name,
               'bytes': size, 'sha256': sha256.hexdigest(), 'checksum_verified': True}
    # Persist the restore location privately, only after verified upload success.
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.upload-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(receipt, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path.with_suffix('.dump.upload.json'))
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def main():
    import oci
    if len(sys.argv) != 2:
        raise SystemExit('Usage: upload_backup.py /private/backup/archive.dump')
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    client = oci.object_storage.ObjectStorageClient(
        {'region': os.environ['STECHPAY_BACKUP_REGION']}, signer=signer, timeout=(10, 60))
    receipt = upload_backup(sys.argv[1], os.environ['STECHPAY_BACKUP_DIR'], client,
                           os.environ['STECHPAY_BACKUP_NAMESPACE'], os.environ['STECHPAY_BACKUP_BUCKET'])
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
