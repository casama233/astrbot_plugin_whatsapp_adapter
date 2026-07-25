"""Pure configuration policy helpers for the WhatsApp adapter."""

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
