"""Pure configuration policy helpers for the WhatsApp adapter."""

from __future__ import annotations

from typing import Any, Mapping

MEDIA_CAPTION_MODES = ("separate", "caption")


def normalize_media_caption_mode(value: Any) -> str:
    """Return a supported media/caption mode, falling back safely."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in MEDIA_CAPTION_MODES else "separate"


def merge_runtime_config(
    runtime_defaults: Mapping[str, Any],
    plugin_defaults: Mapping[str, Any],
    platform_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge config from broadest to most specific scope.

    Plugin configuration supplies global defaults. Values explicitly selected
    on a WhatsApp platform instance must always take precedence.
    """
    return {
        **dict(runtime_defaults),
        **dict(plugin_defaults),
        **dict(platform_config),
    }
