import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'], cwd=ROOT).decode().split('\0')
    names = [name for name in names if name]
    if not names:
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        names = [name for name in names if name]
    forbidden = ('.venv/', '.language-venv/', '.devin/', 'cache/', 'historical/', '__pycache__/')
    patterns = [rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----', rb'github_pat_[A-Za-z0-9_]{20,}', rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'AKIA[0-9A-Z]{16}', rb'sk-[A-Za-z0-9]{32,}', rb'xox[baprs]-[A-Za-z0-9-]{20,}']
    files, issues = {}, []
    for name in names:
        content = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        files[name] = content
        if name.startswith(forbidden) or '/backup' in name.lower() or Path(name).name.startswith('.env') or name.endswith(('.log', '.pem', '.key', '.zip', '.gz', '.whl')):
            issues.append((name, 'forbidden artifact path'))
        if len(content) > 2_000_000:
            issues.append((name, 'file larger than 2 MB'))
        if any(re.search(pattern, content) for pattern in patterns):
            issues.append((name, 'possible credential signature; value not printed'))
        if name.startswith('fixtures/') and re.search(rb'(?i)(authorization:|set-cookie:|/Users/|/home/)', content):
            issues.append((name, 'unsanitized fixture metadata or absolute user path'))
    if issues:
        raise SystemExit(json.dumps({'blocked_files': issues}, indent=2))
    with tempfile.TemporaryDirectory(prefix='signal-noise-checkout-') as temporary:
        target = Path(temporary)
        for name, content in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        check = "import socket,unittest; from unittest.mock import patch; block=AssertionError('Handoff demo test must be offline'); p1=patch('socket.socket.connect',side_effect=block); p2=patch('socket.getaddrinfo',side_effect=block); p1.start(); p2.start(); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('.', pattern='test_demo.py')); raise SystemExit(not result.wasSuccessful())"
        subprocess.run([sys.executable, '-c', check], cwd=target, env=env, check=True)
    print(json.dumps({'staged_files': len(files), 'total_bytes': sum(map(len, files.values())), 'largest_file_bytes': max(map(len, files.values())), 'runtime_json_fixtures': 3, 'runtime_publisher_text_fixtures': len([name for name in names if name.startswith('fixtures/shipping-msc-2026/pages/')]), 'credential_signature_scan': 'no matches; not a guarantee against every secret format', 'isolated_staged_checkout_demo_tests': 'passed with network blocked'}, indent=2))


if __name__ == '__main__':
    main()
