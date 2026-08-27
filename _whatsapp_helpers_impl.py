"""WhatsApp 平台共享輔助函數。"""

from __future__ import annotations

import json
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
from .whatsapp_identity import (
    base_lid_jid,
    base_pn_jid,
    is_lid_jid,
    is_pn_jid,
)
from .whatsapp_chunking import split_whatsapp_text

__all__ = [
    "MentionRef",
    "QuoteState",
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


@dataclass(slots=True)
class QuoteState:
    """Ensure an AstrBot ``Reply`` is attached to one physical send only."""

    message_id: str | None = None
    participant: str | None = None
    consumed: bool = False
    sent_count: int = 0

    def kwargs(self) -> dict[str, str]:
        if self.consumed or not self.message_id:
            return {}
        payload = {"quoted_message_id": self.message_id}
        if self.participant:
            payload["quoted_participant"] = self.participant
        return payload

    def consume(self) -> None:
        self.sent_count += 1
        if self.message_id:
            self.consumed = True


def _placeholder(index: int, kind: str = "PROTECTED") -> str:
    return f"\x00WA{kind}{index}\x00"


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
            if not streaming:
                # 最終文本中的單獨反引號通常來自顏文字或自然語言，不能
                # 把後續整段誤包成 code；保留字面符號並繼續轉換後方 Markdown。
                output.append(value[index:run_end])
                index = run_end
                continue
            # 流式輸入仍可能在後續 token 收到閉合符，暫時保護至結尾；
            # 最終 publish 會使用 ``streaming=False`` 重新判斷並修正顯示。
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


_MARKDOWN_ESCAPABLE = frozenset(r"\`*_{}[]()#+-.!|>~")
_URL_TRAILING_PUNCTUATION = ".,!?;:，。！？；：、"
_WORD_JOINER = "\u2060"


@dataclass(slots=True)
class _InlinePiece:
    """One token in the supported Markdown inline subset."""

    kind: str
    text: str
    marker: str = ""
    size: int = 0
    candidate: bool = False
    structural: bool = False
    pair_id: int = -1


def _append_inline_text(pieces: list[_InlinePiece], text: str) -> None:
    if not text:
        return
    if pieces and pieces[-1].kind == "text":
        pieces[-1].text += text
    else:
        pieces.append(_InlinePiece("text", text))


def _markdown_punctuation(char: str) -> bool:
    return bool(char) and unicodedata.category(char)[0] in {"P", "S"}


def _markdown_flanking(
    value: str,
    start: int,
    end: int,
    marker: str,
) -> tuple[bool, bool]:
    previous = value[start - 1] if start else ""
    following = value[end] if end < len(value) else ""
    previous_space = not previous or previous.isspace()
    following_space = not following or following.isspace()
    previous_punctuation = _markdown_punctuation(previous)
    following_punctuation = _markdown_punctuation(following)
    left_flanking = not following_space and (
        not following_punctuation or previous_space or previous_punctuation
    )
    right_flanking = not previous_space and (
        not previous_punctuation or following_space or following_punctuation
    )
    if marker in {"_", "~"}:
        return (
            left_flanking and (not right_flanking or previous_punctuation),
            right_flanking and (not left_flanking or following_punctuation),
        )
    return left_flanking, right_flanking


def _bare_url_end(
    value: str,
    start: int,
    open_delimiters: Sequence[_InlinePiece] = (),
) -> int | None:
    lowered = value[start : start + 8].lower()
    if not (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or value[start : start + 4].lower() == "www."
    ):
        return None
    end = start
    while end < len(value) and not value[end].isspace() and value[end] not in "<>":
        end += 1
    while end > start and value[end - 1] in _URL_TRAILING_PUNCTUATION:
        end -= 1
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        while value[start:end].count(closing) > value[start:end].count(opening):
            if end <= start or value[end - 1] != closing:
                break
            end -= 1

    # A bare URL may legally contain Markdown punctuation.  Only detach a
    # trailing run when it exactly closes formatting that was already open
    # before the URL; otherwise keep the punctuation as part of the URL.
    for count in range(len(open_delimiters), 0, -1):
        closing = "".join(
            opening.marker * opening.size
            for opening in reversed(open_delimiters[-count:])
        )
        if end - len(closing) < start:
            continue
        if value[end - len(closing) : end] == closing:
            end -= len(closing)
            break
    return end if end > start else None


def _escaped_whatsapp_literal(value: str, *, code_collision: bool = False) -> str:
    protected = "`*_~" if code_collision else "`*_~"
    return "".join(
        char + _WORD_JOINER if char in protected else char
        for char in value
    )


def _contains_whatsapp_bold_span(value: str) -> bool:
    """Return whether rendered inline text already has a real bold pair."""
    markers = 0
    for index, char in enumerate(value):
        if char != "*":
            continue
        previous = value[index - 1] if index else ""
        following = value[index + 1] if index + 1 < len(value) else ""
        if previous != _WORD_JOINER and following != _WORD_JOINER:
            markers += 1
    return markers >= 2


def _render_heading_inline(value: str, *, streaming: bool) -> str:
    rendered = _render_markdown_inline(value, streaming=streaming)
    # Wrapping an existing WhatsApp ``*bold*`` span in another pair produces
    # an ambiguous sequence such as ``**Bold* heading*``.  Preserve the
    # explicit inline emphasis and degrade only the heading-wide emphasis.
    return rendered if _contains_whatsapp_bold_span(rendered) else f"*{rendered}*"


def _render_code_token(content: str, *, block: bool) -> str:
    if not block:
        content = content.replace("\n", " ")
        if (
            len(content) >= 2
            and content.startswith(" ")
            and content.endswith(" ")
            and content.strip(" ")
        ):
            content = content[1:-1]
    if "```" in content:
        # WhatsApp only supports one- and three-backtick delimiters.  A
        # four-backtick CommonMark span containing ``` therefore has no safe
        # equivalent; keep it visible as plain text and neutralize controls.
        return _escaped_whatsapp_literal(content, code_collision=True)
    if block or "`" in content or "\n" in content:
        return f"```{content}```"
    return f"`{content}`"


def _find_backtick_close(value: str, start: int, run_length: int) -> tuple[int, int] | None:
    cursor = start
    while cursor < len(value):
        candidate = value.find("`", cursor)
        if candidate < 0:
            return None
        end = candidate + 1
        while end < len(value) and value[end] == "`":
            end += 1
        if end - candidate == run_length:
            return candidate, end
        cursor = end
    return None


def _count_backtick_runs(value: str, start: int, run_length: int) -> int:
    """Count unescaped backtick runs of one exact delimiter length."""
    count = 0
    cursor = start
    while cursor < len(value):
        candidate = value.find("`", cursor)
        if candidate < 0:
            break
        end = candidate + 1
        while end < len(value) and value[end] == "`":
            end += 1
        slash_count = 0
        slash_index = candidate - 1
        while slash_index >= 0 and value[slash_index] == "\\":
            slash_count += 1
            slash_index -= 1
        if end - candidate == run_length and slash_count % 2 == 0:
            count += 1
        cursor = end
    return count


def _find_label_close(value: str, start: int) -> int | None:
    depth = 1
    index = start
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            index += 2
            continue
        if value[index] == "[":
            depth += 1
        elif value[index] == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _find_link_destination_close(value: str, start: int) -> int | None:
    depth = 1
    index = start
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            index += 2
            continue
        if value[index] == "(":
            depth += 1
        elif value[index] == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _parse_markdown_link(
    value: str,
    start: int,
    *,
    streaming: bool = False,
) -> tuple[int, str] | None:
    image = value.startswith("![", start)
    label_open = start + 1 if image else start
    if label_open >= len(value) or value[label_open] != "[":
        return None
    label_close = _find_label_close(value, label_open + 1)
    if label_close is None or label_close + 1 >= len(value) or value[label_close + 1] != "(":
        return None
    destination_close = _find_link_destination_close(value, label_close + 2)
    if destination_close is None:
        return None

    raw_destination = value[label_close + 2 : destination_close].strip()
    if raw_destination.startswith("<") and ">" in raw_destination:
        destination = raw_destination[1 : raw_destination.find(">")]
    else:
        destination = raw_destination.split(None, 1)[0] if raw_destination else ""
    destination = re.sub(r"\\([\\()])", r"\1", destination)
    label = _render_markdown_inline(
        value[label_open + 1 : label_close],
        streaming=streaming,
    )
    rendered = label
    if destination:
        rendered = destination if label == destination and not image else f"{label} ({destination})"
    return destination_close + 1, rendered


def _delimiter_sizes(marker: str, count: int) -> tuple[list[int], int]:
    if marker == "~":
        return [2] * (count // 2), count % 2
    return ([1] if count % 2 else []) + ([2] * (count // 2)), 0


def _whatsapp_style_marker(piece: _InlinePiece) -> str:
    if piece.marker == "~":
        return "~"
    if piece.size == 2:
        return "*"
    return "_"


def _render_piece_for_boundary(piece: _InlinePiece, *, streaming: bool) -> str:
    if piece.kind != "delimiter":
        return piece.text
    if piece.structural:
        return _whatsapp_style_marker(piece)
    if streaming and piece.candidate:
        return ""
    return (
        _escaped_whatsapp_literal(piece.text)
        if piece.candidate
        else piece.text
    )


def _whatsapp_outer_boundary_safe(char: str) -> bool:
    """Return whether native WhatsApp styling is reliable at this boundary.

    WhatsApp's native ``*bold*``/``_italic_``/``~strike~`` parser is
    stricter around adjacent word characters than CommonMark. Rendered
    spans such as ``中文*bold*中文`` can therefore be displayed literally by
    mobile clients. Whitespace, punctuation/symbols and string boundaries
    are safe; adjacent letters/numbers are not.
    """
    return not char or char.isspace() or _markdown_punctuation(char)


def _unsafe_whatsapp_pair_ids(
    pieces: Sequence[_InlinePiece],
    ignored_indices: set[int],
    *,
    streaming: bool,
) -> set[int]:
    """Find style pairs that would render with unsafe outer boundaries.

    This is a fixpoint: dropping an unsafe outer style can expose an inner
    style directly to a word character, so nested pairs are re-evaluated
    until no newly unsafe pair remains.
    """
    pair_indices: dict[int, list[int]] = {}
    for index, piece in enumerate(pieces):
        if piece.structural and piece.pair_id >= 0:
            pair_indices.setdefault(piece.pair_id, []).append(index)
    pairs = {
        pair_id: (indices[0], indices[-1])
        for pair_id, indices in pair_indices.items()
        if len(indices) == 2
    }
    disabled: set[int] = set()

    def nearest_char(start: int, step: int) -> str:
        index = start
        while 0 <= index < len(pieces):
            if index in ignored_indices:
                index += step
                continue
            piece = pieces[index]
            if piece.structural and piece.pair_id in disabled:
                index += step
                continue
            rendered = _render_piece_for_boundary(
                piece,
                streaming=streaming,
            )
            if rendered:
                return rendered[-1] if step < 0 else rendered[0]
            index += step
        return ""

    while True:
        changed = False
        for pair_id, (opening, closing) in pairs.items():
            if pair_id in disabled:
                continue
            previous = nearest_char(opening - 1, -1)
            following = nearest_char(closing + 1, 1)
            if (
                not _whatsapp_outer_boundary_safe(previous)
                or not _whatsapp_outer_boundary_safe(following)
            ):
                disabled.add(pair_id)
                changed = True
        if not changed:
            return disabled


def _render_markdown_inline(value: str, *, streaming: bool = False) -> str:
    """Render the supported inline subset using paired delimiter tokens."""
    pieces: list[_InlinePiece] = []
    delimiter_stack: list[_InlinePiece] = []
    next_pair_id = 0
    index = 0

    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value) and value[index + 1] in _MARKDOWN_ESCAPABLE:
            escaped = value[index + 1]
            pieces.append(
                _InlinePiece(
                    "escaped",
                    escaped + (_WORD_JOINER if escaped in "`*_~" else ""),
                ),
            )
            index += 2
            continue

        link = (
            _parse_markdown_link(value, index, streaming=streaming)
            if char in "!["
            else None
        )
        if link is not None:
            index, rendered_link = link
            pieces.append(_InlinePiece("opaque", rendered_link))
            continue

        url_end = _bare_url_end(value, index, delimiter_stack)
        if url_end is not None:
            pieces.append(_InlinePiece("opaque", value[index:url_end]))
            index = url_end
            continue

        if char == "`":
            run_end = index + 1
            while run_end < len(value) and value[run_end] == "`":
                run_end += 1
            run_length = run_end - index
            # With an odd number of equal runs, preserve the first as a
            # literal and pair the later runs.  This prevents a stray kaomoji
            # backtick from consuming a subsequent well-formed code span.
            if _count_backtick_runs(value, index, run_length) % 2:
                _append_inline_text(
                    pieces,
                    _escaped_whatsapp_literal(value[index:run_end]),
                )
                index = run_end
                continue
            closing = _find_backtick_close(value, run_end, run_length)
            if closing is None:
                _append_inline_text(
                    pieces,
                    _escaped_whatsapp_literal(value[index:run_end]),
                )
                index = run_end
                continue
            closing_start, closing_end = closing
            pieces.append(
                _InlinePiece(
                    "opaque",
                    _render_code_token(value[run_end:closing_start], block=False),
                ),
            )
            index = closing_end
            continue

        if char not in "*_~":
            _append_inline_text(pieces, char)
            index += 1
            continue

        run_end = index + 1
        while run_end < len(value) and value[run_end] == char:
            run_end += 1
        count = run_end - index
        can_open, can_close = _markdown_flanking(value, index, run_end, char)
        supported_count = count if char != "~" else count - (count % 2)
        remaining = supported_count

        while (
            can_close
            and remaining
            and delimiter_stack
            and delimiter_stack[-1].marker == char
            and delimiter_stack[-1].size <= remaining
        ):
            opening = delimiter_stack.pop()
            opening.structural = True
            opening.pair_id = next_pair_id
            closing = _InlinePiece(
                "delimiter",
                char * opening.size,
                marker=char,
                size=opening.size,
                candidate=True,
                structural=True,
                pair_id=next_pair_id,
            )
            next_pair_id += 1
            pieces.append(closing)
            remaining -= opening.size

        if can_open and remaining:
            sizes, leftover = _delimiter_sizes(char, remaining)
            for size in sizes:
                opening = _InlinePiece(
                    "delimiter",
                    char * size,
                    marker=char,
                    size=size,
                    candidate=True,
                )
                pieces.append(opening)
                delimiter_stack.append(opening)
            remaining = leftover

        if remaining:
            pieces.append(
                _InlinePiece(
                    "delimiter",
                    char * remaining,
                    marker=char,
                    size=remaining,
                    candidate=can_open or can_close,
                ),
            )
        if count > supported_count:
            pieces.append(_InlinePiece("delimiter", char, marker=char, size=1))
        index = run_end

    trailing_unmatched: set[int] = set()
    tail: list[int] = []
    piece_index = len(pieces) - 1

    # Model streams often append a partial ``~*`` immediately before an outer
    # quote or closing parenthesis.  Ignore only such terminal wrapper text
    # while looking for the control fragment; ordinary sentence punctuation
    # remains a hard boundary so literal markers elsewhere are preserved.
    terminal_closers = frozenset(")]}）】〉》」』〕〗〙〛〞〟'\"’”")
    while piece_index >= 0:
        piece = pieces[piece_index]
        if piece.kind != "text" or not piece.text or not all(
            char.isspace()
            or unicodedata.category(char) in {"Pe", "Pf"}
            or char in terminal_closers
            for char in piece.text
        ):
            break
        piece_index -= 1

    for piece_index in range(piece_index, -1, -1):
        piece = pieces[piece_index]
        if piece.kind != "delimiter" or piece.structural:
            break
        tail.append(piece_index)
    tail_text = "".join(pieces[piece_index].text for piece_index in reversed(tail))
    if len(tail_text) >= 2 and all(char in "*_~" for char in tail_text):
        trailing_unmatched.update(tail)

    unsafe_pairs = _unsafe_whatsapp_pair_ids(
        pieces,
        trailing_unmatched,
        streaming=streaming,
    )

    output: list[str] = []
    for piece_index, piece in enumerate(pieces):
        if piece_index in trailing_unmatched:
            continue
        if piece.kind != "delimiter":
            output.append(piece.text)
            continue
        if not piece.structural:
            # Preserve unmatched candidates as visible literals, but prevent
            # WhatsApp or the later chunker from pairing them with unrelated
            # markers. Non-flanking punctuation (for example underscores in
            # identifiers) is already safe and stays byte-for-byte unchanged.
            if not (streaming and piece.candidate):
                output.append(
                    _escaped_whatsapp_literal(piece.text)
                    if piece.candidate
                    else piece.text
                )
            continue
        if piece.pair_id in unsafe_pairs:
            continue
        output.append(_whatsapp_style_marker(piece))
    return "".join(output)


def _line_parts(line: str) -> tuple[str, str]:
    bare = line.rstrip("\n")
    return bare, line[len(bare) :]


def _fence_opening(line: str) -> re.Match[str] | None:
    return re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", line)


def _special_block_start(lines: Sequence[str], index: int) -> bool:
    bare, _ending = _line_parts(lines[index])
    if not bare.strip() or _fence_opening(bare):
        return True
    if bare.startswith("    ") or bare.startswith("\t"):
        return True
    if re.match(r"^[ ]{0,3}#{1,6}(?:[ \t]+|$)", bare):
        return True
    if re.fullmatch(r"[ \t]*(?:\*{3,}|_{3,}|-{3,})[ \t]*", bare):
        return True
    if re.match(r"^[ \t]*(?:[-+*][ \t]+|\d+[.)][ \t]+|>[ \t]?)", bare):
        return True
    if index + 1 < len(lines):
        following, _ = _line_parts(lines[index + 1])
        if ("|" in bare and _is_table_delimiter(following)) or re.fullmatch(
            r"[ ]{0,3}(?:=+|-+)[ \t]*",
            following,
        ):
            return True
    return False


def _render_markdown_blocks(value: str, *, streaming: bool) -> str:
    """Line-tokenize blocks, then apply the paired inline parser."""
    lines = value.splitlines(keepends=True)
    if not lines and value:
        lines = [value]
    output: list[str] = []
    index = 0

    while index < len(lines):
        bare, ending = _line_parts(lines[index])
        if not bare.strip():
            output.append(lines[index])
            index += 1
            continue

        fence = _fence_opening(bare)
        if fence:
            delimiter = fence.group(1)
            tail = fence.group(2)
            same_line_close = re.search(
                rf"{re.escape(delimiter[0])}{{{len(delimiter)},}}[ \t]*$",
                tail,
            )
            if same_line_close:
                code = tail[: same_line_close.start()]
                output.append(_render_code_token(code, block=True) + ending)
                index += 1
                continue

            body: list[str] = []
            index += 1
            closing_ending = ""
            while index < len(lines):
                candidate, candidate_ending = _line_parts(lines[index])
                if re.fullmatch(
                    rf"[ ]{{0,3}}{re.escape(delimiter[0])}{{{len(delimiter)},}}[ \t]*",
                    candidate,
                ):
                    closing_ending = candidate_ending
                    index += 1
                    break
                body.append(lines[index])
                index += 1
            output.append(_render_code_token("".join(body), block=True) + closing_ending)
            continue

        if bare.startswith("    ") or bare.startswith("\t"):
            body: list[str] = []
            while index < len(lines):
                candidate, _ = _line_parts(lines[index])
                if candidate.startswith("    "):
                    body.append(lines[index][4:])
                elif candidate.startswith("\t"):
                    body.append(lines[index][1:])
                else:
                    break
                index += 1
            output.append(_render_code_token("".join(body), block=True))
            continue

        if index + 1 < len(lines):
            delimiter_line, delimiter_ending = _line_parts(lines[index + 1])
            if "|" in bare and _is_table_delimiter(delimiter_line):
                headers = _split_table_row(bare)
                index += 2
                rows: list[list[str]] = []
                while index < len(lines):
                    row_line, _ = _line_parts(lines[index])
                    if not row_line.strip() or "|" not in row_line:
                        break
                    rows.append(_split_table_row(row_line))
                    index += 1
                rendered_headers = [
                    _render_markdown_inline(header, streaming=streaming)
                    for header in headers
                ]
                if rows:
                    for row in rows:
                        fields: list[str] = []
                        for column, header in enumerate(rendered_headers):
                            cell = _render_markdown_inline(
                                row[column] if column < len(row) else "",
                                streaming=streaming,
                            )
                            if header or cell:
                                label = header or f"欄位 {column + 1}"
                                fields.append(f"*{label}:* {cell}")
                        output.append("- " + " | ".join(fields) + "\n")
                    output.append("\n")
                else:
                    output.append(
                        " | ".join(f"*{header}*" for header in rendered_headers) + "\n\n",
                    )
                continue
            if re.fullmatch(r"[ ]{0,3}(?:=+|-+)[ \t]*", delimiter_line):
                output.append(
                    _render_heading_inline(bare.strip(), streaming=streaming)
                    + delimiter_ending,
                )
                index += 2
                continue

        heading = re.match(r"^[ ]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", bare)
        if heading:
            output.append(
                _render_heading_inline(heading.group(1), streaming=streaming)
                + ending,
            )
            index += 1
            continue
        if re.fullmatch(r"[ \t]*(?:\*{3,}|_{3,}|-{3,})[ \t]*", bare):
            output.append(f"──────────{ending}")
            index += 1
            continue

        unordered = re.match(r"^([ \t]*)[-+*][ \t]+(.+)$", bare)
        if unordered:
            output.append(
                f"{unordered.group(1)}- "
                f"{_render_markdown_inline(unordered.group(2), streaming=streaming)}"
                f"{ending}",
            )
            index += 1
            continue
        ordered = re.match(r"^([ \t]*)(\d+)[.)][ \t]+(.+)$", bare)
        if ordered:
            output.append(
                f"{ordered.group(1)}{ordered.group(2)}. "
                f"{_render_markdown_inline(ordered.group(3), streaming=streaming)}"
                f"{ending}",
            )
            index += 1
            continue
        quote = re.match(r"^([ \t]*)>[ \t]?(.*)$", bare)
        if quote:
            output.append(
                f"{quote.group(1)}> "
                f"{_render_markdown_inline(quote.group(2), streaming=streaming)}"
                f"{ending}",
            )
            index += 1
            continue

        paragraph: list[str] = []
        while index < len(lines):
            if paragraph and _special_block_start(lines, index):
                break
            candidate, _ = _line_parts(lines[index])
            if not candidate.strip():
                break
            paragraph.append(lines[index])
            index += 1
        output.append(
            _render_markdown_inline("".join(paragraph), streaming=streaming),
        )

    return "".join(output)


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

    return _render_markdown_blocks(value, streaming=streaming)


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


def _is_emoji_tag(char: str) -> bool:
    return 0xE0020 <= ord(char) <= 0xE007F


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
                or char == "\u20e3"
                or _is_variation_selector(char)
                or _is_emoji_modifier(char)
                or _is_emoji_tag(char)
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
    name = str(getattr(component, "name", "") or "").strip()
    if name:
        return f"@{name.lstrip('@')}"
    value = str(getattr(component, "qq", "") or "")
    value = value.split("@", 1)[0].split(":", 1)[0]
    return f"@{value}"


MentionResolver = Callable[[str], str | None]


def _valid_mention_jid(value: str | None) -> str:
    """Return a strict, device-independent user JID suitable for Baileys."""

    raw = str(value or "").strip()
    if is_lid_jid(raw):
        return base_lid_jid(raw)
    if is_pn_jid(raw):
        return base_pn_jid(raw)
    return ""


def _resolve_public_mention(
    public_id: str,
    resolver: MentionResolver | None,
    fallback: str,
) -> str:
    if resolver is not None:
        try:
            resolved = _valid_mention_jid(resolver(public_id))
        except Exception as exc:
            logger.warning(
                "WhatsApp @提及身份回解析失败: public_id=%s error=%s",
                public_id,
                exc,
            )
        else:
            if resolved:
                return resolved
    return fallback


def mention_jid_from_at(
    component: At,
    resolver: MentionResolver | None = None,
) -> str | None:
    value = str(getattr(component, "qq", "") or getattr(component, "name", "") or "").strip()
    if not value:
        return None
    if value.startswith("@") and value.count("@") == 1:
        value = value[1:].strip()
    if value.lower().lstrip("@") == "all":
        # Baileys exposes WhatsApp's native mention-all bit separately from
        # ordinary JIDs.  The Gateway recognizes this reserved transport token.
        return "all"
    if "@" in value:
        jid = _valid_mention_jid(value)
        if jid:
            return jid
        logger.warning("WhatsApp @提及 JID 解析失败: value=%s", value)
        return None

    lid_match = re.fullmatch(r"lid-(\d+)", value, flags=re.IGNORECASE)
    if lid_match:
        public_id = f"lid-{lid_match.group(1)}"
        return _resolve_public_mention(
            public_id,
            resolver,
            f"{lid_match.group(1)}@lid",
        )

    pn_jid = base_pn_jid(value)
    if pn_jid:
        public_id = pn_jid.split("@", 1)[0]
        return _resolve_public_mention(public_id, resolver, pn_jid)

    logger.warning("WhatsApp @提及 JID 解析失败: value=%s", value)
    return None


def mention_jid_for_token(
    token: str,
    resolver: MentionResolver | None = None,
) -> str | None:
    """Resolve only the explicit public/JID grammar, never scrape digits."""

    component = At(qq=str(token or "").lstrip("@"))
    return mention_jid_from_at(component, resolver)


async def mentions_for_text(
    client: WhatsAppGatewayClient,
    target: str,
    text: str,
    explicit_mentions: Sequence[str | MentionRef],
) -> list[str]:
    del client, target

    def contains_visible_token(token: str) -> bool:
        offset = 0
        while True:
            start = text.find(token, offset)
            if start < 0:
                return False
            end = start + len(token)
            previous = text[start - 1] if start else ""
            following = text[end] if end < len(text) else ""
            if not token.startswith("@") or (
                (not previous or (not previous.isalnum() and previous not in "_@"))
                and (not following or (not following.isalnum() and following != "_"))
            ):
                return True
            offset = start + max(1, len(token))

    resolved: list[str] = []
    for mention in explicit_mentions:
        if isinstance(mention, MentionRef):
            if mention.text and not contains_visible_token(mention.text):
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
    quote_state: QuoteState | None = None,
) -> tuple[None, list[MentionRef]]:
    if not pending:
        return None, list(mentions or [])  # type: ignore[list-item]
    rendered = format_whatsapp_markdown(pending, source_format=source_format)
    if not has_visible_whatsapp_content(rendered):
        return None, []
    state = quote_state or QuoteState(quoted_message_id, quoted_participant)
    atomic_texts = [
        mention.text
        for mention in (mentions or [])
        if isinstance(mention, MentionRef) and mention.text
    ]
    for chunk in split_whatsapp_text(
        rendered,
        text_chunk_limit,
        atomic_texts=atomic_texts,
    ):
        if not has_visible_whatsapp_content(chunk):
            continue
        chunk_mentions = await mentions_for_text(client, target, chunk, mentions or [])
        quote_kwargs = state.kwargs()
        await client.send_text(
            target,
            chunk,
            link_preview=should_link_preview(chunk, link_preview_single_url),
            mentions=chunk_mentions,
            **quote_kwargs,
        )
        state.consume()
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
    mention_resolver: MentionResolver | None = None,
    quote_state: QuoteState | None = None,
) -> tuple[str | None, list[MentionRef]]:
    """累積相鄰 Plain/At 的原始 Markdown，在真正發送前只轉換一次。"""
    pending_raw: str | None = None
    pending_mentions: list[MentionRef] = []
    state = quote_state or QuoteState(quoted_message_id, quoted_participant)
    flush_kwargs: dict[str, Any] = {
        "link_preview_single_url": link_preview_single_url,
        "text_chunk_limit": text_chunk_limit,
        "quote_state": state,
    }

    async def flush() -> None:
        nonlocal pending_raw, pending_mentions
        pending_raw, pending_mentions = await flush_pending_text(
            client,
            target,
            pending_raw,
            pending_mentions,
            **flush_kwargs,
        )

    def append_unavailable(label: str) -> None:
        nonlocal pending_raw
        separator = "" if not pending_raw or pending_raw[-1:].isspace() else " "
        pending_raw = (pending_raw or "") + separator + f"[{label} unavailable]"

    async def prepare_caption() -> tuple[str | None, list[str]]:
        if not pending_raw:
            return None, []
        rendered = format_whatsapp_markdown(pending_raw)
        atomic_texts = [mention.text for mention in pending_mentions if mention.text]
        chunks = [
            chunk
            for chunk in split_whatsapp_text(
                rendered,
                text_chunk_limit,
                atomic_texts=atomic_texts,
            )
            if has_visible_whatsapp_content(chunk)
        ]
        if not chunks:
            return None, []
        for chunk in chunks[:-1]:
            chunk_mentions = await mentions_for_text(
                client, target, chunk, pending_mentions,
            )
            quote_kwargs = state.kwargs()
            await client.send_text(
                target,
                chunk,
                link_preview=should_link_preview(chunk, link_preview_single_url),
                mentions=chunk_mentions,
                **quote_kwargs,
            )
            state.consume()
        caption = chunks[-1]
        caption_mentions = await mentions_for_text(
            client, target, caption, pending_mentions,
        )
        return caption, caption_mentions

    for component in chain:
        if isinstance(component, Reply):
            # Reply is transport metadata, never user-visible nested content.
            continue
        if isinstance(component, Plain):
            pending_raw = (pending_raw or "") + (component.text or "")
            continue
        if isinstance(component, At):
            visible = mention_text_from_at(component)
            jid = mention_jid_from_at(component, mention_resolver)
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
                append_unavailable("Image")
                continue
            if not media_path:
                append_unavailable("Image")
                continue
            media_kind = media_kind_from_component(component, "image")
            if media_kind == "sticker" and use_caption and pending_raw:
                await flush()
            caption, caption_mentions = (
                await prepare_caption()
                if use_caption and media_kind != "sticker"
                else (None, [])
            )
            quote_kwargs = state.kwargs()
            await client.send_media(
                target,
                media_kind,
                media_path,
                caption,
                mentions=caption_mentions,
                **quote_kwargs,
            )
            state.consume()
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
                append_unavailable("Audio")
                continue
            if media_path:
                quote_kwargs = state.kwargs()
                await client.send_media(
                    target,
                    "audio",
                    media_path,
                    None,
                    **quote_kwargs,
                )
                state.consume()
            else:
                append_unavailable("Audio")
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
                append_unavailable("Video")
                continue
            if media_path:
                caption, caption_mentions = (
                    await prepare_caption() if use_caption else (None, [])
                )
                quote_kwargs = state.kwargs()
                await client.send_media(
                    target,
                    "video",
                    media_path,
                    caption,
                    mentions=caption_mentions,
                    **quote_kwargs,
                )
                state.consume()
                pending_raw = None
                pending_mentions = []
            else:
                append_unavailable("Video")
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
                append_unavailable("File")
                continue
            if resolved:
                caption, caption_mentions = (
                    await prepare_caption() if use_caption else (None, [])
                )
                quote_kwargs = state.kwargs()
                await client.send_media(
                    target,
                    "document",
                    resolved,
                    caption,
                    mentions=caption_mentions,
                    file_name=str(getattr(component, "name", "") or ""),
                    **quote_kwargs,
                )
                state.consume()
                pending_raw = None
                pending_mentions = []
            else:
                append_unavailable("File")
        elif isinstance(component, Location):
            await flush()
            quote_kwargs = state.kwargs()
            await client.send_location(
                target,
                float(getattr(component, "lat", 0) or 0),
                float(getattr(component, "lon", 0) or 0),
                str(getattr(component, "title", "") or ""),
                str(getattr(component, "content", "") or ""),
                **quote_kwargs,
            )
            state.consume()
        elif isinstance(component, (WhatsAppButtons, WhatsAppList, WhatsAppPoll, WhatsAppEdit)):
            await flush()
            if isinstance(component, WhatsAppEdit):
                await send_whatsapp_component(client, target, component)
            else:
                quote_kwargs = state.kwargs()
                await send_whatsapp_component(client, target, component, **quote_kwargs)
            # An edit is still a successful transport operation even though it
            # does not create a new message or carry Reply metadata.
            state.consume()
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
                    mention_resolver=mention_resolver,
                    quote_state=state,
                )
                pending_raw = nested_pending
                pending_mentions = nested_mentions
            else:
                fallback = _component_text_fallback(component)
                if fallback:
                    pending_raw = (pending_raw or "") + fallback
                    logger.warning(
                        "WhatsApp 消息组件无原生等价，已降级为文本: %s",
                        component.__class__.__name__,
                    )
                else:
                    logger.warning(
                        "WhatsApp 消息链不支持组件，已忽略: %s",
                        component.__class__.__name__,
                    )

    return pending_raw, pending_mentions


def _iter_nested_components(component: Any) -> list[Any]:
    nested: list[Any] = []
    chain = getattr(component, "chain", None)
    if chain:
        nested.extend(list(chain))
    content = getattr(component, "content", None)
    if isinstance(content, (list, tuple)):
        nested.extend(list(content))
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


def _component_text_fallback(component: Any) -> str:
    """Preserve useful content for standard components WhatsApp cannot send."""

    class_name = component.__class__.__name__
    if class_name in {"Unknown"}:
        return str(getattr(component, "text", "") or "")
    if class_name == "Face":
        return f"[Face:{getattr(component, 'id', '')}]"
    if class_name in {"RPS", "Dice", "Shake"}:
        return f"[{class_name}]"
    if class_name == "Poke":
        target = (
            getattr(component, "id", None)
            or getattr(component, "qq", None)
            or ""
        )
        return f"[Poke{f':{target}' if target else ''}]"
    if class_name == "Share":
        values = [
            getattr(component, "title", ""),
            getattr(component, "content", ""),
            getattr(component, "url", ""),
        ]
        return " — ".join(str(value) for value in values if value)
    if class_name == "Music":
        values = [
            getattr(component, "title", ""),
            getattr(component, "content", ""),
            getattr(component, "audio", "") or getattr(component, "url", ""),
        ]
        detail = " — ".join(str(value) for value in values if value)
        return f"🎵 {detail}" if detail else "🎵"
    if class_name == "Contact":
        target = getattr(component, "id", None) or ""
        return f"[Contact{f':{target}' if target else ''}]"
    if class_name == "Json":
        try:
            return json.dumps(
                getattr(component, "data", {}),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return "[JSON]"
    if class_name == "Forward":
        target = getattr(component, "id", None) or ""
        return f"[Forward{f':{target}' if target else ''}]"
    return ""
