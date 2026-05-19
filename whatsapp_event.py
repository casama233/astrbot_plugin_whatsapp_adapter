from __future__ import annotations

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain, Record, Video
from astrbot.api.platform import AstrBotMessage, PlatformMetadata
from astrbot.core.utils.io import download_image_by_url

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
        self.text_chunk_limit = max(1, text_chunk_limit)

    async def send(self, message: MessageChain):
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
                    await self.client.send_text(
                        self.target_jid,
                        chunk,
                        quoted_message_id=self.quoted_message_id,
                    )
                pending_caption = None
            elif isinstance(component, Image):
                media_path = await self._resolve_media_path(component.file)
                await self.client.send_media(self.target_jid, "image", media_path, pending_caption)
                pending_caption = None
            elif isinstance(component, Record):
                media_path = await self._resolve_media_path(component.file)
                await self.client.send_media(self.target_jid, "audio", media_path, pending_caption)
                pending_caption = None
            elif isinstance(component, Video):
                media_path = await self._resolve_media_path(component.file)
                await self.client.send_media(self.target_jid, "video", media_path, pending_caption)
                pending_caption = None

        if pending_caption:
            for chunk in self._chunk_text(pending_caption):
                await self.client.send_text(
                    self.target_jid,
                    chunk,
                    quoted_message_id=self.quoted_message_id,
                )

        await super().send(message)

    async def _resolve_media_path(self, value: str) -> str:
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
