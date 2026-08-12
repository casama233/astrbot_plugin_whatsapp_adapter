"""Markdown <-> WhatsApp formatting conversion.

This module is the source of truth for text formatting.  It intentionally keeps
Markdown parsing conservative: unsupported constructs are degraded to readable
WhatsApp-native text without deleting literal punctuation that could be user
content.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

SourceFormat = Literal["markdown", "whatsapp", "plain"]


def _placeholder(index: int, kind: str = "PROTECTED") -> str:
    return f"\x00WA{kind}{index}\x00"


def _protect_escaped_markdown(value: str) -> tuple[str, list[str]]:
    """Protect Markdown escapes without stripping the user's backslash.

    Removing the backslash would turn e.g. ``\\*literal\\*`` into active
    WhatsApp formatting markers.  Keeping the original escape sequence is a
    safer lossless degradation on transports that do not define Markdown's
    escaping rules.
    """

    protected: list[str] = []

    def replace(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return _placeholder(len(protected) - 1, "ESC")

    return re.sub(r"\\[\\`*_{}\[\]()#+\-.!|>~]", replace, value), protected


def _restore(value: str, protected: Sequence[str], kind: str) -> str:
    for index, text in enumerate(protected):
        value = value.replace(_placeholder(index, kind), text)
    return value


def _extract_fenced_code(value: str, *, streaming: bool) -> tuple[str, list[str]]:
    """Convert fenced Markdown code to WhatsApp triple-backtick code blocks."""

    del streaming  # Kept in the signature for formatter contract compatibility.
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

        # Language labels are not part of WhatsApp's monospace syntax.
        code = "".join(body)
        protected.append(f"```{code}```")
        output.append(_placeholder(len(protected) - 1, "FENCE"))

    return "".join(output), protected


def _protect_inline_code(value: str, *, streaming: bool) -> tuple[str, list[str]]:
    """Protect Markdown code spans, pairing equal-length backtick runs.

    During streaming an unmatched opener is treated as provisional code and is
    closed for a renderable edit.  For a final message an unmatched opener is
    left literal so one stray backtick cannot swallow the entire remainder of
    the response.
    """

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
                output.append(value[index:run_end])
                index = run_end
                continue
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
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells
    )


def _convert_links(value: str) -> str:
    # WhatsApp auto-links bare URLs. Preserve the label and expose the target.
    value = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)",
        r"\1 (\2)",
        value,
    )
    return re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)",
        r"\1 (\2)",
        value,
    )


def _suppress_streaming_openers(value: str) -> str:
    """Hide only provisional unmatched multi-character Markdown openers."""

    for delimiter in ("**", "__", "~~"):
        if value == delimiter:
            value = ""
            continue
        escaped = re.escape(delimiter)
        value = re.sub(
            rf"(?<!{re.escape(delimiter[0])}){escaped}(?=\S)(?![\s\S]*{escaped})",
            "",
            value,
        )
    return value


def _convert_emphasis(value: str, *, streaming: bool) -> str:
    # Single-star Markdown italic must be handled before introducing WhatsApp
    # bold stars for strong Markdown.
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
    if streaming:
        value = _suppress_streaming_openers(value)
    return value


def _cleanup_malformed_tail(value: str) -> str:
    """Drop only mixed control-marker tails known to come from stale edits.

    Literal single delimiters and homogeneous runs such as ``*``, ``**`` or
    ``~~`` are valid user content and must not be silently deleted.
    """

    match = re.search(r"([*_~]{2,})$", value)
    if not match:
        return value
    run = match.group(1)
    if len(set(run)) < 2:
        return value
    if match.start() > 0 and value[match.start() - 1] == "\\":
        return value
    return value[: match.start()]


def _render_inline_fragment(value: str, *, streaming: bool) -> str:
    return _convert_emphasis(
        _convert_links(value),
        streaming=streaming,
    )


def _protect_native(protected: list[str], value: str) -> str:
    protected.append(value)
    return _placeholder(len(protected) - 1, "NATIVE")


def _convert_markdown_blocks(
    value: str,
    *,
    streaming: bool,
) -> tuple[str, list[str]]:
    """Degrade unsupported Markdown blocks without corrupting inline markup."""

    lines = value.splitlines(keepends=True)
    output: list[str] = []
    protected_native: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        bare = line.rstrip("\r\n")
        ending = line[len(bare) :]

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

            if not rows:
                labels = [
                    _render_inline_fragment(header, streaming=streaming)
                    for header in headers
                    if header
                ]
                if labels:
                    rendered = "- " + " | ".join(
                        label if "*" in label else f"*{label}*" for label in labels
                    ) + "\n"
                    output.append(_protect_native(protected_native, rendered))
                continue

            for row in rows:
                fields: list[str] = []
                for column, header in enumerate(headers):
                    cell = row[column] if column < len(row) else ""
                    if not header and not cell:
                        continue
                    label_raw = header or f"欄位 {column + 1}"
                    label = _render_inline_fragment(label_raw, streaming=streaming)
                    rendered_cell = _render_inline_fragment(cell, streaming=streaming)
                    if "*" in label:
                        label_text = f"{label}:"
                    else:
                        label_text = f"*{label}:*"
                    fields.append(f"{label_text} {rendered_cell}".rstrip())
                output.append(
                    _protect_native(
                        protected_native,
                        "- " + " | ".join(fields) + "\n",
                    )
                )
            output.append("\n")
            continue

        heading = re.match(
            r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$",
            bare,
        )
        if heading:
            rendered = _render_inline_fragment(
                heading.group(1),
                streaming=streaming,
            )
            # Avoid adjacent nested bold markers such as ``**Bold* heading*``.
            # A complex heading keeps its inline formatting; a plain heading is
            # represented as WhatsApp bold to preserve visual hierarchy.
            if any(marker in rendered for marker in ("*", "_", "~", "`")):
                native = rendered
            else:
                native = f"*{rendered}*"
            output.append(
                _protect_native(protected_native, native + ending)
            )
            index += 1
            continue

        if re.fullmatch(r"[ \t]*(?:\*{3,}|_{3,}|-{3,})[ \t]*", bare):
            output.append("──────────" + ending)
            index += 1
            continue

        unordered = re.match(r"^([ \t]*)[-+*][ \t]+(.+)$", bare)
        if unordered:
            output.append(f"{unordered.group(1)}- {unordered.group(2)}{ending}")
            index += 1
            continue

        ordered = re.match(r"^([ \t]*)(\d{1,2})[.)][ \t]+(.+)$", bare)
        if ordered:
            output.append(
                f"{ordered.group(1)}{ordered.group(2)}. {ordered.group(3)}{ending}"
            )
            index += 1
            continue

        quote = re.match(r"^([ \t]*)>[ \t]?(.*)$", bare)
        if quote:
            output.append(f"{quote.group(1)}> {quote.group(2)}{ending}")
            index += 1
            continue

        output.append(line)
        index += 1

    return "".join(output), protected_native


def format_whatsapp_markdown(
    text: str,
    *,
    streaming: bool = False,
    source_format: SourceFormat = "markdown",
) -> str:
    """Convert generic Markdown to WhatsApp-native formatting conservatively."""

    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value or source_format in ("whatsapp", "plain"):
        return value
    if source_format != "markdown":
        raise ValueError(f"unsupported source_format: {source_format}")

    value, escaped = _protect_escaped_markdown(value)
    value, fenced = _extract_fenced_code(value, streaming=streaming)
    value, inline = _protect_inline_code(value, streaming=streaming)
    value, native = _convert_markdown_blocks(value, streaming=streaming)
    value = _convert_links(value)
    value = _convert_emphasis(value, streaming=streaming)
    value = _cleanup_malformed_tail(value)
    value = _restore(value, native, "NATIVE")
    value = _restore(value, fenced, "FENCE")
    value = _restore(value, inline, "INLINE")
    return _restore(value, escaped, "ESC")


def format_markdown_from_whatsapp(text: str) -> str:
    """Convert WhatsApp-native formatting to generic Markdown without code edits."""

    value = str(text or "")
    if not value:
        return ""
    value, fenced = _extract_fenced_code(value, streaming=False)
    value, inline = _protect_inline_code(value, streaming=False)
    value = re.sub(
        r"(?<!\*)\*(?![\s*])([^*\n]*?\S)\*(?!\*)",
        r"**\1**",
        value,
    )
    value = re.sub(
        r"(?<!~)~(?![\s~])([^~\n]*?\S)~(?!~)",
        r"~~\1~~",
        value,
    )
    value = _restore(value, fenced, "FENCE")
    return _restore(value, inline, "INLINE")
