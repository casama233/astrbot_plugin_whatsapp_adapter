from __future__ import annotations

import asyncio
import os
from typing import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.platform import AstrBotMessage, At, PlatformMetadata
from astrbot.core.utils.io import download_image_by_url
from astrbot import logger

from .whatsapp_client import WhatsAppGatewayClient
from .whatsapp_helpers import (
    flush_pending_text as _flush_pending_text,
    format_whatsapp_markdown,
    mention_jid_from_at,
    mention_text_from_at,
    mentions_for_text,
    process_message_chain,
)


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
        media_caption_mode: str = "separate",
        link_preview_single_url: bool = True,
        typing_indicator: bool = True,
        remove_ack_after_reply: bool = False,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client
        self.target_jid = target_jid
        self.quoted_message_id = quoted_message_id
        raw = message_obj.raw_message or {}
        self.quoted_participant = str((raw.get("quoted") or {}).get("participant") or raw.get("senderJid") or "") or None
        self.text_chunk_limit = max(1, text_chunk_limit)
        self.media_caption_mode = media_caption_mode
        self.link_preview_single_url = link_preview_single_url
        self.typing_indicator = typing_indicator
        self._pre_acked = False
        self._remove_ack = remove_ack_after_reply
        self._super_sent = False
        self._temp_files: set[str] = set()

    async def send(self, message: MessageChain):
        logger.debug(
            "WhatsApp event send: target=%s quoted=%s components=%s",
            self.target_jid,
            bool(self.quoted_message_id),
            [component.__class__.__name__ for component in message.chain],
        )
        await self.send_typing()
        try:
            pending_caption, pending_mentions = await process_message_chain(
                self.client, self.target_jid, message.chain,
                link_preview_single_url=self.link_preview_single_url,
                text_chunk_limit=self.text_chunk_limit,
                use_caption=self.media_caption_mode == "caption",
                quoted_message_id=self.quoted_message_id,
                quoted_participant=self.quoted_participant,
                resolve_media_func=self._resolve_media_path,
            )
            await _flush_pending_text(
                self.client, self.target_jid, pending_caption, pending_mentions,
                link_preview_single_url=self.link_preview_single_url,
                text_chunk_limit=self.text_chunk_limit,
                quoted_message_id=self.quoted_message_id,
                quoted_participant=self.quoted_participant,
            )
        except Exception as exc:
            logger.warning("WhatsApp 事件发送完成但存在错误: target=%s error=%s", self.target_jid, exc)
            raise
        finally:
            await self.stop_typing()
            await super().send(message)
            self._super_sent = True
            if self._pre_acked and self._remove_ack:
                try:
                    await self.client.react(self.target_jid, self.quoted_message_id, "", self.quoted_participant)
                except Exception:
                    pass
            # 清理 URL 下載的暫存檔
            for tmp in self._temp_files:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self._temp_files.clear()

    async def send_streaming(self, generator: AsyncGenerator[MessageChain, None], use_fallback: bool = False):
        if use_fallback:
            await self._send_streaming_fallback(generator)
            return

        await self.send_typing()
        try:
            await self._send_streaming_edit(generator)
        except Exception as exc:
            logger.warning("WhatsApp 流式回复出错: target=%s error=%s", self.target_jid, exc)
        finally:
            if not self._super_sent:
                await super().send(MessageChain())
            await self.stop_typing()
            if self._pre_acked and self._remove_ack:
                try:
                    await self.client.react(self.target_jid, self.quoted_message_id, "", self.quoted_participant)
                except Exception:
                    pass

    async def _send_streaming_fallback(self, generator: AsyncGenerator[MessageChain, None]) -> None:
        async for chain in generator:
            if isinstance(chain, MessageChain) and chain.chain:
                await self.send(chain)

    async def _send_streaming_edit(self, generator: AsyncGenerator[MessageChain, None]) -> None:
        message_id: str | None = None
        text_buffer = ""
        last_sent = ""
        last_update = 0.0
        last_typing_update = 0.0
        mentions: list[str] = []
        throttle_seconds = 0.8
        max_edit_length = min(self.text_chunk_limit, 3500)

        async def publish(force: bool = False) -> None:
            nonlocal message_id, last_sent, last_update, text_buffer, mentions
            if not text_buffer or text_buffer == last_sent:
                return
            now = asyncio.get_running_loop().time()
            if not force and now - last_update < throttle_seconds:
                return
            chunk = text_buffer[:max_edit_length]
            if chunk == last_sent:
                return
            chunk_mentions = await mentions_for_text(self.client, self.target_jid, chunk, mentions)
            if not message_id:
                result = await self.client.send_text(
                    self.target_jid,
                    chunk,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                    link_preview=False,
                    mentions=chunk_mentions,
                )
                message_id = str(result.get("id") or "") or None
                if not message_id:
                    raise RuntimeError("Gateway did not return message id for streaming text")
            else:
                try:
                    await self.client.edit_text(self.target_jid, message_id, chunk, mentions=chunk_mentions)
                except Exception as exc:
                    logger.debug("WhatsApp 流式编辑失败，改发新消息: target=%s message_id=%s error=%s", self.target_jid, message_id, exc)
                    result = await self.client.send_text(
                        self.target_jid,
                        chunk,
                        quoted_message_id=self.quoted_message_id,
                        quoted_participant=self.quoted_participant,
                        link_preview=False,
                        mentions=chunk_mentions,
                    )
                    message_id = str(result.get("id") or "") or None
            last_sent = chunk
            last_update = now
            # 如果緩衝區超過單次編輯上限，循環推送剩餘內容
            while len(text_buffer) > max_edit_length:
                text_buffer = text_buffer[max_edit_length:]
                message_id = None
                last_sent = ""
                chunk = text_buffer[:max_edit_length]
                if not chunk or chunk == last_sent:
                    break
                new_mentions = await mentions_for_text(self.client, self.target_jid, chunk, mentions)
                result = await self.client.send_text(
                    self.target_jid,
                    chunk,
                    quoted_message_id=self.quoted_message_id,
                    quoted_participant=self.quoted_participant,
                    link_preview=False,
                    mentions=new_mentions,
                )
                message_id = str(result.get("id") or "") or None
                last_sent = chunk
                last_update = asyncio.get_running_loop().time()

        async for chain in generator:
            if not isinstance(chain, MessageChain):
                continue
            if getattr(chain, "type", None) == "break":
                await publish(force=True)
                message_id = None
                text_buffer = ""
                last_sent = ""
                mentions = []
                continue
            media_chain = MessageChain()
            for component in chain.chain:
                if isinstance(component, Plain):
                    text_buffer += format_whatsapp_markdown(component.text or "")
                elif isinstance(component, At):
                    jid = mention_jid_from_at(component)
                    if jid:
                        mentions.append(jid)
                    text_buffer += mention_text_from_at(component)
                else:
                    media_chain.chain.append(component)
            await publish(force=False)
            now = asyncio.get_running_loop().time()
            if now - last_typing_update >= 10:
                await self.send_typing()
                last_typing_update = now
            if media_chain.chain:
                await publish(force=True)
                await self.send(media_chain)
                message_id = None
                text_buffer = ""
                last_sent = ""
                mentions = []

        await publish(force=True)

    async def send_typing(self) -> None:
        if not self.typing_indicator:
            return
        try:
            await self.client.send_presence(self.target_jid, "composing")
        except Exception as exc:
            logger.debug("WhatsApp 输入状态更新失败: target=%s error=%s", self.target_jid, exc)

    async def stop_typing(self) -> None:
        if not self.typing_indicator:
            return
        try:
            await self.client.send_presence(self.target_jid, "available")
        except Exception as exc:
            logger.debug("WhatsApp 停止输入状态更新失败: target=%s error=%s", self.target_jid, exc)

    async def edit_message(self, message_id: str, text: str, participant: str | None = None) -> dict:
        """編輯先前由本機器人發送的文字訊息。"""
        return await self.client.edit_text(
            self.target_jid,
            message_id,
            format_whatsapp_markdown(text or ""),
            participant=participant,
        )

    async def react(self, emoji: str) -> None:
        if not self.quoted_message_id:
            return
        try:
            await self.client.react(self.target_jid, self.quoted_message_id, emoji, self.quoted_participant)
        except Exception as exc:
            logger.warning("WhatsApp 表情回应发送失败: target=%s message_id=%s error=%s", self.target_jid, self.quoted_message_id, exc)

    async def _resolve_media_path(self, value: str | None) -> str:
        if not value:
            raise ValueError("empty media path")
        if value.startswith("file://"):
            return "/" + value.removeprefix("file:").lstrip("/")
        if value.startswith("http://") or value.startswith("https://"):
            local_path = await download_image_by_url(value)
            self._temp_files.add(local_path)
            return local_path
        return value

