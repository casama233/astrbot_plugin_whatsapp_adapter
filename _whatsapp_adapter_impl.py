from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import traceback
import weakref
from collections.abc import Mapping
from datetime import datetime, timezone
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
from astrbot.api.message_components import AtAll, File, Image, Location, Plain, Record, Reply, Video
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
from .whatsapp_config_policy import (
    DM_POLICIES,
    GROUP_POLICIES,
    LEGACY_GATEWAY_DEFAULTS,
    LOG_LEVELS,
    MEDIA_CAPTION_MODES,
    PRE_ACK_PUBLIC_MODES,
    extract_legacy_behavior_overrides,
    extract_legacy_command_prefix,
    extract_plugin_defaults,
    get_runtime_plugin_defaults,
    get_runtime_wake_prefixes,
    merge_runtime_config,
    normalize_config_enum,
    normalize_pre_ack_public,
)
from .whatsapp_helpers import (
    QuoteState,
    flush_pending_text,
    format_markdown_from_whatsapp,
    process_message_chain,
)
from .whatsapp_identity import (
    IdentityMappingCache,
    active_auth_session_dir as _active_auth_session_dir,
    base_lid_jid as _normalize_lid_jid,
    base_pn_jid as _normalize_pn_jid,
    build_umo_session_id as _build_umo_session_id,
    delivery_jid_from_session_id as _delivery_jid_from_session_id,
    identity_user as _identity_user,
    is_lid_jid as _is_lid_jid,
    is_pn_jid as _is_pn_jid,
    load_lid_mappings as _load_identity_mappings,
    normalize_group_session_id as _normalize_group_session_id,
    normalize_user_jid as _normalize_user_jid,
    phone_from_identity as _phone_from_identity,
    public_numeric_id as _public_numeric_id,
    same_whatsapp_identity as _same_whatsapp_identity,
    save_identity_projections as _save_identity_projections,
    save_lid_mapping as _save_identity_mapping,
)


PLUGIN_DIR = Path(__file__).resolve().parent
_ACTIVE_ADAPTERS: weakref.WeakSet["WhatsAppPlatformAdapter"] = weakref.WeakSet()
_RUNTIME_OWNER_REGISTRY: dict[str, weakref.ReferenceType["WhatsAppPlatformAdapter"]] = {}
_LEGACY_IDENTITY_CACHE = IdentityMappingCache()
# Kept as compatibility aliases for integrations importing these names. Runtime
# adapters use their own ``IdentityMappingCache`` and never share these maps.
_LID_PN_CACHE = _LEGACY_IDENTITY_CACHE.lid_to_pn
_PN_LID_CACHE = _LEGACY_IDENTITY_CACHE.pn_to_lid


class AttrDict(dict):
    """A recursively attribute-accessible mapping compatible with OneBot Event."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.update(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # aiocqhttp.Event returns None for event fields absent from a payload.
        return self.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = _as_attr_dict(value)

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, _as_attr_dict(value))

    def update(self, *args: Any, **kwargs: Any) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]


def _as_attr_dict(value: Any) -> Any:
    if isinstance(value, AttrDict):
        return value
    if isinstance(value, Mapping):
        converted = AttrDict()
        for key, item in value.items():
            converted[key] = item
        return converted
    if isinstance(value, list):
        return [_as_attr_dict(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_as_attr_dict(item) for item in value)
    return value


def _base_pn_jid(pn_jid: str) -> str:
    return _normalize_pn_jid(pn_jid)


def _base_lid_jid(lid_jid: str) -> str:
    return _normalize_lid_jid(lid_jid)


def _cache_lid_mapping(
    lid_jid: str,
    pn_jid: str,
    cache: IdentityMappingCache | None = None,
) -> None:
    (cache if cache is not None else _LEGACY_IDENTITY_CACHE).remember(lid_jid, pn_jid)


def _lid_mapping_path(auth_dir: Path, lid_jid: str) -> Path | None:
    """lid JID 對應的磁碟映射文件路徑（lid-mapping-{lid数字}_reverse.json）。"""
    lid_num = _identity_user(lid_jid)
    if not lid_num.isdigit():
        return None
    return _active_auth_session_dir(auth_dir) / f"lid-mapping-{lid_num}_reverse.json"


def _load_lid_mappings(
    auth_dir: Path,
    cache: IdentityMappingCache | None = None,
) -> None:
    """從 Gateway auth 目錄加載所有 lid-mapping-*_reverse.json 到緩存。"""
    if not auth_dir:
        return
    target = cache if cache is not None else _LEGACY_IDENTITY_CACHE
    try:
        loaded = _load_identity_mappings(auth_dir, target)
        if loaded:
            logger.info("已加載 %d 條 lid→PN 映射到緩存", loaded)
    except Exception as exc:
        logger.debug("加載 lid 映射失敗: %s", exc)


def _save_lid_mapping(
    auth_dir: Path,
    lid_jid: str,
    pn_jid: str,
    cache: IdentityMappingCache | None = None,
) -> None:
    """持久化 lid→PN 映射到 Gateway auth 目錄。"""
    if not auth_dir:
        return
    try:
        _save_identity_mapping(auth_dir, lid_jid, pn_jid, cache=cache)
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

RUNTIME_DEFAULT_CONFIG: dict[str, Any] = {
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
PERSISTED_PLATFORM_KEYS = set(UI_CONFIG_KEYS) | {"type", "enable", "id"}

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
    "预回应表情": "pre_ack_emojis",
    "私聊启用手动回应": "pre_ack_private",
    "群组回应模式": "pre_ack_public",
    "解析入站格式": "parse_inbound_formatting",
    "私聊预回应": "pre_ack_private",
    "群聊预回应": "pre_ack_public",
    "预回应表情": "pre_ack_emojis",
    "启用预回应表情": "pre_ack_emoji",
    "回复完成表情": "pre_ack_done_emoji",
    "媒体上传大小限制(MB)": "media_max_mb",
    "ack_reaction_emoji": "pre_ack_emojis",
    "ack_reaction_direct": "pre_ack_private",
    "ack_reaction_group": "pre_ack_public",
}

DEPRECATED_CONFIG_KEYS = {
    "reaction_level",
    "remove_ack_after_reply",
    "inbound_reaction_events",
    "ack_reaction_emoji",
    "ack_reaction_direct",
    "ack_reaction_group",
    "私聊启用手动回应",
    "群组回应模式",
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
        "options": list(LOG_LEVELS),
        "hint": "可选：silent、fatal、error、warn、info、debug、trace。",
    },
    "dm_policy": {
        "description": "私聊接收策略",
        "type": "string",
        "group": "permissions",
        "options": list(DM_POLICIES),
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
        "options": list(GROUP_POLICIES),
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
        "hint": "启用后，机器人发送回复前向 WhatsApp 显示 typing 状态，发送完停止输入状态。",
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
        "options": list(MEDIA_CAPTION_MODES),
        "hint": "separate=文字与媒体分开发送；caption=紧邻媒体前的文字作为该媒体描述。仅影响普通富媒体消息链，流式回复中的媒体仍分开发送。",
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
    "pre_ack_emoji": {
        "description": "启用预回应表情",
        "type": "bool",
        "group": "ack",
        "hint": "启用后，bot 收到消息时通过 WhatsApp emoji reaction 发出一条预回应。",
    },
    "pre_ack_private": {
        "description": "私聊预回应",
        "type": "bool",
        "group": "ack",
        "hint": "启用后，私聊收到消息时自动触发预回应表情。",
    },
    "pre_ack_public": {
        "description": "群聊预回应模式",
        "type": "string",
        "group": "ack",
        "options": list(PRE_ACK_PUBLIC_MODES),
        "hint": "always=始终触发预回应；mentions=仅被 @ 或回复时触发；never=不触发预回应。",
    },
    "pre_ack_emojis": {
        "description": "预回应表情",
        "type": "string",
        "group": "ack",
        "hint": "收到消息时发送的 WhatsApp reaction。默认 👀。",
    },
    "pre_ack_done_emoji": {
        "description": "回复完成表情",
        "type": "string",
        "group": "ack",
        "hint": "已发送预回应时，机器人回复完成后替换/发送的完成 reaction。留空使用 ✅。",
    },
    "media_max_mb": {
        "description": "媒体上传大小限制 (MB)",
        "type": "int",
        "group": "messaging",
        "hint": "上传到 WhatsApp Gateway 的单个媒体文件大小上限（MB）。预设 50。",
    },
    "apply_ephemeral": {
        "description": "应用聊天室的消失讯息设定",
        "type": "bool",
        "group": "messaging",
        "hint": "开启后，发送消息时会带入聊天室的消失讯息计时器。关闭（默认）可避免 Baileys 触发的「此訊息不會自動刪除 / 傳送者可能正在使用版本較舊的 WhatsApp」警告。",
    },
    "streaming_edit_throttle": {
        "description": "流式编辑间隔（秒）",
        "type": "float",
        "group": "messaging",
        "hint": "流式回复时每次编辑消息的最小间隔（秒）。过小可能导致 WhatsApp 风控。",
    },
    "gateway_health_check_interval": {
        "description": "Gateway 健康检查间隔",
        "type": "int",
        "group": "connection",
        "hint": "定期检查 Gateway 状态的间隔秒数。设为 0 关闭健康检查。",
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
            "hint": "启用后，机器人发送回复前向 WhatsApp 显示 typing 状态，发送完停止输入状态。",
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
            "hint": "separate=文字与媒体分开发送；caption=紧邻媒体前的文字作为该媒体描述。仅影响普通富媒体消息链，流式回复中的媒体仍分开发送。",
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
        "pre_ack_private": {
            "description": "私聊预回应",
            "hint": "启用后，私聊收到消息时自动触发预回应表情。",
        },
        "pre_ack_public": {
            "description": "群聊预回应模式",
            "hint": "always=始终触发预回应；mentions=仅被 @ 或回复时触发；never=不触发预回应。",
        },
        "pre_ack_emojis": {
            "description": "预回应表情",
            "hint": "收到消息时发送的 WhatsApp reaction。默认 👀。",
        },
        "pre_ack_emoji": {
            "description": "启用预回应表情",
            "hint": "启用后，bot 收到消息时通过 WhatsApp emoji reaction 发出一条预回应。",
        },
        "pre_ack_done_emoji": {
            "description": "回复完成表情",
            "hint": "已发送预回应时，机器人回复完成后替换/发送的完成 reaction。留空使用 ✅。",
        },
        "media_max_mb": {
            "description": "媒体上传大小限制 (MB)",
            "hint": "上传到 WhatsApp Gateway 的单个媒体文件大小上限（MB）。预设 50。",
        },
        "apply_ephemeral": {
            "description": "应用聊天室的消失讯息设定",
            "hint": "开启后，外寄消息会带入聊天室的消失讯息计时器。关闭（默认）可避免 Baileys 触发的「此訊息不會自動刪除 / 傳送者可能正在使用版本較舊的 WhatsApp」警告。",
        },
        "streaming_edit_throttle": {
            "description": "流式编辑间隔（秒）",
            "hint": "流式回复时每次编辑消息的最小间隔（秒）。过小可能导致 WhatsApp 风控。",
        },
        "gateway_health_check_interval": {
            "description": "Gateway 健康检查间隔",
            "hint": "定期检查 Gateway 状态的间隔秒数。设为 0 关闭健康检查。",
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
            "hint": "Shows a composing presence before replying and stops the typing state after the reply is sent.",
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
            "hint": "separate sends text and media separately; caption attaches immediately preceding text to ordinary rich-media messages. Streaming media remains separate.",
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
        "pre_ack_private": {
            "description": "DM pre-ack",
            "hint": "When enabled, private messages auto-trigger a pre-ack emoji reaction.",
        },
        "pre_ack_public": {
            "description": "Group pre-ack mode",
            "hint": "always=always react; mentions=only on @mentions or replies; never=no pre-ack.",
        },
        "pre_ack_emojis": {
            "description": "Pre-ack emoji",
            "hint": "WhatsApp reaction sent when a message is received. Default 👀.",
        },
        "pre_ack_emoji": {
            "description": "Enable pre-ack emoji",
            "hint": "When enabled, the bot sends a WhatsApp emoji reaction for each message received.",
        },
        "pre_ack_done_emoji": {
            "description": "Reply-complete emoji",
            "hint": "Completion reaction sent after the bot replies when pre-ack was used. Empty uses ✅.",
        },
        "media_max_mb": {
            "description": "Media upload size limit (MB)",
            "hint": "Maximum size per media file uploaded to the WhatsApp Gateway (MB). Default 50.",
        },
        "apply_ephemeral": {
            "description": "Apply chat disappearing-message timer",
            "hint": "When enabled, outgoing messages inherit the chat's disappearing-message timer. Off (default) avoids the Baileys-triggered 'This message will not auto-delete / The sender may be using an older version of WhatsApp' warning.",
        },
        "streaming_edit_throttle": {
            "description": "Streaming edit interval (s)",
            "hint": "Minimum interval in seconds between message edits during streaming replies. Lower values may trigger WhatsApp rate limits.",
        },
        "gateway_health_check_interval": {
            "description": "Gateway health interval",
            "hint": "Interval in seconds for periodic Gateway health checks. Set 0 to disable.",
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
            "hint": "啟用後，機器人傳送回覆前向 WhatsApp 顯示 typing 狀態，傳送完停止輸入狀態。",
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
            "hint": "separate=文字與媒體分開傳送；caption=緊鄰媒體前的文字作為該媒體描述。僅影響普通富媒體訊息鏈，流式回覆中的媒體仍分開傳送。",
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
        "pre_ack_private": {
            "description": "私聊預回應",
            "hint": "啟用後，私聊收到訊息時自動觸發預回應表情。",
        },
        "pre_ack_public": {
            "description": "群聊預回應模式",
            "hint": "always=始終觸發預回應；mentions=僅被 @ 或回覆時觸發；never=不觸發預回應。",
        },
        "pre_ack_emojis": {
            "description": "預回應表情",
            "hint": "收到訊息時傳送的 WhatsApp reaction。預設 👀。",
        },
        "pre_ack_emoji": {
            "description": "啟用預回應表情",
            "hint": "啟用後，bot 收到訊息時透過 WhatsApp emoji reaction 發出一條預回應。",
        },
        "pre_ack_done_emoji": {
            "description": "回覆完成表情",
            "hint": "已傳送預回應時，機器人回覆完成後替換/傳送的完成 reaction。留空使用 ✅。",
        },
        "media_max_mb": {
            "description": "媒體上傳大小限制 (MB)",
            "hint": "上傳到 WhatsApp Gateway 的單個媒體檔案大小上限（MB）。預設 50。",
        },
        "apply_ephemeral": {
            "description": "套用聊天室的消失訊息設定",
            "hint": "開啟後，外寄訊息會帶入聊天室的消失訊息計時器。關閉（預設）可避免 Baileys 觸發的「此訊息不會自動刪除 / 傳送者可能正在使用版本較舊的 WhatsApp」警告。",
        },
        "streaming_edit_throttle": {
            "description": "流式編輯間隔（秒）",
            "hint": "流式回覆時每次編輯訊息的最小間隔（秒）。過小可能觸發 WhatsApp 風控。",
        },
        "gateway_health_check_interval": {
            "description": "Gateway 健康檢查間隔",
            "hint": "定期檢查 Gateway 狀態的間隔秒數。設為 0 關閉健康檢查。",
        },
    },
}

CONFIG_METADATA = {
    key: value
    for key, value in ((key, CONFIG_METADATA[key]) for key in UI_CONFIG_KEYS if key in CONFIG_METADATA)
    if key in UI_CONFIG_KEYS
}
WHATSAPP_I18N_RESOURCES = {
    locale: {key: resources[key] for key in UI_CONFIG_KEYS if key in resources}
    for locale, resources in WHATSAPP_I18N_RESOURCES.items()
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
        raw_platform_config = dict(platform_config or {})
        sanitized_config = sanitize_whatsapp_platform_config(raw_platform_config)
        if isinstance(platform_config, dict):
            platform_config.clear()
            platform_config.update(sanitized_config)
        super().__init__(sanitized_config, event_queue)
        self.config = self._merged_config(sanitized_config)
        self.client = WhatsAppGatewayClient(self._base_url)
        self.gateway_process: GatewayProcess | None = None
        self._stopped = asyncio.Event()
        self._reconnect_event = asyncio.Event()
        self._health_task: asyncio.Task | None = None
        self._gateway_healthy = False
        self._restarting = False
        self._last_gateway_status_log: tuple[Any, Any, Any] | None = None
        self._quiet_next_gateway_connect = False
        self._platform_config = sanitized_config
        self._platform_settings = platform_settings or {}
        self._registered_commands: list[str] = []
        self._legacy_command_prefix = extract_legacy_command_prefix(sanitized_config)
        self._identity_cache = IdentityMappingCache()
        _ACTIVE_ADAPTERS.add(self)
        self._refresh_registered_commands()
        identity_auth_dir = self._auth_dir()
        _load_lid_mappings(identity_auth_dir, self._identity_cache)
        self._identity_session_dir = _active_auth_session_dir(identity_auth_dir)
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

    def _identity_mappings(
        self,
        *,
        refresh_session: bool = False,
    ) -> IdentityMappingCache:
        """Return this adapter/account's private PN/LID mapping cache."""
        cache = getattr(self, "_identity_cache", None)
        if cache is None:
            cache = IdentityMappingCache()
            self._identity_cache = cache
        elif not all(
            callable(getattr(cache, method, None))
            for method in (
                "project_public_id",
                "pn_for_lid",
                "lid_for_pn",
                "pn_for_public_id",
                "lid_for_public_id",
            )
        ):
            stale_cache = cache
            cache = IdentityMappingCache()
            self._identity_cache = cache
            auth_dir = self._auth_dir()
            _load_lid_mappings(auth_dir, cache)
            # Plugin hot reload replaces the adapter class in place. Instances
            # created by v0.2.39 can therefore retain that release's cache
            # object, which has no public-projection API. Preserve any aliases
            # learned since the last disk write while upgrading the cache.
            stale_mappings = getattr(stale_cache, "lid_to_pn", None)
            if isinstance(stale_mappings, Mapping):
                for lid_jid, pn_jid in sorted(
                    stale_mappings.items(),
                    key=lambda item: (str(item[0]), str(item[1])),
                ):
                    cache.remember(str(lid_jid), str(pn_jid))
            self._identity_session_dir = _active_auth_session_dir(auth_dir)
            if stale_cache is not None:
                logger.info(
                    "已将热重载遗留的 WhatsApp 身份缓存迁移到当前版本"
                )
        if refresh_session:
            previous_dir = getattr(self, "_identity_session_dir", None)
            runtime_config = getattr(self, "config", {}) or {}
            configured_dir = str(runtime_config.get("auth_dir") or "").strip()
            # Properly initialized adapters always have ``previous_dir``. The
            # explicit-config fallback keeps lightweight compatibility objects
            # useful without invoking data migration just to inspect a cache.
            if previous_dir is not None or configured_dir:
                auth_dir = self._auth_dir()
                active_dir = _active_auth_session_dir(auth_dir)
                if active_dir != previous_dir:
                    _load_lid_mappings(auth_dir, cache)
                    self._identity_session_dir = active_dir
        return cache

    def _unique_session_enabled(self) -> bool:
        platform_settings = getattr(self, "_platform_settings", {}) or {}
        return bool(platform_settings.get("unique_session", False))

    async def _resolve_lid_pn(
        self,
        lid_jid: str,
        identity_cache: IdentityMappingCache,
    ) -> str:
        """Resolve one unknown LID before it can create a second public UMO."""

        mapped = identity_cache.pn_for_lid(lid_jid)
        if mapped:
            return mapped
        try:
            resolved = await asyncio.wait_for(
                self.client.resolve_lid(lid_jid),
                timeout=4,
            )
        except Exception:
            return ""
        pn_jid = _base_pn_jid(str(resolved or ""))
        if not pn_jid:
            return ""
        _cache_lid_mapping(lid_jid, pn_jid, identity_cache)
        _save_lid_mapping(self._auth_dir(), lid_jid, pn_jid, identity_cache)
        return pn_jid

    def _project_public_user_id(
        self,
        value: str | None = None,
        *,
        lid_jid: str | None = None,
        pn_jid: str | None = None,
        cache: IdentityMappingCache | None = None,
        persist: bool = True,
    ) -> str:
        """Return this account's stable public PN/LID projection."""

        identity_cache = cache or self._identity_mappings(refresh_session=True)
        public_id = identity_cache.project_public_id(
            value,
            lid_jid=lid_jid,
            pn_jid=pn_jid,
        )
        if persist:
            self._persist_identity_projections(identity_cache)
        return public_id

    def _persist_identity_projections(
        self,
        cache: IdentityMappingCache | None = None,
    ) -> None:
        """Persist a projection batch once at its transport boundary."""

        identity_cache = cache or self._identity_mappings(refresh_session=True)
        if not identity_cache.projections_dirty:
            return
        try:
            _save_identity_projections(self._auth_dir(), identity_cache)
        except Exception as exc:
            logger.debug("持久化 WhatsApp 公开身份投影失败: %s", exc)

    def _delivery_target_from_session_id(
        self,
        session_id: str,
        *,
        is_group: bool = False,
    ) -> str:
        """Recover a transport JID from canonical or legacy AstrBot sessions."""

        return _delivery_jid_from_session_id(
            session_id,
            is_group=is_group,
            cache=self._identity_mappings(refresh_session=True),
        )

    async def send_by_session(self, session: MessageSesion, message_chain: MessageChain):
        target = getattr(session, "session_id", None) or getattr(session, "message_session_id", None)
        if not target:
            raise ValueError("WhatsApp send_by_session 缺少目标会话")

        is_group_session = (
            getattr(session, "message_type", None) == MessageType.GROUP_MESSAGE
        )
        target = self._delivery_target_from_session_id(
            str(target),
            is_group=is_group_session,
        )
        if not target:
            raise ValueError("WhatsApp send_by_session 目标会话 ID 格式无效")

        # PN→lid 正向解析：若目標是 PN 且有緩存 lid，用 lid 確保訊息歸流正確
        target_key = target
        lid_target = self._identity_mappings(refresh_session=True).lid_for_pn(target_key)
        if lid_target:
            target = lid_target

        logger.debug(
            "WhatsApp send_by_session: target=%s components=%s",
            target,
            [component.__class__.__name__ for component in message_chain.chain],
        )
        await self._send_presence(target, "composing")
        reply = next(
            (component for component in message_chain.chain if isinstance(component, Reply)),
            None,
        )
        quote_state = QuoteState(
            str(getattr(reply, "id", "") or "") or None,
        )
        try:
            pending_caption, pending_mentions = await process_message_chain(
                self.client, target, message_chain.chain,
                link_preview_single_url=bool(self.config.get("link_preview_single_url", True)),
                text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
                use_caption=str(self.config.get("media_caption_mode") or "separate") == "caption",
                mention_resolver=self._delivery_target_from_session_id,
                quote_state=quote_state,
            )
            await flush_pending_text(
                self.client, target, pending_caption, pending_mentions,
                link_preview_single_url=bool(self.config.get("link_preview_single_url", True)),
                text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
                quote_state=quote_state,
            )
            if quote_state.sent_count == 0:
                raise RuntimeError("WhatsApp message produced no deliverable content")
            await super().send_by_session(session, message_chain)
        finally:
            await self._send_presence(target, "paused")

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
                                        "WhatsApp 入向消息: 聊天=%s 发送者=%s 自身=%s 消息ID=%s 文字长度=%s 媒体数=%s",
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
                        logger.debug("WhatsApp Gateway 事件流空闲超时，正在重连: %s", exc)
                        self._quiet_next_gateway_connect = True
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
        """熱重載平台配置：僅供插件配置重載時同步運行中實例。"""
        self._platform_config = sanitize_whatsapp_platform_config(platform_config or {})
        self.config = self._merged_config(self._platform_config)
        self._legacy_command_prefix = extract_legacy_command_prefix(self._platform_config)
        self._refresh_registered_commands()
        self.client.update_base_url(self._base_url)
        await self._stop_health_monitor()
        self._force_gateway_restart = True
        identity_auth_dir = self._auth_dir()
        _load_lid_mappings(identity_auth_dir, self._identity_mappings())
        self._identity_session_dir = _active_auth_session_dir(identity_auth_dir)
        if self._stopped.is_set():
            return
        self._reconnect_event.set()

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
        if data.get("fromMe") and self.config.get("ignore_self_messages", False):
            logger.debug("忽略自身消息: message_id=%s", data.get("messageId"))
            return None

        chat_jid = str(data.get("chatJid") or "")
        sender_jid = str(data.get("senderJid") or chat_jid)
        sender_pn = str(data.get("senderPn") or "")
        sender_lid = str(data.get("senderLid") or "")
        sender_phone = str(data.get("senderPhone") or "")
        identity_cache = self._identity_mappings(refresh_session=True)

        if _is_lid_jid(chat_jid) or _is_pn_jid(chat_jid):
            chat_jid = _normalize_user_jid(chat_jid)
            data["chatJid"] = chat_jid
        if _is_lid_jid(sender_jid) or _is_pn_jid(sender_jid):
            sender_jid = _normalize_user_jid(sender_jid)
            data["senderJid"] = sender_jid
        if _is_lid_jid(sender_lid):
            sender_lid = _normalize_lid_jid(sender_lid)
        elif _is_lid_jid(sender_jid):
            sender_lid = _normalize_lid_jid(sender_jid)
        if sender_lid:
            data["senderLid"] = sender_lid
        if _is_pn_jid(sender_pn):
            sender_pn = _base_pn_jid(sender_pn)

        if sender_phone:
            sender_phone = _phone_from_identity(sender_phone)
            digits = sender_phone.lstrip("+")
            if digits and not _is_pn_jid(sender_pn):
                sender_pn = f"{digits}@s.whatsapp.net"

        # 缓存 lid→PN 映射，用于出站 @mention 时解析
        if _is_lid_jid(sender_lid) and _is_pn_jid(sender_pn):
            if not identity_cache.pn_for_lid(sender_lid):
                _cache_lid_mapping(sender_lid, sender_pn, identity_cache)
                _save_lid_mapping(
                    self._auth_dir(),
                    sender_lid,
                    sender_pn,
                    identity_cache,
                )
        if (
            _is_lid_jid(chat_jid)
            and not bool(data.get("fromMe"))
            and not identity_cache.pn_for_lid(chat_jid)
            and _is_pn_jid(sender_pn)
        ):
            _cache_lid_mapping(chat_jid, sender_pn, identity_cache)
            _save_lid_mapping(
                self._auth_dir(),
                chat_jid,
                sender_pn,
                identity_cache,
            )

        # Baileys 并非每一条消息都会带 senderPn；一旦学到过映射，后续必须
        # 继续使用同一个手机号身份，避免同一成员在 QQ 式 user_id 语义下分裂。
        if not _is_pn_jid(sender_pn) and _is_lid_jid(sender_lid):
            sender_pn = identity_cache.pn_for_lid(sender_lid)
        if not sender_pn and _is_lid_jid(sender_lid):
            sender_pn = await self._resolve_lid_pn(sender_lid, identity_cache)
        if not sender_phone and _is_pn_jid(sender_pn):
            sender_phone = _phone_from_identity(sender_pn)
        if sender_pn:
            data["senderPn"] = sender_pn
        if sender_phone:
            data["senderPhone"] = sender_phone

        # Gateway canonicalSessionJid keeps the transport target stable when
        # WhatsApp switches a direct chat between PN and LID addressing modes.
        # UMO uses this account's persistent public projection: normally a QQ-
        # style numeric PN, or an explicit ``lid-N`` namespace when WhatsApp has
        # not proved a phone-number alias yet.
        canonical_session_jid = str(data.get("canonicalSessionJid") or "")
        if _is_lid_jid(canonical_session_jid) or _is_pn_jid(canonical_session_jid):
            canonical_session_jid = _normalize_user_jid(canonical_session_jid)
        canonical_session_pn = str(data.get("canonicalSessionPn") or "")
        if canonical_session_pn:
            canonical_session_pn = _base_pn_jid(canonical_session_pn)
        if not canonical_session_pn and _is_lid_jid(canonical_session_jid):
            canonical_session_pn = identity_cache.pn_for_lid(canonical_session_jid)
        if not canonical_session_pn and _is_lid_jid(canonical_session_jid):
            canonical_session_pn = await self._resolve_lid_pn(
                canonical_session_jid,
                identity_cache,
            )
        if not canonical_session_pn and _is_pn_jid(canonical_session_jid):
            canonical_session_pn = _base_pn_jid(canonical_session_jid)
        if not canonical_session_pn and not bool(data.get("fromMe")):
            canonical_session_pn = sender_pn
        if (
            _is_lid_jid(canonical_session_jid)
            and _is_pn_jid(canonical_session_pn)
            and identity_cache.pn_for_lid(canonical_session_jid)
            != canonical_session_pn
        ):
            _cache_lid_mapping(
                canonical_session_jid,
                canonical_session_pn,
                identity_cache,
            )
            _save_lid_mapping(
                self._auth_dir(),
                canonical_session_jid,
                canonical_session_pn,
                identity_cache,
            )
        extras = data.get("extras") or {}
        reaction = extras.get("reaction")
        if self._is_reaction_only(data):
            text = self._reaction_message_text(reaction)
        else:
            text = str(data.get("text") or "")
            if text and bool(self.config.get("parse_inbound_formatting", True)):
                text = format_markdown_from_whatsapp(text)
        media_items = data.get("media") or []
        # Gateway uses this generic marker only as a transport summary.  Once
        # a concrete media record exists, let the component chain represent
        # either the downloaded file or one explicit ``unavailable`` marker;
        # otherwise failed media without a caption appears twice.
        if media_items and re.fullmatch(r"<media:[a-z]+>(?: x\d+)?", text.strip()):
            text = ""
        is_group = chat_jid.endswith("@g.us")
        group_id = _public_numeric_id(chat_jid) if is_group else None
        if is_group and sender_phone and group_id and self._numeric_whatsapp_id(sender_phone) == group_id:
            sender_phone = ""

        user_id = self._project_public_user_id(
            sender_pn or sender_lid or sender_jid,
            lid_jid=sender_lid or None,
            pn_jid=sender_pn or None,
            cache=identity_cache,
        )

        session_lid = canonical_session_jid if _is_lid_jid(canonical_session_jid) else ""
        if not session_lid and not is_group and _is_lid_jid(chat_jid):
            session_lid = chat_jid
        private_session_id = self._project_public_user_id(
            canonical_session_pn
            or session_lid
            or chat_jid
            or sender_pn
            or sender_jid,
            lid_jid=session_lid or None,
            pn_jid=canonical_session_pn or None,
            cache=identity_cache,
        )

        # Do not let malformed transport identities create empty or unstable
        # AstrBot UMO keys. The Gateway normally filters these first, but this
        # boundary is also used by external Gateway deployments.
        if not user_id:
            logger.warning(
                "忽略身份格式无效的 WhatsApp 消息: chat=%s sender=%s message_id=%s",
                chat_jid,
                sender_jid,
                data.get("messageId"),
            )
            return None
        if is_group and not group_id:
            logger.warning(
                "忽略群组 ID 格式无效的 WhatsApp 消息: chat=%s message_id=%s",
                chat_jid,
                data.get("messageId"),
            )
            return None
        if not is_group and not private_session_id:
            logger.warning(
                "忽略私聊会话 ID 格式无效的 WhatsApp 消息: chat=%s message_id=%s",
                chat_jid,
                data.get("messageId"),
            )
            return None

        abm = AstrBotMessage()
        abm.type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        abm.group_id = group_id
        abm.message_str = self._qq_style_message_str(data, text)
        abm.sender = MessageMember(
            user_id=user_id,
            nickname=str(data.get("senderName") or sender_pn or sender_jid),
        )
        abm.message = self._message_chain(data, text)
        raw_self_jid = str(data.get("selfJid") or "")
        raw_self_lid = str(data.get("selfLid") or "")
        abm.self_id = self._project_public_user_id(
            raw_self_jid or raw_self_lid,
            lid_jid=raw_self_lid or None,
            pn_jid=raw_self_jid or None,
            cache=identity_cache,
        ) or "whatsapp"
        abm.session_id = _build_umo_session_id(
            is_group=is_group,
            group_id=group_id,
            user_id=user_id if is_group else private_session_id,
            unique_session=self._unique_session_enabled(),
        )
        abm.message_id = str(data.get("messageId") or "")
        try:
            abm.timestamp = int(float(data.get("timestamp") or time.time()))
        except (TypeError, ValueError):
            abm.timestamp = int(time.time())

        # 暴露常用 OneBot 同名字段，方便原本面向 QQ raw_message 的插件复用；
        # WhatsApp 原始 JID 字段仍完整保留，不能用这些别名参与实际投递。
        data["post_type"] = "message"
        data["message_type"] = "group" if is_group else "private"
        data["sub_type"] = "normal" if is_group else "friend"
        data["user_id"] = str(user_id)
        if is_group:
            data["group_id"] = str(group_id or "")
        elif "group_id" in data:
            data["group_id"] = str(data["group_id"])
        data["self_id"] = str(abm.self_id)
        data["message_id"] = str(abm.message_id)
        data.setdefault("time", abm.timestamp)
        data.setdefault("font", 0)
        data.setdefault("raw_message", str(data.get("text") or ""))
        data.setdefault("message", self._onebot_message_projection(abm.message))
        raw_sender = data.get("sender")
        sender_projection = dict(raw_sender) if isinstance(raw_sender, Mapping) else {}
        sender_projection["user_id"] = str(user_id)
        sender_projection.setdefault("nickname", abm.sender.nickname)
        sender_projection.setdefault("card", abm.sender.nickname if is_group else "")
        sender_projection.setdefault("role", str(data.get("senderRole") or "member"))
        data["sender"] = sender_projection
        abm.raw_message = _as_attr_dict(data)
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
        platform_settings = getattr(self, "_platform_settings", {}) or {}
        ignore_at_all = bool(platform_settings.get("ignore_at_all", False))
        is_self_mentioned = self._message_mentions_self(
            raw,
            include_at_all=not ignore_at_all,
        )
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
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self.client,
            target_jid=str(raw.get("chatJid") or message.session_id),
            source_message_id=str(raw.get("messageId") or "") or None,
            text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
            media_caption_mode=str(self.config.get("media_caption_mode") or "separate"),
            link_preview_single_url=bool(self.config.get("link_preview_single_url", True)),
            typing_indicator=bool(self.config.get("typing_indicator", True)),
            ack_done_emoji=str(self.config.get("pre_ack_done_emoji", "✅") or "✅"),
            streaming_edit_throttle=float(self.config.get("streaming_edit_throttle") or 1.0),
            mention_resolver=self._delivery_target_from_session_id,
        )
        # get_group() receives full transport identities from the Gateway and
        # must use the same persistent PN/LID projection as inbound messages.
        event.identity_projector = self._project_public_user_id
        # Match aiocqhttp's security boundary: platform group roles remain in
        # raw_message.sender.role, while AstrBot promotes only IDs configured in
        # its global admins_id list to event.role == "admin".
        sender_allowed = await self._is_sender_allowed(raw, is_private)
        pre_ack_enabled = bool(self.config.get("pre_ack_emoji", True))
        pre_ack_private = bool(self.config.get("pre_ack_private", True))
        # 非喚醒消息仍提交給 AstrBot（讓插件統計等處理），但不設 is_wake
        if is_reaction_only:
            logger.debug("忽略表情回應事件: session=%s msg=%s",
                          message.session_id, message.message_id)
            return
        if not sender_allowed:
            logger.info("忽略未授權發送者: session=%s sender=%s",
                          message.session_id, raw.get("senderJid"))
            return
        # Keep protocol wake semantics independent from the optional reaction
        # acknowledgement. AstrBot's core uses these flags in the same way as
        # the QQ adapter, including when pre-ack is disabled.
        if is_self_mentioned or is_command:
            event.is_at_or_wake_command = True
            event.is_wake = True
        if pre_ack_enabled and not is_reaction_only:
            if is_private:
                should_ack = pre_ack_private
            else:
                group_mode = self._group_pre_ack_mode()
                should_ack = group_mode == "always" or (
                    group_mode == "mentions" and (is_self_mentioned or is_command)
                )
            if should_ack:
                await self._pre_ack(event)
        if is_legacy_command:
            event.is_at_or_wake_command = True
            event.is_wake = True
        logger.info(
            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s self_mentioned=%s reply_to_self=%s is_private=%s is_command=%s legacy_command=%s",
            message.session_id,
            getattr(message.sender, "user_id", None),
            raw.get("senderJid"),
            message.message_id,
            len(message.message_str or ""),
            is_self_mentioned,
            is_reply_to_self,
            is_private,
            is_command,
            is_legacy_command,
        )
        self.commit_event(event)

    @staticmethod
    def _text_without_visible_mentions(data: dict[str, Any], text: str) -> str:
        """移除已结构化为 At 组件的 ``@JID``，避免内容中重复显示 ID。"""
        value = str(text or "")
        if not value:
            return ""
        tokens: set[str] = set()
        mentioned_jids = data.get("mentionedJids") or []
        if not isinstance(mentioned_jids, (list, tuple, set)):
            mentioned_jids = []
        for jid in mentioned_jids:
            token = str(jid or "").split("@", 1)[0].split(":", 1)[0]
            if token:
                tokens.add(token)
        self_lid = str(data.get("selfLid") or "")
        self_jid_token = str(data.get("selfJid") or "").split("@", 1)[0].split(":", 1)[0]
        if self_lid and self_jid_token in tokens:
            tokens.add(self_lid.split("@", 1)[0].split(":", 1)[0])
        if not tokens:
            return value
        pattern = r"[ \t]*(?<![\w@])@(?:" + "|".join(
            re.escape(token) for token in sorted(tokens, key=len, reverse=True)
        ) + r")(?![\w@])[ \t]*"
        cleaned = re.sub(pattern, " ", value)
        return cleaned.strip()

    def _qq_style_message_str(self, data: dict[str, Any], text: str) -> str:
        """Build the same plain-text mention view as aiocqhttp."""

        parts: list[str] = []
        first_self_processed = False
        raw_self_jid = str(data.get("selfJid") or "")
        raw_self_lid = str(data.get("selfLid") or "")
        self_id = self._project_public_user_id(
            raw_self_jid or raw_self_lid,
            lid_jid=raw_self_lid or None,
            pn_jid=raw_self_jid or None,
        )
        for component in self._ordered_text_components(data, text):
            if isinstance(component, Plain):
                parts.append(str(component.text or ""))
                continue
            if not isinstance(component, At):
                continue
            mention_id = str(getattr(component, "qq", "") or "")
            if isinstance(component, AtAll) or mention_id.lower() == "all":
                # aiocqhttp keeps At(qq="all") in the component chain for
                # wake checks but omits it from message_str.
                continue
            if mention_id == self_id and not first_self_processed:
                first_self_processed = True
                parts.append(" ")
                continue
            display_name = str(getattr(component, "name", "") or mention_id)
            parts.append(f" @{display_name}({mention_id}) ")
        visible = "".join(parts).strip()
        native_event = (data.get("extras") or {}).get("event")
        if isinstance(native_event, dict):
            # The Gateway's short text is only a fallback.  Expose the full
            # native event metadata in the same plain-text view used by
            # QQ-oriented plugins and the model.
            visible = self._native_event_text(native_event)
        # A failed download must remain visible to plugins and the model even
        # when the WhatsApp media message also has a caption.  The component
        # chain already carries the same placeholder; mirror it in
        # ``message_str`` because many QQ-oriented plugins inspect only that
        # plain-text projection.
        unavailable: list[str] = []
        for media in data.get("media") or []:
            if not isinstance(media, dict) or media.get("path") or media.get("url"):
                continue
            marker = f"<media:{media.get('type') or 'unknown'} unavailable>"
            if marker not in visible and marker not in unavailable:
                unavailable.append(marker)
        return " ".join(part for part in (visible, *unavailable) if part).strip()

    def _mention_entries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        mentioned_jids = data.get("mentionedJids") or []
        if not isinstance(mentioned_jids, (list, tuple, set)):
            return []
        mentioned_names = data.get("mentionedNames") or {}
        if not isinstance(mentioned_names, dict):
            mentioned_names = {}
        raw_self_jid = str(data.get("selfJid") or "")
        raw_self_lid = str(data.get("selfLid") or "")
        identity_cache = self._identity_mappings()
        standard_self_id = self._project_public_user_id(
            raw_self_jid or raw_self_lid,
            lid_jid=raw_self_lid or None,
            pn_jid=raw_self_jid or None,
            cache=identity_cache,
        )
        entries: list[dict[str, Any]] = []
        if data.get("mentionAll"):
            entries.append(
                {
                    "jid": "all",
                    "id": "all",
                    "name": "全体成员",
                    "is_self": False,
                    "is_all": True,
                    "tokens": {"all"},
                }
            )
        for raw_jid in mentioned_jids:
            jid = str(raw_jid or "")
            if not jid:
                continue
            is_self = self._is_self_mention(jid, raw_self_jid, raw_self_lid)
            mapped_pn = identity_cache.pn_for_lid(jid) if _is_lid_jid(jid) else ""
            mapped_lid = identity_cache.lid_for_pn(jid) if _is_pn_jid(jid) else ""
            standard_id = standard_self_id if is_self else self._project_public_user_id(
                mapped_pn or jid,
                lid_jid=jid if _is_lid_jid(jid) else None,
                pn_jid=mapped_pn or (jid if _is_pn_jid(jid) else None),
                cache=identity_cache,
            )
            if not standard_id:
                standard_id = self._whatsapp_user_id(jid)
            display_name = str(
                mentioned_names.get(jid)
                or (mentioned_names.get(mapped_pn) if mapped_pn else "")
                or (mentioned_names.get(mapped_lid) if mapped_lid else "")
                or standard_id
            )
            tokens = {
                self._whatsapp_user_id(jid),
                standard_id,
                display_name.lstrip("@"),
            }
            for alias in (mapped_pn, mapped_lid):
                if alias:
                    tokens.add(self._whatsapp_user_id(alias))
            if is_self:
                tokens.update(
                    self._whatsapp_user_id(value)
                    for value in (raw_self_jid, raw_self_lid)
                    if value
                )
            tokens.discard("")
            entries.append(
                {
                    "jid": jid,
                    "id": standard_id,
                    "name": display_name,
                    "is_self": is_self,
                    "is_all": False,
                    "tokens": tokens,
                }
            )
        return entries

    def _ordered_text_components(
        self,
        data: dict[str, Any],
        text: str,
    ) -> list[Any]:
        """Split visible WhatsApp mention tokens without moving their position."""

        value = str(text or "")
        entries = self._mention_entries(data)
        if not entries:
            return [Plain(text=value)] if value else []

        token_entries: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for index, entry in enumerate(entries):
            for token in entry["tokens"]:
                token_entries.setdefault(str(token).casefold(), []).append((index, entry))
        tokens = sorted(
            {token for entry in entries for token in entry["tokens"] if token},
            key=lambda token: len(str(token)),
            reverse=True,
        )
        if not tokens:
            mentions = [self._mention_component(entry) for entry in entries]
            return [*mentions, *([Plain(text=value)] if value else [])]

        pattern = re.compile(
            r"[ \t]*(?<![\w@])@(?P<token>"
            + "|".join(re.escape(str(token)) for token in tokens)
            + r")(?![\w@])[ \t]*",
            re.IGNORECASE,
        )
        result: list[Any] = []
        used: set[int] = set()
        cursor = 0
        for match in pattern.finditer(value):
            plain = value[cursor : match.start()]
            if plain:
                result.append(Plain(text=plain))
            candidates = token_entries.get(match.group("token").casefold()) or []
            if candidates:
                index, entry = next(
                    ((idx, item) for idx, item in candidates if idx not in used),
                    candidates[0],
                )
                used.add(index)
                result.append(self._mention_component(entry))
            cursor = match.end()
        tail = value[cursor:]
        if tail:
            result.append(Plain(text=tail))

        missing = [
            self._mention_component(entry)
            for index, entry in enumerate(entries)
            if index not in used
        ]
        return [*missing, *result]

    @staticmethod
    def _mention_component(entry: dict[str, Any]) -> Any:
        if entry.get("is_all"):
            return AtAll(name=str(entry.get("name") or "全体成员"))
        return At(qq=entry["id"], name=entry["name"])

    def _message_chain(self, data: dict[str, Any], text: str) -> list[Any]:
        chain: list[Any] = []
        raw_self_jid = str(data.get("selfJid") or "")
        raw_self_lid = str(data.get("selfLid") or "")
        identity_cache = self._identity_mappings()
        standard_self_id = self._project_public_user_id(
            raw_self_jid or raw_self_lid,
            lid_jid=raw_self_lid or None,
            pn_jid=raw_self_jid or None,
            cache=identity_cache,
        ) or "whatsapp"
        quoted_data = data.get("quoted")
        if quoted_data:
            quoted_data = dict(quoted_data)
            quoted_data.setdefault("selfJid", raw_self_jid)
            quoted_data.setdefault("selfLid", raw_self_lid)
            quoted_text = str(quoted_data.get("text") or "")
            if quoted_text and bool(self.config.get("parse_inbound_formatting", True)):
                quoted_text = format_markdown_from_whatsapp(quoted_text)
            quoted_chain = self._quoted_media_chain(quoted_data, quoted_text=quoted_text)
            quoted_sender = str(
                quoted_data.get("participantPn")
                or quoted_data.get("participant")
                or ""
            )
            quoted_is_self = self._is_self_mention(
                quoted_sender,
                raw_self_jid,
                raw_self_lid,
            )
            if quoted_is_self:
                quoted_sender_id = standard_self_id
            else:
                quoted_sender_id = self._project_public_user_id(
                    quoted_sender,
                    lid_jid=quoted_sender if _is_lid_jid(quoted_sender) else None,
                    pn_jid=quoted_sender if _is_pn_jid(quoted_sender) else None,
                    cache=identity_cache,
                ) if quoted_sender else "0"
            quoted_sender_name = str(quoted_data.get("participantName") or quoted_sender)
            quoted_message_str = self._qq_style_message_str(quoted_data, quoted_text)
            try:
                quoted_timestamp = int(float(quoted_data.get("timestamp") or 0))
            except (TypeError, ValueError):
                quoted_timestamp = 0
            chain.append(Reply(
                id=str(quoted_data.get("stanzaId") or ""),
                chain=quoted_chain if quoted_chain else None,
                # AstrBot Core treats Reply.sender_id == self_id as a wake
                # signal. QQ replies also carry an explicit At segment, but a
                # native WhatsApp quote does not. Keep the bot identity in the
                # QQ-compatible field while preventing quote metadata alone
                # from masquerading as an @ mention.
                sender_id="" if quoted_is_self else quoted_sender_id,
                sender_nickname=quoted_sender_name,
                time=quoted_timestamp,
                message_str=quoted_message_str,
                text=quoted_message_str,
                qq=quoted_sender_id,
            ))
        extras = data.get("extras") or {}
        reaction = extras.get("reaction")
        location = extras.get("location")
        contact = extras.get("contact")
        button_response = extras.get("buttonResponse")
        list_response = extras.get("listResponse")
        poll = extras.get("poll")
        native_event = extras.get("event")
        has_structured_summary = any(
            (location, contact, button_response, list_response, poll, native_event)
        )
        display_text = text or (self._reaction_message_text(reaction) if reaction else "")
        if display_text and not has_structured_summary:
            chain.extend(self._ordered_text_components(data, display_text))
        elif data.get("mentionedJids"):
            # Structured messages rarely carry mentions, but retain any real
            # mention metadata rather than discarding it with the summary.
            chain.extend(self._ordered_text_components(data, ""))
        if location:
            chain.append(Location(
                lat=float(location.get("latitude") or 0),
                lon=float(location.get("longitude") or 0),
                title=str(location.get("name") or ""),
                content=str(location.get("address") or ""),
            ))
        if contact:
            name = str(contact.get("displayName") or "")
            vcard = str(contact.get("vcard") or "")
            phones = [m.group(1).strip() for m in re.finditer(r"TEL[^:]*:([^\r\n]+)", vcard, re.IGNORECASE) if m.group(1).strip()]
            label = name or (phones[0] if phones else "Contact")
            detail = f"{label}: {', '.join(phones)}" if phones else label
            chain.append(Plain(text=f"📇 {detail}"))
        if button_response:
            selected = str(button_response.get("selectedDisplayText") or button_response.get("selectedButtonId") or "")
            if selected:
                chain.append(Plain(text=f"[Button] {selected}"))
        if list_response:
            title = str(list_response.get("title") or "")
            row_id = str(list_response.get("singleSelectReply") or "")
            if title or row_id:
                chain.append(Plain(text=f"[List] {title or row_id}"))
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
        if isinstance(native_event, dict):
            chain.append(Plain(text=self._native_event_text(native_event)))
        for media in data.get("media") or []:
            media_type = media.get("type")
            path = media.get("path") or media.get("url") or ""
            if not path:
                chain.append(Plain(text=f"<media:{media_type or 'unknown'} unavailable>"))
                continue
            if media_type == "image":
                chain.append(Image(file=path, path=path))
            elif media_type == "sticker":
                # A sticker is still image content for AstrBot/LLM consumers;
                # the generated filename retains "sticker" so outbound echo
                # can recover native sticker transport.
                chain.append(Image(file=path, path=path))
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

    @staticmethod
    def _native_event_text(event: dict[str, Any]) -> str:
        def timestamp_text(value: Any) -> str:
            try:
                seconds = float(value)
                if not seconds:
                    return ""
                return (
                    datetime.fromtimestamp(seconds, timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
            except (OSError, OverflowError, TypeError, ValueError):
                return ""

        state = "Cancelled" if bool(event.get("isCanceled")) else "Event"
        parts = [f"[{state}] {str(event.get('name') or 'WhatsApp event').strip()}"]
        start = timestamp_text(event.get("startTime"))
        end = timestamp_text(event.get("endTime"))
        if start:
            parts.append(f"{start} → {end}" if end else start)
        location = event.get("location") or {}
        if isinstance(location, dict):
            place = " — ".join(
                value
                for value in (
                    str(location.get("name") or "").strip(),
                    str(location.get("address") or "").strip(),
                )
                if value
            )
            if place:
                parts.append(place)
        description = str(event.get("description") or "").strip()
        if description:
            parts.append(description)
        join_link = str(event.get("joinLink") or "").strip()
        if join_link:
            parts.append(join_link)
        if bool(event.get("extraGuestsAllowed")):
            parts.append("extra guests allowed")
        return " | ".join(parts)

    def _quoted_media_chain(
        self,
        quoted_data: dict[str, Any],
        *,
        quoted_text: str | None = None,
    ) -> list[Any]:
        chain: list[Any] = []
        if quoted_text is None:
            quoted_text = str(quoted_data.get("text") or "")
        if quoted_text and not quoted_text.startswith("<media:"):
            chain.extend(self._ordered_text_components(quoted_data, quoted_text))
        for media in quoted_data.get("media") or []:
            media_type = media.get("type")
            path = media.get("path") or media.get("url") or ""
            if not path:
                chain.append(Plain(text=f"<media:{media_type or 'unknown'} unavailable>"))
                continue
            if media_type == "image":
                chain.append(Image(file=path, path=path))
            elif media_type == "sticker":
                chain.append(Image(file=path, path=path))
            elif media_type == "audio":
                chain.append(Record(file=path))
            elif media_type == "video":
                chain.append(Video(file=path))
            elif media_type == "document":
                chain.append(File(name=str(media.get("fileName") or Path(path).name), file=path))
        return chain

    @staticmethod
    def _onebot_message_projection(chain: list[Any]) -> list[dict[str, Any]]:
        """Return a minimal truthful OneBot-shaped view for QQ-oriented plugins."""

        projected: list[dict[str, Any]] = []
        for component in chain:
            if isinstance(component, Plain):
                projected.append(
                    {"type": "text", "data": {"text": str(component.text or "")}},
                )
            elif isinstance(component, At):
                data = {"qq": str(getattr(component, "qq", "") or "")}
                name = str(getattr(component, "name", "") or "")
                if name:
                    data["name"] = name
                projected.append({"type": "at", "data": data})
            elif isinstance(component, Reply):
                projected.append(
                    {
                        "type": "reply",
                        "data": {"id": str(getattr(component, "id", "") or "")},
                    },
                )
            elif isinstance(component, Image):
                value = str(
                    getattr(component, "path", "")
                    or getattr(component, "file", "")
                    or getattr(component, "url", "")
                    or ""
                )
                projected.append({"type": "image", "data": {"file": value}})
            elif isinstance(component, Record):
                value = str(
                    getattr(component, "path", "")
                    or getattr(component, "file", "")
                    or getattr(component, "url", "")
                    or ""
                )
                projected.append({"type": "record", "data": {"file": value}})
            elif isinstance(component, Video):
                value = str(
                    getattr(component, "path", "")
                    or getattr(component, "file", "")
                    or getattr(component, "url", "")
                    or ""
                )
                projected.append({"type": "video", "data": {"file": value}})
            elif isinstance(component, File):
                projected.append(
                    {
                        "type": "file",
                        "data": {
                            "name": str(getattr(component, "name", "") or ""),
                            "file": str(getattr(component, "file_", "") or ""),
                            "url": str(getattr(component, "url", "") or ""),
                        },
                    },
                )
            elif isinstance(component, Location):
                projected.append(
                    {
                        "type": "location",
                        "data": {
                            "lat": float(getattr(component, "lat", 0) or 0),
                            "lon": float(getattr(component, "lon", 0) or 0),
                            "title": str(getattr(component, "title", "") or ""),
                            "content": str(getattr(component, "content", "") or ""),
                        },
                    },
                )
        return projected

    def _same_whatsapp_user(self, left: str, right: str) -> bool:
        return _same_whatsapp_identity(left, right, self._identity_mappings())

    def _is_self_mention(self, mentioned: str, self_id: str, self_lid: str) -> bool:
        return self._same_whatsapp_user(mentioned, self_id) or (
            bool(self_lid) and self._same_whatsapp_user(mentioned, self_lid)
        )

    async def _is_sender_allowed(self, raw: dict[str, Any], is_private: bool) -> bool:
        chat_jid = str(raw.get("chatJid") or "")
        sender_jid = str(raw.get("senderJid") or "")
        sender_pn = str(raw.get("senderPn") or "")
        sender_phone = str(raw.get("senderPhone") or "")
        identity_cache = self._identity_mappings()

        candidates = set()
        for v in (chat_jid, sender_jid, sender_pn, sender_phone):
            if v:
                candidates.add(v)

        def _normalize_phone(value: str) -> str:
            text = str(value or "").strip()
            if text == "*":
                return "*"
            return _phone_from_identity(text) or text

        def _allowed_by(value: str, allow_list: list) -> bool:
            if not allow_list:
                return False
            normalized = _normalize_phone(value)
            for item in allow_list:
                item_str = str(item or "").strip()
                if item_str == "*":
                    return True
                item_phone = _normalize_phone(item_str)
                if item_phone and normalized and item_phone == normalized:
                    return True
                if item_str == value:
                    return True
                if _same_whatsapp_identity(item_str, value, identity_cache):
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
            # lid→PN 緩存兜底
            for c in candidates:
                if _is_lid_jid(c):
                    pn = identity_cache.pn_for_lid(c)
                    if pn and _allowed_by(pn, allow_from):
                        return True
            # 主動向 Gateway 查詢 lid 映射（等 3 秒）
            if _is_lid_jid(sender_jid) and not sender_phone and not sender_pn:
                try:
                    pn = await asyncio.wait_for(self.client.resolve_lid(sender_jid), timeout=4)
                    if pn and _allowed_by(pn, allow_from):
                        _cache_lid_mapping(sender_jid, pn, identity_cache)
                        _save_lid_mapping(
                            self._auth_dir(), sender_jid, pn, identity_cache,
                        )
                        return True
                except Exception:
                    pass
            return False

        policy = self.config.get("group_policy", "disabled")
        if policy == "disabled":
            return False
        groups = self._coerce_str_list(self.config.get("groups"))
        normalized_groups = {
            f"{group_id}@g.us"
            for value in groups
            if (group_id := _normalize_group_session_id(value))
        }
        if groups and "*" not in groups and chat_jid not in normalized_groups:
            return False
        if policy == "open":
            return True
        group_allow_from = self._coerce_str_list(self.config.get("group_allow_from"))
        if not group_allow_from:
            group_allow_from = self._coerce_str_list(self.config.get("allow_from"))
        if any(_allowed_by(c, group_allow_from) for c in candidates):
            return True
        for c in candidates:
            if _is_lid_jid(c):
                pn = identity_cache.pn_for_lid(c)
                if pn and _allowed_by(pn, group_allow_from):
                    return True
        # lid 兜底查詢（同上）
        if _is_lid_jid(sender_jid) and not sender_phone and not sender_pn:
            try:
                pn = await asyncio.wait_for(self.client.resolve_lid(sender_jid), timeout=4)
                if pn and _allowed_by(pn, group_allow_from):
                    _cache_lid_mapping(sender_jid, pn, identity_cache)
                    _save_lid_mapping(
                        self._auth_dir(), sender_jid, pn, identity_cache,
                    )
                    return True
            except Exception:
                pass
        return False

    def _message_mentions_self(
        self,
        data: Mapping[str, Any],
        *,
        include_at_all: bool = True,
    ) -> bool:
        if include_at_all and data.get("mentionAll"):
            return True
        self_id = str(data.get("selfJid") or "")
        self_lid = str(data.get("selfLid") or "")
        if any(
            self._is_self_mention(str(mentioned or ""), self_id, self_lid)
            for mentioned in data.get("mentionedJids") or []
        ):
            return True
        text = str(data.get("text") or "")
        if "@" not in text:
            return False
        self_public_id = self._project_public_user_id(
            self_id or self_lid,
            lid_jid=self_lid or None,
            pn_jid=self_id or None,
        )
        self_tokens = {self_public_id} if self_public_id else set()
        if not self_tokens:
            return False
        for token in re.findall(r"@([^\s@,，。:：;；)）(（]+)", text):
            token_id = token.strip().lstrip("+")
            if token_id and token_id in self_tokens:
                logger.info("WhatsApp @提及文本兜底命中: token=%s self_id=%s self_lid=%s", token, self_id, self_lid)
                return True
        return False

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

    async def _pre_ack(self, event: WhatsAppMessageEvent) -> None:
        emoji = str(self.config.get("pre_ack_emojis", "👀") or "👀").strip()
        if not emoji:
            return
        emoji = re.split(r'[,，\s]+', emoji, maxsplit=1)[0].strip()
        if not emoji:
            return
        try:
            await event.react(emoji)
            event._pre_acked = True
        except Exception as exc:
            logger.warning("WhatsApp 预回复表情发送失败: target=%s error=%s", event.target_jid, exc)

    def _whatsapp_user_id(self, jid: str) -> str:
        return _identity_user(jid)

    def _numeric_whatsapp_id(self, jid: str) -> str:
        return _public_numeric_id(jid)

    async def _send_presence(self, target: str, state: str) -> None:
        if state in {"composing", "paused"} and not self.config.get("typing_indicator", True):
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
            "applyEphemeral": bool(self.config.get("apply_ephemeral", False)),
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
        return Path.cwd() / "data" / "plugin_data"

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
        loaded_plugin_config = self._normalize_config(self._load_plugin_config())
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
            if key in DEPRECATED_CONFIG_KEYS:
                if key in CONFIG_KEY_ALIASES:
                    normalized_key = CONFIG_KEY_ALIASES[key]
                    normalized[normalized_key] = self._normalize_config_value(normalized_key, value)
                continue
            normalized_key = CONFIG_KEY_ALIASES.get(key, key)
            normalized[normalized_key] = self._normalize_config_value(normalized_key, value)
        return normalized

    def _normalize_config_value(self, key: str, value: Any) -> Any:
        if key in {"allow_from", "group_allow_from", "groups"}:
            return self._coerce_str_list(value)
        if key in {"log_level", "dm_policy", "group_policy", "media_caption_mode"}:
            return normalize_config_enum(key, value)
        if key == "pre_ack_public":
            return normalize_pre_ack_public(value)
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
        if isinstance(value, bool):
            return "mentions" if value else "never"
        return "mentions"

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

    async def _wait_for_gateway(self, quiet: bool = False) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 61):
            try:
                health = await self.client.health()
                log = logger.debug if quiet else logger.info
                log("WhatsApp Gateway: 连接正常 (第%s次尝试)", attempt)
                return
            except Exception as exc:
                last_error = exc
                if attempt in {1, 5, 15, 30, 60}:
                    logger.debug("等待 WhatsApp Gateway 健康检查第 %s 次失败: %s", attempt, exc)
                await asyncio.sleep(1)
        raise WhatsAppGatewayError(f"WhatsApp Gateway did not become healthy: {last_error}")

    async def _ensure_gateway_running(self) -> None:
        if not self.config.get("auto_start_gateway", True):
            try:
                await self.client.configure(self._gateway_config())
                logger.info("WhatsApp Gateway: 已重新配置（外部 Gateway）")
            except Exception as exc:
                logger.warning("配置外部 WhatsApp Gateway 失败: %s", exc)
            return
        # 確保 Gateway 進程健康且配置已同步
        needs_restart = bool(getattr(self, "_force_gateway_restart", False))
        self._force_gateway_restart = False
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
        logger.info("正在重启 WhatsApp Gateway: %s", self._base_url)
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
        quiet = bool(getattr(self, "_quiet_next_gateway_connect", False))
        self._quiet_next_gateway_connect = False
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

        await self._wait_for_gateway(quiet=quiet)
        configured = await self.client.configure(self._gateway_config())
        log = logger.debug if quiet else logger.info
        log("WhatsApp Gateway 配置: 私聊策略=%s 群聊策略=%s 已读回执=%s",
            configured.get("config", {}).get("dmPolicy"),
            configured.get("config", {}).get("groupPolicy"),
            configured.get("config", {}).get("sendReadReceipts"))
        try:
            status = await self.client.status()
            log("WhatsApp Gateway: 状态=%s 就绪=%s%s",
                status.get("status", "?"),
                bool(status.get("ready")),
                f" self={status['selfJid']}" if status.get("selfJid") else "")
        except Exception as exc:
            logger.warning("获取 WhatsApp Gateway 状态失败: %s", exc)
        log("WhatsApp 适配器已连接: %s", self._base_url)
        self._mark_running()
        await self._restart_health_monitor()
        # Other plugins may finish registering after this adapter is created.
        # Refresh here so legacy-prefix compatibility and command pre-ack see
        # the complete active CommandFilter registry after every reconnect.
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
                gw_status = status.get("status", "")
                ok = bool(status.get("ok", True)) and ready
                if ok:
                    if not self._gateway_healthy:
                        logger.info("WhatsApp Gateway: 已恢复健康")
                    self._gateway_healthy = True
                    self._mark_running()
                elif gw_status in (
                    "logged_out",
                    "session_invalid",
                    "resetting",
                    "qr_pending",
                    "qr_expired",
                    "pairing",
                    "pairing_restart",
                    "starting",
                ):
                    if self._gateway_healthy:
                        logger.debug("WhatsApp Gateway 处于 %s 状态，跳过自动重启", gw_status)
                    self._gateway_healthy = False
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
            status_text = event.get("status", "?")
            status_cn = {"connected": "已连接", "starting": "启动中", "disconnected": "已断开", "connecting": "连接中", "logout": "已登出"}.get(status_text, status_text)
            logger.debug("WhatsApp Gateway 事件: 状态=%s (重复)", status_cn)
            return
        self._last_gateway_status_log = current
        status_text = event.get("status", "?")
        status_cn = {"connected": "已连接", "starting": "启动中", "disconnected": "已断开", "connecting": "连接中", "logout": "已登出"}.get(status_text, status_text)
        logger.info("WhatsApp Gateway: %s%s",
                     status_cn,
                     f" (self={event['selfJid']})" if event.get("selfJid") else "")

    def _count_label(self, value: Any) -> str:
        if isinstance(value, list):
            return f"<{len(value)} entries>"
        return "<0 entries>" if value in (None, "") else "<1 entry>"

    def _refresh_registered_commands(self) -> None:
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

def get_active_whatsapp_adapters() -> list["WhatsAppPlatformAdapter"]:
    return list(_ACTIVE_ADAPTERS)


def sanitize_whatsapp_platform_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in UI_CONFIG_KEYS:
        if key not in config:
            continue
        value = config[key]
        if key == "pre_ack_public":
            value = _coerce_pre_ack_public(value)
        sanitized[key] = value

    # Preserve explicit legacy Gateway choices long enough for the plugin page
    # to adopt them, even if an adapter is constructed before plugin.initialize.
    for key, default in LEGACY_GATEWAY_DEFAULTS.items():
        hidden_key = f"_legacy_gateway_{key}"
        if hidden_key in config:
            sanitized[hidden_key] = config[hidden_key]
        elif key in config and config[key] != default:
            sanitized[hidden_key] = config[key]

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


def _coerce_pre_ack_public(value: Any) -> str:
    return normalize_pre_ack_public(value)


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
        if platform_config.get("type") == "whatsapp":
            platform_config = sanitize_whatsapp_platform_config(platform_config)
            logger.info("WhatsApp 平台配置变更，使用 AstrBot 原生完整重载流程: id=%s", platform_id)
        await PlatformManager._whatsapp_original_reload(self, platform_config)

    PlatformManager.reload = reload
    logger.info("WhatsApp 平台配置热重载已启用")
