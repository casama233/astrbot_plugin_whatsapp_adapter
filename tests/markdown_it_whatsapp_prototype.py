"""Research-only markdown-it-py -> WhatsApp renderer prototype.

This module lives under tests on purpose.  It is not imported by the plugin and
must never become a runtime dependency implicitly.  The goal is to compare a
real token parser with the production conservative formatter before deciding
whether a future parser-backed implementation is worth adopting.

The shape follows the same broad architecture as mautrix-whatsapp: parse
structured formatting first, then render platform-native delimiters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

try:
    from markdown_it import MarkdownIt
except ImportError:  # pragma: no cover - exercised by normal dependency-light CI.
    MarkdownIt = None  # type: ignore[assignment]


@dataclass(slots=True)
class _ListState:
    kind: str
    next_index: int = 1


def available() -> bool:
    return MarkdownIt is not None


def _attr(token: Any, name: str) -> str:
    getter = getattr(token, "attrGet", None)
    if callable(getter):
        value = getter(name)
        return str(value or "")
    attrs = getattr(token, "attrs", None) or {}
    if isinstance(attrs, dict):
        return str(attrs.get(name) or "")
    return ""


def _render_inline(children: Iterable[Any]) -> str:
    output: list[str] = []
    links: list[str] = []

    for token in children:
        token_type = str(getattr(token, "type", "") or "")
        content = str(getattr(token, "content", "") or "")

        if token_type in {"text", "text_special", "html_inline"}:
            output.append(content)
        elif token_type == "strong_open":
            output.append("*")
        elif token_type == "strong_close":
            output.append("*")
        elif token_type == "em_open":
            output.append("_")
        elif token_type == "em_close":
            output.append("_")
        elif token_type == "s_open":
            output.append("~")
        elif token_type == "s_close":
            output.append("~")
        elif token_type == "code_inline":
            if "`" in content or "\n" in content:
                output.append(f"```{content}```")
            else:
                output.append(f"`{content}`")
        elif token_type in {"softbreak", "hardbreak"}:
            output.append("\n")
        elif token_type == "link_open":
            links.append(_attr(token, "href"))
        elif token_type == "link_close":
            href = links.pop() if links else ""
            if href:
                output.append(f" ({href})")
        elif token_type == "image":
            src = _attr(token, "src")
            alt = content or _attr(token, "alt")
            output.append(alt)
            if src:
                output.append(f" ({src})")
        else:
            # The prototype is intentionally loss-averse.  Unknown token types
            # contribute their textual content rather than being dropped.
            output.append(content)

    return "".join(output)


def render_markdown_it_whatsapp(text: str) -> str:
    """Render CommonMark tokens into a conservative WhatsApp-native string."""

    if MarkdownIt is None:
        raise RuntimeError("markdown-it-py is not installed")

    parser = MarkdownIt("commonmark")
    try:
        parser.enable("strikethrough")
    except (KeyError, ValueError):
        # Keep the prototype useful across parser versions.  Differential tests
        # will expose the missing strike conversion rather than runtime code
        # depending on an optional extension.
        pass

    tokens = parser.parse(str(text or ""))
    output: list[str] = []
    list_stack: list[_ListState] = []
    quote_depth = 0
    at_line_start = True
    heading_open = False

    def write(value: str) -> None:
        nonlocal at_line_start
        if not value:
            return
        pieces = value.splitlines(keepends=True)
        for piece in pieces:
            if at_line_start and quote_depth:
                output.append("> " * quote_depth)
            output.append(piece)
            at_line_start = piece.endswith("\n")

    def newline() -> None:
        nonlocal at_line_start
        if not output or output[-1].endswith("\n"):
            at_line_start = True
            return
        output.append("\n")
        at_line_start = True

    for token in tokens:
        token_type = str(getattr(token, "type", "") or "")
        content = str(getattr(token, "content", "") or "")

        if token_type == "inline":
            write(_render_inline(getattr(token, "children", None) or ()))
        elif token_type == "paragraph_close":
            newline()
        elif token_type == "heading_open":
            heading_open = True
            write("*")
        elif token_type == "heading_close":
            if heading_open:
                write("*")
            heading_open = False
            newline()
        elif token_type == "blockquote_open":
            quote_depth += 1
        elif token_type == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
        elif token_type == "bullet_list_open":
            list_stack.append(_ListState("bullet"))
        elif token_type == "ordered_list_open":
            start = _attr(token, "start")
            list_stack.append(
                _ListState(
                    "ordered",
                    int(start) if start.isdigit() else 1,
                ),
            )
        elif token_type in {"bullet_list_close", "ordered_list_close"}:
            if list_stack:
                list_stack.pop()
        elif token_type == "list_item_open":
            if list_stack and list_stack[-1].kind == "ordered":
                state = list_stack[-1]
                write(f"{state.next_index}. ")
                state.next_index += 1
            else:
                write("- ")
        elif token_type == "list_item_close":
            newline()
        elif token_type in {"fence", "code_block"}:
            write(f"```{content.rstrip(chr(10))}```")
            newline()
        elif token_type == "hr":
            write("──────────")
            newline()
        elif token_type == "html_block":
            write(content)
            newline()
        elif token_type in {
            "paragraph_open",
            "list_item_open",
            "list_item_close",
        }:
            continue
        else:
            # Unknown block tokens are structural in most cases.  Preserve any
            # explicit content but never emit HTML generated by a fallback
            # renderer.
            if content:
                write(content)

    return "".join(output).rstrip("\n")
