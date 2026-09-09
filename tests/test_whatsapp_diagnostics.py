from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from whatsapp_diagnostics import build_identity, diagnostic_snapshot


class DiagnosticTests(unittest.TestCase):
    def test_export_omits_auth_qr_pairing_identifiers_and_masks_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = 'synthetic-auth-token'
            report = diagnostic_snapshot(root, '0.2.45', '4.28.0',
                {'status': 'dependencies_current', 'ready': True, 'node': {'version': 'v24.20.0'}},
                {'status': 'connected', 'ready': True, 'selfJid': '15551234567@s.whatsapp.net',
                 'qr': 'secret-qr', 'pairCode': 'ABCD-EFGH', 'auth': {'token': token},
                 'runtime': {'node': 'v24.20.0', 'baileys': '7.0.0-rc14'},
                 'lastError': f'Failed for 15551234567@s.whatsapp.net +1 (555) 123-4567 Bearer {token}; pairing_code=ABCD-EFGH'},
                {'endpoint': f'http://user:{token}@127.0.0.1:18789/?token={token}',
                 'targetInstanceId': 'account-15551234567', 'accounts': []},
                [{'instanceId': 'default', 'config': {'allow_from': ['15551234567'], 'auth_dir': '/secret/auth', 'gateway_port': 18789},
                  'sources': {'allow_from': 'platform_instance', 'auth_dir': 'plugin_default', 'gateway_port': 'plugin_default'}}],
                {'version': 1, 'adopted_plugin_values': {'auth_dir': '/secret/auth'}}, [token, 'secret-qr', 'ABCD-EFGH'])
        text = json.dumps(report)
        for private in ['15551234567', 'secret-qr', 'ABCD-EFGH', token, '/secret/auth', 's.whatsapp.net']:
            self.assertNotIn(private, text)
        self.assertEqual(report['scope']['endpoint'], 'http://127.0.0.1:18789')
        self.assertEqual(report['environment']['baileys'], '7.0.0-rc14')
        self.assertEqual(report['configurations'][0]['values'][-1]['value'], 18789)
        self.assertEqual(report['build']['source'], 'unknown')

    def test_build_identifies_modified_release_and_normalizes_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'main.py').write_bytes(b'x\r\n')
            info = {'version': '1.2.3', 'sourceCommit': 'a' * 40, 'files': {'main.py': hashlib.sha256(b'x\n').hexdigest()}}
            (root / '.build-info.json').write_text(json.dumps(info), encoding='utf-8')
            self.assertEqual(build_identity(root, '1.2.3')['source'], 'release')
            (root / 'main.py').write_bytes(b'changed\n')
            result = build_identity(root, '1.2.3')
            self.assertEqual(result['source'], 'modified_release')
            self.assertEqual(result['modifiedFiles'], ['main.py'])
            self.assertEqual(build_identity(root, '9.9.9')['source'], 'unknown')


class RuntimeDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_preflight_node_and_npm_errors_survive_sanitized_export(self):
        from unittest.mock import AsyncMock, patch
        import gateway_runtime
        from tests.test_gateway_runtime import node_output
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scope = {'endpoint': 'http://127.0.0.1:18789', 'accounts': []}
            def report(runtime):
                return diagnostic_snapshot(root, '0.2.46', '4.28.0', runtime, {}, scope, [], {},
                                           ['synthetic-secret'])['runtime']
            with (
                patch('gateway_runtime.shutil.which', return_value='node'),
                patch('gateway_stability._run_node_command', AsyncMock(side_effect=RuntimeError(
                    'Cannot start node: permission denied; token=synthetic-secret'))),
            ):
                runtime = await gateway_runtime.inspect_gateway_requirements(root, 'node')
            result = report(runtime)
            self.assertEqual(result['status'], 'node_unavailable')
            self.assertIn('permission denied', result['error'])
            self.assertEqual(result['nodeError'], result['error'])
            self.assertEqual(result['npmError'], '')
            self.assertNotIn('synthetic-secret', json.dumps(result))
            with (
                patch('gateway_runtime.shutil.which', return_value='node'),
                patch('gateway_stability._run_node_command', AsyncMock(return_value=node_output())),
                patch('gateway_runtime._npm_command', side_effect=RuntimeError('npm-cli.js absent')),
            ):
                runtime = await gateway_runtime.inspect_gateway_requirements(root, 'node')
            result = report(runtime)
            self.assertEqual(result['status'], 'npm_unavailable')
            self.assertIn('npm-cli.js absent', result['npmError'])
            self.assertEqual(result['error'], result['npmError'])
            self.assertEqual(result['nodeError'], '')
            self.assertEqual(report({'ready': True})['error'], '')
            result = report({'error': 'outer error', 'node': {'error': 'node detail'}})
            self.assertEqual(result['error'], 'outer error')
            self.assertEqual(result['nodeError'], 'node detail')
