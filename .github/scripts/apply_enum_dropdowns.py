from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label} not found")
    return text.replace(old, new, 1)


def set_metadata_options(source: str, field: str, constant: str) -> str:
    marker = f'    "{field}": {{'
    start = source.find(marker)
    if start < 0:
        raise RuntimeError(f"metadata field {field} not found")
    end = source.find("\n    },", start)
    if end < 0:
        raise RuntimeError(f"metadata field {field} has no closing block")
    end += len("\n    },")
    block = source[start:end]
    option_line = f'        "options": list({constant}),\n'
    if '"options":' in block:
        block = re.sub(
            r'        "options": .*?\n',
            option_line,
            block,
            count=1,
        )
    else:
        block, count = re.subn(
            r'(        "group": "[^"]+",\n)',
            rf'\1{option_line}',
            block,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"metadata field {field} group line not found")
    return source[:start] + block + source[end:]


adapter_path = ROOT / "whatsapp_adapter.py"
adapter = adapter_path.read_text("utf-8")
adapter = replace_once(
    adapter,
    '''from .whatsapp_config_policy import (
    extract_plugin_defaults,
    merge_runtime_config,
    normalize_media_caption_mode,
)
''',
    '''from .whatsapp_config_policy import (
    DM_POLICIES,
    GROUP_POLICIES,
    LOG_LEVELS,
    MEDIA_CAPTION_MODES,
    PRE_ACK_PUBLIC_MODES,
    extract_plugin_defaults,
    merge_runtime_config,
    normalize_config_enum,
    normalize_pre_ack_public,
)
''',
    "config policy import block",
)

for field, constant in {
    "log_level": "LOG_LEVELS",
    "dm_policy": "DM_POLICIES",
    "group_policy": "GROUP_POLICIES",
    "media_caption_mode": "MEDIA_CAPTION_MODES",
    "pre_ack_public": "PRE_ACK_PUBLIC_MODES",
}.items():
    adapter = set_metadata_options(adapter, field, constant)

adapter = replace_once(
    adapter,
    '''        if key == "media_caption_mode":
            return normalize_media_caption_mode(value)
        if key == "pre_ack_public":
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"mentions", "always", "never"}:
                    return normalized
                if normalized in {"true", "1", "yes", "on"}:
                    return "always"
                if normalized in {"false", "0", "no", "off", "none"}:
                    return "never"
            return value
''',
    '''        if key in {"log_level", "dm_policy", "group_policy", "media_caption_mode"}:
            return normalize_config_enum(key, value)
        if key == "pre_ack_public":
            return normalize_pre_ack_public(value)
''',
    "runtime enum normalization block",
)

adapter, count = re.subn(
    r'''(?ms)^def _coerce_pre_ack_public\(value: Any\) -> str:\n.*?(?=^def patch_platform_manager_hot_reload\(\) -> None:)''',
    '''def _coerce_pre_ack_public(value: Any) -> str:
    return normalize_pre_ack_public(value)


''',
    adapter,
    count=1,
)
if count != 1:
    raise RuntimeError("legacy pre_ack_public coercion block not found")
if "normalize_media_caption_mode" in adapter:
    raise RuntimeError("adapter still uses the old single-field normalizer")
adapter_path.write_text(adapter, "utf-8")

policy_path = ROOT / "whatsapp_config_policy.py"
policy_path.write_text(
    '''"""Pure configuration policy helpers for the WhatsApp adapter."""

from __future__ import annotations

from typing import Any, Mapping

LOG_LEVELS = ("silent", "fatal", "error", "warn", "info", "debug", "trace")
DM_POLICIES = ("allowlist", "open", "disabled")
GROUP_POLICIES = ("allowlist", "open", "disabled")
MEDIA_CAPTION_MODES = ("separate", "caption")
PRE_ACK_PUBLIC_MODES = ("always", "mentions", "never")

CONFIG_ENUM_OPTIONS: dict[str, tuple[str, ...]] = {
    "log_level": LOG_LEVELS,
    "dm_policy": DM_POLICIES,
    "group_policy": GROUP_POLICIES,
    "media_caption_mode": MEDIA_CAPTION_MODES,
    "pre_ack_public": PRE_ACK_PUBLIC_MODES,
}
CONFIG_ENUM_DEFAULTS = {
    "log_level": "info",
    "dm_policy": "allowlist",
    "group_policy": "disabled",
    "media_caption_mode": "separate",
    "pre_ack_public": "mentions",
}

# These keys configure the shared Gateway/plugin runtime and remain accepted
# without a prefix for backwards compatibility with the original plugin page.
PLUGIN_RAW_DEFAULT_KEYS = frozenset(
    {
        "gateway_host",
        "gateway_port",
        "auto_start_gateway",
        "node_executable",
        "auth_dir",
        "log_level",
    }
)

# Generic behaviour is exposed as an explicit plugin-wide default. Prefixing
# these fields avoids implying that they are platform-instance values.
PLUGIN_DEFAULT_ALIASES = {
    "default_link_preview_single_url": "link_preview_single_url",
    "default_typing_indicator": "typing_indicator",
    "default_send_read_receipts": "send_read_receipts",
    "default_mark_online": "mark_online",
    "default_parse_inbound_formatting": "parse_inbound_formatting",
    "default_media_album_debounce_seconds": "media_album_debounce_seconds",
    "default_streaming_edit_throttle": "streaming_edit_throttle",
}

# WhatsApp/Gateway limits and AstrBot-owned command behaviour are intentionally
# not configurable through either plugin or platform settings.
FIXED_RUNTIME_KEYS = frozenset(
    {
        "text_chunk_limit",
        "media_max_mb",
        "command_prefix",
        "register_commands",
    }
)


def normalize_config_enum(key: str, value: Any) -> str:
    """Normalize a finite-choice config field and apply its safe default."""
    if key not in CONFIG_ENUM_OPTIONS:
        raise ValueError(f"Unsupported enum config key: {key}")
    normalized = str(value or "").strip().lower()
    options = CONFIG_ENUM_OPTIONS[key]
    return normalized if normalized in options else CONFIG_ENUM_DEFAULTS[key]


def normalize_media_caption_mode(value: Any) -> str:
    """Backward-compatible wrapper for media caption mode normalization."""
    return normalize_config_enum("media_caption_mode", value)


def normalize_pre_ack_public(value: Any) -> str:
    """Normalize current and legacy group pre-ack values."""
    if isinstance(value, bool):
        return "mentions" if value else "never"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return "mentions"
        if normalized in {"false", "0", "no", "off", "none"}:
            return "never"
    return normalize_config_enum("pre_ack_public", value)


def extract_plugin_defaults(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return only supported plugin-wide defaults.

    Unknown keys, fixed protocol limits, and platform-instance settings are
    ignored. Legacy raw generic keys are accepted as a migration fallback, but
    the current UI writes only ``default_*`` names.
    """
    result: dict[str, Any] = {}
    for key, value in config.items():
        if key in PLUGIN_RAW_DEFAULT_KEYS:
            result[key] = (
                normalize_config_enum(key, value)
                if key in CONFIG_ENUM_OPTIONS
                else value
            )
            continue
        runtime_key = PLUGIN_DEFAULT_ALIASES.get(key)
        if runtime_key:
            result[runtime_key] = value

    # Accept old hidden plugin values for one release cycle without exposing
    # them in the schema. Fixed keys remain blocked.
    for runtime_key in PLUGIN_DEFAULT_ALIASES.values():
        if runtime_key in config and runtime_key not in FIXED_RUNTIME_KEYS:
            result.setdefault(runtime_key, config[runtime_key])
    if "media_caption_mode" in config:
        result["media_caption_mode"] = normalize_media_caption_mode(
            config["media_caption_mode"]
        )
    return result


def merge_runtime_config(
    runtime_defaults: Mapping[str, Any],
    plugin_defaults: Mapping[str, Any],
    platform_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge config from broadest to most specific scope."""
    return {
        **dict(runtime_defaults),
        **dict(plugin_defaults),
        **dict(platform_config),
    }
''',
    "utf-8",
)

schema_path = ROOT / "_conf_schema.json"
schema = json.loads(schema_path.read_text("utf-8"))
schema["log_level"]["options"] = [
    "silent",
    "fatal",
    "error",
    "warn",
    "info",
    "debug",
    "trace",
]
schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", "utf-8")

test_path = ROOT / "tests/test_whatsapp_config_policy.py"
test_path.write_text(
    '''from __future__ import annotations

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
            "merge_runtime_config(\n"
            "            RUNTIME_DEFAULT_CONFIG,\n"
            "            plugin_config,\n"
            "            platform_config,\n"
            "        )",
            source,
        )


if __name__ == "__main__":
    unittest.main()
''',
    "utf-8",
)

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text("utf-8")
needle = "- 恢复 `media_caption_mode` 等真正按 WhatsApp 帐号变化的实例选项。\n"
addition = (
    needle
    + "- 所有有限枚举配置统一改为下拉选项：Gateway 日志级别、私聊/群聊策略、媒体文字模式与群聊预回应模式。\n"
)
if addition not in changelog:
    changelog = replace_once(changelog, needle, addition, "Unreleased dropdown changelog")
changelog_path.write_text(changelog, "utf-8")

config_path = ROOT / ".pre-commit-config.yaml"
config = config_path.read_text("utf-8")
hook = '''      - id: apply-enum-dropdowns
        name: Apply finite-option dropdowns
        entry: python .github/scripts/apply_enum_dropdowns.py
        language: system
        pass_filenames: false
'''
config_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
