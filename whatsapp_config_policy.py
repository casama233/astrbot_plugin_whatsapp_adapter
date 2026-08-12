"""Configuration policy and compatibility helpers for the WhatsApp adapter."""

from __future__ import annotations

import hashlib
import re
import socket
import threading
from pathlib import Path
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

_PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
_DEFAULT_INSTANCE_ID = "whatsapp"
_AUTO_GATEWAY_PORT_BY_INSTANCE: dict[str, int] = {}
_AUTO_GATEWAY_INSTANCE_BY_PORT: dict[int, str] = {}
_AUTO_GATEWAY_PORT_LOCK = threading.Lock()


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
            hidden_key = f"_legacy_gateway_{key}"
            if hidden_key in config:
                value = config[hidden_key]
            elif key in config:
                value = config[key]
            else:
                continue
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


def _plugin_data_dir() -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    except ImportError:
        return Path.cwd() / "data" / "plugin_data" / _PLUGIN_NAME
    return Path(get_astrbot_data_path()) / "plugin_data" / _PLUGIN_NAME


def _safe_instance_segment(instance_id: str) -> str:
    raw = str(instance_id or _DEFAULT_INSTANCE_ID).strip()
    segment = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip(".")
    if not segment:
        segment = "instance"
    changed = segment != raw or len(segment) > 80
    if len(segment) > 80:
        segment = segment[:80].rstrip(".") or "instance"
    if changed:
        digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=4).hexdigest()
        segment = f"{segment}-{digest}"
    return segment


def _port_available(host: str, port: int) -> bool:
    """Best-effort check used only before assigning a new auto-start port."""
    try:
        addresses = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
    except OSError:
        # Let GatewayProcess surface invalid/non-local bind hosts consistently.
        return True
    for family, socktype, proto, _canonname, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.bind(sockaddr)
            return True
        except OSError:
            continue
        finally:
            sock.close()
    return False


def _allocate_instance_gateway_port(config: dict[str, Any], instance_id: str) -> None:
    if instance_id == _DEFAULT_INSTANCE_ID:
        return
    if not bool(config.get("auto_start_gateway", True)):
        # External Gateway endpoints are user-managed and must never be silently
        # rewritten by the adapter's local multi-instance allocator.
        return

    configured_port = int(config.get("gateway_port", LEGACY_GATEWAY_DEFAULTS["gateway_port"]))
    host = str(config.get("gateway_host") or LEGACY_GATEWAY_DEFAULTS["gateway_host"])
    if not 1 <= configured_port <= 65535:
        raise ValueError(f"Invalid WhatsApp gateway_port: {configured_port}")

    with _AUTO_GATEWAY_PORT_LOCK:
        existing = _AUTO_GATEWAY_PORT_BY_INSTANCE.get(instance_id)
        if existing is not None:
            config["gateway_port"] = existing
            return

        # The default account owns the configured plugin-wide port. Additional
        # auto-start accounts start scanning at the next port, even if the
        # default account has not been instantiated yet.
        candidate = configured_port + 1
        while candidate <= 65535:
            owner = _AUTO_GATEWAY_INSTANCE_BY_PORT.get(candidate)
            if owner is None and _port_available(host, candidate):
                _AUTO_GATEWAY_PORT_BY_INSTANCE[instance_id] = candidate
                _AUTO_GATEWAY_INSTANCE_BY_PORT[candidate] = instance_id
                config["gateway_port"] = candidate
                return
            candidate += 1

    raise ValueError(
        f"No free WhatsApp Gateway port available after {configured_port} "
        f"for instance {instance_id!r}"
    )


def _isolate_instance_auth_dir(config: dict[str, Any], instance_id: str) -> None:
    if instance_id == _DEFAULT_INSTANCE_ID:
        return
    segment = _safe_instance_segment(instance_id)
    configured = str(config.get("auth_dir") or "").strip()
    if configured:
        base = Path(configured).expanduser().resolve()
        config["auth_dir"] = str(base.with_name(f"{base.name}-{segment}"))
        return
    config["auth_dir"] = str(
        (_plugin_data_dir() / f"whatsapp-auth-{segment}").resolve()
    )


def _apply_multi_instance_defaults(config: dict[str, Any]) -> dict[str, Any]:
    instance_id = str(config.get("id") or _DEFAULT_INSTANCE_ID).strip() or _DEFAULT_INSTANCE_ID
    _allocate_instance_gateway_port(config, instance_id)
    _isolate_instance_auth_dir(config, instance_id)
    return config


def _reset_multi_instance_registry_for_tests() -> None:
    with _AUTO_GATEWAY_PORT_LOCK:
        _AUTO_GATEWAY_PORT_BY_INSTANCE.clear()
        _AUTO_GATEWAY_INSTANCE_BY_PORT.clear()


def merge_runtime_config(
    runtime_defaults: Mapping[str, Any],
    plugin_defaults: Mapping[str, Any],
    platform_config: Mapping[str, Any],
) -> dict[str, Any]:
    merged = {
        **dict(runtime_defaults),
        **dict(plugin_defaults),
        **dict(platform_config),
    }
    return _apply_multi_instance_defaults(merged)
