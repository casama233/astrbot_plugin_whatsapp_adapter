from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CanonicalArchitectureTests(unittest.TestCase):
    def test_public_classes_have_explicit_methods_and_no_source_execution(self):
        for filename, class_name in [('whatsapp_adapter.py', 'WhatsAppPlatformAdapter'),
                                     ('whatsapp_event.py', 'WhatsAppMessageEvent'),
                                     ('whatsapp_client.py', 'GatewayProcess')]:
            tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
            classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
            self.assertEqual(len(classes), 1)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {'exec', 'compile'})
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                            self.assertNotIn(target.value.id, {'WhatsAppPlatformAdapter', 'WhatsAppMessageEvent', 'GatewayProcess', 'WhatsAppGatewayClient'})
            methods = [n.name for n in classes[0].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            self.assertEqual(len(methods), len(set(methods)), 'duplicate method implementation')

    def test_gateway_startup_does_not_transform_or_write_source(self):
        launcher = (ROOT / 'gateway/whatsapp-gateway.mjs').read_text(encoding="utf-8").strip()
        self.assertEqual(launcher, 'import "./whatsapp-gateway-impl.mjs";')
        security = ast.parse((ROOT / 'gateway_security.py').read_text(encoding="utf-8"))
        for node in ast.walk(security):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotEqual(node.func.attr, 'create_subprocess_exec')
        client = ast.parse((ROOT / 'whatsapp_client.py').read_text(encoding="utf-8"))
        start = next(n for n in ast.walk(client) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_start_unlocked')
        spawns = [n for n in ast.walk(start) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'create_subprocess_exec']
        self.assertEqual(len(spawns), 1)
