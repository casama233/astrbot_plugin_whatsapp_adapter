"""Read-only diagnostics with a narrow, sanitized export contract."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

_JID = re.compile(r"[\w:+.\-]+@(?:s\.whatsapp\.net|g\.us|hosted(?:\.lid)?|lid)", re.I)
_PHONE = re.compile(r"(?<![\w])\+?\d[\d ()-]{5,}\d(?![\w])")
_SECRET = re.compile(r"(?i)(bearer\s+)[^\s,;]+|((?:token|password|secret|pair(?:ing)?[ _-]?code|qr(?:dataurl)?)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+")


def sanitize_text(value, secrets=()):
    text = str(value or "")[:4000]
    for secret in secrets:
        if secret:
            text = text.replace(str(secret), '<redacted>')
    text = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or '') + '<redacted>', text)
    text = _JID.sub('<jid>', text)
    return _PHONE.sub('<phone>', text)


def safe_endpoint(value):
    try:
        parsed = urlsplit(str(value))
        host = parsed.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        return f'{parsed.scheme}://{host}' + (f':{parsed.port}' if parsed.port else '')
    except ValueError:
        return '<invalid endpoint>'


def build_identity(root: Path, version: str):
    try:
        info = json.loads((root / '.build-info.json').read_text(encoding='utf-8'))
        if info.get('version') != version or not re.fullmatch(r'[0-9a-f]{40}', info.get('sourceCommit', '')):
            raise ValueError('invalid build identity')
        if not isinstance(info.get('files'), dict) or not info['files']:
            raise ValueError('empty build manifest')
        changed = []
        for name, expected in info.get('files', {}).items():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise ValueError('invalid build manifest')
            path = root / relative
            if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
                changed.append(name)
        return {'source': 'modified_release' if changed else 'release',
                'sourceCommit': info['sourceCommit'], 'modifiedFiles': sorted(changed)}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if (root / '.git').exists():
        try:
            commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], timeout=2, stderr=subprocess.DEVNULL).decode().strip()
            dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], timeout=2))
            return {'source': 'development', 'sourceCommit': commit, 'modified': dirty}
        except (OSError, subprocess.SubprocessError):
            pass
    return {'source': 'unknown', 'sourceCommit': None}


def safe_configuration(config, sources, secrets=()):
    rows = []
    for key in sorted(sources):
        if key.startswith('_') or key not in config:
            continue
        value = config[key]
        if key == 'auth_dir':
            value = '<custom>' if value else '<default>'
        elif isinstance(value, (list, dict)):
            value = {'entries': len(value)}
        elif isinstance(value, str):
            value = sanitize_text(value, secrets)
        rows.append({'key': key, 'value': value, 'source': sources[key]})
    return rows


def diagnostic_snapshot(root, version, astrbot_version, runtime, status, scope, configurations, migration, secrets=()):
    gateway_runtime = status.get('runtime') or {}
    def version_value(value):
        return value if isinstance(value, str) and re.fullmatch(r'v?\d+\.\d+\.\d+(?:-[\w.-]+)?', value) else 'unknown'
    def state_value(value):
        return sanitize_text(value, secrets)[:80]

    return {
        'pluginVersion': version, 'build': build_identity(root, version),
        'environment': {'astrbot': version_value(astrbot_version), 'python': sys.version.split()[0],
                        'configuredNode': version_value((runtime.get('node') or {}).get('version')),
                        'gatewayNode': version_value(gateway_runtime.get('node')),
                        'baileys': version_value(gateway_runtime.get('baileys'))},
        'runtime': {'status': state_value(runtime.get('status', 'unknown')), 'ready': bool(runtime.get('ready')),
                    'error': sanitize_text(runtime.get('error'), secrets)},
        'gateway': {'status': state_value(status.get('status', 'unreachable')), 'ready': bool(status.get('ready')),
                    'configured': bool(status.get('configured', status.get('config'))),
                    'lastError': sanitize_text(status.get('lastError') or status.get('error'), secrets)},
        'scope': {'operation': 'base_gateway', 'endpoint': safe_endpoint(scope['endpoint']),
                  'targetInstanceId': sanitize_text(scope.get('targetInstanceId'), secrets) or None,
                  'accounts': [{'instanceId': sanitize_text(a['instanceId'], secrets),
                                'endpoint': safe_endpoint(a['endpoint']), 'managed': a['managed']}
                               for a in scope['accounts']]},
        'configurationMigration': {'version': migration.get('version', 0)},
        'configurations': [{'instanceId': sanitize_text(c['instanceId'], secrets),
                            'values': safe_configuration(c['config'], c['sources'], secrets)}
                           for c in configurations],
    }
