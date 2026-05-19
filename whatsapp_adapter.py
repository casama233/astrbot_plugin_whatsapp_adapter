from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain, Record, Video
from astrbot.api.platform import (
    AstrBotMessage,
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
    "text_chunk_limit": 4000,
    "media_max_mb": 50,
}


@register_platform_adapter("whatsapp", "WhatsApp Web Gateway 适配器", default_config_tmpl=DEFAULT_CONFIG)
class WhatsAppPlatformAdapter(Platform):
    def __init__(
        self,
        platform_config: dict[str, Any],
        platform_settings: dict[str, Any],
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(event_queue)
        self.config = {**DEFAULT_CONFIG, **(platform_config or {})}
        self.settings = platform_settings
        self.client = WhatsAppGatewayClient(self._base_url)
        self.gateway_process: GatewayProcess | None = None
        self._stopped = asyncio.Event()

    @property
    def _base_url(self) -> str:
        return f"http://{self.config['gateway_host']}:{int(self.config['gateway_port'])}"

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata("whatsapp", "WhatsApp Web Gateway 适配器")

    async def send_by_session(self, session: MessageSesion, message_chain: MessageChain):
        target = getattr(session, "session_id", None) or getattr(session, "message_session_id", None)
        if not target:
            await super().send_by_session(session, message_chain)
            return

        for component in message_chain.chain:
            if isinstance(component, Plain):
                for chunk in self._chunk_text(component.text or ""):
                    await self.client.send_text(target, chunk)
            elif isinstance(component, Image):
                await self.client.send_media(target, "image", component.file)
            elif isinstance(component, Record):
                await self.client.send_media(target, "audio", component.file)
            elif isinstance(component, Video):
                await self.client.send_media(target, "video", component.file)

        await super().send_by_session(session, message_chain)

    async def run(self):
        await self.client.start()
        if self.config.get("auto_start_gateway", True):
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
        await self.client.configure(self._gateway_config())
        logger.info("WhatsApp adapter connected to Gateway at %s", self._base_url)

        while not self._stopped.is_set():
            try:
                async for event in self.client.events():
                    if event.get("type") == "message":
                        abm = await self.convert_message(event)
                        if abm:
                            await self.handle_msg(abm)
                    elif event.get("type") in {"qr", "status"}:
                        logger.info("WhatsApp Gateway event: %s", event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WhatsApp Gateway event stream interrupted: %s", exc)
                await asyncio.sleep(3)

    async def terminate(self):
        self._stopped.set()
        await self.client.close()
        if self.gateway_process:
            await self.gateway_process.stop()

    async def convert_message(self, data: dict[str, Any]) -> AstrBotMessage | None:
        if data.get("fromMe"):
            return None

        chat_jid = str(data.get("chatJid") or "")
        sender_jid = str(data.get("senderJid") or chat_jid)
        text = str(data.get("text") or "")
        is_group = chat_jid.endswith("@g.us")

        abm = AstrBotMessage()
        abm.type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
        abm.group_id = chat_jid if is_group else None
        abm.message_str = text
        abm.sender = MessageMember(
            user_id=sender_jid,
            nickname=str(data.get("senderName") or sender_jid),
        )
        abm.message = self._message_chain(data, text)
        abm.raw_message = data
        abm.self_id = str(data.get("selfJid") or "whatsapp")
        abm.session_id = chat_jid
        abm.message_id = str(data.get("messageId") or "")
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
        self.commit_event(event)

    def _message_chain(self, data: dict[str, Any], text: str) -> list[Any]:
        chain: list[Any] = []
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
            else:
                chain.append(Plain(text=f"<media:{media_type or 'unknown'}> {path}"))
        if not chain:
            chain.append(Plain(text=""))
        return chain

    def _gateway_config(self) -> dict[str, Any]:
        return {
            "dmPolicy": self.config.get("dm_policy", "allowlist"),
            "allowFrom": self.config.get("allow_from") or [],
            "groupPolicy": self.config.get("group_policy", "disabled"),
            "groupAllowFrom": self.config.get("group_allow_from") or [],
            "groups": self.config.get("groups") or [],
            "sendReadReceipts": bool(self.config.get("send_read_receipts", True)),
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

    async def _wait_for_gateway(self) -> None:
        last_error: Exception | None = None
        for _ in range(60):
            try:
                await self.client.health()
                return
            except (OSError, WhatsAppGatewayError, asyncio.TimeoutError, Exception) as exc:
                last_error = exc
                await asyncio.sleep(1)
        raise WhatsAppGatewayError(f"WhatsApp Gateway did not become healthy: {last_error}")
