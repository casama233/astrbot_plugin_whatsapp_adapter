from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
adapter_path = ROOT / "whatsapp_adapter.py"
adapter = adapter_path.read_text("utf-8")

replacement = '''RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {
    **BASE_GATEWAY_CONFIG,
    "dm_policy": "allowlist",
    "allow_from": [],
    "group_policy": "disabled",
    "groups": [],
    "group_allow_from": [],
    "media_caption_mode": "separate",
    # Protocol/Gateway limits and AstrBot-owned command behaviour stay internal.
    "text_chunk_limit": 4000,
    "media_max_mb": 50,
    "command_prefix": "/",
    "register_commands": True,
    # Generic behaviour can be supplied through plugin-level default_* fields.
    "link_preview_single_url": True,
    "typing_indicator": True,
    "send_read_receipts": True,
    "mark_online": False,
    "gateway_health_check_interval": 60,
    "parse_inbound_formatting": True,
    "media_album_debounce_seconds": 2.5,
    "streaming_edit_throttle": 1.0,
    # These options may reasonably differ between WhatsApp account instances.
    "ignore_self_messages": False,
    "pre_ack_private": True,
    "pre_ack_public": "mentions",
    "pre_ack_emojis": "👀",
    "pre_ack_emoji": True,
    "pre_ack_done_emoji": "✅",
    "apply_ephemeral": False,
}

DEFAULT_CONFIG: dict[str, Any] = {
    **BASE_GATEWAY_CONFIG,
    "dm_policy": "allowlist",
    "allow_from": [],
    "group_policy": "disabled",
    "groups": [],
    "group_allow_from": [],
    "media_caption_mode": "separate",
    "ignore_self_messages": False,
    "pre_ack_emoji": True,
    "pre_ack_emojis": "👀",
    "pre_ack_private": True,
    "pre_ack_public": "mentions",
    "pre_ack_done_emoji": "✅",
    "apply_ephemeral": False,
}

UI_CONFIG_KEYS = tuple(DEFAULT_CONFIG)
'''

adapter, count = re.subn(
    r"(?ms)^RUNTIME_DEFAULT_CONFIG: dict\[str, Any\] = \{.*?^UI_CONFIG_KEYS = tuple\(DEFAULT_CONFIG\)\n",
    replacement,
    adapter,
    count=1,
)
if count != 1:
    raise RuntimeError("runtime/default config region not found")
adapter_path.write_text(adapter, "utf-8")

config_path = ROOT / ".pre-commit-config.yaml"
config = config_path.read_text("utf-8")
hook = '''      - id: fix-config-blocks
        name: Fix config blocks
        entry: python .github/scripts/fix_config_blocks.py
        language: system
        pass_filenames: false
'''
config_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
