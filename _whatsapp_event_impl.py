from __future__ import annotations

import asyncio
import inspect
import os
import re
import textwrap
from dataclasses import dataclass, field
from typing import AsyncGenerator, Callable
from urllib.parse import unquote

from astrbot import logger

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain, Reply
from astrbot.api.platform import AstrBotMessage, At, PlatformMetadata
from astrbot.core.utils.io import download_image_by_url
from astrbot.core.utils.metrics import Metric

try:
    from astrbot.core.pipeline.context import call_event_hook as _call_event_hook
    from astrbot.core.star.star_handler import EventType as _EventType
except ImportError:  # AstrBot compatibility stubs and older supported builds.
    _call_event_hook = None
    _EventType = None


def _needs_streaming_after_hook_compat(source: str | None = None) -> bool:
    """Detect whether Core returns before its post-send hook for streams.

    AstrBot 4.27.2 (and current upstream) calls ``send_streaming`` and then
    immediately returns, leaving the later ``OnAfterMessageSentEvent`` hook
    unreachable.  Inspecting the actual Core control flow is safer than
    guessing which future version might fix it.  If source inspection is not
    available, retain the compatibility hook because skipping it is the known
    failure mode on every currently supported build.
    """

    if source is None:
        try:
            from astrbot.core.pipeline.respond.stage import RespondStage

            source = inspect.getsource(RespondStage.process)
        except (ImportError, OSError, TypeError):
            return True
    normalized = textwrap.dedent(str(source or ""))
    streaming_call = re.search(r"await\s+event\.send_streaming\s*\(", normalized)
    if streaming_call is None:
        return True
    remainder = normalized[streaming_call.end() :]
    hook_offset = remainder.find("OnAfterMessageSentEvent")
    first_return = re.search(r"(?m)^[ \t]*return(?:\s|$)", remainder)
    core_handles_hook = hook_offset >= 0 and (
        first_return is None or hook_offset < first_return.start()
    )
    return not core_handles_hook


_STREAMING_AFTER_HOOK_COMPAT = _needs_streaming_after_hook_compat()

from .whatsapp_client import WhatsAppGatewayClient
from .whatsapp_helpers import (
    MentionRef,
    QuoteState,
    flush_pending_text as _flush_pending_text,
    format_whatsapp_markdown,
    has_visible_whatsapp_content,
    mention_jid_from_at,
    mention_text_from_at,
    mentions_for_text,
    process_message_chain,
    split_whatsapp_text,
)


@dataclass(slots=True)
class _StreamingTextState:
    raw: str = ""
    mentions: list[MentionRef] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    rendered_chunks: list[str] = field(default_factory=list)
    last_update: float = 0.0
    edit_failed: bool = False
    fallback_raw_offset: int = 0
    uneditable_sent_offset: int = 0
    final_fallback_sent: bool = False

    def reset(self) -> None:
        self.raw = ""
        self.mentions.clear()
        self.message_ids.clear()
        self.rendered_chunks.clear()
        self.last_update = 0.0
        self.edit_failed = False
        self.fallback_raw_offset = 0
        self.uneditable_sent_offset = 0
        self.final_fallback_sent = False


class WhatsAppMessageEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
        client: WhatsAppGatewayClient,
        target_jid: str,
        source_message_id: str | None = None,
        text_chunk_limit: int = 4000,
        media_caption_mode: str = "separate",
        link_preview_single_url: bool = True,
        typing_indicator: bool = True,
        ack_done_emoji: str = "",
        unsupported_streaming_strategy: str = "",
        streaming_edit_throttle: float = 1.0,
        mention_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client
        self.target_jid = target_jid
        self.source_message_id = source_message_id
        raw = message_obj.raw_message or {}
        self.source_participant = str(raw.get("senderJid") or "") or None
        self.text_chunk_limit = max(1, text_chunk_limit)
        self.media_caption_mode = media_caption_mode
        self.link_preview_single_url = link_preview_single_url
        self.typing_indicator = typing_indicator
        self.mention_resolver = mention_resolver
        # Kept as a constructor keyword for compatibility with older adapter
        # instances. AstrBot resolves the provider-level strategy and passes the
        # result to ``send_streaming(..., use_fallback=...)`` for every stream.
        self.streaming_edit_throttle = max(0.1, streaming_edit_throttle)
        self._pre_acked = False
        self._done_emoji = ack_done_emoji or "✅"
        self._super_sent = False
        self._stream_after_hook_called = False
        self._temp_files: set[str] = set()

    async def send(self, message: MessageChain):
        quoted_message_id, quoted_participant = self._quote_target(message)
        quote_state = QuoteState(quoted_message_id, quoted_participant)
        logger.debug(
            "WhatsApp event send: target=%s quoted=%s components=%s",
            self.target_jid,
            bool(quoted_message_id),
            [component.__class__.__name__ for component in message.chain],
        )
        await self.send_typing()
        try:
            pending_caption, pending_mentions = await process_message_chain(
                self.client,
                self.target_jid,
                message.chain,
                link_preview_single_url=self.link_preview_single_url,
                text_chunk_limit=self.text_chunk_limit,
                use_caption=self.media_caption_mode == "caption",
                quoted_message_id=quoted_message_id,
                quoted_participant=quoted_participant,
                resolve_media_func=self._resolve_media_path,
                mention_resolver=self.mention_resolver,
                quote_state=quote_state,
            )
            await _flush_pending_text(
                self.client,
                self.target_jid,
                pending_caption,
                pending_mentions,
                link_preview_single_url=self.link_preview_single_url,
                text_chunk_limit=self.text_chunk_limit,
                quote_state=quote_state,
            )
            if quote_state.sent_count == 0:
                raise RuntimeError("WhatsApp message produced no deliverable content")
            await super().send(message)
            self._super_sent = True
            await self._complete_pre_ack()
        except Exception as exc:
            logger.warning("WhatsApp 事件发送失败: target=%s error=%s", self.target_jid, exc)
            if quote_state.sent_count and not self._super_sent:
                self._mark_streaming_sent()
            await self._clear_pre_ack()
            raise
        finally:
            await self.stop_typing()
            for temporary in self._temp_files:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
            self._temp_files.clear()

    async def _notify_streaming_after_message_sent(self) -> None:
        """Restore AstrBot's post-send hook contract for streaming delivery.

        AstrBot 4.27's RespondStage returns immediately after ``send_streaming``
        and therefore skips ``OnAfterMessageSentEvent``.  Plugins such as
        AngelHeart use that hook to cancel patience messages and release their
        per-chat processing lock.  Dispatch it once after a real WhatsApp
        delivery, including a partially delivered stream that later failed.
        """

        if (
            not _STREAMING_AFTER_HOOK_COMPAT
            or self._stream_after_hook_called
            or not self._super_sent
            or _call_event_hook is None
            or _EventType is None
        ):
            return
        self._stream_after_hook_called = True
        try:
            await _call_event_hook(self, _EventType.OnAfterMessageSentEvent)
        except Exception as exc:
            logger.warning(
                "WhatsApp 流式发送后钩子执行失败: target=%s error=%s",
                self.target_jid,
                exc,
            )

    async def send_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ):
        logger.info(
            "WhatsApp 进入流式回复: target=%s use_fallback=%s",
            self.target_jid,
            use_fallback,
        )
        await self.send_typing()
        sent = False
        try:
            sent = await self._send_streaming_edit(
                generator,
                use_fallback=use_fallback,
            )
            if sent and not self._super_sent:
                self._mark_streaming_sent()
            if sent:
                await self._complete_pre_ack()
            else:
                await self._clear_pre_ack()
        except asyncio.CancelledError:
            await self._clear_pre_ack()
            raise
        except Exception as exc:
            logger.warning("WhatsApp 流式回复出错: target=%s error=%s", self.target_jid, exc)
            await self._clear_pre_ack()
            raise
        finally:
            await self.stop_typing()
            for temporary in self._temp_files:
                try:
                    os.remove(temporary)
                except OSError:
                    pass
            self._temp_files.clear()
            await self._notify_streaming_after_message_sent()

    async def _complete_pre_ack(self) -> None:
        if not self._pre_acked:
            return
        try:
            await self.client.react(
                self.target_jid,
                self.source_message_id,
                self._done_emoji,
                self.source_participant,
            )
            self._pre_acked = False
        except Exception:
            pass

    async def _clear_pre_ack(self) -> None:
        if not self._pre_acked:
            return
        try:
            await self.client.react(
                self.target_jid,
                self.source_message_id,
                "",
                self.source_participant,
            )
            self._pre_acked = False
        except Exception:
            pass

    def _mark_streaming_sent(self) -> None:
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.platform_meta.name),
        )
        self._has_send_oper = True
        self._super_sent = True

    async def _send_streaming_fallback(
        self,
        generator: AsyncGenerator[MessageChain, None],
    ) -> None:
        async for chain in generator:
            if isinstance(chain, MessageChain) and chain.chain:
                await self.send(chain)

    async def _send_streaming_edit(
        self,
        generator: AsyncGenerator[MessageChain, None],
        *,
        use_fallback: bool = False,
    ) -> bool:
        state = _StreamingTextState()
        quote_state = QuoteState()
        delivered = False
        throttle_seconds = self.streaming_edit_throttle
        max_edit_length = min(self.text_chunk_limit, 3500)
        realtime_fallback = bool(use_fallback)
        last_typing_update = 0.0

        async def send_new(text: str, mentions: list[str]) -> str | None:
            nonlocal delivered
            quote_kwargs = quote_state.kwargs()
            result = await self.client.send_text(
                self.target_jid,
                text,
                link_preview=False,
                mentions=mentions,
                **quote_kwargs,
            )
            quote_state.consume()
            delivered = True
            if not self._super_sent:
                # Record delivery immediately. If the token generator fails
                # afterwards, AstrBot must not treat the partial reply as an
                # unsent response and emit a duplicate fallback.
                self._mark_streaming_sent()
            return str((result or {}).get("id") or "") or None

        async def send_rendered_chunks(chunks: list[str]) -> None:
            for chunk in chunks:
                if not has_visible_whatsapp_content(chunk):
                    continue
                chunk_mentions = await mentions_for_text(
                    self.client,
                    self.target_jid,
                    chunk,
                    state.mentions,
                )
                await send_new(chunk, chunk_mentions)

        async def publish_realtime_fallback(*, force: bool) -> None:
            pending = state.raw[state.fallback_raw_offset :]
            if not pending:
                return
            if force:
                cut = len(pending)
            else:
                boundaries = list(re.finditer(r"[。？！!?\n]+", pending))
                if not boundaries:
                    return
                cut = boundaries[-1].end()
            stable_raw = pending[:cut]
            rendered = format_whatsapp_markdown(stable_raw, streaming=not force)
            chunks = split_whatsapp_text(
                rendered,
                max_edit_length,
                atomic_texts=[mention.text for mention in state.mentions if mention.text],
            )
            await send_rendered_chunks(chunks)
            state.fallback_raw_offset += cut

        async def publish_final_fallback() -> None:
            if state.final_fallback_sent:
                return
            state.final_fallback_sent = True
            if realtime_fallback:
                await publish_realtime_fallback(force=True)
                return
            raw = (
                state.raw[state.uneditable_sent_offset :]
                if state.uneditable_sent_offset
                else state.raw
            )
            if not raw:
                return
            rendered = format_whatsapp_markdown(raw, streaming=False)
            await send_rendered_chunks(
                split_whatsapp_text(
                    rendered,
                    max_edit_length,
                    atomic_texts=[mention.text for mention in state.mentions if mention.text],
                ),
            )

        async def publish(*, force: bool = False) -> None:
            if not state.raw:
                return

            now = asyncio.get_running_loop().time()
            # 先節流再渲染，避免每個 token 都重掃整個累積 Markdown。
            if not force and now - state.last_update < throttle_seconds:
                return

            if state.edit_failed:
                if force:
                    await publish_final_fallback()
                elif realtime_fallback:
                    await publish_realtime_fallback(force=False)
                state.last_update = now
                return

            rendered = format_whatsapp_markdown(state.raw, streaming=not force)
            chunks = [
                chunk
                for chunk in split_whatsapp_text(
                    rendered,
                    max_edit_length,
                    atomic_texts=[mention.text for mention in state.mentions if mention.text],
                )
                if has_visible_whatsapp_content(chunk)
            ]
            if not chunks or chunks == state.rendered_chunks:
                state.last_update = now
                return

            # 單調增長的 raw stream 正常不應令已送出的 chunk 數量縮短。
            # 若轉換結構回溯造成縮短，停止 edit 並在結束時完整補發，避免殘留舊 chunk。
            if len(chunks) < len(state.rendered_chunks):
                logger.warning(
                    "WhatsApp 流式分段回溯，停止编辑并等待最终补发: target=%s old=%s new=%s",
                    self.target_jid,
                    len(state.rendered_chunks),
                    len(chunks),
                )
                state.edit_failed = True
                if force:
                    await publish_final_fallback()
                state.last_update = now
                return

            for index, chunk in enumerate(chunks):
                previous = state.rendered_chunks[index] if index < len(state.rendered_chunks) else None
                if previous == chunk:
                    continue
                chunk_mentions = await mentions_for_text(
                    self.client,
                    self.target_jid,
                    chunk,
                    state.mentions,
                )
                try:
                    if index < len(state.message_ids):
                        message_id = state.message_ids[index]
                        if not message_id:
                            raise RuntimeError("streaming message id unavailable")
                        await self.client.edit_text(
                            self.target_jid,
                            message_id,
                            chunk,
                            mentions=chunk_mentions,
                        )
                    else:
                        message_id = await send_new(chunk, chunk_mentions)
                        state.message_ids.append(message_id or "")
                        if not message_id:
                            # Delivery succeeded, but the message cannot be
                            # edited. Remember exactly which raw prefix is
                            # already visible so final fallback sends only the
                            # unseen suffix instead of duplicating the message.
                            state.uneditable_sent_offset = len(state.raw)
                            state.fallback_raw_offset = len(state.raw)
                            raise RuntimeError("Gateway did not return message id for streaming text")
                except Exception as exc:
                    logger.warning(
                        "WhatsApp 流式编辑不可用，停止中途更新并在结束时补发: target=%s error=%s",
                        self.target_jid,
                        exc,
                    )
                    state.edit_failed = True
                    if force:
                        await publish_final_fallback()
                    state.last_update = now
                    return

            state.rendered_chunks = chunks
            # Raw content up to this point is visible through a successful
            # send/edit. Realtime fallback must start here if a later edit
            # fails, otherwise the failed increment is silently skipped.
            state.fallback_raw_offset = len(state.raw)
            state.last_update = asyncio.get_running_loop().time()

        async def send_transport_component(component) -> None:
            nonlocal delivered
            before = quote_state.sent_count
            try:
                pending, mentions = await process_message_chain(
                    self.client,
                    self.target_jid,
                    [component],
                    link_preview_single_url=self.link_preview_single_url,
                    text_chunk_limit=self.text_chunk_limit,
                    use_caption=False,
                    resolve_media_func=self._resolve_media_path,
                    mention_resolver=self.mention_resolver,
                    quote_state=quote_state,
                )
                await _flush_pending_text(
                    self.client,
                    self.target_jid,
                    pending,
                    mentions,
                    link_preview_single_url=self.link_preview_single_url,
                    text_chunk_limit=self.text_chunk_limit,
                    quote_state=quote_state,
                )
            finally:
                if quote_state.sent_count > before:
                    delivered = True
                    if not self._super_sent:
                        self._mark_streaming_sent()

        async for chain in generator:
            if not isinstance(chain, MessageChain):
                continue
            if getattr(chain, "type", None) == "break":
                await publish(force=True)
                state.reset()
                continue

            for component in chain.chain:
                if isinstance(component, Plain):
                    state.raw += component.text or ""
                elif isinstance(component, Reply):
                    # Reply is transport metadata: attach it to the first
                    # successful physical send only, like normal/QQ replies.
                    if quote_state.sent_count == 0 and not quote_state.message_id:
                        message_id, participant = self._quote_target(
                            MessageChain([component]),
                        )
                        quote_state.message_id = message_id
                        quote_state.participant = participant
                    continue
                elif isinstance(component, At):
                    visible = mention_text_from_at(component)
                    jid = mention_jid_from_at(component, self.mention_resolver)
                    # QQ/OneBot 发送 At 后会补空格；流式与普通发送保持相同语义。
                    state.raw += visible + " "
                    if jid:
                        state.mentions.append(MentionRef(jid=jid, text=visible))
                else:
                    await publish(force=True)
                    state.reset()
                    await send_transport_component(component)

            await publish(force=False)
            now = asyncio.get_running_loop().time()
            if now - last_typing_update >= 10:
                await self.send_typing()
                last_typing_update = now

        await publish(force=True)
        return delivered

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
            await self.client.send_presence(self.target_jid, "paused")
        except Exception as exc:
            logger.debug("WhatsApp 停止输入状态更新失败: target=%s error=%s", self.target_jid, exc)

    async def edit_message(
        self,
        message_id: str,
        text: str,
        participant: str | None = None,
    ) -> dict:
        return await self.client.edit_text(
            self.target_jid,
            message_id,
            format_whatsapp_markdown(text or ""),
            participant=participant,
        )

    async def react(self, emoji: str) -> None:
        if not self.source_message_id:
            return
        try:
            await self.client.react(
                self.target_jid,
                self.source_message_id,
                emoji,
                self.source_participant,
            )
        except Exception as exc:
            logger.warning(
                "WhatsApp 表情回应发送失败: target=%s message_id=%s error=%s",
                self.target_jid,
                self.source_message_id,
                exc,
            )

    def _quote_target(self, message: MessageChain) -> tuple[str | None, str | None]:
        """Mirror Telegram: quote only when the outgoing chain contains Reply."""
        for component in message.chain:
            if not isinstance(component, Reply):
                continue
            message_id = str(getattr(component, "id", "") or "")
            if not message_id:
                return None, None
            if message_id == self.source_message_id:
                return message_id, self.source_participant
            participant = str(
                getattr(component, "sender_id", "")
                or getattr(component, "qq", "")
                or ""
            )
            # Numeric AstrBot user IDs are not valid WhatsApp participants. The
            # Gateway will recover the participant from its chat-scoped cache.
            return message_id, participant if "@" in participant else None
        return None, None

    async def _resolve_media_path(self, value: str | None) -> str:
        if not value:
            raise ValueError("empty media path")
        if value.startswith("file://"):
            return unquote("/" + value.removeprefix("file:").lstrip("/"))
        if value.startswith("http://") or value.startswith("https://"):
            local_path = await download_image_by_url(value)
            self._temp_files.add(local_path)
            return local_path
        return value
