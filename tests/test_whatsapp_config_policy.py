from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from whatsapp_config_policy import (
    CONFIG_ENUM_DEFAULTS,
    CONFIG_ENUM_OPTIONS,
    DM_POLICIES,
    FIXED_RUNTIME_KEYS,
    GROUP_POLICIES,
    LEGACY_BEHAVIOR_DEFAULTS,
    LOG_LEVELS,
    MEDIA_CAPTION_MODES,
    PLUGIN_DEFAULT_ALIASES,
    PRE_ACK_PUBLIC_MODES,
    adopt_legacy_gateway_defaults,
    extract_legacy_behavior_overrides,
    extract_legacy_command_prefix,
    extract_plugin_defaults,
    get_runtime_plugin_defaults,
    get_runtime_wake_prefixes,
    merge_runtime_config,
    normalize_config_enum,
    normalize_media_caption_mode,
    normalize_pre_ack_public,
    set_runtime_plugin_defaults,
    set_runtime_wake_prefixes,
)

ROOT = Path(__file__).resolve().parents[1]


def _top_level_dict_keys(source: str, name: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        if isinstance(target, ast.Name) and target.id == name:
            if not isinstance(node.value, ast.Dict):
                raise AssertionError(f"{name} is not a dict literal")
            return {
                key.value for key in node.value.keys
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
        bindings: dict[str, str] = {}
        assert isinstance(node.value, ast.Dict)
        for key_node, value_node in zip(node.value.keys, node.value.values):
            if not (isinstance(key_node, ast.Constant) and isinstance(value_node, ast.Dict)):
                continue
            for inner_key, inner_value in zip(value_node.keys, value_node.values):
                if isinstance(inner_key, ast.Constant) and inner_key.value == "options":
                    assert isinstance(inner_value, ast.Call)
                    assert isinstance(inner_value.args[0], ast.Name)
                    bindings[str(key_node.value)] = inner_value.args[0].id
        return bindings
    raise AssertionError("CONFIG_METADATA not found")


def _adapter_implementation_source() -> str:
    return (ROOT / "_whatsapp_adapter_impl.py").read_text("utf-8")


class WhatsAppConfigPolicyTests(unittest.TestCase):
    def test_merge_priority(self) -> None:
        merged = merge_runtime_config(
            {"typing_indicator": True, "media_caption_mode": "separate"},
            {"typing_indicator": False, "media_caption_mode": "caption"},
            {"media_caption_mode": "separate"},
        )
        self.assertFalse(merged["typing_indicator"])
        self.assertEqual(merged["media_caption_mode"], "separate")

    def test_finite_choice_normalization(self) -> None:
        self.assertEqual(LOG_LEVELS, ("silent", "fatal", "error", "warn", "info", "debug", "trace"))
        self.assertEqual(DM_POLICIES, ("allowlist", "open", "disabled"))
        self.assertEqual(GROUP_POLICIES, ("allowlist", "open", "disabled"))
        self.assertEqual(MEDIA_CAPTION_MODES, ("separate", "caption"))
        self.assertEqual(PRE_ACK_PUBLIC_MODES, ("always", "mentions", "never"))
        self.assertEqual(set(CONFIG_ENUM_OPTIONS), set(CONFIG_ENUM_DEFAULTS))
        self.assertEqual(normalize_config_enum("log_level", " DEBUG "), "debug")
        self.assertEqual(normalize_config_enum("dm_policy", "invalid"), "allowlist")
        self.assertEqual(normalize_media_caption_mode(" CAPTION "), "caption")
        self.assertEqual(normalize_pre_ack_public(True), "mentions")
        self.assertEqual(normalize_pre_ack_public(False), "never")

    def test_plugin_defaults_and_fixed_keys(self) -> None:
        extracted = extract_plugin_defaults({
            "gateway_port": 18888,
            "log_level": " DEBUG ",
            "default_typing_indicator": False,
            "text_chunk_limit": 100,
            "media_max_mb": 1,
            "command_prefix": "!",
        })
        self.assertEqual(extracted["gateway_port"], 18888)
        self.assertEqual(extracted["log_level"], "debug")
        self.assertFalse(extracted["typing_indicator"])
        for key in FIXED_RUNTIME_KEYS:
            self.assertNotIn(key, extracted)

    def test_legacy_gateway_adoption_keeps_page_and_adapter_aligned(self) -> None:
        plugin = {
            "gateway_host": "127.0.0.1",
            "gateway_port": 18789,
            "auto_start_gateway": True,
            "node_executable": "node",
            "auth_dir": "",
            "log_level": "info",
        }
        effective, migrated = adopt_legacy_gateway_defaults(plugin, [{
            "type": "whatsapp",
            "enable": True,
            "gateway_port": 18888,
            "auth_dir": "/tmp/legacy-auth",
        }])
        self.assertEqual(effective["gateway_port"], 18888)
        self.assertEqual(effective["auth_dir"], "/tmp/legacy-auth")
        self.assertEqual(set(migrated), {"gateway_port", "auth_dir"})

        hidden_effective, hidden_migrated = adopt_legacy_gateway_defaults(plugin, [{
            "type": "whatsapp",
            "_legacy_gateway_gateway_port": 17777,
        }])
        self.assertEqual(hidden_effective["gateway_port"], 17777)
        self.assertEqual(hidden_migrated["gateway_port"], 17777)

        explicit_plugin = dict(plugin, gateway_port=19999)
        effective, migrated = adopt_legacy_gateway_defaults(explicit_plugin, [{
            "type": "whatsapp", "gateway_port": 18888,
        }])
        self.assertEqual(effective["gateway_port"], 19999)
        self.assertNotIn("gateway_port", migrated)

    def test_legacy_behavior_only_preserves_explicit_changes(self) -> None:
        config = dict(LEGACY_BEHAVIOR_DEFAULTS)
        config["send_read_receipts"] = False
        config["streaming_edit_throttle"] = 2.5
        self.assertEqual(
            extract_legacy_behavior_overrides(config),
            {"send_read_receipts": False, "streaming_edit_throttle": 2.5},
        )
        self.assertEqual(
            extract_legacy_behavior_overrides({"_legacy_typing_indicator": False}),
            {"typing_indicator": False},
        )

    def test_legacy_command_prefix_migration(self) -> None:
        self.assertEqual(extract_legacy_command_prefix({"command_prefix": "!"}), "!")
        self.assertEqual(extract_legacy_command_prefix({"command_prefix": "/"}), "")
        self.assertEqual(extract_legacy_command_prefix({"command_prefix": "!", "register_commands": False}), "")
        self.assertEqual(extract_legacy_command_prefix({"_legacy_command_prefix": "?"}), "?")

    def test_runtime_policy_registry(self) -> None:
        set_runtime_plugin_defaults({"gateway_port": 18888, "default_typing_indicator": False})
        self.assertEqual(get_runtime_plugin_defaults()["gateway_port"], 18888)
        self.assertFalse(get_runtime_plugin_defaults()["typing_indicator"])
        set_runtime_wake_prefixes(["/", "!"])
        self.assertEqual(get_runtime_wake_prefixes(), ("/", "!"))

    def test_platform_ui_has_only_account_specific_fields(self) -> None:
        defaults = _top_level_dict_keys(_adapter_implementation_source(), "DEFAULT_CONFIG")
        for key in ("media_caption_mode", "ignore_self_messages", "apply_ephemeral"):
            self.assertIn(key, defaults)
        for key in (
            "gateway_host", "gateway_port", "auto_start_gateway", "node_executable", "auth_dir", "log_level",
            "command_prefix", "register_commands", "text_chunk_limit", "media_max_mb",
            "typing_indicator", "send_read_receipts", "mark_online", "streaming_edit_throttle",
        ):
            self.assertNotIn(key, defaults)

    def test_all_finite_platform_fields_are_dropdowns(self) -> None:
        bindings = _metadata_option_bindings(_adapter_implementation_source())
        self.assertEqual(bindings["dm_policy"], "DM_POLICIES")
        self.assertEqual(bindings["group_policy"], "GROUP_POLICIES")
        self.assertEqual(bindings["media_caption_mode"], "MEDIA_CAPTION_MODES")
        self.assertEqual(bindings["pre_ack_public"], "PRE_ACK_PUBLIC_MODES")

    def test_plugin_schema_is_global_and_uses_dropdown(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text("utf-8"))
        self.assertEqual(schema["log_level"]["options"], list(LOG_LEVELS))
        self.assertTrue(set(PLUGIN_DEFAULT_ALIASES) <= set(schema))
        for fixed_key in FIXED_RUNTIME_KEYS:
            self.assertNotIn(fixed_key, schema)

    def test_source_integrations_and_version(self) -> None:
        adapter = _adapter_implementation_source()
        wrapper = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        main = (ROOT / "main.py").read_text("utf-8")
        updater = (ROOT / "plugin_updater.py").read_text("utf-8")
        metadata = (ROOT / "metadata.yaml").read_text("utf-8")
        self.assertRegex(
            adapter,
            re.compile(
                r"^RUNTIME_DEFAULT_CONFIG: dict\[str, Any\] = \{\n    \*\*BASE_GATEWAY_CONFIG,",
                re.MULTILINE,
            ),
        )
        self.assertNotRegex(
            adapter,
            re.compile(
                r"^DEFAULT_CONFIG: dict\[str, Any\] = \{\n    \*\*BASE_GATEWAY_CONFIG,",
                re.MULTILINE,
            ),
        )
        self.assertIn("get_runtime_plugin_defaults()", adapter)
        self.assertIn("extract_legacy_behavior_overrides(platform_config)", adapter)
        self.assertIn("_legacy_gateway_", adapter)
        self.assertIn("_message_matches_known_command", adapter)
        self.assertIn(
            "await self._restart_health_monitor()\n"
            "        # Other plugins may finish registering after this adapter is created.\n"
            "        # Refresh here so legacy-prefix compatibility and command pre-ack see\n"
            "        # the complete active CommandFilter registry after every reconnect.\n"
            "        self._refresh_registered_commands()",
            adapter,
        )
        self.assertIn("_convert_message_with_group_name", wrapper)
        self.assertIn("apply_group_name", wrapper)
        self.assertIn("adopt_legacy_gateway_defaults", main)
        self.assertIn("await self._reload_active_adapters()", main)
        self.assertIn("set_runtime_plugin_defaults", main)
        self.assertIn('f"/{PLUGIN_NAME}/update/check"', main)
        self.assertIn('f"/{PLUGIN_NAME}/update/install"', main)
        self.assertIn('f"/{PLUGIN_NAME}/pair-code"', main)
        self.assertIn("self.page_client.pair_code(phone)", main)
        self.assertIn("atomic_swap_plugin", main)
        self.assertIn("restore_plugin_backup", main)
        self.assertIn("api.github.com/repos/{PLUGIN_REPOSITORY}/releases", updater)
        self.assertIn("TRUSTED_DOWNLOAD_HOSTS", updater)
        self.assertIn("MAX_ARCHIVE_BYTES", updater)
        self.assertIn('"0.2.31"', main)
        self.assertIn("version: 0.2.31", metadata)
        self.assertIn('astrbot_version: ">=4.24.2,<5"', metadata)
        self.assertIn("category: 三方集成", metadata)
        self.assertIn("from astrbot.api.web import json_response, request", main)


if __name__ == "__main__":
    unittest.main()
