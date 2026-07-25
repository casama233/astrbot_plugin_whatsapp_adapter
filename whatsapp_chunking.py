"""Formatting-aware WhatsApp text chunking.

This module keeps message splitting independent from Markdown conversion so the
streaming path can safely close and reopen WhatsApp-native formatting markers.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator


def _is_variation_selector(char: str) -> bool:
    code = ord(char)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def _is_emoji_modifier(char: str) -> bool:
    return 0x1F3FB <= ord(char) <= 0x1F3FF


def _grapheme_units(text: str) -> Iterator[str]:
    """Yield practical grapheme clusters without external dependencies."""
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


def split_whatsapp_text(text: str, limit: int) -> list[str]:
    """Split WhatsApp-native text while preserving formatting across chunks.

    Each emitted chunk contains visible payload, never just formatting markers.
    Active bold/italic/strike/code delimiters are closed at the end of a chunk
    and reopened in the next one.
    """
    value = str(text or "")
    if not value:
        return []
    limit = max(16, int(limit))
    units = list(_grapheme_units(value))
    chunks: list[str] = []
    current: list[str] = []
    active: list[str] = []
    code_delimiter: str | None = None
    payload_units = 0
    index = 0

    def closing_suffix() -> str:
        suffix = code_delimiter or ""
        suffix += "".join(reversed(active))
        return suffix

    def opening_prefix() -> str:
        prefix = "".join(active)
        if code_delimiter:
            prefix += code_delimiter
        return prefix

    def flush() -> None:
        nonlocal current, payload_units
        if payload_units > 0:
            chunks.append("".join(current) + closing_suffix())
        current = list(opening_prefix())
        payload_units = 0

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

        before_code = code_delimiter
        structural = False
        current.append(token)

        if token.startswith("`") and set(token) == {"`"}:
            if code_delimiter == token:
                code_delimiter = None
                structural = True
            elif code_delimiter is None and len(token) in (1, 3):
                code_delimiter = token
                structural = True
        elif code_delimiter is None and token in ("*", "_", "~"):
            previous = units[index - 1] if index > 0 else ""
            following = units[index + 1] if index + 1 < len(units) else ""
            at_list_prefix = (
                token == "*"
                and (index == 0 or previous == "\n")
                and following.isspace()
            )
            if not at_list_prefix:
                if active and active[-1] == token and previous and not previous.isspace():
                    active.pop()
                    structural = True
                elif following and not following.isspace():
                    active.append(token)
                    structural = True

        if not structural or (before_code is not None and token != before_code):
            payload_units += 1
        index += 1

    if current and payload_units > 0:
        chunks.append("".join(current) + closing_suffix())

    return [chunk for chunk in chunks if chunk]
