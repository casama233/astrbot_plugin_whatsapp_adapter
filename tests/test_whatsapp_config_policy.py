from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from whatsapp_config_policy import (
    FIXED_RUNTIME_KEYS,
    MEDIA_CAPTION_MODES,
    PLUGIN_DEFAULT_ALIASES,
    extract_plugin_defaults,
    merge_runtime_config,
    normalize_media_caption_mode,
)

ROOT = Path(__file__).resolve().parents[1]


def _top_level_dict(source: str, name: str) -> dict:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found")


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
            {"typing_indicator": True},
            {"typing_indicator": False},
            {},
        )
        self.assertFalse(merged["typing_indicator"])

    def test_media_caption_mode_validation(self) -> None:
        self.assertEqual(MEDIA_CAPTION_MODES, ("separate", "caption"))
        self.assertEqual(normalize_media_caption_mode(" CAPTION "), "caption")
        for invalid in (None, "", "before", "after", 123):
            with self.subTest(invalid=invalid):
                self.assertEqual(normalize_media_caption_mode(invalid), "separate")

    def test_plugin_default_aliases_and_fixed_keys(self) -> None:
        config = {
            "default_typing_indicator": False,
            "default_streaming_edit_throttle": 0.5,
            "gateway_port": 18888,
            "text_chunk_limit": 100,
            "media_max_mb": 1,
            "command_prefix": "!",
            "register_commands": False,
            "unknown": "ignored",
        }
        extracted = extract_plugin_defaults(config)
        self.assertEqual(extracted["typing_indicator"], False)
        self.assertEqual(extracted["streaming_edit_throttle"], 0.5)
        self.assertEqual(extracted["gateway_port"], 18888)
        for key in FIXED_RUNTIME_KEYS:
            self.assertNotIn(key, extracted)
        self.assertNotIn("unknown", extracted)

    def test_platform_ui_contains_only_instance_specific_message_options(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        defaults = _top_level_dict(source, "DEFAULT_CONFIG")
        self.assertIn("media_caption_mode", defaults)
        self.assertIn("ignore_self_messages", defaults)
        self.assertIn("apply_ephemeral", defaults)

        hidden = {
            "command_prefix",
            "register_commands",
            "text_chunk_limit",
            "media_max_mb",
            "link_preview_single_url",
            "typing_indicator",
            "send_read_receipts",
            "mark_online",
            "parse_inbound_formatting",
            "media_album_debounce_seconds",
            "streaming_edit_throttle",
        }
        self.assertTrue(hidden.isdisjoint(defaults))

    def test_plugin_schema_exposes_only_prefixed_global_defaults(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
        self.assertTrue(set(PLUGIN_DEFAULT_ALIASES) <= set(schema))
        for runtime_key in PLUGIN_DEFAULT_ALIASES.values():
            self.assertNotIn(runtime_key, schema)
        for fixed_key in FIXED_RUNTIME_KEYS:
            self.assertNotIn(fixed_key, schema)

    def test_adapter_filters_stale_platform_keys(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        self.assertIn("extract_plugin_defaults(loaded_plugin_config)", source)
        self.assertIn("if key in PERSISTED_PLATFORM_KEYS", source)
        self.assertIn(
            "merge_runtime_config(
            RUNTIME_DEFAULT_CONFIG,
"
            "            plugin_config,
            platform_config,
        )",
            source,
        )


if __name__ == "__main__":
    unittest.main()
