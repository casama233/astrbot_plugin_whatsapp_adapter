"""WhatsApp 平台共享輔助函數，減少 whatsapp_adapter.py 與 whatsapp_event.py 之間的重複代碼。"""

from __future__ import annotations

import re
from typing import Any, Callable, Coroutine, Iterator

from astrbot import logger
from astrbot.api.platform import At
from astrbot.api.message_components import File, Image, Plain, Record, Video

from .whatsapp_client import WhatsAppGatewayClient
from .whatsapp_components import WhatsAppButton, WhatsAppButtons, WhatsAppEdit, WhatsAppList, WhatsAppPoll

__all__ = [
    "chunk_text",
    "flush_pending_text",
    "format_markdown_from_whatsapp",
    "format_whatsapp_markdown",
    "is_single_url",
    "media_kind_from_component",
    "mention_jid_for_token",
    "mention_jid_from_at",
    "mention_text_from_at",
    "mentions_for_text",
    "normalize_media_value",
    "process_message_chain",
    "send_whatsapp_component",
    "should_link_preview",
]


def format_whatsapp_markdown(text: str) -> str:
    """出站 Markdown → WhatsApp 格式。"""
    text = re.sub(r"\*\*([^*\n][\s\S]*?[^*\n])\*\*", r"*\1*", text)
    text = re.sub(r"__([^_\n][\s\S]*?[^_\n])__", r"_\1_", text)
    text = re.sub(r"~~([^~\n][\s\S]*?[^~\n])~~", r"~\1~", text)
    text = re.sub(r"(?<!`)`([^`\n]+)`(?!`)", r"```\1```", text)
    return text


def format_markdown_from_whatsapp(text: str) -> str:
    """入站 WhatsApp 格式 → Markdown。"""
    text = re.sub(r"```([^`\n]+)```", r"`\1`", text)
    text = re.sub(r"(?<!\*)\*([^*\n][\s\S]*?[^*\n])\*(?!\*)", r"**\1**", text)
    text = re.sub(r"(?<!_)_([^_\n][\s\S]*?[^_\n])_(?!_)", r"*\1*", text)
    text = re.sub(r"(?<!~)~([^~\n][\s\S]*?[^~\n])~(?!~)", r"~~\1~~", text)
    return text


def chunk_text(text: str, limit: int) -> Iterator[str]:
    """將文字按 limit 大小切片，返回 lazy generator。"""
    if len(text) <= limit:
        yield text
    else:
        remaining = text
        while remaining:
            yield remaining[:limit]
            remaining = remaining[limit:]


def is_single_url(text: str) -> bool:
    """是否為單一 URL 訊息。"""
    value = str(text or "").strip()
    return (value.startswith("http://") or value.startswith("https://")) and len(value.split()) == 1


def should_link_preview(text: str, link_preview_single_url: bool) -> bool:
    """根據配置判斷是否啟用連結預覽。"""
    return bool(link_preview_single_url) and is_single_url(text)


def normalize_media_value(value: str | None) -> str:
    """正規化媒體路徑，處理 file:// 前綴。"""
    if not value:
        return ""
    if value.startswith("file://"):
        return "/" + value.removeprefix("file:").lstrip("/")
    return value


def mention_text_from_at(component: At) -> str:
    """從 At 元件產生 @文字。"""
    value = str(getattr(component, "name", "") or getattr(component, "qq", "") or "")
    value = value.split("@", 1)[0].split(":", 1)[0]
    return f"@{value}"


def mention_jid_from_at(component: At) -> str | None:
    """從 At 元件解析完整 JID。優先使用已有 @ 的值，否則 fallback 到數字 + @s.whatsapp.net。"""
    value = str(getattr(component, "qq", "") or getattr(component, "name", "") or "").strip()
    if not value:
        return None
    if "@" in value:
        if value.endswith("@lid"):
            from .whatsapp_adapter import _LID_PN_CACHE
            pn = _LID_PN_CACHE.get(value)
            if pn:
                return pn
        return value
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits:
        logger.warning(
            "WhatsApp mention resolved via numeric fallback: value=%s jid=%s@s.whatsapp.net",
            value,
            digits,
        )
        return f"{digits}@s.whatsapp.net"
    logger.warning("WhatsApp @提及 JID 解析失败: value=%s", value)
    return None


def mention_jid_for_token(token: str) -> str:
    """純文字 @token → 數字 + @s.whatsapp.net。"""
    digits = "".join(ch for ch in token if ch.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else ""


async def mentions_for_text(
    client: WhatsAppGatewayClient,
    target: str,
    text: str,
    explicit_mentions: list[str],
) -> list[str]:
    """回傳顯式提及（At 元件）清單，不解析純文字 @token。"""
    return list(dict.fromkeys(explicit_mentions))


def media_kind_from_component(component: Any, default: str) -> str:
    """偵測元件是否為 sticker，否則返回 default。"""
    kind = str(getattr(component, "type", "") or getattr(component, "_type", "") or "").lower()
    name = str(getattr(component, "name", "") or getattr(component, "filename", "") or getattr(component, "file", "") or "").lower()
    if kind == "sticker" or kind.endswith("sticker") or (name.endswith(".webp") and "sticker" in name):
        return "sticker"
    return default


async def flush_pending_text(
    client: WhatsAppGatewayClient,
    target: str,
    pending: str | None,
    mentions: list[str] | None = None,
    *,
    link_preview_single_url: bool = True,
    text_chunk_limit: int = 4000,
    quoted_message_id: str | None = None,
    quoted_participant: str | None = None,
) -> tuple[None, list[str]]:
    """將累積的文字 flush 發送到 WhatsApp。"""
    if not pending:
        return None, mentions or []
    for chunk in chunk_text(pending, text_chunk_limit):
        chunk_mentions = await mentions_for_text(client, target, chunk, mentions or [])
        await client.send_text(
            target,
            chunk,
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
            link_preview=should_link_preview(chunk, link_preview_single_url),
            mentions=chunk_mentions,
        )
    return None, []


async def send_whatsapp_component(
    client: WhatsAppGatewayClient,
    target: str,
    component: WhatsAppButtons | WhatsAppList | WhatsAppPoll | WhatsAppEdit,
    quoted_message_id: str | None = None,
    quoted_participant: str | None = None,
) -> None:
    """發送 WhatsApp 專用元件：按鈕、清單、投票、編輯。"""
    if isinstance(component, WhatsAppButtons):
        buttons = [
            {"id": btn.id or f"btn_{i}", "text": btn.text}
            for i, btn in enumerate(component.buttons or [])
        ]
        await client.send_buttons(
            target, component.body, buttons,
            footer=component.footer,
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
        )
    elif isinstance(component, WhatsAppList):
        sections = []
        for section in component.sections or []:
            rows = [
                {"id": row.id, "title": row.title, "description": row.description}
                for row in (section.rows or [])
            ]
            sections.append({"title": section.title, "rows": rows})
        await client.send_list(
            target, component.title, sections,
            description=component.description,
            button_text=component.button_text,
            footer=component.footer,
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
        )
    elif isinstance(component, WhatsAppPoll):
        await client.send_poll(
            target, component.name, list(component.options or []),
            selectable_count=int(component.selectable_count or 0),
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
        )
    elif isinstance(component, WhatsAppEdit):
        await client.edit_text(
            target, component.message_id,
            format_whatsapp_markdown(component.text or ""),
            participant=component.participant,
        )


MediaResolver = Callable[[str | None], Coroutine[Any, Any, str]]


async def process_message_chain(
    client: WhatsAppGatewayClient,
    target: str,
    chain: list,
    *,
    link_preview_single_url: bool = True,
    text_chunk_limit: int = 4000,
    use_caption: bool = False,
    quoted_message_id: str | None = None,
    quoted_participant: str | None = None,
    resolve_media_func: MediaResolver | None = None,
) -> tuple[str | None, list[str]]:
    """處理訊息鏈中的每個 component，累積 caption 文字與 mentions。

    兩個地方使用此函數，消除 send() 與 send_by_session() 的重複。
    返回剩餘的 (pending_caption, pending_mentions)，呼叫方應再呼叫 flush_pending_text。
    """
    pending_caption: str | None = None
    pending_mentions: list[str] = []
    _flush_kw: dict[str, Any] = dict(
        link_preview_single_url=link_preview_single_url,
        text_chunk_limit=text_chunk_limit,
        quoted_message_id=quoted_message_id,
        quoted_participant=quoted_participant,
    )
    _send_kw: dict[str, Any] = {}
    if quoted_message_id:
        _send_kw["quoted_message_id"] = quoted_message_id
    if quoted_participant:
        _send_kw["quoted_participant"] = quoted_participant

    async def _resolve(value: str | None) -> str:
        if resolve_media_func:
            return await resolve_media_func(value)
        return normalize_media_value(value or "") or ""

    for component in chain:
        if isinstance(component, Plain):
            text = format_whatsapp_markdown(component.text or "")
            if not text:
                continue
            if use_caption:
                pending_caption = (pending_caption or "") + text
            else:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, (pending_caption or "") + text, pending_mentions, **_flush_kw,
                )
        elif isinstance(component, At):
            jid = mention_jid_from_at(component)
            if jid:
                pending_mentions.append(jid)
            text = mention_text_from_at(component)
            if use_caption:
                pending_caption = (pending_caption or "") + text
            else:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, (pending_caption or "") + text, pending_mentions, **_flush_kw,
                )
        elif isinstance(component, Image):
            if not use_caption:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, pending_caption, pending_mentions, **_flush_kw,
                )
            try:
                media_path = await _resolve(component.file)
            except ValueError as exc:
                logger.warning("WhatsApp 消息链跳过图片: %s", exc)
                continue
            if not media_path:
                logger.warning("WhatsApp 消息链跳过图片: 文件路径为空")
                continue
            media_kind = media_kind_from_component(component, "image")
            await client.send_media(
                target, media_kind, media_path,
                None if media_kind == "sticker" else pending_caption if use_caption else None,
                **_send_kw,
            )
            pending_caption = None
            pending_mentions = []
        elif isinstance(component, Record):
            pending_caption, pending_mentions = await flush_pending_text(
                client, target, pending_caption, pending_mentions, **_flush_kw,
            )
            try:
                media_path = await _resolve(component.file)
            except ValueError as exc:
                logger.warning("WhatsApp 消息链跳过音频: %s", exc)
                continue
            if not media_path:
                logger.warning("WhatsApp 消息链跳过音频: 文件路径为空")
                continue
            await client.send_media(target, "audio", media_path, None, **_send_kw)
        elif isinstance(component, Video):
            if not use_caption:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, pending_caption, pending_mentions, **_flush_kw,
                )
            try:
                media_path = await _resolve(component.file)
            except ValueError as exc:
                logger.warning("WhatsApp 消息链跳过视频: %s", exc)
                continue
            if not media_path:
                logger.warning("WhatsApp 消息链跳过视频: 文件路径为空")
                continue
            await client.send_media(
                target, "video", media_path, pending_caption if use_caption else None, **_send_kw,
            )
            pending_caption = None
            pending_mentions = []
        elif isinstance(component, File):
            media_path = component.file or component.url
            if not media_path:
                logger.warning("WhatsApp 消息链跳过文档: 路径为空")
                continue
            if not use_caption:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, pending_caption, pending_mentions, **_flush_kw,
                )
            try:
                resolved = await _resolve(media_path)
            except ValueError as exc:
                logger.warning("WhatsApp 消息链跳过文档: %s", exc)
                continue
            await client.send_media(
                target, "document", resolved, pending_caption if use_caption else None, **_send_kw,
            )
            pending_caption = None
            pending_mentions = []
        elif isinstance(component, (WhatsAppButtons, WhatsAppList, WhatsAppPoll, WhatsAppEdit)):
            pending_caption, pending_mentions = await flush_pending_text(
                client, target, pending_caption, pending_mentions, **_flush_kw,
            )
            await send_whatsapp_component(client, target, component, **_send_kw)

    return pending_caption, pending_mentions
