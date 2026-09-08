"""Create release provenance from the candidate source, before its commit."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--version', required=True)
parser.add_argument('--source-commit', required=True)
args = parser.parse_args()
if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
    parser.error('source commit must be a full Git SHA')
root = Path(__file__).resolve().parents[1]
files = {}
tracked = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'])
attributes = subprocess.check_output(['git', '-C', str(root), 'check-attr', '-z', '--stdin', 'export-ignore'], input=tracked).decode().split('\0')
ignored = {attributes[i] for i in range(0, len(attributes) - 2, 3) if attributes[i + 2] == 'set'}
for name in tracked.decode().split('\0'):
    if name in ignored:
        continue
    if not name or name.startswith(('tests/', '.github/', '.release/')) or '.test.' in name:
        continue
    path = root / name
    if path.suffix not in {'.py', '.mjs', '.js', '.json', '.yaml', '.html', '.css', '.svg'} or name == '.build-info.json':
        continue
    files[name] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
(root / '.build-info.json').write_text(json.dumps({'version': args.version, 'sourceCommit': args.source_commit, 'files': files}, indent=2) + '\n', encoding='utf-8')
