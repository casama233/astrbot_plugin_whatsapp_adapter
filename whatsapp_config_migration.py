"""One-time migration of plugin defaults and retained per-account settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .whatsapp_config_policy import (
        LEGACY_GATEWAY_DEFAULTS, PLUGIN_DEFAULT_ALIASES,
        adopt_legacy_gateway_defaults, extract_legacy_behavior_overrides,
        extract_legacy_command_prefix,
    )
except ImportError:  # Standalone maintenance tests.
    from whatsapp_config_policy import (
        LEGACY_GATEWAY_DEFAULTS, PLUGIN_DEFAULT_ALIASES,
        adopt_legacy_gateway_defaults, extract_legacy_behavior_overrides,
        extract_legacy_command_prefix,
    )


MIGRATION_VERSION = 1
MIGRATION_KEY = "_whatsapp_migration"


def platform_migration(config: Mapping[str, Any]) -> dict[str, Any]:
    """Translate legacy fields once; subsequent edits use only retained values."""
    existing = config.get(MIGRATION_KEY)
    if isinstance(existing, dict) and existing.get("version", 0) > MIGRATION_VERSION:
        raise RuntimeError("WhatsApp account configuration was written by a newer plugin")
    if isinstance(existing, dict) and existing.get("version") == MIGRATION_VERSION:
        return dict(existing)
    gateway = {}
    for key, default in LEGACY_GATEWAY_DEFAULTS.items():
        value = config.get(f"_legacy_gateway_{key}", config.get(key, default))
        if value != default:
            gateway[key] = value
    return {
        "version": MIGRATION_VERSION,
        "behavior": extract_legacy_behavior_overrides(config),
        "command_prefix": extract_legacy_command_prefix(config),
        "gateway": gateway,
    }


def read_migration_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(state, dict):
        raise RuntimeError("WhatsApp configuration migration record is invalid")
    return state


def migrate_plugin_configuration(
    plugin_config: Mapping[str, Any],
    platform_configs: list[dict[str, Any]],
    state_path: Path,
    *,
    save_plugin: Callable[[dict[str, Any]], None],
    save_platforms: Callable[[list[dict[str, Any]]], None],
    sanitize_platform: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = read_migration_state(state_path)
    if state.get("version", 0) > MIGRATION_VERSION:
        raise RuntimeError("WhatsApp configuration migration was written by a newer plugin")
    if state.get("version") == MIGRATION_VERSION:
        normalized = [sanitize_platform(cfg) if cfg.get("type") == "whatsapp" else cfg
                      for cfg in platform_configs]
        if normalized != platform_configs:
            save_platforms(normalized)
            for old, new in zip(platform_configs, normalized):
                if old is not new:
                    old.clear()
                    old.update(new)
        return dict(plugin_config), state
    candidates = []
    migrated_platforms = []
    for config in platform_configs:
        if config.get("type") != "whatsapp":
            migrated_platforms.append(config)
            continue
        migrated = sanitize_platform(config)
        migrated_platforms.append(migrated)
        retained = platform_migration(migrated)
        candidates.append({**migrated, **retained["gateway"]})
    effective, adopted = adopt_legacy_gateway_defaults(plugin_config, candidates)
    for alias, runtime_key in PLUGIN_DEFAULT_ALIASES.items():
        if alias not in effective and runtime_key in effective:
            effective[alias] = effective[runtime_key]
        effective.pop(runtime_key, None)
    # Persist configuration before marking completion. A failed write leaves the
    # migration retryable; repeating an already-written plan is idempotent.
    save_plugin(effective)
    save_platforms(migrated_platforms)
    state = {"version": MIGRATION_VERSION, "adopted_plugin_values": adopted}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, state_path)
    for original, migrated in zip(platform_configs, migrated_platforms):
        if original is not migrated:
            original.clear()
            original.update(migrated)
    return effective, state
