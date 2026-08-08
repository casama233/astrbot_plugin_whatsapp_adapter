"""WhatsApp 平台共享輔助函數。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Iterator, Literal, Sequence
from urllib.parse import unquote

from astrbot import logger
from astrbot.api.message_components import File, Image, Location, Plain, Record, Reply, Video
from astrbot.api.platform import At

from .whatsapp_client import WhatsAppGatewayClient
from .whatsapp_components import WhatsAppButtons, WhatsAppEdit, WhatsAppList, WhatsAppPoll

__all__ = [
    "MentionRef",
    "chunk_text",
    "flush_pending_text",
    "format_markdown_from_whatsapp",
    "format_whatsapp_markdown",
    "has_visible_whatsapp_content",
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
    "split_whatsapp_text",
]

SourceFormat = Literal["markdown", "whatsapp", "plain"]


@dataclass(frozen=True, slots=True)
class MentionRef:
    """將 WhatsApp JID 與訊息中的可見 @文字綁定。"""

    jid: str
    text: str


def _placeholder(index: int, kind: str = "PROTECTED") -> str:
    return f"\x00WA{kind}{index}\x00"


def _protect_escaped_markdown(value: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def replace(match: re.Match[str]) -> str:
        protected.append(match.group(1))
        return _placeholder(len(protected) - 1, "ESC")

    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", replace, value), protected


def _restore_escaped_markdown(value: str, protected: Sequence[str]) -> str:
    for index, text in enumerate(protected):
        value = value.replace(_placeholder(index, "ESC"), text)
    return value


def _extract_fenced_code(value: str, *, streaming: bool) -> tuple[str, list[str]]:
    """將 Markdown fenced code 轉成 WhatsApp 官方三反引號格式並保護。"""
    lines = value.splitlines(keepends=True)
    output: list[str] = []
    protected: list[str] = []
    index = 0

    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.rstrip("\r\n")
        opening = re.match(r"^[ \t]*(`{3,}|~{3,})(.*)$", line)
        if not opening:
            output.append(raw_line)
            index += 1
            continue

        delimiter = opening.group(1)
        fence_char = delimiter[0]
        minimum = len(delimiter)
        tail = opening.group(2)
        same_line_close = re.search(
            rf"{re.escape(fence_char)}{{{minimum},}}[ \t]*$",
            tail,
        )
        if same_line_close:
            code = tail[: same_line_close.start()]
            protected.append(f"```{code}```")
            output.append(_placeholder(len(protected) - 1, "FENCE"))
            index += 1
            continue

        body: list[str] = []
        index += 1
        while index < len(lines):
            closing = re.match(
                rf"^[ \t]*{re.escape(fence_char)}{{{minimum},}}[ \t]*(?:\r?\n|$)",
                lines[index],
            )
            if closing:
                index += 1
                break
            body.append(lines[index])
            index += 1

        # opening line上的語言標籤不屬於 WhatsApp monospace 語法。
        code = "".join(body)
        protected.append(f"```{code}```")
        output.append(_placeholder(len(protected) - 1, "FENCE"))

    return "".join(output), protected

def _protect_inline_code(value: str, *, streaming: bool) -> tuple[str, list[str]]:
    """保護 Markdown code spans；反引號 run 以完全相同長度配對。"""
    output: list[str] = []
    protected: list[str] = []
    index = 0

    while index < len(value):
        if value[index] != "`":
            output.append(value[index])
            index += 1
            continue

        run_end = index + 1
        while run_end < len(value) and value[run_end] == "`":
            run_end += 1
        delimiter_length = run_end - index
        search_from = run_end
        closing_start = -1
        closing_end = -1

        while search_from < len(value):
            candidate = value.find("`", search_from)
            if candidate < 0:
                break
            candidate_end = candidate + 1
            while candidate_end < len(value) and value[candidate_end] == "`":
                candidate_end += 1
            if candidate_end - candidate == delimiter_length:
                closing_start = candidate
                closing_end = candidate_end
                break
            search_from = candidate_end

        if closing_start < 0:
            # 不完整輸入亦保守地保護至結尾並補上閉合符，避免 code 內的
            # Markdown 標記被誤轉；流式和最終輸出都保持 WhatsApp 可解析。
            content = value[run_end:]
            index = len(value)
        else:
            content = value[run_end:closing_start]
            index = closing_end

        if "`" in content or "\n" in content:
            rendered = f"```{content}```"
        else:
            rendered = f"`{content}`"
        protected.append(rendered)
        output.append(_placeholder(len(protected) - 1, "INLINE"))

    return "".join(output), protected


def _restore_code(value: str, protected: Sequence[str], kind: str) -> str:
    for index, code in enumerate(protected):
        value = value.replace(_placeholder(index, kind), code)
    return value


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    backtick_run = 0
    index = 0
    while index < len(value):
        char = value[index]
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            current.append(char)
            escaped = True
        elif char == "`":
            run_end = index + 1
            while run_end < len(value) and value[run_end] == "`":
                run_end += 1
            run = run_end - index
            backtick_run = 0 if backtick_run == run else run
            current.append(value[index:run_end])
            index = run_end - 1
        elif char == "|" and backtick_run == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _is_table_delimiter(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _convert_markdown_blocks(value: str, *, streaming: bool) -> str:
    """把 WhatsApp 不支援的 Markdown block 降級到官方支援格式。"""
    lines = value.splitlines(keepends=True)
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        bare = line.rstrip("\r\n")
        ending = line[len(bare):]

        if (
            index + 1 < len(lines)
            and "|" in bare
            and _is_table_delimiter(lines[index + 1].rstrip("\r\n"))
        ):
            headers = _split_table_row(bare)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index]:
                candidate = lines[index].rstrip("\r\n")
                if not candidate.strip():
                    break
                rows.append(_split_table_row(candidate))
                index += 1

            for row in rows:
                fields: list[str] = []
                for column, header in enumerate(headers):
                    cell = row[column] if column < len(row) else ""
                    if header or cell:
                        fields.append(f"**{header or f'欄位 {column + 1}'}:** {cell}")
                output.append("- " + " | ".join(fields) + "\n")
            if rows:
                output.append("\n")
            continue

        heading = re.match(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", bare)
        if heading:
            output.append(f"**{heading.group(1)}**{ending}")
            index += 1
            continue

        if re.fullmatch(r"[ \t]*(?:\*{3,}|_{3,}|-{3,})[ \t]*", bare):
            output.append(f"──────────{ending}")
            index += 1
            continue

        unordered = re.match(r"^([ \t]*)[-+*][ \t]+(.+)$", bare)
        if unordered:
            output.append(f"{unordered.group(1)}- {unordered.group(2)}{ending}")
            index += 1
            continue

        ordered = re.match(r"^([ \t]*)(\d{1,2})[.)][ \t]+(.+)$", bare)
        if ordered:
            output.append(f"{ordered.group(1)}{ordered.group(2)}. {ordered.group(3)}{ending}")
            index += 1
            continue

        quote = re.match(r"^([ \t]*)>[ \t]?(.*)$", bare)
        if quote:
            output.append(f"{quote.group(1)}> {quote.group(2)}{ending}")
            index += 1
            continue

        output.append(line)
        index += 1

    return "".join(output)


def _convert_links(value: str) -> str:
    # WhatsApp 自動識別裸 URL；保留標籤並把 URL 放在括號中。
    value = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)", r"\1 (\2)", value)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)", r"\1 (\2)", value)


def _convert_emphasis(value: str, *, streaming: bool) -> str:
    # 單星號 Markdown 斜體必須先處理，以免把稍後生成的 WhatsApp 粗體再轉一次。
    value = re.sub(r"(?<!\*)\*(?![\s*])([^*\n]*?\S)\*(?!\*)", r"_\1_", value)
    value = re.sub(r"\*\*\*(?=\S)([\s\S]*?\S)\*\*\*", r"*_\1_*", value)
    value = re.sub(r"___(?=\S)([\s\S]*?\S)___", r"*_\1_*", value)
    value = re.sub(r"\*\*(?=\S)([\s\S]*?\S)\*\*", r"*\1*", value)
    value = re.sub(r"__(?=\S)([\s\S]*?\S)__", r"*\1*", value)
    value = re.sub(r"~~(?=\S)([\s\S]*?\S)~~", r"~\1~", value)

    # 不為流式中的未閉合 Markdown 人工補尾符。WhatsApp edit 訊息可能在
    # 不同裝置上亂序到達；舊版本產生的臨時 ``~`` / ``*`` 因而可能覆蓋
    # 最終 edit，留下肉眼可見的 ``~*``。完整標記已在上方轉換，這裡只需
    # 移除剩餘的無配對 Markdown delimiter，先以純文字顯示即可。
    value = re.sub(r"(?<!\*)\*{2,}(?!\*)", "", value)
    value = re.sub(r"(?<![\w_])_{2,}(?!_)|(?<!_)_{2,}(?![\w_])", "", value)
    value = re.sub(r"(?<![\w~])~{2,}(?!~)|(?<!~)~{2,}(?![\w~])", "", value)
    return value


def _remove_unmatched_single_delimiters(value: str) -> str:
    """移除孤立的 WhatsApp/Markdown 单分隔符，保留成对格式与正文内符号。"""
    delimiters = {"*", "_", "~"}
    stack: list[tuple[str, int]] = []
    remove: set[int] = set()

    for index, token in enumerate(value):
        if token not in delimiters:
            continue
        previous = value[index - 1] if index else ""
        following = value[index + 1] if index + 1 < len(value) else ""

        if stack and stack[-1][0] == token and previous and not previous.isspace():
            stack.pop()
            continue

        previous_is_boundary = not previous or previous.isspace() or (
            not previous.isalnum() and previous not in {"_", "\x00"}
        )
        following_has_content = bool(following and not following.isspace())
        if previous_is_boundary and following_has_content:
            stack.append((token, index))
            continue

        following_is_boundary = not following or following.isspace() or not following.isalnum()
        if previous and not previous.isspace() and following_is_boundary:
            remove.add(index)

    remove.update(index for _token, index in stack)
    if not remove:
        return value
    return "".join(char for index, char in enumerate(value) if index not in remove)


def format_whatsapp_markdown(
    text: str,
    *,
    streaming: bool = False,
    source_format: SourceFormat = "markdown",
) -> str:
    """轉成 WhatsApp 官方支援的文字格式。

    官方輸出語法：``*粗體*``、``_斜體_``、``~刪除線~``、
    `````等寬`````、``- 清單``、``1. 清單``、``> 引用``、`` `行內程式碼` ``。

    ``source_format`` 明確解決 Markdown ``*斜體*`` 與 WhatsApp ``*粗體*``
    的語法衝突；已是 WhatsApp 原生格式的文字必須傳 ``"whatsapp"``，
    避免重複轉換。
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value or source_format in ("whatsapp", "plain"):
        return value
    if source_format != "markdown":
        raise ValueError(f"unsupported source_format: {source_format}")

    value, escaped = _protect_escaped_markdown(value)
    value, fenced = _extract_fenced_code(value, streaming=streaming)
    value, inline = _protect_inline_code(value, streaming=streaming)
    value = _convert_markdown_blocks(value, streaming=streaming)
    value = _convert_links(value)
    value = _convert_emphasis(value, streaming=streaming)
    value = _remove_unmatched_single_delimiters(value)
    value = _restore_code(value, fenced, "FENCE")
    value = _restore_code(value, inline, "INLINE")
    value = _restore_escaped_markdown(value, escaped)
    return value


def format_markdown_from_whatsapp(text: str) -> str:
    """將 WhatsApp 官方格式轉成通用 Markdown，且不改寫 code。"""
    value = str(text or "")
    if not value:
        return ""
    value, fenced = _extract_fenced_code(value, streaming=False)
    value, inline = _protect_inline_code(value, streaming=False)
    value = re.sub(r"(?<!\*)\*(?![\s*])([^*\n]*?\S)\*(?!\*)", r"**\1**", value)
    # _italic_ 與 Markdown 相同，不必改寫。
    value = re.sub(r"(?<!~)~(?![\s~])([^~\n]*?\S)~(?!~)", r"~~\1~~", value)
    value = _restore_code(value, fenced, "FENCE")
    return _restore_code(value, inline, "INLINE")


def _is_variation_selector(char: str) -> bool:
    code = ord(char)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def _is_emoji_modifier(char: str) -> bool:
    return 0x1F3FB <= ord(char) <= 0x1F3FF


def _grapheme_units(text: str) -> Iterator[str]:
    """標準庫近似 grapheme segmentation，避免拆開組合字、ZWJ emoji 與旗幟。"""
    cluster = ""
    regional_count = 0
    join_next = False
    for char in text:
        code = ord(char)
        is_regional = 0x1F1E6 <= code <= 0x1F1FF
        attach = (
            bool(cluster)
            and (
                join_next
                or char == "\u200d"
                or unicodedata.combining(char) != 0
                or _is_variation_selector(char)
                or _is_emoji_modifier(char)
                or (is_regional and regional_count == 1)
            )
        )
        if cluster and not attach:
            yield cluster
            cluster = ""
            regional_count = 0
        cluster += char
        join_next = char == "\u200d"
        if is_regional:
            regional_count = (regional_count + 1) % 2
        elif char != "\u200d":
            regional_count = 0
    if cluster:
        yield cluster


def chunk_text(text: str, limit: int) -> Iterator[str]:
    """純文字 grapheme-safe 切片；拼接後內容保持完全一致。"""
    limit = max(1, int(limit))
    current: list[str] = []
    length = 0
    for unit in _grapheme_units(str(text or "")):
        if current and length + len(unit) > limit:
            yield "".join(current)
            current = []
            length = 0
        current.append(unit)
        length += len(unit)
    if current:
        yield "".join(current)


def split_whatsapp_text(text: str, limit: int) -> list[str]:
    """切分 WhatsApp 原生格式並在跨訊息邊界自動關閉／重開格式標記。"""
    value = str(text or "")
    if not value:
        return []
    limit = max(16, int(limit))
    units = list(_grapheme_units(value))
    chunks: list[str] = []
    current: list[str] = []
    active: list[str] = []
    code_delimiter: str | None = None
    index = 0

    def closing_suffix() -> str:
        suffix = ""
        if code_delimiter:
            suffix += code_delimiter
        suffix += "".join(reversed(active))
        return suffix

    def opening_prefix() -> str:
        prefix = "".join(active)
        if code_delimiter:
            prefix += code_delimiter
        return prefix

    def flush() -> None:
        nonlocal current
        body = "".join(current)
        suffix = closing_suffix()
        if body and body != opening_prefix():
            chunks.append(body + suffix)
        current = list(opening_prefix())

    while index < len(units):
        unit = units[index]
        token = unit

        if unit == "`":
            run = 1
            while index + run < len(units) and units[index + run] == "`":
                run += 1
            token = "`" * run
            index += run - 1

        reserve = len(closing_suffix())
        if current and len("".join(current)) + len(token) + reserve > limit:
            flush()

        current.append(token)

        if token.startswith("`") and set(token) == {"`"}:
            if code_delimiter == token:
                code_delimiter = None
            elif code_delimiter is None and len(token) in (1, 3):
                code_delimiter = token
        elif code_delimiter is None and token in ("*", "_", "~"):
            previous = units[index - 1] if index > 0 else ""
            following = units[index + 1] if index + 1 < len(units) else ""
            at_list_prefix = token == "*" and (index == 0 or previous == "\n") and following.isspace()
            if not at_list_prefix:
                if active and active[-1] == token and previous and not previous.isspace():
                    active.pop()
                elif following and not following.isspace():
                    active.append(token)
        index += 1

    if current:
        body = "".join(current)
        suffix = closing_suffix()
        if body and body != opening_prefix():
            chunks.append(body + suffix)

    return [chunk for chunk in chunks if chunk]


def has_visible_whatsapp_content(text: str) -> bool:
    """拒絕只含格式控制符的暫時訊息，例如單獨的 ``*`` 或 ```。"""
    value = re.sub(r"[`*_~>\-\s\u200b\u2060]+", "", str(text or ""))
    return bool(value)


def is_single_url(text: str) -> bool:
    value = str(text or "").strip()
    return (value.startswith("http://") or value.startswith("https://")) and len(value.split()) == 1


def should_link_preview(text: str, link_preview_single_url: bool) -> bool:
    return bool(link_preview_single_url) and is_single_url(text)


def normalize_media_value(value: str | None) -> str:
    if not value:
        return ""
    if value.startswith("file://"):
        return unquote("/" + value.removeprefix("file:").lstrip("/"))
    return value


def mention_text_from_at(component: At) -> str:
    value = str(getattr(component, "name", "") or getattr(component, "qq", "") or "")
    value = value.split("@", 1)[0].split(":", 1)[0]
    return f"@{value}"


def mention_jid_from_at(component: At) -> str | None:
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
    digits = "".join(char for char in value if char.isdigit())
    if digits:
        return f"{digits}@s.whatsapp.net"
    logger.warning("WhatsApp @提及 JID 解析失败: value=%s", value)
    return None


def mention_jid_for_token(token: str) -> str | None:
    digits = "".join(char for char in token if char.isdigit())
    return f"{digits}@s.whatsapp.net" if digits else None


async def mentions_for_text(
    client: WhatsAppGatewayClient,
    target: str,
    text: str,
    explicit_mentions: Sequence[str | MentionRef],
) -> list[str]:
    del client, target
    resolved: list[str] = []
    for mention in explicit_mentions:
        if isinstance(mention, MentionRef):
            if mention.text and mention.text not in text:
                continue
            jid = mention.jid
        else:
            jid = str(mention)
        if jid and jid not in resolved:
            resolved.append(jid)
    return resolved


def media_kind_from_component(component: Any, default: str) -> str:
    kind = str(getattr(component, "type", "") or getattr(component, "_type", "") or "").lower()
    name = str(
        getattr(component, "name", "")
        or getattr(component, "filename", "")
        or getattr(component, "file", "")
        or ""
    ).lower()
    if kind == "sticker" or kind.endswith("sticker") or (name.endswith(".webp") and "sticker" in name):
        return "sticker"
    return default


async def flush_pending_text(
    client: WhatsAppGatewayClient,
    target: str,
    pending: str | None,
    mentions: Sequence[str | MentionRef] | None = None,
    *,
    link_preview_single_url: bool = True,
    text_chunk_limit: int = 4000,
    quoted_message_id: str | None = None,
    quoted_participant: str | None = None,
    source_format: SourceFormat = "markdown",
) -> tuple[None, list[MentionRef]]:
    if not pending:
        return None, list(mentions or [])  # type: ignore[list-item]
    rendered = format_whatsapp_markdown(pending, source_format=source_format)
    if not has_visible_whatsapp_content(rendered):
        return None, []
    for chunk in split_whatsapp_text(rendered, text_chunk_limit):
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
    if isinstance(component, WhatsAppButtons):
        buttons = [
            {"id": button.id or f"btn_{index}", "text": button.text}
            for index, button in enumerate(component.buttons or [])
        ]
        await client.send_buttons(
            target,
            component.body,
            buttons,
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
            target,
            component.title,
            sections,
            description=component.description,
            button_text=component.button_text,
            footer=component.footer,
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
        )
    elif isinstance(component, WhatsAppPoll):
        await client.send_poll(
            target,
            component.name,
            list(component.options or []),
            selectable_count=int(component.selectable_count or 0),
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
        )
    elif isinstance(component, WhatsAppEdit):
        await client.edit_text(
            target,
            component.message_id,
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
) -> tuple[str | None, list[MentionRef]]:
    """累積相鄰 Plain/At 的原始 Markdown，在真正發送前只轉換一次。"""
    pending_raw: str | None = None
    pending_mentions: list[MentionRef] = []
    flush_kwargs: dict[str, Any] = {
        "link_preview_single_url": link_preview_single_url,
        "text_chunk_limit": text_chunk_limit,
        "quoted_message_id": quoted_message_id,
        "quoted_participant": quoted_participant,
    }
    send_kwargs: dict[str, Any] = {}
    if quoted_message_id:
        send_kwargs["quoted_message_id"] = quoted_message_id
    if quoted_participant:
        send_kwargs["quoted_participant"] = quoted_participant

    async def flush() -> None:
        nonlocal pending_raw, pending_mentions
        pending_raw, pending_mentions = await flush_pending_text(
            client,
            target,
            pending_raw,
            pending_mentions,
            **flush_kwargs,
        )

    async def prepare_caption() -> str | None:
        if not pending_raw:
            return None
        chunks = split_whatsapp_text(format_whatsapp_markdown(pending_raw), text_chunk_limit)
        if not chunks:
            return None
        for chunk in chunks[:-1]:
            chunk_mentions = await mentions_for_text(
                client, target, chunk, pending_mentions,
            )
            await client.send_text(
                target,
                chunk,
                quoted_message_id=quoted_message_id,
                quoted_participant=quoted_participant,
                link_preview=should_link_preview(chunk, link_preview_single_url),
                mentions=chunk_mentions,
            )
        return chunks[-1]

    for component in chain:
        if isinstance(component, Reply):
            # Reply is transport metadata, never user-visible nested content.
            continue
        if isinstance(component, Plain):
            pending_raw = (pending_raw or "") + (component.text or "")
            continue
        if isinstance(component, At):
            visible = mention_text_from_at(component)
            jid = mention_jid_from_at(component)
            # 与 aiocqhttp 一致：At 后固定保留一个分隔空格，避免昵称和正文粘连。
            pending_raw = (pending_raw or "") + visible + " "
            if jid:
                pending_mentions.append(MentionRef(jid=jid, text=visible))
            continue

        if isinstance(component, Image):
            if not use_caption:
                await flush()
            try:
                media_path = await _resolve_media_component(
                    component,
                    "path",
                    "file",
                    "url",
                    resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
                logger.warning("WhatsApp 消息链跳过图片: %s", exc)
                continue
            if not media_path:
                continue
            media_kind = media_kind_from_component(component, "image")
            if media_kind == "sticker" and use_caption and pending_raw:
                await flush()
            await client.send_media(
                target,
                media_kind,
                media_path,
                None if media_kind == "sticker" else await prepare_caption() if use_caption else None,
                **send_kwargs,
            )
            pending_raw = None
            pending_mentions = []
        elif isinstance(component, Record):
            await flush()
            try:
                media_path = await _resolve_media_component(
                    component,
                    "path",
                    "file",
                    "url",
                    resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
                logger.warning("WhatsApp 消息链跳过音频: %s", exc)
                continue
            if media_path:
                await client.send_media(target, "audio", media_path, None, **send_kwargs)
        elif isinstance(component, Video):
            if not use_caption:
                await flush()
            try:
                media_path = await _resolve_media_component(
                    component,
                    "path",
                    "file",
                    "url",
                    resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
                logger.warning("WhatsApp 消息链跳过视频: %s", exc)
                continue
            if media_path:
                await client.send_media(
                    target,
                    "video",
                    media_path,
                    await prepare_caption() if use_caption else None,
                    **send_kwargs,
                )
            pending_raw = None
            pending_mentions = []
        elif isinstance(component, File):
            if not use_caption:
                await flush()
            try:
                resolved = await _resolve_media_component(
                    component,
                    "file_",
                    "file",
                    "url",
                    resolve_media_func=resolve_media_func,
                )
            except Exception as exc:
                logger.warning("WhatsApp 消息链跳过文档: %s", exc)
                continue
            if resolved:
                await client.send_media(
                    target,
                    "document",
                    resolved,
                    await prepare_caption() if use_caption else None,
                    **send_kwargs,
                )
            pending_raw = None
            pending_mentions = []
        elif isinstance(component, Location):
            await flush()
            await client.send_location(
                target,
                float(getattr(component, "lat", 0) or 0),
                float(getattr(component, "lon", 0) or 0),
                str(getattr(component, "title", "") or ""),
                str(getattr(component, "content", "") or ""),
                **send_kwargs,
            )
        elif isinstance(component, (WhatsAppButtons, WhatsAppList, WhatsAppPoll, WhatsAppEdit)):
            await flush()
            await send_whatsapp_component(client, target, component, **send_kwargs)
        else:
            nested = _iter_nested_components(component)
            if nested:
                await flush()
                nested_pending, nested_mentions = await process_message_chain(
                    client,
                    target,
                    nested,
                    link_preview_single_url=link_preview_single_url,
                    text_chunk_limit=text_chunk_limit,
                    use_caption=use_caption,
                    quoted_message_id=quoted_message_id,
                    quoted_participant=quoted_participant,
                    resolve_media_func=resolve_media_func,
                )
                pending_raw = nested_pending
                pending_mentions = nested_mentions
            else:
                logger.debug("WhatsApp 消息链跳过不支持组件: %s", component.__class__.__name__)

    return pending_raw, pending_mentions


def _iter_nested_components(component: Any) -> list[Any]:
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
