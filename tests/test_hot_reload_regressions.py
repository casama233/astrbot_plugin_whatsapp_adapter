from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HotReloadRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "main.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_hot_swap_does_not_call_removed_send_buffer_initializer(self) -> None:
        restore = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_restore_platform_adapters"
        )
        called_attributes = {
            node.func.attr
            for node in ast.walk(restore)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertNotIn("_ensure_send_buffer_state", called_attributes)

    def test_gateway_health_success_log_has_one_placeholder_per_argument(self) -> None:
        ensure_gateway = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_ensure_page_gateway_unlocked"
        )
        matching_calls = []
        for node in ast.walk(ensure_gateway):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            first = node.args[0]
            if (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and "健康检查通过" in first.value
            ):
                matching_calls.append(node)
        self.assertEqual(len(matching_calls), 1)
        call = matching_calls[0]
        template = call.args[0].value
        self.assertEqual(template.count("%s"), len(call.args) - 1)
        self.assertIn("_safe_status", ast.unparse(call))


if __name__ == "__main__":
    unittest.main()
