"""Formatting-aware WhatsApp text chunking.

Only delimiters that can be paired in the complete rendered text are treated
as transport formatting.  Literal or still-unmatched Markdown markers remain
payload and are never "fixed" by appending a synthetic closing marker.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass


_URL_RE = re.compile(r"(?i)(?:https?://|www\.)[^\s<>]+")
_TRAILING_URL_PUNCTUATION = ".,!?;:，。！？；：、"
_WORD_JOINER = "\u2060"


@dataclass(frozen=True, slots=True)
class _Atom:
    text: str
    start: int
    end: int
    atomic: bool = False


def _is_variation_selector(char: str) -> bool:
    code = ord(char)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def _is_emoji_modifier(char: str) -> bool:
    return 0x1F3FB <= ord(char) <= 0x1F3FF


def _is_emoji_tag(char: str) -> bool:
    return 0xE0020 <= ord(char) <= 0xE007F


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


def _balanced_url_end(value: str, start: int, raw_end: int) -> int:
    """Trim prose punctuation without truncating balanced URL parentheses."""
    end = raw_end
    while end > start and value[end - 1] in _TRAILING_URL_PUNCTUATION:
        end -= 1
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    changed = True
    while changed and end > start:
        changed = False
        for opening, closing in pairs:
            if value[end - 1] == closing:
                candidate = value[start:end]
                if candidate.count(closing) > candidate.count(opening):
                    end -= 1
                    changed = True
                    break
    return end


def _url_spans(value: str) -> Iterator[tuple[int, int]]:
    active_styles: list[str] = []
    active_code: str | None = None
    scan_cursor = 0
    for match in _URL_RE.finditer(value):
        active_styles, active_code = _advance_transport_state(
            value,
            scan_cursor,
            match.start(),
            active_styles,
            active_code,
        )
        end = _balanced_url_end(value, match.start(), match.end())
        protected_end = end

        # The URL matcher deliberately accepts RFC-safe punctuation such as
        # ``*``, ``_`` and ``~``.  When a URL is the final payload inside an
        # already-open WhatsApp formatting span, however, the closing marker
        # belongs to the transport rather than the URL.  Leave those confirmed
        # closers visible to the delimiter parser below.
        if (
            active_code
            and protected_end - len(active_code) >= match.start()
            and value[protected_end - len(active_code) : protected_end] == active_code
        ):
            protected_end -= len(active_code)
        elif not active_code:
            for count in range(len(active_styles), 0, -1):
                closing = "".join(reversed(active_styles[-count:]))
                if (
                    protected_end - len(closing) >= match.start()
                    and value[protected_end - len(closing) : protected_end] == closing
                ):
                    protected_end -= len(closing)
                    break

        if protected_end > match.start():
            yield match.start(), protected_end
        scan_cursor = protected_end


def _atomic_spans(value: str, atomic_texts: Sequence[str]) -> list[tuple[int, int]]:
    candidates = list(_url_spans(value))
    for raw in atomic_texts:
        needle = str(raw or "")
        if not needle:
            continue
        offset = 0
        while True:
            start = value.find(needle, offset)
            if start < 0:
                break
            end = start + len(needle)
            previous = value[start - 1] if start else ""
            following = value[end] if end < len(value) else ""
            mention_boundary = not needle.startswith("@") or (
                (not previous or (not previous.isalnum() and previous not in "_@"))
                and (not following or (not following.isalnum() and following != "_"))
            )
            if mention_boundary:
                candidates.append((start, end))
            offset = start + max(1, len(needle))

    # Prefer the longest span at each position, then discard overlaps.  A URL
    # and an @mention may overlap only in malformed input; deterministic
    # longest-first selection is safer than partially protecting either one.
    selected: list[tuple[int, int]] = []
    cursor = 0
    for start, end in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if start < cursor or end <= start:
            continue
        selected.append((start, end))
        cursor = end
    return selected


def _atoms(value: str, atomic_texts: Sequence[str]) -> list[_Atom]:
    spans = _atomic_spans(value, atomic_texts)
    by_start = {start: end for start, end in spans}
    result: list[_Atom] = []
    index = 0
    while index < len(value):
        atomic_end = by_start.get(index)
        if atomic_end is not None:
            result.append(_Atom(value[index:atomic_end], index, atomic_end, True))
            index = atomic_end
            continue

        # Do not let a grapheme scan cross the beginning of an opaque span.
        next_atomic = min((start for start in by_start if start > index), default=len(value))
        segment = value[index:next_atomic]
        segment_offset = index
        units = list(_grapheme_units(segment))
        unit_index = 0
        while unit_index < len(units):
            unit = units[unit_index]
            start = segment_offset
            if unit == "`":
                run = 1
                while unit_index + run < len(units) and units[unit_index + run] == "`":
                    run += 1
                unit = "`" * run
                unit_index += run - 1
            end = start + len(unit)
            result.append(_Atom(unit, start, end))
            segment_offset = end
            unit_index += 1
        index = next_atomic
    return result


def _is_punctuation(char: str) -> bool:
    return bool(char) and unicodedata.category(char)[0] in {"P", "S"}


def _delimiter_flanking(value: str, atom: _Atom) -> tuple[bool, bool]:
    previous = value[atom.start - 1] if atom.start else ""
    following = value[atom.end] if atom.end < len(value) else ""
    if previous == _WORD_JOINER or following == _WORD_JOINER:
        return False, False
    previous_space = not previous or previous.isspace()
    following_space = not following or following.isspace()
    previous_punct = _is_punctuation(previous)
    following_punct = _is_punctuation(following)
    left_flanking = not following_space and (
        not following_punct or previous_space or previous_punct
    )
    right_flanking = not previous_space and (
        not previous_punct or following_space or following_punct
    )
    if atom.text in {"_", "~"}:
        can_open = left_flanking and (not right_flanking or previous_punct)
        can_close = right_flanking and (not left_flanking or following_punct)
    else:
        can_open = left_flanking
        can_close = right_flanking
    return can_open, can_close


def _advance_transport_state(
    value: str,
    start: int,
    end: int,
    active_styles: Sequence[str],
    active_code: str | None,
) -> tuple[list[str], str | None]:
    """Advance lightweight WhatsApp formatting state outside opaque URLs."""
    styles = list(active_styles)
    code = active_code
    index = start
    while index < end:
        char = value[index]
        if char == "`":
            run_end = index + 1
            while run_end < end and value[run_end] == "`":
                run_end += 1
            delimiter = value[index:run_end]
            protected = (
                (index and value[index - 1] == _WORD_JOINER)
                or (run_end < len(value) and value[run_end] == _WORD_JOINER)
            )
            if not protected:
                if code == delimiter:
                    code = None
                elif code is None and delimiter in {"`", "```"}:
                    code = delimiter
            index = run_end
            continue

        if code or char not in {"*", "_", "~"}:
            index += 1
            continue
        atom = _Atom(char, index, index + 1)
        can_open, can_close = _delimiter_flanking(value, atom)
        at_list_prefix = (
            char == "*"
            and (index == 0 or value[index - 1] == "\n")
            and index + 1 < len(value)
            and value[index + 1].isspace()
        )
        if not at_list_prefix:
            following = value[index + 1] if index + 1 < len(value) else ""
            if styles and styles[-1] == char and (can_close or not following):
                styles.pop()
            elif can_open:
                styles.append(char)
        index += 1
    return styles, code


def _paired_delimiters(
    value: str,
    atoms: Sequence[_Atom],
) -> tuple[dict[int, str], dict[int, str]]:
    """Return roles for confirmed code and style delimiter pairs."""
    code_roles: dict[int, str] = {}
    code_ranges: list[tuple[int, int]] = []

    def pair_code_delimiter(
        delimiter: str,
        excluded: set[int],
        *,
        reset_at_newline: bool,
    ) -> None:
        opening: int | None = None
        for index, atom in enumerate(atoms):
            if index in excluded or atom.atomic or atom.text != delimiter:
                continue
            if (
                (atom.start and value[atom.start - 1] == _WORD_JOINER)
                or (atom.end < len(value) and value[atom.end] == _WORD_JOINER)
            ):
                continue
            if opening is None:
                opening = index
                continue
            if reset_at_newline and "\n" in value[atoms[opening].end : atom.start]:
                opening = index
                continue
            code_roles[opening] = "open"
            code_roles[index] = "close"
            code_ranges.append((opening, index))
            opening = None

    # A stray single backtick must not prevent a later, valid triple fence from
    # being recognized. Pair fences first, then inline code outside them.
    pair_code_delimiter("```", set(), reset_at_newline=False)
    triple_covered: set[int] = set()
    for opening, closing in code_ranges:
        triple_covered.update(range(opening, closing + 1))
    pair_code_delimiter("`", triple_covered, reset_at_newline=True)

    inside_code: set[int] = set()
    for opening, closing in code_ranges:
        inside_code.update(range(opening + 1, closing))

    style_roles: dict[int, str] = {}
    stack: list[tuple[str, int]] = []
    for index, atom in enumerate(atoms):
        if atom.atomic or index in inside_code or atom.text not in {"*", "_", "~"}:
            continue
        can_open, can_close = _delimiter_flanking(value, atom)
        at_list_prefix = (
            atom.text == "*"
            and (atom.start == 0 or value[atom.start - 1] == "\n")
            and atom.end < len(value)
            and value[atom.end].isspace()
        )
        if at_list_prefix:
            continue
        following = value[atom.end] if atom.end < len(value) else ""
        confirmed_transport_close = (
            bool(stack)
            and stack[-1][0] == atom.text
            and (can_close or not following)
        )
        if confirmed_transport_close:
            _marker, opening = stack.pop()
            style_roles[opening] = "open"
            style_roles[index] = "close"
        elif can_open:
            stack.append((atom.text, index))
    return code_roles, style_roles


def _fit_delimiter_roles_to_limit(
    atoms: Sequence[_Atom],
    code_roles: dict[int, str],
    style_roles: dict[int, str],
    limit: int,
) -> tuple[dict[int, str], dict[int, str], set[int]]:
    """Keep only formatting pairs whose generated wrappers leave payload room.

    Each continued chunk needs both an opening prefix and a closing suffix.
    When adversarial input nests more delimiters than the configured transport
    limit can hold, the excess pair is omitted instead of emitting an oversized
    marker-only wrapper. Code spans take precedence over cosmetic styles.
    """

    pairs: list[tuple[int, int, str, str]] = []
    code_open: dict[str, int] = {}
    style_stack: list[tuple[str, int]] = []
    for index, atom in enumerate(atoms):
        code_role = code_roles.get(index)
        if code_role == "open":
            code_open[atom.text] = index
        elif code_role == "close":
            opening = code_open.pop(atom.text, None)
            if opening is not None:
                pairs.append((opening, index, atom.text, "code"))

        style_role = style_roles.get(index)
        if style_role == "open":
            style_stack.append((atom.text, index))
        elif style_role == "close":
            for stack_index in range(len(style_stack) - 1, -1, -1):
                marker, opening = style_stack[stack_index]
                if marker == atom.text:
                    del style_stack[stack_index:]
                    pairs.append((opening, index, atom.text, "style"))
                    break

    wrapper_budget = max(0, (limit - 1) // 2)
    active_weight = [0] * (len(atoms) + 1)
    kept: set[tuple[int, int]] = set()
    # Preserve semantic code first, then retain outer styles in source order.
    ordered = sorted(
        pairs,
        key=lambda pair: (0 if pair[3] == "code" else 1, pair[0], -pair[1]),
    )
    for opening, closing, marker, _kind in ordered:
        weight = len(marker)
        if weight > wrapper_budget:
            continue
        if max(active_weight[opening:closing], default=0) + weight > wrapper_budget:
            continue
        kept.add((opening, closing))
        for index in range(opening, closing):
            active_weight[index] += weight

    kept_code: dict[int, str] = {}
    kept_style: dict[int, str] = {}
    ignored: set[int] = set()
    for opening, closing, _marker, kind in pairs:
        if (opening, closing) in kept:
            target = kept_code if kind == "code" else kept_style
            target[opening] = "open"
            target[closing] = "close"
        else:
            ignored.update((opening, closing))
    return kept_code, kept_style, ignored


def split_whatsapp_text(
    text: str,
    limit: int,
    *,
    atomic_texts: Sequence[str] = (),
) -> list[str]:
    """Split WhatsApp-native text without cutting URLs or named atoms.

    Formatting is closed and reopened only for delimiters that form a
    confirmed pair in the complete input.  An individual URL or ``atomic_text``
    longer than ``limit`` is intentionally emitted intact and may therefore
    exceed the limit; corrupting a link or a native mention is worse than one
    oversized transport unit.
    """
    value = str(text or "")
    if not value:
        return []
    limit = max(16, int(limit))
    units = _atoms(value, tuple(str(item) for item in atomic_texts if item))
    code_roles, style_roles = _paired_delimiters(value, units)
    code_roles, style_roles, ignored_delimiters = _fit_delimiter_roles_to_limit(
        units,
        code_roles,
        style_roles,
        limit,
    )

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    payload_units = 0
    active_styles: list[str] = []
    code_delimiter: str | None = None

    def closing_suffix(
        styles: Sequence[str] | None = None,
        code: str | None | object = ...,
    ) -> str:
        selected_styles = active_styles if styles is None else styles
        selected_code = code_delimiter if code is ... else code
        return (str(selected_code) if selected_code else "") + "".join(
            reversed(selected_styles),
        )

    def opening_prefix() -> str:
        return "".join(active_styles) + (code_delimiter or "")

    def next_state(index: int, atom: _Atom) -> tuple[list[str], str | None]:
        styles = list(active_styles)
        code = code_delimiter
        code_role = code_roles.get(index)
        style_role = style_roles.get(index)
        if code_role == "open":
            code = atom.text
        elif code_role == "close":
            code = None
        elif style_role == "open":
            styles.append(atom.text)
        elif style_role == "close":
            if styles and styles[-1] == atom.text:
                styles.pop()
            elif atom.text in styles:
                styles.remove(atom.text)
        return styles, code

    def flush() -> None:
        nonlocal current, current_length, payload_units
        if payload_units > 0:
            suffix = closing_suffix()
            chunks.append("".join(current) + suffix)
        prefix = opening_prefix()
        current = [prefix] if prefix else []
        current_length = len(prefix)
        payload_units = 0

    for index, atom in enumerate(units):
        if index in ignored_delimiters:
            continue
        opens_format = (
            code_roles.get(index) == "open"
            or style_roles.get(index) == "open"
        )
        if opens_format and payload_units > 0 and index + 1 < len(units):
            opened_styles, opened_code = next_state(index, atom)
            next_atom = units[index + 1]
            if (
                next_atom.atomic
                and current_length
                + len(atom.text)
                + len(next_atom.text)
                + len(closing_suffix(opened_styles, opened_code))
                > limit
            ):
                flush()

        next_styles, next_code = next_state(index, atom)
        reserve = len(closing_suffix(next_styles, next_code))
        closes_format = (
            code_roles.get(index) == "close"
            or style_roles.get(index) == "close"
        )
        if (
            payload_units > 0
            and not closes_format
            and current_length + len(atom.text) + reserve > limit
        ):
            flush()
            next_styles, next_code = next_state(index, atom)

        current.append(atom.text)
        current_length += len(atom.text)
        active_styles = next_styles
        code_delimiter = next_code
        if index not in code_roles and index not in style_roles:
            payload_units += 1

    if payload_units > 0:
        chunks.append("".join(current) + closing_suffix())
    return [chunk for chunk in chunks if chunk]
