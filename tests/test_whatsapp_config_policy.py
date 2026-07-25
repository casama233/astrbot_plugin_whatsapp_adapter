from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from whatsapp_config_policy import (
    MEDIA_CAPTION_MODES,
    merge_runtime_config,
    normalize_media_caption_mode,
)

ROOT = Path(__file__).resolve().parents[1]


class WhatsAppConfigPolicyTests(unittest.TestCase):
    def test_platform_instance_overrides_plugin_defaults(self) -> None:
        merged = merge_runtime_config(
            {"media_caption_mode": "separate", "typing_indicator": True},
            {"media_caption_mode": "caption", "typing_indicator": False},
            {"media_caption_mode": "separate"},
        )
        self.assertEqual(merged["media_caption_mode"], "separate")
        self.assertFalse(merged["typing_indicator"])

    def test_plugin_config_is_only_a_default(self) -> None:
        merged = merge_runtime_config(
            {"media_caption_mode": "separate"},
            {"media_caption_mode": "caption"},
            {},
        )
        self.assertEqual(merged["media_caption_mode"], "caption")

    def test_media_caption_mode_validation(self) -> None:
        self.assertEqual(MEDIA_CAPTION_MODES, ("separate", "caption"))
        self.assertEqual(normalize_media_caption_mode(" CAPTION "), "caption")
        self.assertEqual(normalize_media_caption_mode("separate"), "separate")
        for invalid in (None, "", "before", "after", 123):
            with self.subTest(invalid=invalid):
                self.assertEqual(normalize_media_caption_mode(invalid), "separate")

    def test_instance_message_settings_are_visible_on_platform_ui(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        tree = ast.parse(source)
        default_keys: set[str] = set()
        metadata: dict = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if not isinstance(target, ast.Name):
                continue
            if target.id == "DEFAULT_CONFIG" and isinstance(node.value, ast.Dict):
                default_keys = {
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
            if target.id == "CONFIG_METADATA":
                metadata = ast.literal_eval(node.value)

        expected = {
            "command_prefix",
            "register_commands",
            "media_caption_mode",
            "text_chunk_limit",
            "link_preview_single_url",
            "typing_indicator",
            "send_read_receipts",
            "mark_online",
            "ignore_self_messages",
            "parse_inbound_formatting",
            "media_album_debounce_seconds",
            "media_max_mb",
            "apply_ephemeral",
            "streaming_edit_throttle",
        }
        self.assertTrue(expected <= default_keys)
        self.assertEqual(
            metadata["media_caption_mode"]["options"],
            ["separate", "caption"],
        )

    def test_instance_settings_are_not_moved_to_plugin_schema(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
        for key in (
            "media_caption_mode",
            "text_chunk_limit",
            "typing_indicator",
            "streaming_edit_throttle",
        ):
            self.assertNotIn(key, schema)

    def test_adapter_uses_specificity_order_helper(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        self.assertIn(
            "merge_runtime_config(\n            RUNTIME_DEFAULT_CONFIG,\n"
            "            plugin_config,\n            platform_config,\n        )",
            source,
        )
        self.assertIn(
            'if key == "media_caption_mode":\n'
            "            return normalize_media_caption_mode(value)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
