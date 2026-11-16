"""Small, value-redacting CI guard for credentials in application/configuration files."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
patterns = [re.compile(r'sk_(?:live|test)_[A-Za-z0-9]{20,}'),
            re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
            re.compile(r'''(?i)["']?password["']?\s*[:=]\s*["'](?!\$)[^"']{6,}["']''')]
findings = []
for name in paths:
    path = Path(name)
    if not name.startswith(('backend/', 'frontend/', 'deployment/', '.github/')):
        continue
    if path.name in ('tests.py', 'test_settings.py') or path.name.startswith('test_') or path.suffix not in ('.py', '.js', '.jsx', '.yml', '.yaml', '.sh', '.ps1', '.example'):
        continue
    for number, line in enumerate((ROOT / name).read_text(encoding='utf-8-sig').splitlines(), 1):
        if 'REPLACE_' in line or 'ci-only-disposable-database' in line:
            continue
        if any(pattern.search(line) for pattern in patterns):
            findings.append(f'{name}:{number}: possible credential literal (value redacted)')
print('\n'.join(findings) if findings else 'No credential literals matched the application/configuration guard.')
raise SystemExit(bool(findings))
