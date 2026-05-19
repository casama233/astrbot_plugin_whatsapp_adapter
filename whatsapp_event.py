from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import File, Image, Plain, Record, Video
from astrbot.api.platform import AstrBotMessage, PlatformMetadata
from astrbot.core.utils.io import download_image_by_url
from astrbot import logger

from .whatsapp_client import WhatsAppGatewayClient


class WhatsAppMessageEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: WhatsAppGatewayClient,
        target_jid: str,
        quoted_message_id: str | None = None,
        text_chunk_limit: int = 4000,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client
        self.target_jid = target_jid
        self.quoted_message_id = quoted_message_id
        raw = message_obj.raw_message or {}
        self.quoted_participant = str(raw.get("senderJid") or "") or None
        self.text_chunk_limit = max(1, text_chunk_limit)

    async def send(self, message: MessageChain):
        logger.debug(
            "WhatsApp event send: target=%s quoted=%s components=%s",
            self.target_jid,
            bool(self.quoted_message_id),
            [component.__class__.__name__ for component in message.chain],
        )
        pending_caption: str | None = None
        for component in message.chain:
            if isinstance(component, Plain):
                text = component.text or ""
                if not text:
                    continue
                if pending_caption is None:
                    pending_caption = text
                else:
                    pending_caption += text
                for chunk in self._chunk_text(pending_caption):
                    logger.debug("WhatsApp event send text chunk: target=%s length=%s", self.target_jid, len(chunk))
                    await self.client.send_text(
                        self.target_jid,
                        chunk,
                        quoted_message_id=self.quoted_message_id,
                        quoted_participant=self.quoted_participant,
                    )
                pending_caption = None
            elif isinstance(component, Image):
                media_path = await self._resolve_media_path(component.file)
                logger.debug("WhatsApp event send image: target=%s path=%s", self.target_jid, media_path)
                await self.client.send_media(
                    self.target_jid,
                    "image",
                    media_path,
                    pending_caption,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                )
                pending_caption = None
            elif isinstance(component, Record):
                media_path = await self._resolve_media_path(component.file)
                logger.debug("WhatsApp event send audio: target=%s path=%s", self.target_jid, media_path)
                await self.client.send_media(
                    self.target_jid,
                    "audio",
                    media_path,
                    pending_caption,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                )
                pending_caption = None
            elif isinstance(component, Video):
                media_path = await self._resolve_media_path(component.file)
                logger.debug("WhatsApp event send video: target=%s path=%s", self.target_jid, media_path)
                await self.client.send_media(
                    self.target_jid,
                    "video",
                    media_path,
                    pending_caption,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                )
                pending_caption = None
            elif isinstance(component, File):
                media_path = await self._resolve_media_path(component.file or component.url)
                logger.debug("WhatsApp event send document: target=%s path=%s", self.target_jid, media_path)
                await self.client.send_media(
                    self.target_jid,
                    "document",
                    media_path,
                    pending_caption,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                )
                pending_caption = None

        if pending_caption:
            for chunk in self._chunk_text(pending_caption):
                logger.debug("WhatsApp event send trailing text chunk: target=%s length=%s", self.target_jid, len(chunk))
                await self.client.send_text(
                    self.target_jid,
                    chunk,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                )

        await super().send(message)

    async def react(self, emoji: str) -> None:
        if not self.quoted_message_id:
            return
        try:
            await self.client.react(self.target_jid, self.quoted_message_id, emoji, self.quoted_participant)
        except Exception as exc:
            logger.warning("WhatsApp event reaction failed: target=%s message_id=%s error=%s", self.target_jid, self.quoted_message_id, exc)

    async def _resolve_media_path(self, value: str) -> str:
        if not value:
            raise ValueError("empty media path")
        if value.startswith("file:///"):
            return value[8:]
        if value.startswith("http://") or value.startswith("https://"):
            return await download_image_by_url(value)
        return value

    def _chunk_text(self, text: str) -> list[str]:
        if len(text) <= self.text_chunk_limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            chunks.append(remaining[: self.text_chunk_limit])
            remaining = remaining[self.text_chunk_limit :]
        return chunks
