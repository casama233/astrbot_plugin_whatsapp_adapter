"""WhatsApp 平台共享輔助函數，減少 whatsapp_adapter.py 與 whatsapp_event.py 之間的重複代碼。"""

from __future__ import annotations

import re
from typing import Any, Callable, Coroutine, Iterator
from urllib.parse import unquote

from astrbot import logger
from astrbot.api.platform import At
from astrbot.api.message_components import File, Image, Plain, Record, Video

from .whatsapp_client import WhatsAppGatewayClient
from .whatsapp_components import WhatsAppButtons, WhatsAppEdit, WhatsAppList, WhatsAppPoll

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


def _protect_backtick_code(value: str) -> tuple[str, list[str]]:
    """以惰性 placeholder 保護 code span/fence。

    反引號以「連續長度完全相同」配對，因此 `` `nested` ``、多行 fence，
    以及 fence 內較短的反引號都不會被誤配。若尚未閉合，保守地保護至
    字串結尾，這對流式回覆尤其重要。
    """
    protected: list[str] = []
    output: list[str] = []
    index = 0

    while index < len(value):
        if value[index] != "`":
            output.append(value[index])
            index += 1
            continue

        run_end = index + 1
        while run_end < len(value) and value[run_end] == "`":
            run_end += 1
        delimiter = value[index:run_end]
        delimiter_length = len(delimiter)

        search_from = run_end
        closing_end: int | None = None
        while search_from < len(value):
            candidate = value.find("`", search_from)
            if candidate < 0:
                break
            candidate_end = candidate + 1
            while candidate_end < len(value) and value[candidate_end] == "`":
                candidate_end += 1
            if candidate_end - candidate == delimiter_length:
                closing_end = candidate_end
                break
            search_from = candidate_end

        if closing_end is None:
            segment = value[index:]
            index = len(value)
        else:
            segment = value[index:closing_end]
            index = closing_end

        placeholder = f"\x00WACODE{len(protected)}\x00"
        protected.append(segment)
        output.append(placeholder)

    return "".join(output), protected


def _restore_backtick_code(value: str, protected: list[str]) -> str:
    for index, segment in enumerate(protected):
        value = value.replace(f"\x00WACODE{index}\x00", segment)
    return value


def format_whatsapp_markdown(text: str, *, streaming: bool = False) -> str:
    """將標準 Markdown 轉為 WhatsApp 原生文字格式。

    WhatsApp 原生支援 ``*粗體*``、``_斜體_``、``~刪除線~``、
    行內反引號與多反引號程式碼。轉換會保守避開程式碼及識別字；任何
    未配對的 ``**``、``__``、``~~`` 在中途與最終輸出都會降級，避免
    不支援的雙重標記流入 WhatsApp。``streaming`` 參數保留作 API 相容。
    """
    value = str(text or "")
    if not value:
        return ""

    value, protected = _protect_backtick_code(value)

    value = re.sub(
        r"(?m)^[ \t]*(?:\*{3,}|_{3,}|-{3,})[ \t]*$",
        "──────────",
        value,
    )

    value = re.sub(
        r"(?<!\*)\*(?![\s*])([^*\n]*?\S)\*(?!\*)",
        r"_\1_",
        value,
    )

    value = re.sub(r"\*\*\*(?=\S)([\s\S]*?\S)\*\*\*", r"*_\1_*", value)
    value = re.sub(r"___(?=\S)([\s\S]*?\S)___", r"*_\1_*", value)
    value = re.sub(r"\*\*(?=\S)([\s\S]*?\S)\*\*", r"*\1*", value)
    value = re.sub(r"__(?=\S)([\s\S]*?\S)__", r"*\1*", value)
    value = re.sub(r"~~(?=\S)([\s\S]*?\S)~~", r"~\1~", value)

    value = re.sub(r"(?<!\*)\*{2,}(?!\*)", "*", value)
    value = re.sub(
        r"(?<![\w_])_{2,}(?!_)|(?<!_)_{2,}(?![\w_])",
        "*",
        value,
    )
    value = re.sub(
        r"(?<![\w~])~{2,}(?!~)|(?<!~)~{2,}(?![\w~])",
        "~",
        value,
    )

    return _restore_backtick_code(value, protected)


def format_markdown_from_whatsapp(text: str) -> str:
    """將 WhatsApp 格式轉為 Markdown，且不改寫程式碼內容。"""
    value = str(text or "")
    if not value:
        return ""

    value, protected = _protect_backtick_code(value)
    value = re.sub(
        r"(?<!\*)\*(?![\s*])([^*\n]*?\S)\*(?!\*)",
        r"**\1**",
        value,
    )
    value = re.sub(
        r"(?<!_)_(?![\s_])([^_\n]*?\S)_(?!_)",
        r"*\1*",
        value,
    )
    value = re.sub(
        r"(?<!~)~(?![\s~])([^~\n]*?\S)~(?!~)",
        r"~~\1~~",
        value,
    )
    return _restore_backtick_code(value, protected)


def chunk_text(text: str, limit: int) -> Iterator[str]:
    """按 limit 切片，優先在換行、半形或全形空白邊界分割。"""
    limit = max(1, int(limit))
    remaining = str(text or "")
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(
            window.rfind("\n"),
            window.rfind(" "),
            window.rfind("　"),
        )
        if cut <= 0 or cut < limit // 2:
            cut = limit
        else:
            cut += 1
        yield remaining[:cut]
        remaining = remaining[cut:]
    if remaining:
        yield remaining


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
        return unquote("/" + value.removeprefix("file:").lstrip("/"))
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
            logger.warning("WhatsApp @提及 lid 未缓存，使用原始 JID: %s", value)
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


def mention_jid_for_token(token: str) -> str | None:
    """純文字 @token → 數字 + @s.whatsapp.net，無法解析時返回 None。"""
    digits = "".join(ch for ch in token if ch.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else None


async def mentions_for_text(
    client: WhatsAppGatewayClient,
    target: str,
    text: str,
    explicit_mentions: list[str],
) -> list[str]:
    """回傳顯式提及（At 元件）清單（client/target/text 參數預留給未來 @token 解析）。"""
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


def _first_component_value(component: Any, *names: str) -> str:
    for name in names:
        value = getattr(component, name, None)
        if value:
            return str(value)
    return ""


async def _resolve_media_component(
    component: Any,
    *names: str,
    resolve_media_func: MediaResolver | None = None,
    allow_url: bool = True,
) -> str:
    if isinstance(component, File) and hasattr(component, "get_file"):
        value = await component.get_file(allow_return_url=allow_url)
        if value:
            if allow_url and (value.startswith("http://") or value.startswith("https://")):
                return value
            return await resolve_media_func(value) if resolve_media_func else normalize_media_value(value)

    value = _first_component_value(component, *names, "file", "url", "path")
    if allow_url and (value.startswith("http://") or value.startswith("https://")):
        return value
    if value.startswith("base64://") or value.startswith("data:"):
        converter = getattr(component, "convert_to_file_path", None)
        if converter:
            return str(await converter())
    if resolve_media_func:
        return await resolve_media_func(value)
    return normalize_media_value(value)


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
                media_path = await _resolve_media_component(
                    component, "path", "file", "url", resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
                logger.warning("WhatsApp 消息链跳过图片: %s", exc)
                continue
            if not media_path:
                logger.warning("WhatsApp 消息链跳过图片: 文件路径为空")
                continue
            media_kind = media_kind_from_component(component, "image")
            if media_kind == "sticker" and use_caption and pending_caption:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, pending_caption, pending_mentions, **_flush_kw,
                )
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
                media_path = await _resolve_media_component(
                    component, "path", "file", "url", resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
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
                media_path = await _resolve_media_component(
                    component, "path", "file", "url", resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
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
            if not use_caption:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, pending_caption, pending_mentions, **_flush_kw,
                )
            try:
                resolved = await _resolve_media_component(
                    component, "file_", "file", "url", resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
                logger.warning("WhatsApp 消息链跳过文档: %s", exc)
                continue
            if not resolved:
                logger.warning("WhatsApp 消息链跳过文档: 路径为空")
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
        else:
            nested = _iter_nested_components(component)
            if nested:
                pending_caption, pending_mentions = await flush_pending_text(
                    client, target, pending_caption, pending_mentions, **_flush_kw,
                )
                pending_caption, pending_mentions = await process_message_chain(
                    client, target, nested,
                    link_preview_single_url=link_preview_single_url,
                    text_chunk_limit=text_chunk_limit,
                    use_caption=use_caption,
                    quoted_message_id=quoted_message_id,
                    quoted_participant=quoted_participant,
                    resolve_media_func=resolve_media_func,
                )
            else:
                logger.debug(
                    "WhatsApp 消息链跳过不支持组件: %s",
                    component.__class__.__name__,
                )

    return pending_caption, pending_mentions


def _iter_nested_components(component: Any) -> list[Any]:
    """Best-effort flattening for Node/Nodes style components on non-WhatsApp platforms."""
    nested: list[Any] = []
    chain = getattr(component, "chain", None)
    if chain:
        nested.extend(list(chain))
    for attr in ("nodes", "node", "messages"):
        value = getattr(component, attr, None)
        if not value:
            continue
        items = value if isinstance(value, (list, tuple)) else [value]
        for item in items:
            item_chain = getattr(item, "chain", None)
            if item_chain:
                nested.extend(list(item_chain))
                continue
            item_message = getattr(item, "message", None) or getattr(item, "content", None)
            if isinstance(item_message, list):
                nested.extend(item_message)
            elif item_message:
                nested.append(item_message)
    return nested
