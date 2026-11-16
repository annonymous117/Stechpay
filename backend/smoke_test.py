"""Run isolated checks instead of editing the configured application's database."""
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SECRET_KEY", "isolated-test-key-not-for-deployment-000000000000000000000")
    result = subprocess.run(
        [sys.executable, "manage.py", "test", "userapp", "--settings=stechpay.test_settings"],
        cwd=Path(__file__).resolve().parent,
        check=False,
    )
    sys.exit(result.returncode)
