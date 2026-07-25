"""Pure configuration policy helpers for the WhatsApp adapter."""

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
