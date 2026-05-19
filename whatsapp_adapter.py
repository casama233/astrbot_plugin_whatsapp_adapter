from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import File, Image, Plain, Record, Video
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

from .whatsapp_client import GatewayProcess, WhatsAppGatewayClient, WhatsAppGatewayError
from .whatsapp_event import WhatsAppMessageEvent


PLUGIN_DIR = Path(__file__).resolve().parent


DEFAULT_CONFIG: dict[str, Any] = {
    "gateway_host": "127.0.0.1",
    "gateway_port": 18789,
    "auto_start_gateway": True,
    "node_executable": "node",
    "auth_dir": "",
    "log_level": "info",
    "allow_from": [],
    "dm_policy": "allowlist",
    "group_policy": "disabled",
    "group_allow_from": [],
    "groups": [],
    "send_read_receipts": True,
    "mark_online": True,
    "text_chunk_limit": 4000,
    "media_max_mb": 50,
}

CONFIG_KEY_ALIASES: dict[str, str] = {
    "Gateway 绑定地址": "gateway_host",
    "Gateway 端口": "gateway_port",
    "自动启动 Gateway": "auto_start_gateway",
    "Node.js 可执行文件": "node_executable",
    "登录态目录": "auth_dir",
    "Gateway 日志级别": "log_level",
    "私聊允许名单": "allow_from",
    "私聊策略": "dm_policy",
    "群聊策略": "group_policy",
    "群聊发送者允许名单": "group_allow_from",
    "允许接入的群 JID": "groups",
    "发送已读回执": "send_read_receipts",
    "标记在线状态": "mark_online",
    "文本分片长度": "text_chunk_limit",
    "媒体大小上限 MB": "media_max_mb",
}

CONFIG_METADATA: dict[str, Any] = {
    "gateway_host": {
        "description": "Gateway 绑定地址",
        "type": "string",
        "hint": "Gateway HTTP 监听地址。AstrBot 与 Gateway 同容器时建议保持 127.0.0.1。",
    },
    "gateway_port": {
        "description": "Gateway 端口",
        "type": "int",
        "hint": "Gateway HTTP/SSE 端口。默认 18789。",
    },
    "auto_start_gateway": {
        "description": "自动启动 Gateway",
        "type": "bool",
        "hint": "启用后，平台启动时自动拉起内置 Node.js WhatsApp Gateway。",
    },
    "node_executable": {
        "description": "Node.js 可执行文件",
        "type": "string",
        "hint": "用于运行 Gateway 的 Node.js 命令或绝对路径。建议 Node.js 20+。",
    },
    "auth_dir": {
        "description": "登录态目录",
        "type": "string",
        "hint": "WhatsApp Web/Baileys 登录态保存目录。留空时使用插件数据目录下的 whatsapp-auth。",
    },
    "log_level": {
        "description": "Gateway 日志级别",
        "type": "string",
        "hint": "可选 silent、fatal、error、warn、info、debug、trace。",
    },
    "allow_from": {
        "description": "私聊允许名单",
        "type": "list",
        "hint": "允许私聊接入的电话号码，建议 E.164 格式，例如 +85212345678。使用 * 表示允许所有私聊。",
    },
    "dm_policy": {
        "description": "私聊策略",
        "type": "string",
        "hint": "allowlist=仅允许名单，open=开放私聊，disabled=禁用私聊。",
    },
    "group_policy": {
        "description": "群聊策略",
        "type": "string",
        "hint": "allowlist=按群成员允许名单，open=允许已接入群，disabled=禁用群聊。",
    },
    "group_allow_from": {
        "description": "群聊发送者允许名单",
        "type": "list",
        "hint": "允许在群聊中触发机器人的发送者号码。留空时回退到私聊允许名单。使用 * 表示允许所有群成员。",
    },
    "groups": {
        "description": "允许接入的群 JID",
        "type": "list",
        "hint": "允许接入的 WhatsApp 群 JID，例如 120363xxx@g.us。使用 * 表示允许所有群。",
    },
    "send_read_receipts": {
        "description": "发送已读回执",
        "type": "bool",
        "hint": "启用后，对已接受的入站消息发送 WhatsApp 已读回执。",
    },
    "mark_online": {
        "description": "标记在线状态",
        "type": "bool",
        "hint": "启用后，连接成功后定期向 WhatsApp 发送 available presence。",
    },
    "text_chunk_limit": {
        "description": "文本分片长度",
        "type": "int",
        "hint": "超过该长度的出站文本会分片发送。",
    },
    "media_max_mb": {
        "description": "媒体大小上限 MB",
        "type": "int",
        "hint": "入站媒体下载大小上限，单位 MB。",
    },
}

@register_platform_adapter(
    "whatsapp",
    "WhatsApp Web Gateway 适配器",
    default_config_tmpl=DEFAULT_CONFIG,
    adapter_display_name="WhatsApp Web Gateway 适配器",
    config_metadata=CONFIG_METADATA,
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
        self.settings = platform_settings
        self.client = WhatsAppGatewayClient(self._base_url)
        self.gateway_process: GatewayProcess | None = None
        self._stopped = asyncio.Event()
        self._last_gateway_status_log: tuple[Any, Any, Any] | None = None
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
        )

    async def send_by_session(self, session: MessageSesion, message_chain: MessageChain):
        target = getattr(session, "session_id", None) or getattr(session, "message_session_id", None)
        if not target:
            logger.debug("WhatsApp send_by_session skipped custom send because target session is missing")
            await super().send_by_session(session, message_chain)
            return

        logger.debug(
            "WhatsApp send_by_session: target=%s components=%s",
            target,
            [component.__class__.__name__ for component in message_chain.chain],
        )
        pending_caption: str | None = None
        for component in message_chain.chain:
            if isinstance(component, Plain):
                pending_caption = (pending_caption or "") + (component.text or "")
            elif isinstance(component, Image):
                logger.debug("Sending WhatsApp image: target=%s file=%s", target, component.file)
                await self.client.send_media(target, "image", component.file, pending_caption)
                pending_caption = None
            elif isinstance(component, Record):
                logger.debug("Sending WhatsApp audio: target=%s file=%s", target, component.file)
                await self.client.send_media(target, "audio", component.file, pending_caption)
                pending_caption = None
            elif isinstance(component, Video):
                logger.debug("Sending WhatsApp video: target=%s file=%s", target, component.file)
                await self.client.send_media(target, "video", component.file, pending_caption)
                pending_caption = None
            elif isinstance(component, File):
                media_path = component.file or component.url
                if media_path:
                    logger.debug("Sending WhatsApp document: target=%s file=%s", target, media_path)
                    await self.client.send_media(target, "document", media_path, pending_caption)
                    pending_caption = None

        if pending_caption:
            for chunk in self._chunk_text(pending_caption):
                logger.debug("Sending WhatsApp trailing text chunk: target=%s length=%s", target, len(chunk))
                await self.client.send_text(target, chunk)

        await super().send_by_session(session, message_chain)

    async def run(self):
        logger.info("Starting WhatsApp platform adapter run loop: gateway=%s", self._base_url)
        await self.client.start()
        if self.config.get("auto_start_gateway", True):
            try:
                health = await self.client.health()
                logger.info("WhatsApp Gateway already healthy before platform start: %s", self._safe_status(health))
            except Exception:
                logger.info("Starting WhatsApp Gateway for platform adapter at %s", self._base_url)
                self.gateway_process = GatewayProcess(
                    node_executable=str(self.config["node_executable"]),
                    script_path=PLUGIN_DIR / "gateway" / "whatsapp-gateway.mjs",
                    host=str(self.config["gateway_host"]),
                    port=int(self.config["gateway_port"]),
                    auth_dir=self._auth_dir(),
                    log_level=str(self.config["log_level"]),
                    data_dir=self._data_dir(),
                )
                await self.gateway_process.start()
        else:
            logger.info("WhatsApp platform auto-start disabled; expecting Gateway at %s", self._base_url)

        await self._wait_for_gateway()
        configured = await self.client.configure(self._gateway_config())
        logger.info("WhatsApp Gateway configured: %s", self._safe_status(configured))
        try:
            status = await self.client.status()
            logger.info("WhatsApp Gateway status after configure: %s", self._safe_status(status))
        except Exception as exc:
            logger.warning("Failed to fetch WhatsApp Gateway status after configure: %s", exc)
        logger.info("WhatsApp adapter connected to Gateway at %s", self._base_url)

        while not self._stopped.is_set():
            try:
                async for event in self.client.events():
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
                    elif event.get("type") in {"qr", "status"}:
                        self._log_gateway_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WhatsApp Gateway event stream interrupted: %s", exc)
                await self._ensure_gateway_running()
                await asyncio.sleep(3)

    async def terminate(self):
        logger.info("Terminating WhatsApp platform adapter")
        self._stopped.set()
        await self.client.close()
        if self.gateway_process:
            await self.gateway_process.stop()

    async def convert_message(self, data: dict[str, Any]) -> AstrBotMessage | None:
        if data.get("fromMe"):
            logger.debug("Ignoring WhatsApp message from self: message_id=%s", data.get("messageId"))
            return None

        chat_jid = str(data.get("chatJid") or "")
        sender_jid = str(data.get("senderJid") or chat_jid)
        text = str(data.get("text") or "")
        is_group = chat_jid.endswith("@g.us")
        group_id = chat_jid.split("@", 1)[0] if is_group else None

        abm = AstrBotMessage()
        abm.type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        abm.group_id = group_id
        abm.message_str = text
        abm.sender = MessageMember(
            user_id=self._numeric_whatsapp_id(sender_jid),
            nickname=str(data.get("senderName") or sender_jid),
        )
        abm.message = self._message_chain(data, text)
        abm.raw_message = data
        abm.self_id = str(data.get("selfJid") or "whatsapp")
        abm.session_id = chat_jid
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
        event = WhatsAppMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            client=self.client,
            target_jid=str(raw.get("chatJid") or message.session_id),
            quoted_message_id=str(raw.get("messageId") or "") or None,
            text_chunk_limit=int(self.config.get("text_chunk_limit") or 4000),
        )
        logger.info(
            "Committing WhatsApp event: session=%s sender=%s raw_sender=%s message_id=%s text_len=%s",
            message.session_id,
            getattr(message.sender, "user_id", None),
            raw.get("senderJid"),
            message.message_id,
            len(message.message_str or ""),
        )
        self.commit_event(event)

    def _message_chain(self, data: dict[str, Any], text: str) -> list[Any]:
        chain: list[Any] = []
        self_id = str(data.get("selfJid") or "whatsapp")
        for mentioned_jid in data.get("mentionedJids") or []:
            mentioned = str(mentioned_jid or "")
            if not mentioned:
                continue
            at_id = self_id if self._same_whatsapp_user(mentioned, self_id) else mentioned
            chain.append(At(qq=at_id, name=mentioned))
        if text:
            chain.append(Plain(text=text))
        for media in data.get("media") or []:
            media_type = media.get("type")
            path = media.get("path") or media.get("url") or ""
            if not path:
                continue
            if media_type == "image":
                chain.append(Image(file=path))
            elif media_type == "audio":
                chain.append(Record(file=path))
            elif media_type == "video":
                chain.append(Video(file=path))
            elif media_type == "document":
                chain.append(File(name=str(media.get("fileName") or Path(path).name), file=path))
            elif media_type == "sticker":
                chain.append(Image(file=path))
            else:
                chain.append(Plain(text=f"<media:{media_type or 'unknown'}> {path}"))
        if not chain:
            chain.append(Plain(text=""))
        return chain

    def _same_whatsapp_user(self, left: str, right: str) -> bool:
        return self._whatsapp_user_id(left) == self._whatsapp_user_id(right)

    def _whatsapp_user_id(self, jid: str) -> str:
        return str(jid or "").split(":", 1)[0].split("@", 1)[0]

    def _numeric_whatsapp_id(self, jid: str) -> str:
        digits = "".join(ch for ch in self._whatsapp_user_id(jid) if ch.isdigit())
        return digits or self._whatsapp_user_id(jid)

    def _gateway_config(self) -> dict[str, Any]:
        return {
            "dmPolicy": self.config.get("dm_policy", "allowlist"),
            "allowFrom": self.config.get("allow_from") or [],
            "groupPolicy": self.config.get("group_policy", "disabled"),
            "groupAllowFrom": self.config.get("group_allow_from") or [],
            "groups": self.config.get("groups") or [],
            "sendReadReceipts": bool(self.config.get("send_read_receipts", True)),
            "markOnline": bool(self.config.get("mark_online", True)),
            "mediaMaxMb": int(self.config.get("media_max_mb") or 50),
        }

    def _auth_dir(self) -> Path:
        configured = str(self.config.get("auth_dir") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return self._data_dir() / "whatsapp-auth"

    def _data_dir(self) -> Path:
        return Path.cwd() / "data" / "astrbot_plugin_whatsapp_adapter"

    def _chunk_text(self, text: str) -> list[str]:
        limit = max(1, int(self.config.get("text_chunk_limit") or 4000))
        if len(text) <= limit:
            return [text]
        return [text[i : i + limit] for i in range(0, len(text), limit)]

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
            normalized[CONFIG_KEY_ALIASES.get(key, key)] = value
        return normalized

    def _load_plugin_config(self) -> dict[str, Any]:
        config_path = Path.cwd() / "data" / "config" / "astrbot_plugin_whatsapp_adapter_config.json"
        try:
            with config_path.open("r", encoding="utf-8-sig") as fp:
                data = json.load(fp)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("Failed to load WhatsApp plugin config from %s: %s", config_path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    async def _wait_for_gateway(self) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 61):
            try:
                health = await self.client.health()
                logger.info("WhatsApp Gateway health check passed on attempt %s: %s", attempt, self._safe_status(health))
                return
            except (OSError, WhatsAppGatewayError, asyncio.TimeoutError, Exception) as exc:
                last_error = exc
                if attempt in {1, 5, 15, 30, 60}:
                    logger.debug("Waiting for WhatsApp Gateway health attempt %s failed: %s", attempt, exc)
                await asyncio.sleep(1)
        raise WhatsAppGatewayError(f"WhatsApp Gateway did not become healthy: {last_error}")

    async def _ensure_gateway_running(self) -> None:
        if not self.config.get("auto_start_gateway", True):
            return
        try:
            await self.client.health()
            return
        except Exception:
            pass
        if self.gateway_process and self.gateway_process.process:
            if self.gateway_process.process.returncode is None:
                return
        logger.info("Restarting WhatsApp Gateway after event stream interruption at %s", self._base_url)
        self.gateway_process = GatewayProcess(
            node_executable=str(self.config["node_executable"]),
            script_path=PLUGIN_DIR / "gateway" / "whatsapp-gateway.mjs",
            host=str(self.config["gateway_host"]),
            port=int(self.config["gateway_port"]),
            auth_dir=self._auth_dir(),
            log_level=str(self.config["log_level"]),
            data_dir=self._data_dir(),
        )
        await self.gateway_process.start()
        await self._wait_for_gateway()
        configured = await self.client.configure(self._gateway_config())
        logger.info("WhatsApp Gateway reconfigured after restart: %s", self._safe_status(configured))

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
        if event.get("type") != "status":
            logger.info("WhatsApp Gateway event: %s", self._safe_status(event))
            return

        current = (event.get("status"), event.get("ready"), event.get("selfJid"))
        if current == self._last_gateway_status_log:
            logger.debug("WhatsApp Gateway duplicate status event: %s", self._safe_status(event))
            return
        self._last_gateway_status_log = current
        logger.info("WhatsApp Gateway event: %s", self._safe_status(event))

    def _count_label(self, value: Any) -> str:
        if isinstance(value, list):
            return f"<{len(value)} entries>"
        return "<0 entries>" if value in (None, "") else "<1 entry>"
