from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

adapter_path = ROOT / "whatsapp_adapter.py"
adapter = adapter_path.read_text("utf-8")

adapter = adapter.replace(
    "from .whatsapp_config_policy import merge_runtime_config, normalize_media_caption_mode",
    "from .whatsapp_config_policy import (\n"
    "    extract_plugin_defaults,\n"
    "    merge_runtime_config,\n"
    "    normalize_media_caption_mode,\n"
    ")",
)

new_default_config = '''DEFAULT_CONFIG: dict[str, Any] = {
    **BASE_GATEWAY_CONFIG,
    "dm_policy": "allowlist",
    "allow_from": [],
    "group_policy": "disabled",
    "groups": [],
    "group_allow_from": [],
    # Only options that can reasonably differ between WhatsApp accounts live
    # on the platform instance. Generic behaviour is configured globally in
    # the plugin page, while protocol limits stay internal constants.
    "media_caption_mode": "separate",
    "ignore_self_messages": False,
    "pre_ack_emoji": True,
    "pre_ack_emojis": "👀",
    "pre_ack_private": True,
    "pre_ack_public": "mentions",
    "pre_ack_done_emoji": "✅",
    "apply_ephemeral": False,
}
'''
adapter, count = re.subn(
    r"DEFAULT_CONFIG: dict\[str, Any\] = \{.*?\n\}\n\n(?=UI_CONFIG_KEYS =)",
    new_default_config + "\n",
    adapter,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise RuntimeError("DEFAULT_CONFIG block not found")

old_merge = '''    def _merged_config(self, platform_config: dict[str, Any]) -> dict[str, Any]:
        plugin_config = self._normalize_config(self._load_plugin_config())
        platform_config = self._normalize_config(platform_config)
        merged = merge_runtime_config(
            RUNTIME_DEFAULT_CONFIG,
            plugin_config,
            platform_config,
        )
'''
new_merge = '''    def _merged_config(self, platform_config: dict[str, Any]) -> dict[str, Any]:
        loaded_plugin_config = self._normalize_config(self._load_plugin_config())
        plugin_config = extract_plugin_defaults(loaded_plugin_config)
        # Ignore stale keys left by older platform UI versions. This prevents
        # removed generic/fixed settings from silently overriding global or
        # protocol defaults after an upgrade.
        platform_config = {
            key: value
            for key, value in self._normalize_config(platform_config).items()
            if key in PERSISTED_PLATFORM_KEYS
        }
        merged = merge_runtime_config(
            RUNTIME_DEFAULT_CONFIG,
            plugin_config,
            platform_config,
        )
'''
if old_merge not in adapter:
    raise RuntimeError("_merged_config block not found")
adapter = adapter.replace(old_merge, new_merge, 1)
adapter_path.write_text(adapter, "utf-8")

policy_path = ROOT / "whatsapp_config_policy.py"
policy_path.write_text(
    '''"""Pure configuration policy helpers for the WhatsApp adapter."""

from __future__ import annotations

from typing import Any, Mapping

MEDIA_CAPTION_MODES = ("separate", "caption")

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


def normalize_media_caption_mode(value: Any) -> str:
    """Return a supported media/caption mode, falling back safely."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in MEDIA_CAPTION_MODES else "separate"


def extract_plugin_defaults(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return only supported plugin-wide defaults.

    Unknown keys, fixed protocol limits, and platform-instance settings are
    ignored. Legacy raw generic keys are accepted as a migration fallback, but
    the current UI writes only ``default_*`` names.
    """
    result: dict[str, Any] = {}
    for key, value in config.items():
        if key in PLUGIN_RAW_DEFAULT_KEYS:
            result[key] = value
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
schema.update(
    {
        "default_link_preview_single_url": {
            "description": "默认启用单链接预览",
            "type": "bool",
            "default": True,
            "group": "messaging_defaults",
            "hint": "所有 WhatsApp 平台实例的全局默认值；仅当纯文字消息只包含一个 URL 时生成预览。",
        },
        "default_typing_indicator": {
            "description": "默认发送输入状态",
            "type": "bool",
            "default": True,
            "group": "messaging_defaults",
            "hint": "所有 WhatsApp 平台实例的全局默认值。",
        },
        "default_send_read_receipts": {
            "description": "默认发送已读回执",
            "type": "bool",
            "default": True,
            "group": "messaging_defaults",
            "hint": "所有 WhatsApp 平台实例的全局默认值。",
        },
        "default_mark_online": {
            "description": "默认标记在线状态",
            "type": "bool",
            "default": False,
            "group": "messaging_defaults",
            "hint": "所有 WhatsApp 平台实例的全局默认值。",
        },
        "default_parse_inbound_formatting": {
            "description": "默认解析入站格式",
            "type": "bool",
            "default": True,
            "group": "messaging_defaults",
            "hint": "将 WhatsApp 原生粗体、斜体、删除线和代码转换为 Markdown 供 AstrBot 阅读。",
        },
        "default_media_album_debounce_seconds": {
            "description": "默认相簿去抖时间（秒）",
            "type": "float",
            "default": 2.5,
            "group": "messaging_defaults",
            "hint": "连续无文字图片合并为相簿的全局等待时间；设为 0 关闭。",
        },
        "default_streaming_edit_throttle": {
            "description": "默认流式编辑间隔（秒）",
            "type": "float",
            "default": 1.0,
            "group": "messaging_defaults",
            "hint": "所有 WhatsApp 平台实例编辑流式文字的全局最小间隔。",
        },
    }
)
schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", "utf-8")

test_path = ROOT / "tests/test_whatsapp_config_policy.py"
test_path.write_text(
    '''from __future__ import annotations

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
            "merge_runtime_config(\n            RUNTIME_DEFAULT_CONFIG,\n"
            "            plugin_config,\n            platform_config,\n        )",
            source,
        )


if __name__ == "__main__":
    unittest.main()
''',
    "utf-8",
)

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text("utf-8")
changelog, count = re.subn(
    r"## \[Unreleased\]\n\n### Changed\n.*?\n\n(?=## \[0\.2\.19\])",
    """## [Unreleased]\n\n### Changed\n- 恢复 `media_caption_mode` 等真正按 WhatsApp 帐号变化的实例选项。\n- 链接预览、输入/已读/在线状态、入站格式解析、相簿去抖与流式节流改为插件级 `default_*` 全局默认。\n- 文字和媒体大小限制改为内部固定值，不再允许配置覆盖。\n- 移除重复的指令前缀与斜线指令配置，沿用 AstrBot 的指令体系。\n- 配置优先级调整为“运行时默认 < 插件全局默认 < 平台实例配置”，并忽略旧平台配置中已移除的键。\n\n""",
    changelog,
    count=1,
    flags=re.DOTALL,
)
if count != 1:
    raise RuntimeError("Unreleased changelog block not found")
changelog_path.write_text(changelog, "utf-8")

precommit_path = ROOT / ".pre-commit-config.yaml"
precommit = precommit_path.read_text("utf-8")
precommit = precommit.replace(
    "python -m compileall -q whatsapp_helpers.py _whatsapp_helpers_impl.py whatsapp_chunking.py whatsapp_event.py tests",
    "python -m compileall -q whatsapp_adapter.py whatsapp_config_policy.py whatsapp_helpers.py _whatsapp_helpers_impl.py whatsapp_chunking.py whatsapp_event.py tests",
)
precommit_path.write_text(precommit, "utf-8")

for relative in (
    ".github/scripts/refine_config_scopes.py",
    ".github/workflows/refine-config-scopes.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
