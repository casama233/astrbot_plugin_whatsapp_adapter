from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from whatsapp_config_policy import (
    CONFIG_ENUM_DEFAULTS,
    CONFIG_ENUM_OPTIONS,
    DM_POLICIES,
    FIXED_RUNTIME_KEYS,
    GROUP_POLICIES,
    LOG_LEVELS,
    MEDIA_CAPTION_MODES,
    PLUGIN_DEFAULT_ALIASES,
    PRE_ACK_PUBLIC_MODES,
    extract_plugin_defaults,
    merge_runtime_config,
    normalize_config_enum,
    normalize_media_caption_mode,
    normalize_pre_ack_public,
)

ROOT = Path(__file__).resolve().parents[1]


def _top_level_dict_keys(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if not isinstance(target, ast.Name) or target.id != name:
            continue
        if not isinstance(node.value, ast.Dict):
            raise AssertionError(f"{name} is not a dict literal")
        return {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    raise AssertionError(f"{name} not found")


def _metadata_option_bindings(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "CONFIG_METADATA":
            continue
        if not isinstance(node.value, ast.Dict):
            raise AssertionError("CONFIG_METADATA is not a dict literal")
        bindings: dict[str, str] = {}
        for key_node, value_node in zip(node.value.keys, node.value.values):
            if not (
                isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)
                and isinstance(value_node, ast.Dict)
            ):
                continue
            for inner_key, inner_value in zip(value_node.keys, value_node.values):
                if not (
                    isinstance(inner_key, ast.Constant)
                    and inner_key.value == "options"
                ):
                    continue
                if not (
                    isinstance(inner_value, ast.Call)
                    and isinstance(inner_value.func, ast.Name)
                    and inner_value.func.id == "list"
                    and len(inner_value.args) == 1
                    and isinstance(inner_value.args[0], ast.Name)
                ):
                    raise AssertionError(
                        f"{key_node.value} options must use list(CONSTANT)"
                    )
                bindings[key_node.value] = inner_value.args[0].id
        return bindings
    raise AssertionError("CONFIG_METADATA not found")


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

    def test_all_finite_choices_have_safe_normalization(self) -> None:
        self.assertEqual(LOG_LEVELS, ("silent", "fatal", "error", "warn", "info", "debug", "trace"))
        self.assertEqual(DM_POLICIES, ("allowlist", "open", "disabled"))
        self.assertEqual(GROUP_POLICIES, ("allowlist", "open", "disabled"))
        self.assertEqual(MEDIA_CAPTION_MODES, ("separate", "caption"))
        self.assertEqual(PRE_ACK_PUBLIC_MODES, ("always", "mentions", "never"))
        self.assertEqual(set(CONFIG_ENUM_OPTIONS), set(CONFIG_ENUM_DEFAULTS))

        valid_values = {
            "log_level": " DEBUG ",
            "dm_policy": " OPEN ",
            "group_policy": " ALLOWLIST ",
            "media_caption_mode": " CAPTION ",
            "pre_ack_public": " ALWAYS ",
        }
        for key, value in valid_values.items():
            with self.subTest(key=key):
                self.assertEqual(normalize_config_enum(key, value), value.strip().lower())

        for key, default in CONFIG_ENUM_DEFAULTS.items():
            with self.subTest(key=key):
                self.assertEqual(normalize_config_enum(key, "invalid"), default)

        with self.assertRaises(ValueError):
            normalize_config_enum("unknown", "value")

    def test_media_caption_and_pre_ack_legacy_values(self) -> None:
        self.assertEqual(normalize_media_caption_mode(" CAPTION "), "caption")
        self.assertEqual(normalize_media_caption_mode("before"), "separate")
        self.assertEqual(normalize_pre_ack_public(True), "mentions")
        self.assertEqual(normalize_pre_ack_public(False), "never")
        self.assertEqual(normalize_pre_ack_public("yes"), "mentions")
        self.assertEqual(normalize_pre_ack_public("off"), "never")
        self.assertEqual(normalize_pre_ack_public("always"), "always")
        self.assertEqual(normalize_pre_ack_public("invalid"), "mentions")

    def test_plugin_default_aliases_and_fixed_keys(self) -> None:
        config = {
            "default_typing_indicator": False,
            "default_streaming_edit_throttle": 0.5,
            "gateway_port": 18888,
            "log_level": " DEBUG ",
            "text_chunk_limit": 100,
            "media_max_mb": 1,
            "command_prefix": "!",
            "register_commands": False,
            "unknown": "ignored",
        }
        extracted = extract_plugin_defaults(config)
        self.assertIs(extracted["typing_indicator"], False)
        self.assertEqual(extracted["streaming_edit_throttle"], 0.5)
        self.assertEqual(extracted["gateway_port"], 18888)
        self.assertEqual(extracted["log_level"], "debug")
        for key in FIXED_RUNTIME_KEYS:
            self.assertNotIn(key, extracted)
        self.assertNotIn("unknown", extracted)

    def test_platform_ui_contains_only_instance_specific_message_options(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        defaults = _top_level_dict_keys(source, "DEFAULT_CONFIG")
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

    def test_all_finite_platform_fields_are_dropdowns(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        self.assertEqual(
            _metadata_option_bindings(source),
            {
                "log_level": "LOG_LEVELS",
                "dm_policy": "DM_POLICIES",
                "group_policy": "GROUP_POLICIES",
                "media_caption_mode": "MEDIA_CAPTION_MODES",
                "pre_ack_public": "PRE_ACK_PUBLIC_MODES",
            },
        )
        self.assertIn(
            'if key in {"log_level", "dm_policy", "group_policy", "media_caption_mode"}:',
            source,
        )
        self.assertIn("return normalize_pre_ack_public(value)", source)

    def test_plugin_schema_exposes_only_prefixed_global_defaults(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
        self.assertTrue(set(PLUGIN_DEFAULT_ALIASES) <= set(schema))
        for runtime_key in PLUGIN_DEFAULT_ALIASES.values():
            self.assertNotIn(runtime_key, schema)
        for fixed_key in FIXED_RUNTIME_KEYS:
            self.assertNotIn(fixed_key, schema)
        self.assertEqual(schema["log_level"]["options"], list(LOG_LEVELS))

    def test_astrbot_owns_command_wake_handling(self) -> None:
        adapter_source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        main_source = (ROOT / "main.py").read_text("utf-8")
        runtime_keys = _top_level_dict_keys(adapter_source, "RUNTIME_DEFAULT_CONFIG")
        self.assertNotIn("command_prefix", runtime_keys)
        self.assertNotIn("register_commands", runtime_keys)
        for token in (
            "collect_registered_commands",
            "message_matches_command",
            "_refresh_registered_commands",
            "_registered_commands",
        ):
            self.assertNotIn(token, adapter_source)
            self.assertNotIn(token, main_source)

    def test_adapter_filters_stale_platform_keys(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        self.assertIn("extract_plugin_defaults(loaded_plugin_config)", source)
        self.assertIn("if key in PERSISTED_PLATFORM_KEYS", source)
        self.assertIn(
            "merge_runtime_config(
"
            "            RUNTIME_DEFAULT_CONFIG,
"
            "            plugin_config,
"
            "            platform_config,
"
            "        )",
            source,
        )


if __name__ == "__main__":
    unittest.main()
