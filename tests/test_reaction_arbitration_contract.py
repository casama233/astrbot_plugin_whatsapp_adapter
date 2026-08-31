from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


class ReactionArbitrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter_source = (ROOT / "whatsapp_adapter.py").read_text(
            encoding="utf-8"
        )
        cls.adapter_tree = ast.parse(cls.adapter_source)
        cls.event_source = (ROOT / "whatsapp_event.py").read_text(encoding="utf-8")
        cls.event_tree = ast.parse(cls.event_source)

    def test_inbound_reaction_is_recorded_before_legacy_handler_discards_it(self) -> None:
        handler = _function(
            self.adapter_tree,
            "_handle_msg_with_reaction_journal",
        )
        source = ast.unparse(handler)
        self.assertIn("self._is_reaction_only(raw)", source)
        self.assertIn("self.config.get('ignore_self_messages', False)", source)
        self.assertIn("_reaction_journal.record", source)
        self.assertIn("return await _original_handle_msg(self, message)", source)
        self.assertLess(
            source.index("_reaction_journal.record"),
            source.index("_original_handle_msg"),
        )

    def test_adapter_installs_inbound_wrapper(self) -> None:
        assignments = [
            ast.unparse(node)
            for node in self.adapter_tree.body
            if isinstance(node, ast.Assign)
        ]
        self.assertIn(
            "WhatsAppPlatformAdapter.handle_msg = _handle_msg_with_reaction_journal",
            assignments,
        )

    def test_outgoing_reaction_is_recorded_only_after_gateway_call(self) -> None:
        handler = _function(self.event_tree, "_react_with_reaction_journal")
        source = ast.unparse(handler)
        self.assertIn("await self.client.react", source)
        self.assertIn("_reaction_journal.record", source)
        self.assertLess(
            source.index("await self.client.react"),
            source.index("_reaction_journal.record"),
        )

    def test_event_exposes_arbitration_reader(self) -> None:
        reader = _function(self.event_tree, "_get_arbiter_reaction_users")
        self.assertIn("_reaction_journal.users", ast.unparse(reader))
        assignments = [
            ast.unparse(node)
            for node in self.event_tree.body
            if isinstance(node, ast.Assign)
        ]
        self.assertIn(
            "WhatsAppMessageEvent.get_arbiter_reaction_users = _get_arbiter_reaction_users",
            assignments,
        )


if __name__ == "__main__":
    unittest.main()
