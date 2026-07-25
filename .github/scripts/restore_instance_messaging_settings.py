from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "whatsapp_adapter.py"
text = ADAPTER.read_text("utf-8")

text = text.replace(
    "from .whatsapp_event import WhatsAppMessageEvent\nfrom .whatsapp_helpers import (",
    "from .whatsapp_event import WhatsAppMessageEvent\n"
    "from .whatsapp_config_policy import merge_runtime_config, normalize_media_caption_mode\n"
    "from .whatsapp_helpers import (",
    1,
)

old_default = '''DEFAULT_CONFIG: dict[str, Any] = {
    **BASE_GATEWAY_CONFIG,
    "dm_policy": "allowlist",
    "allow_from": [],
    "group_policy": "disabled",
    "groups": [],
    "group_allow_from": [],
    "pre_ack_emoji": True,
    "pre_ack_emojis": "👀",
    "pre_ack_private": True,
    "pre_ack_public": "mentions",
    "pre_ack_done_emoji": "✅",
    "apply_ephemeral": False,
    "streaming_edit_throttle": 1.0,
}
'''
new_default = '''DEFAULT_CONFIG: dict[str, Any] = {
    **BASE_GATEWAY_CONFIG,
    "dm_policy": "allowlist",
    "allow_from": [],
    "group_policy": "disabled",
    "groups": [],
    "group_allow_from": [],
    "command_prefix": "/",
    "register_commands": True,
    "media_caption_mode": "separate",
    "text_chunk_limit": 4000,
    "link_preview_single_url": True,
    "typing_indicator": True,
    "send_read_receipts": True,
    "mark_online": False,
    "ignore_self_messages": False,
    "parse_inbound_formatting": True,
    "media_album_debounce_seconds": 2.5,
    "media_max_mb": 50,
    "pre_ack_emoji": True,
    "pre_ack_emojis": "👀",
    "pre_ack_private": True,
    "pre_ack_public": "mentions",
    "pre_ack_done_emoji": "✅",
    "apply_ephemeral": False,
    "streaming_edit_throttle": 1.0,
}
'''
if old_default not in text:
    raise RuntimeError("DEFAULT_CONFIG block not found")
text = text.replace(old_default, new_default, 1)

old_meta = '''    "media_caption_mode": {
        "description": "媒体附加文字模式",
        "type": "string",
        "group": "messaging",
        "hint": "separate=文字与媒体分开发送（两条消息）；caption=紧邻媒体前的文字作为该媒体的描述。",
    },
'''
new_meta = '''    "media_caption_mode": {
        "description": "媒体附加文字模式",
        "type": "string",
        "group": "messaging",
        "options": ["separate", "caption"],
        "hint": "separate=文字与媒体分开发送；caption=紧邻媒体前的文字作为该媒体描述。仅影响普通富媒体消息链，流式回复中的媒体仍分开发送。",
    },
'''
if old_meta not in text:
    raise RuntimeError("media_caption_mode metadata not found")
text = text.replace(old_meta, new_meta, 1)

text = text.replace(
    '"hint": "separate=文字与媒体分开发送（两条消息）；caption=紧邻媒体前的文字作为该媒体的描述。",',
    '"hint": "separate=文字与媒体分开发送；caption=紧邻媒体前的文字作为该媒体描述。仅影响普通富媒体消息链，流式回复中的媒体仍分开发送。",',
    1,
)
text = text.replace(
    '"hint": "separate=text and media sent as separate messages; caption=text immediately before media becomes its caption.",',
    '"hint": "separate sends text and media separately; caption attaches immediately preceding text to ordinary rich-media messages. Streaming media remains separate.",',
    1,
)
text = text.replace(
    '"hint": "separate=文字與媒體分開傳送（兩條訊息）；caption=緊鄰媒體前的文字作為該媒體的描述。",',
    '"hint": "separate=文字與媒體分開傳送；caption=緊鄰媒體前的文字作為該媒體描述。僅影響普通富媒體訊息鏈，流式回覆中的媒體仍分開傳送。",',
    1,
)

old_merge = '        merged = {**RUNTIME_DEFAULT_CONFIG, **platform_config, **plugin_config}\n'
new_merge = '''        merged = merge_runtime_config(
            RUNTIME_DEFAULT_CONFIG,
            plugin_config,
            platform_config,
        )
'''
if old_merge not in text:
    raise RuntimeError("config merge expression not found")
text = text.replace(old_merge, new_merge, 1)

old_normalize = '''        if key in {"allow_from", "group_allow_from", "groups"}:
            return self._coerce_str_list(value)
        if key == "pre_ack_public":
'''
new_normalize = '''        if key in {"allow_from", "group_allow_from", "groups"}:
            return self._coerce_str_list(value)
        if key == "media_caption_mode":
            return normalize_media_caption_mode(value)
        if key == "pre_ack_public":
'''
if old_normalize not in text:
    raise RuntimeError("normalize insertion point not found")
text = text.replace(old_normalize, new_normalize, 1)

ADAPTER.write_text(text, "utf-8")

changelog = ROOT / "CHANGELOG.md"
change_text = changelog.read_text("utf-8")
entry = '''## [Unreleased]\n\n### Changed\n- 将媒体文字模式、链接预览、打字/已读状态、入站格式解析、相簿去抖、媒体上限等实例相关选项恢复到 WhatsApp 平台适配器页面。\n- 配置优先级调整为“运行时默认 < 插件全局默认 < 平台实例配置”，避免插件配置覆盖实例选择。\n- `media_caption_mode` 改为 `separate` / `caption` 下拉选项，非法旧值安全回退到 `separate`，并明确流式媒体仍分开发送。\n\n'''
if "## [Unreleased]" not in change_text:
    first_heading_end = change_text.find("\n", change_text.find("# ")) + 1
    change_text = change_text[:first_heading_end] + "\n" + entry + change_text[first_heading_end:]
    changelog.write_text(change_text, "utf-8")

for relative in (
    ".github/scripts/restore_instance_messaging_settings.py",
    ".github/workflows/restore-instance-messaging-settings.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
