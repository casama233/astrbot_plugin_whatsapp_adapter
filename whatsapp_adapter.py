from __future__ import annotations

import asyncio
import json
import random
import re
import shutil
import time
import traceback
import weakref
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api.event import MessageChain

PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"
_OLD_DATA_DIR_NAME = "astrbot_plugin_whatsapp_adapter"
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path as _get_astrbot_data_path
except ImportError:
    _get_astrbot_data_path = None
from astrbot.api.message_components import File, Image, Location, Plain, Record, Reply, Video
from astrbot.api.platform import (
    AstrBotMessage,
    At,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.core.platform.platform import PlatformStatus

from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient, WhatsAppGatewayError
from .whatsapp_commands import collect_registered_commands, message_matches_command
from .whatsapp_components import (
    WhatsAppButtons,
    WhatsAppEdit,
    WhatsAppList,
    WhatsAppPoll,
)
from .whatsapp_event import WhatsAppMessageEvent
from .whatsapp_helpers import (
    flush_pending_text,
    format_markdown_from_whatsapp,
    process_message_chain,
)


PLUGIN_DIR = Path(__file__).resolve().parent
_ACTIVE_ADAPTERS: weakref.WeakSet["WhatsAppPlatformAdapter"] = weakref.WeakSet()
_RUNTIME_OWNER_REGISTRY: dict[str, weakref.ReferenceType["WhatsAppPlatformAdapter"]] = {}
_LID_PN_CACHE: dict[str, str] = {}  # lid JID → pn JID (e.g. "xxx@lid" → "yyy@s.whatsapp.net")
_PN_LID_CACHE: dict[str, str] = {}  # pn JID → lid JID，用於出站時目標 JID 還原


def _lid_mapping_path(auth_dir: Path, lid_jid: str) -> Path | None:
    """lid JID 對應的磁碟映射文件路徑（lid-mapping-{lid数字}_reverse.json）。"""
    lid_num = lid_jid.split("@", 1)[0].split(":", 1)[0]
    if not lid_num.isdigit():
        return None
    return auth_dir / f"lid-mapping-{lid_num}_reverse.json"


def _load_lid_mappings(auth_dir: Path) -> None:
    """從 Gateway auth 目錄加載所有 lid-mapping-*_reverse.json 到緩存。"""
    if not auth_dir or not auth_dir.is_dir():
        return
    _LID_PN_CACHE.clear()
    _PN_LID_CACHE.clear()
    try:
        for f in auth_dir.iterdir():
            m = re.match(r"^lid-mapping-(\d+)_reverse\.json$", f.name)
            if not m:
                continue
            try:
                phone = json.loads(f.read_text("utf-8"))
                if phone and isinstance(phone, str):
                    lid_jid = f"{m.group(1)}@lid"
                    pn_jid = f"{phone}@s.whatsapp.net"
                    _LID_PN_CACHE[lid_jid] = pn_jid
                    _PN_LID_CACHE[pn_jid] = lid_jid
            except Exception:
                continue
        if _LID_PN_CACHE:
            logger.info("已加載 %d 條 lid→PN 映射到緩存", len(_LID_PN_CACHE))
    except Exception as exc:
        logger.debug("加載 lid 映射失敗: %s", exc)


def _save_lid_mapping(lid_jid: str, pn_jid: str) -> None:
    """持久化 lid→PN 映射到 Gateway auth 目錄。"""
    if not _get_astrbot_data_path:
        return
    plugin_data = Path(_get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
    auth_dir = plugin_data / "whatsapp-auth"
    path = _lid_mapping_path(auth_dir, lid_jid)
    if not path or path.exists():
        return
    try:
        auth_dir.mkdir(parents=True, exist_ok=True)
        pn = re.sub(r"\D", "", pn_jid.split("@", 1)[0].split(":", 1)[0])
        path.write_text(json.dumps(pn), "utf-8")
    except Exception:
        pass


def _runtime_owner_registry() -> dict[str, weakref.ReferenceType["WhatsAppPlatformAdapter"]]:
    return _RUNTIME_OWNER_REGISTRY

LOGO_ABSOLUTE = str(PLUGIN_DIR / "logo.svg")
# 熱重載時建立版本化 logo 副本，使用 timestamp 確保 cache key 唯一
_LOGO_TS = str(int(time.time() * 1000))
_LOGO_VERSIONED = PLUGIN_DIR / f"logo_{_LOGO_TS}.svg"
try:
    shutil.copy2(LOGO_ABSOLUTE, _LOGO_VERSIONED)
    LOGO_ABSOLUTE = str(_LOGO_VERSIONED)
    # 清理 30 秒前的舊副本，避免累積
    _now = time.time()
    for f in PLUGIN_DIR.glob("logo_*.svg"):
        if f.name.startswith("logo_") and f.name != _LOGO_VERSIONED.name:
            try:
                if _now - f.stat().st_mtime > 30:
                    f.unlink()
            except OSError:
                pass
except Exception:
    pass

GATEWAY_MEDIA_MAX_MB = 50
GATEWAY_MEDIA_MESSAGE_MAX_MB = 100
GATEWAY_DOCUMENT_MAX_MB = 2048
GATEWAY_AUDIO_MAX_MB = 16

BASE_GATEWAY_CONFIG: dict[str, Any] = {
    "gateway_host": "127.0.0.1",
    "gateway_port": 18789,
    "auto_start_gateway": True,
    "node_executable": "node",
    "auth_dir": "",
    "log_level": "info",
}

DEFAULT_CONFIG: dict[str, Any] = {
    **BASE_GATEWAY_CONFIG,
    "dm_policy": "allowlist",
    "allow_from": [],
    "group_policy": "disabled",
    "groups": [],
    "group_allow_from": [],
    "media_caption_mode": "separate",
    "text_chunk_limit": 4000,
    "link_preview_single_url": True,
    "typing_indicator": True,
    "send_read_receipts": True,
    "mark_online": False,
    "gateway_health_check_interval": 60,
    "reaction_level": "ack",
    "remove_ack_after_reply": False,
    "parse_inbound_formatting": True,
    "inbound_reaction_events": False,
    "media_album_debounce_seconds": 2.5,
    "ignore_self_messages": False,
    "command_prefix": "/",
    "register_commands": True,
    "pre_ack_private": True,
    "pre_ack_public": "mentions",
    "pre_ack_emojis": "✍️",
    "pre_ack_emoji": True,
    "media_max_mb": 50,
}

CONFIG_KEY_ALIASES: dict[str, str] = {
    "Gateway 绑定地址": "gateway_host",
    "Gateway 端口": "gateway_port",
    "自动启动 Gateway": "auto_start_gateway",
    "Node.js 可执行文件": "node_executable",
    "登录态目录": "auth_dir",
    "Gateway 日志级别": "log_level",
    "私聊策略": "dm_policy",
    "私聊允许名单": "allow_from",
    "群聊策略": "group_policy",
    "允许接入的群 JID": "groups",
    "群聊发送者允许名单": "group_allow_from",
    "媒体文字模式": "media_caption_mode",
    "文本分片长度": "text_chunk_limit",
    "单独链接启用预览": "link_preview_single_url",
    "启用打字指示": "typing_indicator",
    "发送已读回执": "send_read_receipts",
    "标记在线状态": "mark_online",
    "Gateway 健康检查间隔": "gateway_health_check_interval",
    "反应级别": "reaction_level",
    "预回应表情": "pre_ack_emojis",
    "私聊启用手动回应": "pre_ack_private",
    "群组回应模式": "pre_ack_public",
    "回复后清除回应": "remove_ack_after_reply",
    "解析入站格式": "parse_inbound_formatting",
    "入站表情回应事件": "inbound_reaction_events",
    "媒体相册去抖秒数": "media_album_debounce_seconds",
    "忽略自身消息": "ignore_self_messages",
    "指令前缀": "command_prefix",
    "注册斜线指令": "register_commands",
    "私聊预回应": "pre_ack_private",
    "群聊预回应": "pre_ack_public",
    "预回应表情列表": "pre_ack_emojis",
    "启用预回应表情": "pre_ack_emoji",
    "媒体上传大小限制(MB)": "media_max_mb",
    "ack_reaction_emoji": "pre_ack_emojis",
    "ack_reaction_direct": "pre_ack_private",
    "ack_reaction_group": "pre_ack_public",
}

CONFIG_METADATA: dict[str, Any] = {
    "gateway_host": {
        "description": "Gateway 绑定地址",
        "type": "string",
        "group": "connection",
        "hint": "Gateway HTTP 监听地址。AstrBot 与 Gateway 同容器时建议保持 127.0.0.1。",
    },
    "gateway_port": {
        "description": "Gateway 端口",
        "type": "int",
        "group": "connection",
        "hint": "Gateway HTTP/SSE 端口，预设 18789。",
    },
    "auto_start_gateway": {
        "description": "自动启动 Gateway",
        "type": "bool",
        "group": "connection",
        "hint": "启用后，平台启动时自动拉起内置 Node.js WhatsApp Gateway。如使用外部 Gateway 可关闭此项。",
    },
    "node_executable": {
        "description": "Node.js 路径",
        "type": "string",
        "group": "connection",
        "hint": "执行 Gateway 的 Node.js 命令或绝对路径。一般保持 node 即可，需 Node.js 20+。",
    },
    "auth_dir": {
        "description": "认证目录（留空自动）",
        "type": "string",
        "group": "connection",
        "hint": "WhatsApp 登录状态（凭证）保存目录。留空时自动使用 {data_dir}/whatsapp-auth。",
    },
    "log_level": {
        "description": "Gateway 日志级别",
        "type": "string",
        "group": "connection",
        "hint": "可选：silent、fatal、error、warn、info、debug、trace。",
    },
    "dm_policy": {
        "description": "私聊接收策略",
        "type": "string",
        "group": "permissions",
        "hint": "allowlist=仅允许名单中号码；open=开放所有人私聊；disabled=关闭私聊功能。",
    },
    "allow_from": {
        "description": "私聊允许名单",
        "type": "list",
        "group": "permissions",
        "hint": "允许私聊的 WhatsApp 号码，建议 E.164 格式：+85212345678。[\"*\"] 表示开放所有私聊。",
    },
    "group_policy": {
        "description": "群聊接收策略",
        "type": "string",
        "group": "permissions",
        "hint": "allowlist=仅允许在群名单中的群；open=允许所有已加入群；disabled=关闭群聊功能。",
    },
    "groups": {
        "description": "允许接入的群 JID",
        "type": "list",
        "group": "permissions",
        "hint": "允许机器人接入的 WhatsApp 群 JID 列表（如 120363xxx@g.us）。[\"*\"] 表示允许所有群。",
    },
    "group_allow_from": {
        "description": "群聊发送者白名单",
        "type": "list",
        "group": "permissions",
        "hint": "允许在群聊中触发机器人的发送者号码。留空时回退到私聊允许名单。[\"*\"] 允许所有群成员。",
    },
    "command_prefix": {
        "description": "指令前缀",
        "type": "string",
        "group": "commands",
        "hint": "斜线指令触发前缀，例如 /help 会触发已注册的 help 指令。",
    },
    "register_commands": {
        "description": "启用斜线指令",
        "type": "bool",
        "group": "commands",
        "hint": "启用后，以前缀开头且匹配已注册指令的消息会标记为唤醒指令。",
    },
    "typing_indicator": {
        "description": "发送打字状态",
        "type": "bool",
        "group": "presence",
        "hint": "启用后，机器人发送回复前向 WhatsApp 显示 typing 状态，发送完恢复 available。",
    },
    "send_read_receipts": {
        "description": "发送已读回执",
        "type": "bool",
        "group": "presence",
        "hint": "对已接受入站消息发送 WhatsApp 已读蓝勾。",
    },
    "mark_online": {
        "description": "标记在线状态",
        "type": "bool",
        "group": "presence",
        "hint": "启用后定期向 WhatsApp 发送 available 状态。",
    },
    "media_caption_mode": {
        "description": "媒体附加文字模式",
        "type": "string",
        "group": "messaging",
        "hint": "separate=文字与媒体分开发送（两条消息）；caption=紧邻媒体前的文字作为该媒体的描述。",
    },
    "text_chunk_limit": {
        "description": "文字切片长度",
        "type": "int",
        "group": "messaging",
        "hint": "超过此长度的出站文字会被自动切分为多条消息发送。",
    },
    "link_preview_single_url": {
        "description": "链接预览",
        "type": "bool",
        "group": "messaging",
        "hint": "仅当一条纯文字消息只包含一个 URL 时启用 WhatsApp 链接预览卡片。",
    },
    "ignore_self_messages": {
        "description": "忽略自身消息",
        "type": "bool",
        "group": "messaging",
        "hint": "启用后，发送者 JID 与机器人自身 JID 相同时忽略该消息。解决同号码自己 @自己或给自己发消息触发机器人的问题。",
    },
    "parse_inbound_formatting": {
        "description": "解析入站 WhatsApp 格式",
        "type": "bool",
        "group": "messaging",
        "hint": "入站 *粗体* _斜体_ ~删除线~ ```代码``` 转为 Markdown 供 LLM 阅读。",
    },
    "media_album_debounce_seconds": {
        "description": "相簿去抖时间（秒）",
        "type": "float",
        "group": "messaging",
        "hint": "同发送者在短时间内连续发送多张无文字图片时，合并为一条相簿消息。设为 0 关闭。",
    },
    "reaction_level": {
        "description": "反应级别",
        "type": "string",
        "group": "pre_ack",
        "hint": "off=禁用所有反应；ack=仅预回应回应（收到消息时先发表情）；minimal=回应+LLM可发保守表情；extensive=回应+LLM可用更多表情。",
    },
    "remove_ack_after_reply": {
        "description": "回复后清除回应",
        "type": "bool",
        "group": "pre_ack",
        "hint": "启用后，机器人发送回复后自动清除预回应表情，不留残留在消息上。",
    },
    "gateway_health_check_interval": {
        "description": "健康检查间隔（秒）",
        "type": "int",
        "group": "advanced",
        "hint": "后台检查 Gateway 健康状态的间隔秒数。设为 0 可关闭健康检查。",
    },
    "inbound_reaction_events": {
        "description": "入站表情回应事件",
        "type": "bool",
        "group": "advanced",
        "hint": "将用户对消息的表情回应（emoji reaction）转为 AstrBot 事件。默认关闭。",
    },
    "pre_ack_private": {
        "description": "私聊预回应",
        "type": "bool",
        "group": "pre_ack",
        "hint": "启用后，私聊收到消息时自动触发预回应表情。",
    },
    "pre_ack_public": {
        "description": "群聊预回应模式",
        "type": "string",
        "group": "pre_ack",
        "hint": "always=始终触发预回应；mentions=仅被 @ 或回复时触发；never=不触发预回应。",
    },
    "pre_ack_emojis": {
        "description": "预回应表情列表",
        "type": "string",
        "group": "pre_ack",
        "hint": "预回应时使用的 WhatsApp emoji，例如 💭、✍️。",
    },
    "pre_ack_emoji": {
        "description": "启用预回应表情",
        "type": "bool",
        "group": "pre_ack",
        "hint": "启用后，bot 收到消息时通过 WhatsApp emoji reaction 发出一条预回应。",
    },
    "media_max_mb": {
        "description": "媒体上传大小限制 (MB)",
        "type": "float",
        "group": "messaging",
        "hint": "上传到 WhatsApp Gateway 的单个媒体文件大小上限（MB）。预设 50。",
    },
}

WHATSAPP_I18N_RESOURCES: dict[str, dict] = {
    "zh-CN": {
        "gateway_host": {
            "description": "Gateway 绑定地址",
            "hint": "Gateway HTTP 监听地址。AstrBot 与 Gateway 同容器时建议保持 127.0.0.1。",
        },
        "gateway_port": {
            "description": "Gateway 端口",
            "hint": "Gateway HTTP/SSE 端口，预设 18789。",
        },
        "auto_start_gateway": {
            "description": "自动启动 Gateway",
            "hint": "启用后，平台启动时自动拉起内置 Node.js WhatsApp Gateway。如使用外部 Gateway 可关闭此项。",
        },
        "node_executable": {
            "description": "Node.js 路径",
            "hint": "执行 Gateway 的 Node.js 命令或绝对路径。一般保持 node 即可，需 Node.js 20+。",
        },
        "auth_dir": {
            "description": "认证目录（留空自动）",
            "hint": "WhatsApp 登录状态（凭证）保存目录。留空时自动使用 {data_dir}/whatsapp-auth。",
        },
        "log_level": {
            "description": "Gateway 日志级别",
            "hint": "可选：silent、fatal、error、warn、info、debug、trace。",
        },
        "dm_policy": {
            "description": "私聊接收策略",
            "hint": "allowlist=仅允许名单中号码；open=开放所有人私聊；disabled=关闭私聊功能。",
        },
        "allow_from": {
            "description": "私聊允许名单",
            "hint": "允许私聊的 WhatsApp 号码，建议 E.164 格式：+85212345678。[\"*\"] 表示开放所有私聊。",
        },
        "group_policy": {
            "description": "群聊接收策略",
            "hint": "allowlist=仅允许在群名单中的群；open=允许所有已加入群；disabled=关闭群聊功能。",
        },
        "groups": {
            "description": "允许接入的群 JID",
            "hint": "允许机器人接入的 WhatsApp 群 JID 列表（如 120363xxx@g.us）。[\"*\"] 表示允许所有群。",
        },
        "group_allow_from": {
            "description": "群聊发送者白名单",
            "hint": "允许在群聊中触发机器人的发送者号码。留空时回退到私聊允许名单。[\"*\"] 允许所有群成员。",
        },
        "command_prefix": {
            "description": "指令前缀",
            "hint": "斜线指令触发前缀，例如 /help 会触发已注册的 help 指令。",
        },
        "register_commands": {
            "description": "启用斜线指令",
            "hint": "启用后，以前缀开头且匹配已注册指令的消息会标记为唤醒指令。",
        },
        "typing_indicator": {
            "description": "发送打字状态",
            "hint": "启用后，机器人发送回复前向 WhatsApp 显示 typing 状态，发送完恢复 available。",
        },
        "send_read_receipts": {
            "description": "发送已读回执",
            "hint": "对已接受入站消息发送 WhatsApp 已读蓝勾。",
        },
        "mark_online": {
            "description": "标记在线状态",
            "hint": "启用后定期向 WhatsApp 发送 available 状态。",
        },
        "media_caption_mode": {
            "description": "媒体附加文字模式",
            "hint": "separate=文字与媒体分开发送（两条消息）；caption=紧邻媒体前的文字作为该媒体的描述。",
        },
        "text_chunk_limit": {
            "description": "文字切片长度",
            "hint": "超过此长度的出站文字会被自动切分为多条消息发送。",
        },
        "link_preview_single_url": {
            "description": "链接预览",
            "hint": "仅当一条纯文字消息只包含一个 URL 时启用 WhatsApp 链接预览卡片。",
        },
        "ignore_self_messages": {
            "description": "忽略自身消息",
            "hint": "启用后，发送者 JID 与机器人自身 JID 相同时忽略该消息。解决同号码自己 @自己或给自己发消息触发机器人的问题。",
        },
        "parse_inbound_formatting": {
            "description": "解析入站 WhatsApp 格式",
            "hint": "入站 *粗体* _斜体_ ~删除线~ ```代码``` 转为 Markdown 供 LLM 阅读。",
        },
        "media_album_debounce_seconds": {
            "description": "相簿去抖时间（秒）",
            "hint": "同发送者在短时间内连续发送多张无文字图片时，合并为一条相簿消息。设为 0 关闭。",
        },
        "reaction_level": {
            "description": "反应级别",
            "hint": "off=禁用所有反应；ack=仅预回应（收到消息时先发表情）；minimal=回应+LLM可发保守表情；extensive=回应+LLM可用更多表情。",
        },
        "remove_ack_after_reply": {
            "description": "回复后清除回应",
            "hint": "启用后，机器人发送回复后自动清除预回应表情，不留残留在消息上。",
        },
        "gateway_health_check_interval": {
            "description": "健康检查间隔（秒）",
            "hint": "后台检查 Gateway 健康状态的间隔秒数。设为 0 可关闭健康检查。",
        },
        "inbound_reaction_events": {
            "description": "入站表情回应事件",
            "hint": "将用户对消息的表情回应（emoji reaction）转为 AstrBot 事件。默认关闭。",
        },
        "pre_ack_private": {
            "description": "私聊预回应",
            "hint": "启用后，私聊收到消息时自动触发预回应表情。",
        },
        "pre_ack_public": {
            "description": "群聊预回应模式",
            "hint": "always=始终触发预回应；mentions=仅被 @ 或回复时触发；never=不触发预回应。",
        },
        "pre_ack_emojis": {
            "description": "预回应表情列表",
            "hint": "预回应时使用的 WhatsApp emoji，例如 💭、✍️。",
        },
        "pre_ack_emoji": {
            "description": "启用预回应表情",
            "hint": "启用后，bot 收到消息时通过 WhatsApp emoji reaction 发出一条预回应。",
        },
        "media_max_mb": {
            "description": "媒体上传大小限制 (MB)",
            "hint": "上传到 WhatsApp Gateway 的单个媒体文件大小上限（MB）。预设 50。",
        },
    },
    "en-US": {
        "gateway_host": {
            "description": "Gateway bind address",
            "hint": "Gateway HTTP listen address. Keep 127.0.0.1 when AstrBot and Gateway run in the same container.",
        },
        "gateway_port": {
            "description": "Gateway port",
            "hint": "Gateway HTTP/SSE port, default 18789.",
        },
        "auto_start_gateway": {
            "description": "Auto-start Gateway",
            "hint": "Starts the bundled Node.js WhatsApp Gateway when the platform starts. Disable when using an external Gateway.",
        },
        "node_executable": {
            "description": "Node.js executable",
            "hint": "Node.js command or absolute path to run the Gateway. Keep as 'node'. Node.js 20+ required.",
        },
        "auth_dir": {
            "description": "Auth directory (auto if empty)",
            "hint": "Directory to persist WhatsApp login credentials. Leave empty for auto: {data_dir}/whatsapp-auth.",
        },
        "log_level": {
            "description": "Gateway log level",
            "hint": "Options: silent, fatal, error, warn, info, debug, trace.",
        },
        "dm_policy": {
            "description": "DM policy",
            "hint": "allowlist=numbers in allow_from only; open=accept all DMs; disabled=reject DMs.",
        },
        "allow_from": {
            "description": "DM allowlist",
            "hint": "WhatsApp numbers allowed to DM the bot, E.164 format: +15551234567. [\"*\"] means allow all DMs.",
        },
        "group_policy": {
            "description": "Group policy",
            "hint": "allowlist=groups in the groups list only; open=all joined groups; disabled=no group support.",
        },
        "groups": {
            "description": "Allowed group JIDs",
            "hint": "WhatsApp group JIDs the bot may join, e.g. 120363xxx@g.us. [\"*\"] allows all groups.",
        },
        "group_allow_from": {
            "description": "Group sender allowlist",
            "hint": "Senders allowed to invoke the bot in groups. Falls back to DM allowlist when empty. [\"*\"] allows all members.",
        },
        "command_prefix": {
            "description": "Command prefix",
            "hint": "Prefix for slash commands, e.g. /help triggers the registered help command.",
        },
        "register_commands": {
            "description": "Register slash commands",
            "hint": "When enabled, messages starting with the prefix that match a registered command are treated as wake commands.",
        },
        "typing_indicator": {
            "description": "Typing indicator",
            "hint": "Shows a composing presence before replying and restores 'available' after the reply is sent.",
        },
        "send_read_receipts": {
            "description": "Send read receipts",
            "hint": "Sends WhatsApp blue check marks for processed inbound messages.",
        },
        "mark_online": {
            "description": "Online presence",
            "hint": "Periodically sends 'available' presence to WhatsApp.",
        },
        "media_caption_mode": {
            "description": "Media caption mode",
            "hint": "separate=text and media sent as separate messages; caption=text immediately before media becomes its caption.",
        },
        "text_chunk_limit": {
            "description": "Text chunk length",
            "hint": "Outbound text exceeding this limit is automatically split into multiple messages.",
        },
        "link_preview_single_url": {
            "description": "Link preview",
            "hint": "Enable WhatsApp link preview only when a plain text message contains exactly one URL.",
        },
        "ignore_self_messages": {
            "description": "Ignore self messages",
            "hint": "Ignores messages where the sender JID matches the bot's own JID. Prevents self-triggering.",
        },
        "parse_inbound_formatting": {
            "description": "Parse inbound formatting",
            "hint": "Converts inbound *bold* _italic_ ~strikethrough~ ```code``` to Markdown for the LLM.",
        },
        "media_album_debounce_seconds": {
            "description": "Album debounce (seconds)",
            "hint": "Merges consecutive images from the same sender within this window into one album message. Set 0 to disable.",
        },
        "reaction_level": {
            "description": "Reaction level",
            "hint": "off=no reactions; ack=pre-ack only; minimal=ack + conservative agent reactions; extensive=ack + more agent reactions.",
        },
        "remove_ack_after_reply": {
            "description": "Remove ack after reply",
            "hint": "When enabled, the pre-ack emoji is removed after the bot sends its reply.",
        },
        "gateway_health_check_interval": {
            "description": "Health check interval (s)",
            "hint": "Interval in seconds for background Gateway health checks. Set 0 to disable.",
        },
        "inbound_reaction_events": {
            "description": "Inbound reaction events",
            "hint": "Converts emoji reactions on messages to AstrBot events. Disabled by default.",
        },
        "pre_ack_private": {
            "description": "DM pre-ack",
            "hint": "When enabled, private messages auto-trigger a pre-ack emoji reaction.",
        },
        "pre_ack_public": {
            "description": "Group pre-ack mode",
            "hint": "always=always react; mentions=only on @mentions or replies; never=no pre-ack.",
        },
        "pre_ack_emojis": {
            "description": "Pre-ack emojis",
            "hint": "WhatsApp emojis used for pre-ack reactions, e.g. 💭, ✍️.",
        },
        "pre_ack_emoji": {
            "description": "Enable pre-ack emoji",
            "hint": "When enabled, the bot sends a WhatsApp emoji reaction for each message received.",
        },
        "media_max_mb": {
            "description": "Media upload size limit (MB)",
            "hint": "Maximum size per media file uploaded to the WhatsApp Gateway (MB). Default 50.",
        },
    },
    "zh-TW": {
        "gateway_host": {
            "description": "Gateway 綁定位址",
            "hint": "Gateway HTTP 監聽位址。AstrBot 與 Gateway 同容器時建議保持 127.0.0.1。",
        },
        "gateway_port": {
            "description": "Gateway 連接埠",
            "hint": "Gateway HTTP/SSE 連接埠，預設 18789。",
        },
        "auto_start_gateway": {
            "description": "自動啟動 Gateway",
            "hint": "啟用後，平台啟動時自動拉起內建 Node.js WhatsApp Gateway。如使用外部 Gateway 可關閉此項。",
        },
        "node_executable": {
            "description": "Node.js 路徑",
            "hint": "執行 Gateway 的 Node.js 命令或絕對路徑。一般保持 node 即可，需 Node.js 20+。",
        },
        "auth_dir": {
            "description": "認證目錄（留空自動）",
            "hint": "WhatsApp 登入狀態（憑證）儲存目錄。留空時自動使用 {data_dir}/whatsapp-auth。",
        },
        "log_level": {
            "description": "Gateway 日誌級別",
            "hint": "可選：silent、fatal、error、warn、info、debug、trace。",
        },
        "dm_policy": {
            "description": "私聊接收策略",
            "hint": "allowlist=僅允許名單中號碼；open=開放所有人私聊；disabled=關閉私聊功能。",
        },
        "allow_from": {
            "description": "私聊允許名單",
            "hint": "允許私聊的 WhatsApp 號碼，建議 E.164 格式：+85212345678。[\"*\"] 表示開放所有私聊。",
        },
        "group_policy": {
            "description": "群聊接收策略",
            "hint": "allowlist=僅允許在群名單中的群；open=允許所有已加入群；disabled=關閉群聊功能。",
        },
        "groups": {
            "description": "允許接入的群 JID",
            "hint": "允許機器人接入的 WhatsApp 群 JID 列表（如 120363xxx@g.us）。[\"*\"] 表示允許所有群。",
        },
        "group_allow_from": {
            "description": "群聊傳送者白名單",
            "hint": "允許在群聊中觸發機器人的傳送者號碼。留空時回退到私聊允許名單。[\"*\"] 允許所有群成員。",
        },
        "command_prefix": {
            "description": "指令前綴",
            "hint": "斜線指令觸發前綴，例如 /help 會觸發已註冊的 help 指令。",
        },
        "register_commands": {
            "description": "啟用斜線指令",
            "hint": "啟用後，以前綴開頭且匹配已註冊指令的訊息會標記為喚醒指令。",
        },
        "typing_indicator": {
            "description": "傳送打字狀態",
            "hint": "啟用後，機器人傳送回覆前向 WhatsApp 顯示 typing 狀態，傳送完恢復 available。",
        },
        "send_read_receipts": {
            "description": "傳送已讀回執",
            "hint": "對已接受入站訊息傳送 WhatsApp 已讀藍勾。",
        },
        "mark_online": {
            "description": "標記線上狀態",
            "hint": "啟用後定期向 WhatsApp 傳送 available 狀態。",
        },
        "media_caption_mode": {
            "description": "媒體附加文字模式",
            "hint": "separate=文字與媒體分開傳送（兩條訊息）；caption=緊鄰媒體前的文字作為該媒體的描述。",
        },
        "text_chunk_limit": {
            "description": "文字切片長度",
            "hint": "超過此長度的出站文字會被自動切分為多條訊息傳送。",
        },
        "link_preview_single_url": {
            "description": "連結預覽",
            "hint": "僅當一條純文字訊息只包含一個 URL 時啟用 WhatsApp 連結預覽卡片。",
        },
        "ignore_self_messages": {
            "description": "忽略自身訊息",
            "hint": "啟用後，傳送者 JID 與機器人自身 JID 相同時忽略該訊息。解決同號碼自己 @自己或給自己發訊息觸發機器人的問題。",
        },
        "parse_inbound_formatting": {
            "description": "解析入站 WhatsApp 格式",
            "hint": "入站 *粗體* _斜體_ ~刪除線~ ```程式碼``` 轉為 Markdown 供 LLM 閱讀。",
        },
        "media_album_debounce_seconds": {
            "description": "相簿去抖時間（秒）",
            "hint": "同傳送者在短時間內連續傳送多張無文字圖片時，合併為一條相簿訊息。設為 0 關閉。",
        },
        "reaction_level": {
            "description": "反應級別",
            "hint": "off=停用所有反應；ack=僅預回應（收到訊息時先發表情）；minimal=回應+LLM可發保守表情；extensive=回應+LLM可用更多表情。",
        },
        "remove_ack_after_reply": {
            "description": "回覆後清除回應",
            "hint": "啟用後，機器人傳送回覆後自動清除預回應表情，不留殘留在訊息上。",
        },
        "gateway_health_check_interval": {
            "description": "健康檢查間隔（秒）",
            "hint": "後台檢查 Gateway 健康狀態的間隔秒數。設為 0 可關閉健康檢查。",
        },
        "inbound_reaction_events": {
            "description": "入站表情回應事件",
            "hint": "將使用者對訊息的表情回應（emoji reaction）轉為 AstrBot 事件。預設關閉。",
        },
        "pre_ack_private": {
            "description": "私聊預回應",
            "hint": "啟用後，私聊收到訊息時自動觸發預回應表情。",
        },
        "pre_ack_public": {
            "description": "群聊預回應模式",
            "hint": "always=始終觸發預回應；mentions=僅被 @ 或回覆時觸發；never=不觸發預回應。",
        },
        "pre_ack_emojis": {
            "description": "預回應表情列表",
            "hint": "預回應時使用的 WhatsApp emoji，例如 💭、✍️。",
        },
        "pre_ack_emoji": {
            "description": "啟用預回應表情",
            "hint": "啟用後，bot 收到訊息時透過 WhatsApp emoji reaction 發出一條預回應。",
        },
        "media_max_mb": {
            "description": "媒體上傳大小限制 (MB)",
            "hint": "上傳到 WhatsApp Gateway 的單個媒體檔案大小上限（MB）。預設 50。",
        },
    },
}

@register_platform_adapter(
    "whatsapp",
    "WhatsApp Web Gateway 适配器",
    default_config_tmpl=DEFAULT_CONFIG,
    adapter_display_name="WhatsApp Web Gateway 适配器",
    logo_path=LOGO_ABSOLUTE,
    config_metadata=CONFIG_METADATA,
    support_streaming_message=True,
    i18n_resources=WHATSAPP_I18N_RESOURCES,
)
class WhatsAppPlatformAdapter(Platform):
    def __init__(
        self,
        platform_config: dict[str, Any],
        platform_settings: dict[str, Any],
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config or {}, event_queue)
        self.config = self._merged_config(platform_config or {})
        self.client = WhatsAppGatewayClient(self._base_url)
        self.gateway_process: GatewayProcess | None = None
        self._stopped = asyncio.Event()
        self._reconnect_event = asyncio.Event()
        self._health_task: asyncio.Task | None = None
        self._gateway_healthy = False
        self._restarting = False
        self._last_gateway_status_log: tuple[Any, Any, Any] | None = None
        self._platform_config = platform_config or {}
        self._registered_commands: list[str] = []
        _ACTIVE_ADAPTERS.add(self)
        self._refresh_registered_commands()
        _load_lid_mappings(self._auth_dir())
        logger.info(
            "WhatsApp platform adapter initialized: gateway=%s auto_start=%s dm_policy=%s allow_from=%s group_policy=%s groups=%s auth_dir=%s",
            self._base_url,
            bool(self.config.get("auto_start_gateway", True)),
            self.config.get("dm_policy"),
            self._count_label(self.config.get("allow_from")),
            self.config.get("group_policy"),
            self._count_label(self.config.get("groups")),
            str(self._auth_dir()),
        )

    @property
    def _base_url(self) -> str:
        return f"http://{self.config['gateway_host']}:{int(self.config['gateway_port'])}"

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            "whatsapp",
            "WhatsApp Web Gateway 适配器",
            str(self.config.get("id") or "whatsapp"),
            logo_path=LOGO_ABSOLUTE,
        )

    async def send_by_session(self, session: MessageSesion, message_chain: MessageChain):
        target = getattr(session, "session_id", None) or getattr(session, "message_session_id", None)
        if not target:
            logger.debug("WhatsApp send_by_session 跳过自訂發送（無目標會話）")
            await super().send_by_session(session, message_chain)
            return

        # PN→lid 正向解析：若目標是 PN 且有緩存 lid，用 lid 確保訊息歸流正確
        lid_target = _PN_LID_CACHE.get(str(target)) if target else None
        if lid_target:
            target = lid_target

        logger.debug(
            "WhatsApp send_by_session: target=%s components=%s",
            target,
            [component.__class__.__name__ for component in message_chain.chain],
        )
        await self._send_presence(target, "composing")
        try:
            pending_caption, pending_mentions = await process_message_chain(
                self.client, target, message_chain.chain,
                link_preview_single_url=bool(self.config.get("link_preview_single_url", True)),
                text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
                use_caption=str(self.config.get("media_caption_mode") or "separate") == "caption",
            )
            await flush_pending_text(
                self.client, target, pending_caption, pending_mentions,
                link_preview_single_url=bool(self.config.get("link_preview_single_url", True)),
                text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
            )
            await super().send_by_session(session, message_chain)
        finally:
            await self._send_presence(target, "available")

    async def run(self):
        if self._stopped.is_set():
            self._stopped.clear()
            self._reconnect_event.clear()
        await self._claim_runtime_owner()
        logger.info("正在启动 WhatsApp 适配器事件循环: gateway=%s", self._base_url)
        try:
            while not self._stopped.is_set():
                try:
                    await self._connect_gateway()
                    while not self._stopped.is_set() and not self._reconnect_event.is_set():
                        try:
                            async for event in self.client.events():
                                if self._stopped.is_set() or self._reconnect_event.is_set():
                                    break
                                if event.get("type") == "message":
                                    logger.info(
                                        "WhatsApp inbound message event: chat=%s sender=%s from_me=%s message_id=%s text_len=%s media_count=%s",
                                        event.get("chatJid"),
                                        event.get("senderJid"),
                                        event.get("fromMe"),
                                        event.get("messageId"),
                                        len(str(event.get("text") or "")),
                                        len(event.get("media") or []),
                                    )
                                    abm = await self.convert_message(event)
                                    if abm:
                                        await self.handle_msg(abm)
                                elif event.get("type") == "rejected":
                                    logger.info(
                                        "WhatsApp DM rejected by allowlist: chat=%s sender=%s phone=%s reason=%s message_id=%s text_len=%s",
                                        event.get("chatJid"),
                                        event.get("senderJid"),
                                        event.get("senderPhone"),
                                        event.get("reason"),
                                        event.get("messageId"),
                                        len(str(event.get("text") or "")),
                                    )
                                elif event.get("type") in {"qr", "status"}:
                                    self._log_gateway_event(event)
                        except asyncio.CancelledError:
                            raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._stopped.is_set() or self._reconnect_event.is_set():
                        break
                    is_timeout = isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__
                    if is_timeout:
                        logger.info("WhatsApp Gateway 事件流空闲超时，正在重连: %s", exc)
                    else:
                        logger.warning("WhatsApp Gateway 事件流中断: %s", exc)
                        self._record_gateway_error(f"WhatsApp Gateway 事件流中断: {exc}", exc_info=exc)
                    try:
                        await self._ensure_gateway_running()
                        self._mark_running()
                    except Exception as restart_exc:
                        self._record_gateway_error(f"WhatsApp Gateway restart failed: {restart_exc}", exc_info=restart_exc)
                    await asyncio.sleep(3)
                    if self._reconnect_event.is_set():
                        self._reconnect_event.clear()
                        await self._shutdown_gateway_transport()
                        continue
        except asyncio.CancelledError:
            logger.info("WhatsApp 平台适配器运行已取消")
            raise
        finally:
            await self._shutdown_gateway_transport()
            self._release_runtime_owner()

    async def reload(self, platform_config: dict[str, Any]) -> None:
        """熱重載平台配置：重啟 Gateway 進程以確保載入最新 Gateway 代碼與配置。"""
        self._platform_config = platform_config or {}
        self.config = self._merged_config(self._platform_config)
        self._reconnect_event.set()
        await self.client.close()
        if self.gateway_process:
            await self.gateway_process.stop()
            self.gateway_process = None
        await self._ensure_gateway_running()

    async def terminate(self):
        logger.info("正在终止 WhatsApp 平台适配器")
        self._stopped.set()
        self._reconnect_event.set()
        run_task = getattr(self, '_run_task', None)
        if run_task is not None and not run_task.done():
            run_task.cancel()
            try:
                await asyncio.wait_for(run_task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await self._shutdown_gateway_transport()
        self._release_runtime_owner()
        _ACTIVE_ADAPTERS.discard(self)
        self._status = PlatformStatus.STOPPED

    async def convert_message(self, data: dict[str, Any]) -> AstrBotMessage | None:
        if data.get("fromMe"):
            logger.debug("忽略自身消息: message_id=%s", data.get("messageId"))
            return None

        chat_jid = str(data.get("chatJid") or "")
        sender_jid = str(data.get("senderJid") or chat_jid)
        sender_pn = str(data.get("senderPn") or "")
        sender_phone = str(data.get("senderPhone") or "")

        if sender_phone:
            digits = "".join(ch for ch in sender_phone if ch.isdigit())
            if digits and not sender_pn.endswith("@s.whatsapp.net"):
                sender_pn = f"{digits}@s.whatsapp.net"

        # 缓存 lid→PN 映射，用于出站 @mention 时解析
        if sender_jid.endswith("@lid") and sender_pn.endswith("@s.whatsapp.net"):
            if sender_jid not in _LID_PN_CACHE:
                _LID_PN_CACHE[sender_jid] = sender_pn
                _PN_LID_CACHE[sender_pn] = sender_jid
                _save_lid_mapping(sender_jid, sender_pn)
        if chat_jid.endswith("@lid") and chat_jid not in _LID_PN_CACHE and sender_pn.endswith("@s.whatsapp.net"):
            _LID_PN_CACHE[chat_jid] = sender_pn
            _save_lid_mapping(chat_jid, sender_pn)

        # session_id 統一用 PN JID（@s.whatsapp.net），避免 lid 不穩定
        if chat_jid.endswith("@g.us"):
            normalized_chat_jid = chat_jid
        elif sender_pn.endswith("@s.whatsapp.net") and (chat_jid.endswith("@lid") or sender_jid.endswith("@lid")):
            normalized_chat_jid = sender_pn
        else:
            normalized_chat_jid = chat_jid
        extras = data.get("extras") or {}
        reaction = extras.get("reaction")
        if self._is_reaction_only(data):
            if not bool(self.config.get("inbound_reaction_events", False)):
                logger.debug("忽略表情回应消息: message_id=%s", data.get("messageId"))
                return None
            text = self._reaction_message_text(reaction)
        else:
            text = str(data.get("text") or "")
            if text and bool(self.config.get("parse_inbound_formatting", True)):
                text = format_markdown_from_whatsapp(text)
        is_group = chat_jid.endswith("@g.us")
        group_id = chat_jid.split("@", 1)[0] if is_group else None

        user_id = ""
        if sender_pn and sender_pn.endswith("@s.whatsapp.net"):
            user_id = self._numeric_whatsapp_id(sender_pn)
        elif sender_phone:
            user_id = "".join(ch for ch in sender_phone if ch.isdigit())
        if not user_id:
            user_id = self._numeric_whatsapp_id(sender_jid)

        abm = AstrBotMessage()
        abm.type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        abm.group_id = group_id
        abm.message_str = text
        abm.sender = MessageMember(
            user_id=user_id,
            nickname=str(data.get("senderName") or sender_pn or sender_jid),
        )
        abm.message = self._message_chain(data, text)
        abm.raw_message = data
        abm.self_id = str(data.get("selfJid") or "whatsapp")
        abm.session_id = normalized_chat_jid
        abm.message_id = str(data.get("messageId") or "")
        logger.debug(
            "Converted WhatsApp message: type=%s session=%s sender=%s message_id=%s chain=%s",
            abm.type,
            abm.session_id,
            sender_jid,
            abm.message_id,
            [component.__class__.__name__ for component in abm.message],
        )
        return abm

    async def handle_msg(self, message: AstrBotMessage):
        raw = message.raw_message or {}
        if self.config.get("ignore_self_messages", False):
            sender_jid = str(raw.get("senderJid") or "")
            self_id = str(raw.get("selfJid") or "")
            self_lid = str(raw.get("selfLid") or "")
            if sender_jid and self._is_self_mention(sender_jid, self_id, self_lid):
                logger.info("忽略自身消息: sender=%s", sender_jid)
                return
        is_private = message.type == MessageType.FRIEND_MESSAGE
        is_self_mentioned = self._message_mentions_self(raw)
        is_reply_to_self = self._reply_targets_self(raw)
        is_reaction_only = self._is_reaction_only(raw)
        is_command = self._message_matches_command(message.message_str or "")
        prefix = str(self.config.get("command_prefix") or "/")
        has_prefix = (message.message_str or "").strip().startswith(prefix)
        event = WhatsAppMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self.client,
            target_jid=str(raw.get("chatJid") or message.session_id),
            quoted_message_id=str(raw.get("messageId") or "") or None,
            text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
            media_caption_mode=str(self.config.get("media_caption_mode") or "separate"),
            link_preview_single_url=bool(self.config.get("link_preview_single_url", True)),
            typing_indicator=bool(self.config.get("typing_indicator", True)),
            remove_ack_after_reply=bool(self.config.get("remove_ack_after_reply", False)),
        )
        sender_allowed = self._is_sender_allowed(raw, is_private)
        reaction_level = str(self.config.get("reaction_level", "ack") or "ack")
        pre_ack_enabled = bool(self.config.get("pre_ack_emoji", True))
        pre_ack_private = bool(self.config.get("pre_ack_private", True))
        # 獨立判斷群消息是否應該喚醒機器人（與預回復表情分開）
        is_group_wake = is_private or is_self_mentioned or is_reply_to_self or is_command or has_prefix
        if not is_group_wake:
            logger.debug("忽略非喚醒群消息: session=%s msg=%s text=%s",
                          message.session_id, message.message_id, (message.message_str or "")[:40])
            return
        if is_reaction_only:
            logger.debug("忽略表情回應事件: session=%s msg=%s",
                          message.session_id, message.message_id)
            return
        if not sender_allowed:
            logger.info("忽略未授權發送者: session=%s sender=%s",
                          message.session_id, raw.get("senderJid"))
            return
        if pre_ack_enabled and reaction_level != "off" and not is_reaction_only:
            if is_private:
                should_ack = pre_ack_private
            else:
                group_mode = self._group_pre_ack_mode()
                should_ack = group_mode == "always" or (
                    group_mode == "mentions" and (is_self_mentioned or is_reply_to_self or is_command)
                )
            if should_ack:
                if not is_command:
                    event.is_at_or_wake_command = True
                    event.is_wake = True
                await self._pre_ack(event, reaction_level)
        if is_command:
            event.is_at_or_wake_command = True
        logger.info(
            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s self_mentioned=%s reply_to_self=%s is_private=%s is_command=%s",
            message.session_id,
            getattr(message.sender, "user_id", None),
            raw.get("senderJid"),
            message.message_id,
            len(message.message_str or ""),
            is_self_mentioned,
            is_reply_to_self,
            is_private,
            is_command,
        )
        self.commit_event(event)

    def _message_chain(self, data: dict[str, Any], text: str) -> list[Any]:
        chain: list[Any] = []
        self_id = str(data.get("selfJid") or "whatsapp")
        self_lid = str(data.get("selfLid") or "")
        quoted_data = data.get("quoted")
        if quoted_data:
            quoted_chain = self._quoted_media_chain(quoted_data)
            quoted_text = str(quoted_data.get("text") or "")
            quoted_sender = str(quoted_data.get("participant") or "")
            quoted_sender_id = self._numeric_whatsapp_id(quoted_sender) if quoted_sender else "0"
            chain.append(Reply(
                id=str(quoted_data.get("stanzaId") or ""),
                chain=quoted_chain if quoted_chain else None,
                sender_id=quoted_sender_id,
                sender_nickname=quoted_sender,
                time=0,
                message_str=quoted_text,
                text=quoted_text,
                qq=quoted_sender_id,
            ))
        for mentioned_jid in data.get("mentionedJids") or []:
            mentioned = str(mentioned_jid or "")
            if not mentioned:
                continue
            at_id = self_id if self._is_self_mention(mentioned, self_id, self_lid) else mentioned
            logger.info(
                "WhatsApp mention mapped: mentioned=%s at_id=%s self_id=%s self_lid=%s",
                mentioned,
                at_id,
                self_id,
                self_lid,
            )
            chain.append(At(qq=at_id, name=mentioned))
        extras = data.get("extras") or {}
        reaction = extras.get("reaction")
        display_text = text or (self._reaction_message_text(reaction) if reaction else "")
        if display_text:
            chain.append(Plain(text=display_text))
        location = extras.get("location")
        if location:
            chain.append(Location(
                lat=float(location.get("latitude") or 0),
                lon=float(location.get("longitude") or 0),
                title=str(location.get("name") or ""),
                content=str(location.get("address") or ""),
            ))
        contact = extras.get("contact")
        if contact:
            name = str(contact.get("displayName") or "")
            vcard = str(contact.get("vcard") or "")
            phones = [m.group(1).strip() for m in re.finditer(r"TEL[^:]*:([^\r\n]+)", vcard, re.IGNORECASE) if m.group(1).strip()]
            label = name or (phones[0] if phones else "Contact")
            detail = f"{label}: {', '.join(phones)}" if phones else label
            chain.append(Plain(text=f"📇 {detail}"))
        button_response = extras.get("buttonResponse")
        if button_response:
            selected = str(button_response.get("selectedDisplayText") or button_response.get("selectedButtonId") or "")
            if selected:
                chain.append(Plain(text=f"[Button] {selected}"))
        list_response = extras.get("listResponse")
        if list_response:
            title = str(list_response.get("title") or "")
            row_id = str(list_response.get("singleSelectReply") or "")
            if title or row_id:
                chain.append(Plain(text=f"[List] {title or row_id}"))
        poll = extras.get("poll")
        if poll:
            name = str(poll.get("name") or "Poll")
            options = poll.get("options") or []
            selectable = int(poll.get("selectableCount") or 0)
            option_text = ", ".join(str(item) for item in options if str(item))
            multi = "多選" if selectable > 1 else ("單選" if selectable == 1 else "")
            detail = f"{name}: {option_text}" if option_text else name
            if multi:
                detail = f"[Poll/{multi}] {detail}"
            else:
                detail = f"[Poll] {detail}"
            chain.append(Plain(text=detail))
        for media in data.get("media") or []:
            media_type = media.get("type")
            path = media.get("path") or media.get("url") or ""
            if not path:
                continue
            if media_type == "image":
                chain.append(Image(file=path))
            elif media_type == "sticker":
                chain.append(Image(file=path))
                chain.append(Plain(text="[Sticker]"))
            elif media_type == "audio":
                chain.append(Record(file=path))
            elif media_type == "video":
                chain.append(Video(file=path))
            elif media_type == "document":
                chain.append(File(name=str(media.get("fileName") or Path(path).name), file=path))
            else:
                chain.append(Plain(text=f"<media:{media_type or 'unknown'}> {path}"))
            logger.debug(
                "WhatsApp inbound media mapped: type=%s path=%s file_name=%s mimetype=%s size=%s",
                media_type,
                path,
                media.get("fileName"),
                media.get("mimetype"),
                media.get("size"),
            )
        if not chain:
            chain.append(Plain(text=""))
        return chain

    def _quoted_media_chain(self, quoted_data: dict[str, Any]) -> list[Any]:
        chain: list[Any] = []
        quoted_text = str(quoted_data.get("text") or "")
        if quoted_text and not quoted_text.startswith("<media:"):
            chain.append(Plain(text=quoted_text))
        for media in quoted_data.get("media") or []:
            media_type = media.get("type")
            path = media.get("path") or media.get("url") or ""
            if not path:
                continue
            if media_type == "image":
                chain.append(Image(file=path))
            elif media_type == "sticker":
                chain.append(Image(file=path))
                chain.append(Plain(text="[Sticker]"))
            elif media_type == "audio":
                chain.append(Record(file=path))
            elif media_type == "video":
                chain.append(Video(file=path))
            elif media_type == "document":
                chain.append(File(name=str(media.get("fileName") or Path(path).name), file=path))
        return chain

    def _same_whatsapp_user(self, left: str, right: str) -> bool:
        return self._whatsapp_user_id(left) == self._whatsapp_user_id(right)

    def _is_self_mention(self, mentioned: str, self_id: str, self_lid: str) -> bool:
        return self._same_whatsapp_user(mentioned, self_id) or (
            bool(self_lid) and self._same_whatsapp_user(mentioned, self_lid)
        )

    def _is_sender_allowed(self, raw: dict[str, Any], is_private: bool) -> bool:
        chat_jid = str(raw.get("chatJid") or "")
        sender_jid = str(raw.get("senderJid") or "")
        sender_pn = str(raw.get("senderPn") or "")
        sender_phone = str(raw.get("senderPhone") or "")

        candidates = set()
        for v in (chat_jid, sender_jid, sender_pn, sender_phone):
            if v:
                candidates.add(v)

        def _normalize_phone(value: str) -> str:
            digits = re.sub(r"\D", "", value)
            return f"+{digits}" if digits else ""

        def _allowed_by(value: str, allow_list: list) -> bool:
            if not allow_list:
                return False
            normalized = _normalize_phone(value)
            for item in allow_list:
                item_str = str(item or "").strip()
                if item_str == "*" or _normalize_phone(item_str) == "*":
                    return True
                if _normalize_phone(item_str) == normalized:
                    return True
                if item_str == value:
                    return True
            return False

        if is_private:
            policy = self.config.get("dm_policy", "allowlist")
            if policy == "disabled":
                return False
            if policy == "open":
                return True
            allow_from = self._coerce_str_list(self.config.get("allow_from"))
            if any(_allowed_by(c, allow_from) for c in candidates):
                return True
            # lid→PN 緩存兜底：如果候選中有 lid JID，嘗試用快取的 PN 匹配
            for c in candidates:
                if c.endswith("@lid"):
                    pn = _LID_PN_CACHE.get(c)
                    if pn and _allowed_by(pn, allow_from):
                        return True
            # Gateway 已放行的 lid 用戶（lid_unresolved_allow）：信任 Gateway 判斷
            if sender_jid.endswith("@lid") and not sender_phone and not sender_pn:
                return True
            return False

        policy = self.config.get("group_policy", "disabled")
        if policy == "disabled":
            return False
        groups = self._coerce_str_list(self.config.get("groups"))
        if groups and "*" not in groups and chat_jid not in groups:
            return False
        if policy == "open":
            return True
        group_allow_from = self._coerce_str_list(self.config.get("group_allow_from"))
        if not group_allow_from:
            group_allow_from = self._coerce_str_list(self.config.get("allow_from"))
        if any(_allowed_by(c, group_allow_from) for c in candidates):
            return True
        # lid 用戶無 PN 時信任 Gateway 放行判斷
        if sender_jid.endswith("@lid") and not sender_phone and not sender_pn:
            return True
        return False

    def _message_mentions_self(self, data: dict[str, Any]) -> bool:
        self_id = str(data.get("selfJid") or "")
        self_lid = str(data.get("selfLid") or "")
        return any(
            self._is_self_mention(str(mentioned or ""), self_id, self_lid)
            for mentioned in data.get("mentionedJids") or []
        )

    def _reply_targets_self(self, data: dict[str, Any]) -> bool:
        quoted = data.get("quoted")
        if not quoted:
            return False
        participant = str(quoted.get("participant") or "")
        if not participant:
            return False
        self_id = str(data.get("selfJid") or "")
        self_lid = str(data.get("selfLid") or "")
        return self._is_self_mention(participant, self_id, self_lid)

    async def _pre_ack(self, event: WhatsAppMessageEvent, reaction_level: str = "ack") -> None:
        emoji_str = str(self.config.get("pre_ack_emojis", "✍️") or "✍️")
        emojis = [e.strip() for e in re.split(r'[,，\s]+', emoji_str) if e.strip()]
        if not emojis:
            return
        emoji = random.choice(emojis)
        try:
            logger.info("WhatsApp 预回复表情: target=%s emoji=%s level=%s", event.target_jid, emoji, reaction_level)
            await event.react(emoji)
            event._pre_acked = True
        except Exception as exc:
            logger.warning("WhatsApp 预回复表情发送失败: target=%s error=%s", event.target_jid, exc)

    def _whatsapp_user_id(self, jid: str) -> str:
        return str(jid or "").split(":", 1)[0].split("@", 1)[0]

    def _numeric_whatsapp_id(self, jid: str) -> str:
        digits = "".join(ch for ch in self._whatsapp_user_id(jid) if ch.isdigit())
        return digits or self._whatsapp_user_id(jid)

    async def _send_presence(self, target: str, state: str) -> None:
        if state == "composing" and not self.config.get("typing_indicator", True):
            return
        try:
            await self.client.send_presence(target, state)
        except Exception as exc:
            logger.debug("WhatsApp 在线状态更新失败: target=%s state=%s error=%s", target, state, exc)

    def _is_reaction_only(self, data: dict[str, Any]) -> bool:
        extras = data.get("extras") or {}
        if not extras.get("reaction"):
            return False
        if data.get("media"):
            return False
        text = str(data.get("text") or "").strip()
        if text and not text.startswith("<media:"):
            return False
        for key in ("location", "contact", "buttonResponse", "listResponse"):
            if extras.get(key):
                return False
        return True

    def _reaction_message_text(self, reaction: dict[str, Any] | None) -> str:
        if not reaction:
            return "[Reaction]"
        emoji = str(reaction.get("text") or "").strip() or "?"
        target_key = reaction.get("key") or {}
        target_id = str(target_key.get("id") or "").strip()
        if target_id:
            return f"[Reaction] {emoji} → {target_id}"
        return f"[Reaction] {emoji}"

    def _gateway_config(self) -> dict[str, Any]:
        return {
            "dmPolicy": self.config.get("dm_policy", "allowlist"),
            "allowFrom": self.config.get("allow_from") or [],
            "groupPolicy": self.config.get("group_policy", "disabled"),
            "groupAllowFrom": self.config.get("group_allow_from") or [],
            "groups": self.config.get("groups") or [],
            "sendReadReceipts": bool(self.config.get("send_read_receipts", True)),
            "markOnline": bool(self.config.get("mark_online", False)),
            "mediaMaxMb": int(float(self.config.get("media_max_mb", GATEWAY_MEDIA_MAX_MB) or GATEWAY_MEDIA_MAX_MB)),
            "mediaMessageMaxMb": GATEWAY_MEDIA_MESSAGE_MAX_MB,
            "documentMaxMb": GATEWAY_DOCUMENT_MAX_MB,
            "audioMaxMb": GATEWAY_AUDIO_MAX_MB,
            "mediaAlbumDebounceMs": max(
                0,
                int(float(self.config.get("media_album_debounce_seconds") or 0) * 1000),
            ),
            "ignoreSelfMessages": bool(self.config.get("ignore_self_messages", False)),
        }

    def _auth_dir(self) -> Path:
        configured = str(self.config.get("auth_dir") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return self._data_dir() / "whatsapp-auth"

    _migrated = False

    def _data_dir(self) -> Path:
        if not self.__class__._migrated:
            self._migrate_old_data()
            self.__class__._migrated = True
        return self._resolve_data_base() / PLUGIN_NAME

    @staticmethod
    def _resolve_data_base() -> Path:
        if _get_astrbot_data_path:
            return Path(_get_astrbot_data_path()) / "plugin_data"
        return Path.cwd() / "data"

    def _migrate_old_data(self) -> None:
        old_root = Path.cwd() / "data" / _OLD_DATA_DIR_NAME
        if not old_root.is_dir():
            return
        new_root = self._resolve_data_base() / PLUGIN_NAME
        if new_root.is_dir():
            return
        try:
            import shutil
            shutil.copytree(str(old_root), str(new_root), symlinks=False)
            logger.info("已迁移插件数据: %s → %s", old_root, new_root)
        except Exception as exc:
            logger.warning("迁移旧插件数据失败: %s → %s: %s", old_root, new_root, exc)

    def _merged_config(self, platform_config: dict[str, Any]) -> dict[str, Any]:
        plugin_config = self._normalize_config(self._load_plugin_config())
        platform_config = self._normalize_config(platform_config)
        merged = {**DEFAULT_CONFIG, **platform_config, **plugin_config}
        logger.debug(
            "WhatsApp config merged: platform_keys=%s plugin_overrides=%s effective=%s",
            sorted(platform_config.keys()),
            sorted(plugin_config.keys()),
            self._safe_config(merged),
        )
        return merged

    def _normalize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in config.items():
            normalized_key = CONFIG_KEY_ALIASES.get(key, key)
            normalized[normalized_key] = self._normalize_config_value(normalized_key, value)
        return normalized

    def _normalize_config_value(self, key: str, value: Any) -> Any:
        if key in {"allow_from", "group_allow_from", "groups"}:
            return self._coerce_str_list(value)
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
        return value

    def _coerce_str_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, (tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            if "\n" in text or "\r" in text or "," in text or "，" in text or ";" in text:
                parts = re.split(r"[\r\n,，;]+", text)
                return [part.strip() for part in parts if part.strip()]
            return [text]
        return [str(value).strip()] if str(value).strip() else []

    def _group_pre_ack_mode(self) -> str:
        value = self.config.get("pre_ack_public", "mentions")
        if isinstance(value, str) and value in {"mentions", "always", "never"}:
            return value
        return "always" if bool(value) else "never"

    def _load_plugin_config(self) -> dict[str, Any]:
        config_path = self._data_dir() / "config.json"
        try:
            with config_path.open("r", encoding="utf-8-sig") as fp:
                data = json.load(fp)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("加载 WhatsApp 插件配置失败: %s: %s", config_path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    async def _wait_for_gateway(self) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 61):
            try:
                health = await self.client.health()
                logger.info("WhatsApp Gateway: 连接正常 (第%s次尝试)", attempt)
                return
            except Exception as exc:
                last_error = exc
                if attempt in {1, 5, 15, 30, 60}:
                    logger.debug("等待 WhatsApp Gateway 健康检查第 %s 次失败: %s", attempt, exc)
                await asyncio.sleep(1)
        raise WhatsAppGatewayError(f"WhatsApp Gateway did not become healthy: {last_error}")

    async def _ensure_gateway_running(self) -> None:
        if not self.config.get("auto_start_gateway", True):
            return
        # 確保 Gateway 進程健康且配置已同步
        needs_restart = False
        try:
            health = await self.client.health()
            if not health.get("configured", False):
                logger.info("WhatsApp Gateway 已就绪但未配置，正在下发配置")
                configured = await self.client.configure(self._gateway_config())
                logger.info("WhatsApp Gateway 配置已完成")
                return
        except Exception:
            needs_restart = True
        if not needs_restart:
            if self.gateway_process and self.gateway_process.process:
                if self.gateway_process.process.returncode is None:
                    return
        logger.info("事件流中断，正在重启 WhatsApp Gateway: %s", self._base_url)
        if self.gateway_process:
            await self.gateway_process.stop()
        self.gateway_process = self._create_gateway_process()
        await self.gateway_process.start()
        await self._wait_for_gateway()
        configured = await self.client.configure(self._gateway_config())
        logger.info("WhatsApp Gateway: 已重新配置（重启后）")
        self._mark_running()

    async def _connect_gateway(self) -> None:
        self._reconnect_event.clear()
        await self.client.start()
        self.client.update_base_url(self._base_url)
        if self.config.get("auto_start_gateway", True):
            force_restart = getattr(self, '_force_gateway_restart', False)
            self._force_gateway_restart = False
            if not force_restart:
                try:
                    health = await self.client.health()
                    logger.debug("WhatsApp Gateway 已就绪")
                except Exception:
                    force_restart = True
            if force_restart:
                logger.info("正在启动 WhatsApp Gateway: %s", self._base_url)
                if self.gateway_process:
                    await self.gateway_process.stop()
                self.gateway_process = self._create_gateway_process()
                await self.gateway_process.start()
        else:
            logger.info("WhatsApp 平台自动启动已关闭，预期 Gateway 运行于 %s", self._base_url)

        await self._wait_for_gateway()
        configured = await self.client.configure(self._gateway_config())
        logger.info("WhatsApp Gateway 配置: 私聊策略=%s 群聊策略=%s 已读回执=%s",
                     configured.get("config", {}).get("dmPolicy"),
                     configured.get("config", {}).get("groupPolicy"),
                     configured.get("config", {}).get("sendReadReceipts"))
        try:
            status = await self.client.status()
            logger.info("WhatsApp Gateway: 状态=%s 就绪=%s%s",
                         status.get("status", "?"),
                         bool(status.get("ready")),
                         f" self={status['selfJid']}" if status.get("selfJid") else "")
        except Exception as exc:
            logger.warning("获取 WhatsApp Gateway 状态失败: %s", exc)
        logger.info("WhatsApp 适配器已连接: %s", self._base_url)
        self._mark_running()
        await self._restart_health_monitor()
        self._refresh_registered_commands()

    def _create_gateway_process(self) -> GatewayProcess:
        return GatewayProcess(
            node_executable=str(self.config["node_executable"]),
            script_path=PLUGIN_DIR / "gateway" / "whatsapp-gateway.mjs",
            host=str(self.config["gateway_host"]),
            port=int(self.config["gateway_port"]),
            auth_dir=self._auth_dir(),
            log_level=str(self.config["log_level"]),
            data_dir=self._data_dir(),
        )

    async def _shutdown_gateway_transport(self) -> None:
        await self._stop_gateway_and_client()

    async def _stop_gateway_and_client(self) -> None:
        await self._stop_health_monitor()
        await self.client.close()
        if self.gateway_process:
            await self.gateway_process.stop()
            self.gateway_process = None

    async def _stop_health_monitor(self) -> None:
        if not self._health_task:
            return
        self._health_task.cancel()
        try:
            await self._health_task
        except (asyncio.CancelledError, Exception):
            pass
        self._health_task = None

    def _gateway_connection_signature(self) -> tuple[Any, ...]:
        return (
            self._base_url,
            str(self.config.get("id") or "whatsapp"),
            str(self.config.get("node_executable") or "node"),
            str(self._auth_dir()),
            str(self.config.get("log_level") or "info"),
            bool(self.config.get("auto_start_gateway", True)),
        )

    def _runtime_owner_key(self) -> str:
        return "|".join(str(part) for part in self._gateway_connection_signature())

    async def _claim_runtime_owner(self) -> None:
        registry = _runtime_owner_registry()
        key = self._runtime_owner_key()
        existing_ref = registry.get(key)
        existing = existing_ref() if existing_ref else None
        if existing is self:
            return
        if existing is not None:
            logger.warning(
                "Found stale WhatsApp adapter runtime owner, terminating previous instance: key=%s old_id=%s new_id=%s",
                key,
                getattr(existing.meta(), "id", None) if hasattr(existing, "meta") else None,
                getattr(self.meta(), "id", None),
            )
            try:
                await existing.terminate()
            except Exception as exc:
                logger.warning("终止旧 WhatsApp 适配器运行时所有权失败: key=%s error=%s", key, exc)
        registry[key] = weakref.ref(self)

    def _release_runtime_owner(self) -> None:
        registry = _runtime_owner_registry()
        key = self._runtime_owner_key()
        existing_ref = registry.get(key)
        existing = existing_ref() if existing_ref else None
        if existing is self or existing is None:
            registry.pop(key, None)

    def _start_health_monitor(self) -> None:
        interval = int(self.config.get("gateway_health_check_interval") or 0)
        if interval <= 0 or self._health_task:
            return
        self._health_task = asyncio.create_task(self._health_monitor_loop(interval))

    async def _restart_health_monitor(self) -> None:
        await self._stop_health_monitor()
        self._start_health_monitor()

    async def _health_monitor_loop(self, interval: int) -> None:
        while not self._stopped.is_set():
            await asyncio.sleep(interval)
            if self._stopped.is_set():
                return
            try:
                status = await self.client.status()
                ready = bool(status.get("ready"))
                ok = bool(status.get("ok", True)) and ready
                if ok:
                    if not self._gateway_healthy:
                        logger.info("WhatsApp Gateway: 已恢复健康")
                    self._gateway_healthy = True
                    self._mark_running()
                else:
                    self._gateway_healthy = False
                    self._record_gateway_error(f"WhatsApp Gateway not ready: {self._safe_status(status)}")
                    await self._safe_restart_gateway()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._gateway_healthy = False
                self._record_gateway_error(f"WhatsApp Gateway health check failed: {exc}", exc_info=exc)
                await self._safe_restart_gateway()

    async def _safe_restart_gateway(self) -> None:
        if getattr(self, '_restarting', False):
            return
        self._restarting = True
        try:
            await self._ensure_gateway_running()
        except Exception as restart_exc:
            self._record_gateway_error(f"WhatsApp Gateway health restart failed: {restart_exc}", exc_info=restart_exc)
        finally:
            self._restarting = False

    def _mark_running(self) -> None:
        self._status = PlatformStatus.RUNNING
        self._gateway_healthy = True
        self.clear_errors()

    def _record_gateway_error(self, message: str, exc_info: BaseException | None = None) -> None:
        logger.warning(message)
        try:
            self.record_error(message, traceback.format_exc() if exc_info else "")
        except Exception:
            self._status = PlatformStatus.ERROR

    def _safe_config(self, config: dict[str, Any]) -> dict[str, Any]:
        safe = dict(config)
        for key in ("allow_from", "group_allow_from", "groups"):
            if key in safe:
                safe[key] = self._count_label(safe.get(key))
        return safe

    def _safe_status(self, status: dict[str, Any]) -> dict[str, Any]:
        safe = dict(status)
        if "config" in safe and isinstance(safe["config"], dict):
            config = dict(safe["config"])
            for key in ("allowFrom", "groupAllowFrom", "groups"):
                if key in config:
                    config[key] = self._count_label(config.get(key))
            safe["config"] = config
        if safe.get("qr"):
            safe["qr"] = "<hidden>"
        if safe.get("qrDataUrl"):
            safe["qrDataUrl"] = "<hidden>"
        return safe

    def _log_gateway_event(self, event: dict[str, Any]) -> None:
        ev_type = event.get("type", "?")
        if ev_type != "status":
            jid = event.get("chatJid") or event.get("senderJid") or ""
            msg_id = event.get("messageId") or ""
            logger.info("WhatsApp Gateway 事件: 类型=%s%s%s",
                         ev_type,
                         f" chat={jid}" if jid else "",
                         f" msg={msg_id}" if msg_id else "")
            return

        current = (event.get("status"), event.get("ready"), event.get("selfJid"))
        if current == self._last_gateway_status_log:
            logger.debug("WhatsApp Gateway 事件: 状态=%s (重复)", event.get("status"))
            return
        self._last_gateway_status_log = current
        logger.info("WhatsApp Gateway: %s%s",
                     event.get("status", "?"),
                     f" (self={event['selfJid']})" if event.get("selfJid") else "")

    def _count_label(self, value: Any) -> str:
        if isinstance(value, list):
            return f"<{len(value)} entries>"
        return "<0 entries>" if value in (None, "") else "<1 entry>"

    def _refresh_registered_commands(self) -> None:
        if not bool(self.config.get("register_commands", True)):
            self._registered_commands = []
            return
        self._registered_commands = collect_registered_commands()
        if self._registered_commands:
            logger.info(
                "WhatsApp registered slash commands: count=%s prefix=%s",
                len(self._registered_commands),
                self.config.get("command_prefix", "/"),
            )

    def _message_matches_command(self, text: str) -> bool:
        if not bool(self.config.get("register_commands", True)):
            return False
        prefix = str(self.config.get("command_prefix") or "/")
        return message_matches_command(text, self._registered_commands, prefix=prefix)

def get_active_whatsapp_adapters() -> list["WhatsAppPlatformAdapter"]:
    return list(_ACTIVE_ADAPTERS)


def patch_platform_manager_hot_reload() -> None:
    try:
        from astrbot.core.platform.manager import PlatformManager
    except Exception as exc:
        logger.debug("WhatsApp 热重载补丁跳过（平台管理器不可用）: %s", exc)
        return
    original_reload = getattr(PlatformManager, "_whatsapp_original_reload", None)
    if original_reload is not None:
        return
    PlatformManager._whatsapp_original_reload = PlatformManager.reload

    async def reload(self, platform_config: dict) -> None:
        platform_id = platform_config.get("id")
        info = self._inst_map.get(platform_id) if platform_id else None
        inst = info.get("inst") if info else None
        if (
            platform_config.get("enable")
            and platform_config.get("type") == "whatsapp"
            and inst is not None
            and hasattr(inst, "reload")
        ):
            try:
                await inst.reload(platform_config)
                for index, platform in enumerate(self.platforms_config):
                    if platform.get("id") == platform_id:
                        self.platforms_config[index] = platform_config
                        break
                logger.info("WhatsApp 平台已原地热重载: id=%s", platform_id)
                return
            except Exception as exc:
                logger.warning(
                    "WhatsApp 原地热重载失败，回退到完整平台重载: id=%s error=%s",
                    platform_id,
                    exc,
                )
        await PlatformManager._whatsapp_original_reload(self, platform_config)

    PlatformManager.reload = reload
    logger.info("WhatsApp 平台配置热重载已启用")
