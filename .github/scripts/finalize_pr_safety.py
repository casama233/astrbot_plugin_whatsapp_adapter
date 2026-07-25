from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

POLICY = '''"""Configuration policy and compatibility helpers for the WhatsApp adapter."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

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
PLUGIN_DEFAULT_ALIASES = {
    "default_link_preview_single_url": "link_preview_single_url",
    "default_typing_indicator": "typing_indicator",
    "default_send_read_receipts": "send_read_receipts",
    "default_mark_online": "mark_online",
    "default_parse_inbound_formatting": "parse_inbound_formatting",
    "default_media_album_debounce_seconds": "media_album_debounce_seconds",
    "default_streaming_edit_throttle": "streaming_edit_throttle",
}
FIXED_RUNTIME_KEYS = frozenset(
    {
        "text_chunk_limit",
        "media_max_mb",
        "command_prefix",
        "register_commands",
    }
)

# Historical platform-template defaults. Only values that differ from these are
# considered explicit old-instance choices during migration.
LEGACY_GATEWAY_DEFAULTS: dict[str, Any] = {
    "gateway_host": "127.0.0.1",
    "gateway_port": 18789,
    "auto_start_gateway": True,
    "node_executable": "node",
    "auth_dir": "",
    "log_level": "info",
}
LEGACY_BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "link_preview_single_url": True,
    "typing_indicator": True,
    "send_read_receipts": True,
    "mark_online": False,
    "parse_inbound_formatting": True,
    "media_album_debounce_seconds": 2.5,
    "streaming_edit_throttle": 1.0,
}

_RUNTIME_PLUGIN_DEFAULTS: dict[str, Any] = {}
_RUNTIME_WAKE_PREFIXES: tuple[str, ...] = ("/",)


def normalize_config_enum(key: str, value: Any) -> str:
    if key not in CONFIG_ENUM_OPTIONS:
        raise ValueError(f"Unsupported enum config key: {key}")
    normalized = str(value or "").strip().lower()
    options = CONFIG_ENUM_OPTIONS[key]
    return normalized if normalized in options else CONFIG_ENUM_DEFAULTS[key]


def normalize_media_caption_mode(value: Any) -> str:
    return normalize_config_enum("media_caption_mode", value)


def normalize_pre_ack_public(value: Any) -> str:
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
    result: dict[str, Any] = {}
    for key, value in config.items():
        if key in PLUGIN_RAW_DEFAULT_KEYS:
            result[key] = normalize_config_enum(key, value) if key == "log_level" else value
            continue
        runtime_key = PLUGIN_DEFAULT_ALIASES.get(key)
        if runtime_key:
            result[runtime_key] = value

    # One-release migration fallback for old hidden plugin values.
    for runtime_key in PLUGIN_DEFAULT_ALIASES.values():
        if runtime_key in config and runtime_key not in FIXED_RUNTIME_KEYS:
            result.setdefault(runtime_key, config[runtime_key])
    if "media_caption_mode" in config:
        result["media_caption_mode"] = normalize_media_caption_mode(
            config["media_caption_mode"]
        )
    return result


def adopt_legacy_gateway_defaults(
    plugin_config: Mapping[str, Any],
    platform_configs: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adopt one old WhatsApp instance's explicit Gateway settings.

    The plugin page and platform adapter must always resolve the same Gateway.
    Old platform values are adopted only when they differ from historical
    defaults and the plugin-wide field is still at its default.
    """
    effective = dict(plugin_config)
    migrated: dict[str, Any] = {}
    candidates = [
        cfg for cfg in platform_configs
        if isinstance(cfg, Mapping) and cfg.get("type") == "whatsapp"
    ]
    candidates.sort(key=lambda cfg: not bool(cfg.get("enable", False)))
    for config in candidates[:1]:
        for key, historical_default in LEGACY_GATEWAY_DEFAULTS.items():
            if key not in config:
                continue
            value = config[key]
            if key == "log_level":
                value = normalize_config_enum(key, value)
            if value == historical_default:
                continue
            if effective.get(key, historical_default) != historical_default:
                continue
            effective[key] = value
            migrated[key] = value
    return effective, migrated


def extract_legacy_behavior_overrides(config: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, default in LEGACY_BEHAVIOR_DEFAULTS.items():
        hidden_key = f"_legacy_{key}"
        if hidden_key in config:
            result[key] = config[hidden_key]
        elif key in config and config[key] != default:
            result[key] = config[key]
    return result


def extract_legacy_command_prefix(config: Mapping[str, Any]) -> str:
    hidden = str(config.get("_legacy_command_prefix") or "").strip()
    if hidden:
        return hidden
    if config.get("register_commands") is False:
        return ""
    prefix = str(config.get("command_prefix") or "/").strip()
    return prefix if prefix and prefix != "/" else ""


def set_runtime_plugin_defaults(config: Mapping[str, Any]) -> None:
    _RUNTIME_PLUGIN_DEFAULTS.clear()
    _RUNTIME_PLUGIN_DEFAULTS.update(extract_plugin_defaults(config))


def get_runtime_plugin_defaults() -> dict[str, Any]:
    return dict(_RUNTIME_PLUGIN_DEFAULTS)


def set_runtime_wake_prefixes(values: Any) -> None:
    global _RUNTIME_WAKE_PREFIXES
    if isinstance(values, str):
        values = [values]
    normalized = tuple(
        str(value).strip()
        for value in (values or [])
        if str(value).strip()
    )
    _RUNTIME_WAKE_PREFIXES = normalized or ("/",)


def get_runtime_wake_prefixes() -> tuple[str, ...]:
    return _RUNTIME_WAKE_PREFIXES


def merge_runtime_config(
    runtime_defaults: Mapping[str, Any],
    plugin_defaults: Mapping[str, Any],
    platform_config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **dict(runtime_defaults),
        **dict(plugin_defaults),
        **dict(platform_config),
    }
'''
(ROOT / "whatsapp_config_policy.py").write_text(POLICY, "utf-8")

COMMANDS = '''"""Legacy command-prefix compatibility for pre-0.2.20 configurations."""

from __future__ import annotations

import re

from astrbot import logger


def collect_registered_commands() -> list[str]:
    commands: list[str] = []
    try:
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.command_group import CommandGroupFilter
        from astrbot.core.star.star import star_handlers_registry, star_map
    except Exception as exc:
        logger.debug("WhatsApp legacy command collection skipped: %s", exc)
        return commands

    for handler_md in star_handlers_registry:
        module = star_map.get(handler_md.handler_module_path)
        if not module or not module.activated or not handler_md.enabled:
            continue
        for event_filter in handler_md.event_filters:
            if isinstance(event_filter, CommandGroupFilter):
                continue
            if not isinstance(event_filter, CommandFilter):
                continue
            if event_filter.parent_command_names and event_filter.parent_command_names != [""]:
                continue
            for name in [event_filter.command_name, *list(event_filter.alias or [])]:
                normalized = str(name or "").strip().lower()
                if normalized and normalized not in commands:
                    commands.append(normalized)
    return sorted(commands)


def message_matches_command(text: str, commands: list[str], prefix: str = "/") -> bool:
    if not commands or not prefix:
        return False
    message = re.sub(r"\\s+", " ", str(text or "").strip())
    if not message.startswith(prefix):
        return False
    token = message[len(prefix):].split(" ", 1)[0].lower()
    return bool(token and token in commands)
'''
(ROOT / "whatsapp_commands.py").write_text(COMMANDS, "utf-8")

adapter_path = ROOT / "whatsapp_adapter.py"
adapter = adapter_path.read_text("utf-8")

adapter = adapter.replace(
    "from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient, WhatsAppGatewayError\n",
    "from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient, WhatsAppGatewayError\n"
    "from .whatsapp_commands import collect_registered_commands, message_matches_command\n",
    1,
)
adapter = adapter.replace(
    "    extract_plugin_defaults,\n    merge_runtime_config,\n",
    "    extract_legacy_behavior_overrides,\n"
    "    extract_legacy_command_prefix,\n"
    "    extract_plugin_defaults,\n"
    "    get_runtime_plugin_defaults,\n"
    "    get_runtime_wake_prefixes,\n"
    "    merge_runtime_config,\n",
    1,
)
adapter = adapter.replace(
    "DEFAULT_CONFIG: dict[str, Any] = {\n    **BASE_GATEWAY_CONFIG,\n",
    "DEFAULT_CONFIG: dict[str, Any] = {\n",
    1,
)

old_init = '''        if isinstance(platform_config, dict):
            sanitized_inplace = sanitize_whatsapp_platform_config(platform_config)
            if platform_config is not sanitized_inplace:
                platform_config.clear()
                platform_config.update(sanitized_inplace)
        super().__init__(platform_config or {}, event_queue)
        self.config = self._merged_config(platform_config or {})
'''
new_init = '''        raw_platform_config = dict(platform_config or {})
        sanitized_config = sanitize_whatsapp_platform_config(raw_platform_config)
        if isinstance(platform_config, dict):
            platform_config.clear()
            platform_config.update(sanitized_config)
        super().__init__(sanitized_config, event_queue)
        self.config = self._merged_config(sanitized_config)
'''
if old_init not in adapter:
    raise RuntimeError("adapter init config block not found")
adapter = adapter.replace(old_init, new_init, 1)
adapter = adapter.replace(
    "        self._platform_config = platform_config or {}\n        self._platform_settings = platform_settings or {}\n",
    "        self._platform_config = sanitized_config\n"
    "        self._platform_settings = platform_settings or {}\n"
    "        self._registered_commands: list[str] = []\n"
    "        self._legacy_command_prefix = extract_legacy_command_prefix(sanitized_config)\n",
    1,
)
adapter = adapter.replace(
    "        _ACTIVE_ADAPTERS.add(self)\n        _load_lid_mappings(self._auth_dir())\n",
    "        _ACTIVE_ADAPTERS.add(self)\n"
    "        self._refresh_registered_commands()\n"
    "        _load_lid_mappings(self._auth_dir())\n",
    1,
)

old_reload = '''        self._platform_config = platform_config or {}
        self.config = self._merged_config(self._platform_config)
        self.client.update_base_url(self._base_url)
'''
new_reload = '''        self._platform_config = sanitize_whatsapp_platform_config(platform_config or {})
        self.config = self._merged_config(self._platform_config)
        self._legacy_command_prefix = extract_legacy_command_prefix(self._platform_config)
        self._refresh_registered_commands()
        self.client.update_base_url(self._base_url)
'''
if old_reload not in adapter:
    raise RuntimeError("adapter reload block not found")
adapter = adapter.replace(old_reload, new_reload, 1)

old_handle = '''        is_self_mentioned = self._message_mentions_self(raw)
        is_reply_to_self = self._reply_targets_self(raw)
        is_reaction_only = self._is_reaction_only(raw)
        event = WhatsAppMessageEvent(
            message_str=message.message_str,
'''
new_handle = '''        is_self_mentioned = self._message_mentions_self(raw)
        is_reply_to_self = self._reply_targets_self(raw)
        is_reaction_only = self._is_reaction_only(raw)
        original_text = message.message_str or ""
        is_command = self._message_matches_known_command(original_text)
        is_legacy_command = bool(
            self._legacy_command_prefix
            and message_matches_command(
                original_text,
                self._registered_commands,
                prefix=self._legacy_command_prefix,
            )
        )
        event_text = original_text
        if is_legacy_command:
            stripped = original_text.strip()
            event_text = stripped[len(self._legacy_command_prefix):].strip()
        event = WhatsAppMessageEvent(
            message_str=event_text,
'''
if old_handle not in adapter:
    raise RuntimeError("adapter handle block not found")
adapter = adapter.replace(old_handle, new_handle, 1)

adapter = adapter.replace(
    '                    group_mode == "mentions" and (is_self_mentioned or is_reply_to_self)\n',
    '                    group_mode == "mentions" and (is_self_mentioned or is_reply_to_self or is_command)\n',
    1,
)
adapter = adapter.replace(
    '''            if should_ack:
                event.is_at_or_wake_command = True
                event.is_wake = True
                await self._pre_ack(event)
        logger.info(
            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s self_mentioned=%s reply_to_self=%s is_private=%s",
''',
    '''            if should_ack:
                if not is_command:
                    event.is_at_or_wake_command = True
                    event.is_wake = True
                await self._pre_ack(event)
        if is_legacy_command:
            event.is_at_or_wake_command = True
            event.is_wake = True
        logger.info(
            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s self_mentioned=%s reply_to_self=%s is_private=%s is_command=%s legacy_command=%s",
''',
    1,
)
adapter = adapter.replace(
    '''            is_reply_to_self,
            is_private,
        )
''',
    '''            is_reply_to_self,
            is_private,
            is_command,
            is_legacy_command,
        )
''',
    1,
)

old_merge = '''        loaded_plugin_config = self._normalize_config(self._load_plugin_config())
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
new_merge = '''        loaded_plugin_config = self._normalize_config(self._load_plugin_config())
        plugin_config = {
            **extract_plugin_defaults(loaded_plugin_config),
            **get_runtime_plugin_defaults(),
        }
        legacy_behavior = extract_legacy_behavior_overrides(platform_config)
        instance_config = {
            key: value
            for key, value in self._normalize_config(platform_config).items()
            if key in PERSISTED_PLATFORM_KEYS
        }
        merged = merge_runtime_config(
            RUNTIME_DEFAULT_CONFIG,
            plugin_config,
            {**legacy_behavior, **instance_config},
        )
'''
if old_merge not in adapter:
    raise RuntimeError("adapter merge block not found")
adapter = adapter.replace(old_merge, new_merge, 1)

insert_point = '''    def _count_label(self, value: Any) -> str:
        if isinstance(value, list):
            return f"<{len(value)} entries>"
        return "<0 entries>" if value in (None, "") else "<1 entry>"

'''
command_methods = '''    def _refresh_registered_commands(self) -> None:
        self._registered_commands = collect_registered_commands()

    def _message_matches_known_command(self, text: str) -> bool:
        prefixes = list(get_runtime_wake_prefixes())
        if self._legacy_command_prefix:
            prefixes.append(self._legacy_command_prefix)
        return any(
            message_matches_command(text, self._registered_commands, prefix=prefix)
            for prefix in dict.fromkeys(prefixes)
            if prefix
        )

'''
if insert_point not in adapter:
    raise RuntimeError("command method insertion point not found")
adapter = adapter.replace(insert_point, insert_point + command_methods, 1)

old_sanitize = '''def sanitize_whatsapp_platform_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in UI_CONFIG_KEYS:
        if key not in config:
            continue
        value = config[key]
        if key == "pre_ack_public":
            value = _coerce_pre_ack_public(value)
        sanitized[key] = value
    for key in ("type", "enable", "id"):
        if key in config:
            sanitized[key] = config[key]
    return sanitized
'''
new_sanitize = '''def sanitize_whatsapp_platform_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in UI_CONFIG_KEYS:
        if key not in config:
            continue
        value = config[key]
        if key == "pre_ack_public":
            value = _coerce_pre_ack_public(value)
        sanitized[key] = value

    # Preserve only explicit old per-instance behaviour choices. Historical
    # template defaults are ignored so plugin-wide default_* settings can work.
    for key, value in extract_legacy_behavior_overrides(config).items():
        sanitized[f"_legacy_{key}"] = value
    legacy_prefix = extract_legacy_command_prefix(config)
    if legacy_prefix:
        sanitized["_legacy_command_prefix"] = legacy_prefix

    for key in ("type", "enable", "id"):
        if key in config:
            sanitized[key] = config[key]
    return sanitized
'''
if old_sanitize not in adapter:
    raise RuntimeError("sanitize block not found")
adapter = adapter.replace(old_sanitize, new_sanitize, 1)

adapter_path.write_text(adapter, "utf-8")

main_path = ROOT / "main.py"
main = main_path.read_text("utf-8")
main = main.replace(
    "from .whatsapp_adapter import BASE_GATEWAY_CONFIG\n",
    "from .whatsapp_adapter import BASE_GATEWAY_CONFIG\n"
    "from .whatsapp_config_policy import (\n"
    "    adopt_legacy_gateway_defaults,\n"
    "    set_runtime_plugin_defaults,\n"
    "    set_runtime_wake_prefixes,\n"
    ")\n",
    1,
)
main = main.replace('    "0.2.8",\n', '    "0.2.20",\n', 1)
main = main.replace(
    "        self.config = {**BASE_GATEWAY_CONFIG, **(dict(config or {}))}\n",
    "        self.config = {**BASE_GATEWAY_CONFIG, **(dict(config or {}))}\n"
    "        self._sync_runtime_policy()\n",
    1,
)

old_reload_config = '''    async def reload_config(self, new_config: dict | None = None) -> None:
        if new_config:
            self.config = {**BASE_GATEWAY_CONFIG, **dict(new_config)}
        logger.info("WhatsApp 插件配置已重载: gateway=%s", self._base_url)
        self.page_client.update_base_url(self._base_url)
'''
new_reload_config = '''    async def reload_config(self, new_config: dict | None = None) -> None:
        if new_config:
            self.config = {**BASE_GATEWAY_CONFIG, **dict(new_config)}
        self._adopt_legacy_platform_gateway_defaults()
        self._sync_runtime_policy()
        logger.info("WhatsApp 插件配置已重载: gateway=%s", self._base_url)
        self.page_client.update_base_url(self._base_url)
'''
if old_reload_config not in main:
    raise RuntimeError("main reload_config block not found")
main = main.replace(old_reload_config, new_reload_config, 1)

main = main.replace(
    '''    async def initialize(self) -> None:
        await super().initialize()
        await self._restore_platform_adapters()
''',
    '''    async def initialize(self) -> None:
        await super().initialize()
        self._adopt_legacy_platform_gateway_defaults()
        self._sync_runtime_policy()
        self.page_client.update_base_url(self._base_url)
        await self._restore_platform_adapters()
''',
    1,
)

helper_insert = '''    def _safe_status(self, status: dict[str, Any]) -> dict[str, Any]:
'''
helpers = '''    def _root_config(self) -> dict[str, Any]:
        try:
            config = self.context.get_config()
            return dict(config or {})
        except Exception:
            return {}

    def _platform_configs(self) -> list[dict[str, Any]]:
        manager = getattr(self.context, "platform_manager", None)
        configs = getattr(manager, "platforms_config", None)
        return list(configs or [])

    def _adopt_legacy_platform_gateway_defaults(self) -> None:
        effective, migrated = adopt_legacy_gateway_defaults(
            self.config,
            self._platform_configs(),
        )
        self.config = effective
        if migrated:
            logger.warning(
                "已从旧 WhatsApp 平台实例迁移 Gateway 配置到本次运行的插件全局配置: keys=%s。请在插件配置页确认后保存。",
                sorted(migrated),
            )

    def _sync_runtime_policy(self) -> None:
        set_runtime_plugin_defaults(self.config)
        set_runtime_wake_prefixes(self._root_config().get("wake_prefix", ["/"]))

'''
if helper_insert not in main:
    raise RuntimeError("main helper insertion point not found")
main = main.replace(helper_insert, helpers + helper_insert, 1)

main = main.replace(
    '''                inst.__class__ = NewAdapter
                _ACTIVE_ADAPTERS.add(inst)
                inst._platform_settings = self.context.get_config().get("platform_settings", {})
                inst._ensure_send_buffer_state()
''',
    '''                inst.__class__ = NewAdapter
                _ACTIVE_ADAPTERS.add(inst)
                inst._platform_config = sanitized_config
                inst._platform_settings = self.context.get_config().get("platform_settings", {})
                inst.config = inst._merged_config(sanitized_config)
                inst.client.update_base_url(inst._base_url)
                inst._legacy_command_prefix = extract_legacy_command_prefix(sanitized_config)
                inst._registered_commands = []
                inst._refresh_registered_commands()
                inst._ensure_send_buffer_state()
''',
    1,
)
# The hot-swap block needs the compatibility helper import.
main = main.replace(
    "            from .whatsapp_adapter import sanitize_whatsapp_platform_config\n",
    "            from .whatsapp_adapter import sanitize_whatsapp_platform_config\n"
    "            from .whatsapp_config_policy import extract_legacy_command_prefix\n",
    1,
)
main_path.write_text(main, "utf-8")

TESTS = '''from __future__ import annotations

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
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        defaults = _top_level_dict_keys(source, "DEFAULT_CONFIG")
        for key in ("media_caption_mode", "ignore_self_messages", "apply_ephemeral"):
            self.assertIn(key, defaults)
        for key in (
            "gateway_host", "gateway_port", "auto_start_gateway", "node_executable", "auth_dir", "log_level",
            "command_prefix", "register_commands", "text_chunk_limit", "media_max_mb",
            "typing_indicator", "send_read_receipts", "mark_online", "streaming_edit_throttle",
        ):
            self.assertNotIn(key, defaults)

    def test_all_finite_platform_fields_are_dropdowns(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        bindings = _metadata_option_bindings(source)
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
        adapter = (ROOT / "whatsapp_adapter.py").read_text("utf-8")
        main = (ROOT / "main.py").read_text("utf-8")
        metadata = (ROOT / "metadata.yaml").read_text("utf-8")
        self.assertIn("get_runtime_plugin_defaults()", adapter)
        self.assertIn("extract_legacy_behavior_overrides(platform_config)", adapter)
        self.assertIn("_message_matches_known_command", adapter)
        self.assertIn("adopt_legacy_gateway_defaults", main)
        self.assertIn("set_runtime_plugin_defaults", main)
        self.assertIn('"0.2.20"', main)
        self.assertIn("version: 0.2.20", metadata)


if __name__ == "__main__":
    unittest.main()
'''
(ROOT / "tests/test_whatsapp_config_policy.py").write_text(TESTS, "utf-8")

metadata_path = ROOT / "metadata.yaml"
metadata = metadata_path.read_text("utf-8")
metadata = re.sub(r"(?m)^version:\s*.*$", "version: 0.2.20", metadata, count=1)
metadata_path.write_text(metadata, "utf-8")

changelog_path = ROOT / "CHANGELOG.md"
changelog = changelog_path.read_text("utf-8")
changelog = changelog.replace("## [Unreleased]", "## [0.2.20] - 2026-07-26", 1)
needle = "- 配置优先级调整为“运行时默认 < 插件全局默认 < 平台实例配置”，并忽略旧平台配置中已移除的键。\n"
extra = (
    "- Gateway 连接改为插件全局单一来源，避免登录页与平台实例连接不同端口或认证目录；旧实例的显式非默认连接值会在运行时自动接管并提示保存。\n"
    "- 旧平台实例中显式修改过的通用消息行为会以隐藏兼容覆盖保留一个版本；历史模板默认值不会阻挡新的插件全局默认。\n"
    "- 旧版自定义 WhatsApp 指令前缀提供隐藏兼容迁移；新配置统一使用 AstrBot 全局 `wake_prefix`。\n"
)
if needle in changelog and extra not in changelog:
    changelog = changelog.replace(needle, needle + extra, 1)
changelog_path.write_text(changelog, "utf-8")

for doc_name in ("README.md", "docs/zh-CN.md"):
    path = ROOT / doc_name
    text = path.read_text("utf-8")
    text = text.replace(
        "这些字段可在插件页提供全局默认，也可由某个平台实例覆盖：",
        "这些字段只在插件配置页设置，登录管理页与所有 WhatsApp 平台实例共用同一组 Gateway 连接：",
    )
    text = text.replace(
        "Gateway 的 `gateway_host`、`gateway_port`、`auto_start_gateway`、`node_executable`、`auth_dir`、`log_level` 也可在插件页作为全局默认，并由平台实例覆盖。",
        "Gateway 的 `gateway_host`、`gateway_port`、`auto_start_gateway`、`node_executable`、`auth_dir`、`log_level` 只在插件页设置，避免登录页与平台实例连接到不同 Gateway。",
    )
    marker = "- 指令前缀、指令启用状态和命令匹配由 AstrBot Core 的 `wake_prefix`、CommandFilter 和插件启用状态统一处理。"
    replacement = marker + "旧版非 `/` 的 WhatsApp 专用前缀会兼容一个版本并输出迁移提示。"
    text = text.replace(marker, replacement)
    path.write_text(text, "utf-8")

precommit_path = ROOT / ".pre-commit-config.yaml"
precommit = precommit_path.read_text("utf-8")
precommit = precommit.replace(
    "python -m compileall -q main.py whatsapp_adapter.py whatsapp_config_policy.py whatsapp_helpers.py _whatsapp_helpers_impl.py whatsapp_chunking.py whatsapp_event.py tests",
    "python -m compileall -q main.py whatsapp_adapter.py whatsapp_commands.py whatsapp_config_policy.py whatsapp_helpers.py _whatsapp_helpers_impl.py whatsapp_chunking.py whatsapp_event.py tests",
)
temporary_hook = '''      - id: finalize-pr-safety
        name: Finalize PR safety fixes
        entry: python .github/scripts/finalize_pr_safety.py
        language: system
        pass_filenames: false
'''
precommit_path.write_text(precommit.replace(temporary_hook, ""), "utf-8")

Path(__file__).unlink(missing_ok=True)
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
