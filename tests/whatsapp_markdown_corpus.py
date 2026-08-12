"""Reference corpus for WhatsApp Markdown conversion.

The cases are intentionally split between exact rendering contracts and
adversarial payload-preservation checks.  The latter keep the formatter free to
change its visual degradation policy without allowing user-visible text to be
silently deleted.

Sources for the case shapes include CommonMark edge cases and the boundary-
aware formatting approach used by mautrix-whatsapp.  No third-party code is
copied here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarkdownCase:
    name: str
    source: str
    expected: str | None = None
    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ("\x00WA",)
    complete: bool = True


CASES: tuple[MarkdownCase, ...] = (
    MarkdownCase("strong", "**bold**", "*bold*"),
    MarkdownCase("underscore-strong", "__bold__", "*bold*"),
    MarkdownCase("emphasis", "*italic*", "_italic_"),
    MarkdownCase("underscore-emphasis", "_italic_", "_italic_"),
    MarkdownCase("strike", "~~strike~~", "~strike~"),
    MarkdownCase("strong-emphasis", "***both***", "*_both_*"),
    MarkdownCase("underscore-strong-emphasis", "___both___", "*_both_*"),
    MarkdownCase(
        "nested-strong-emphasis",
        "**foo *bar* baz**",
        "*foo _bar_ baz*",
    ),
    MarkdownCase(
        "nested-strike-strong",
        "~~**foo**~~",
        "~*foo*~",
    ),
    MarkdownCase("intraword-underscore", "foo_bar_baz", "foo_bar_baz"),
    MarkdownCase("math-stars", "2 ** 3", "2 ** 3"),
    MarkdownCase("spaced-tilde", "a ~ b", "a ~ b"),
    MarkdownCase("literal-star-tail", "pattern*", "pattern*"),
    MarkdownCase("literal-underscore-tail", "foo_", "foo_"),
    MarkdownCase("literal-tilde-tail", "abc~", "abc~"),
    MarkdownCase("escaped-star", r"\*literal\*", r"\*literal\*"),
    MarkdownCase(
        "escaped-strong",
        r"\*\*literal\*\*",
        r"\*\*literal\*\*",
    ),
    MarkdownCase(
        "inline-code-protects-markdown",
        "`code **bold** _italic_ ~~strike~~`",
        "`code **bold** _italic_ ~~strike~~`",
    ),
    MarkdownCase(
        "nested-backtick-code",
        "``code ` inside``",
        "```code ` inside```",
    ),
    MarkdownCase(
        "fenced-code-language",
        "```python\nvalue = '**bold**'\n```",
        "```value = '**bold**'\n```",
    ),
    MarkdownCase("plain-heading", "# Heading", "*Heading*"),
    MarkdownCase(
        "formatted-heading",
        "# **Bold** heading",
        "*Bold* heading",
    ),
    MarkdownCase("blockquote-formatting", "> **bold**", "> *bold*"),
    MarkdownCase("unordered-formatting", "- **bold**", "- *bold*"),
    MarkdownCase("star-list-formatting", "* **bold**", "- *bold*"),
    MarkdownCase("ordered-formatting", "1) *italic*", "1. _italic_"),
    MarkdownCase(
        "link-formatting",
        "[OpenAI](https://openai.com)",
        "OpenAI (https://openai.com)",
    ),
    MarkdownCase(
        "formatted-link-label",
        "[a **bold** link](https://example.com)",
        "a *bold* link (https://example.com)",
    ),
    MarkdownCase(
        "header-only-table",
        "| A | B |\n|---|---|\n",
        "- *A* | *B*\n",
    ),
    MarkdownCase(
        "table-inline-code",
        "| **A** | `B|C` |\n|---|---|\n| x | y |\n",
        required=("A", "B|C", "x", "y"),
        forbidden=("\x00WA", "|---"),
    ),
    MarkdownCase(
        "nested-link-label",
        "[outer [inner]](https://example.com/path)",
        required=("outer", "inner", "https://example.com/path"),
    ),
    MarkdownCase(
        "parenthesized-link-target",
        "[docs](https://example.com/a_(b))",
        required=("docs", "https://example.com/a_(b)"),
    ),
    MarkdownCase(
        "autolink-like-url",
        "<https://example.com/a?b=1&c=2>",
        required=("https://example.com/a?b=1&c=2",),
    ),
    MarkdownCase(
        "setext-heading",
        "Heading\n=======",
        required=("Heading",),
    ),
    MarkdownCase("horizontal-rule", "---", "──────────"),
    MarkdownCase("underscore-horizontal-rule", "___", "──────────"),
    MarkdownCase(
        "code-fence-internal-markers",
        "```\n*** not emphasis ***\na_b_c\n```",
        required=("*** not emphasis ***", "a_b_c"),
    ),
    MarkdownCase(
        "html-is-readable-text",
        "<kbd>Ctrl</kbd> + **K**",
        required=("kbd", "Ctrl", "K"),
    ),
    MarkdownCase(
        "unicode-combining",
        "Cafe\u0301 **ok**",
        required=("Cafe\u0301", "ok"),
    ),
    MarkdownCase(
        "emoji-zwj-and-keycap",
        "👨‍👩‍👧‍👦 1️⃣ **family**",
        required=("👨‍👩‍👧‍👦", "1️⃣", "family"),
    ),
    MarkdownCase(
        "unmatched-strong-final",
        "prefix **unfinished",
        required=("prefix", "unfinished"),
        complete=False,
    ),
    MarkdownCase(
        "unmatched-inline-code-final",
        "Use `foo and **important**",
        required=("Use", "foo", "important"),
        complete=False,
    ),
    MarkdownCase(
        "unmatched-fence-final",
        "```python\nprint('x')\n",
        required=("print('x')",),
        complete=False,
    ),
)


EXACT_CASES = tuple(case for case in CASES if case.expected is not None)
COMPLETE_CASES = tuple(case for case in CASES if case.complete)
