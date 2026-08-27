from __future__ import annotations

from pathlib import Path
from textwrap import dedent


path = Path("_whatsapp_helpers_impl.py")
text = path.read_text(encoding="utf-8")

old = "    candidate: bool = False\n    structural: bool = False\n"
new = "    candidate: bool = False\n    structural: bool = False\n    pair_id: int = -1\n"
if old not in text:
    raise SystemExit("InlinePiece marker not found")
text = text.replace(old, new, 1)

anchor = dedent(
    '''\
    def _delimiter_sizes(marker: str, count: int) -> tuple[list[int], int]:
        if marker == "~":
            return [2] * (count // 2), count % 2
        return ([1] if count % 2 else []) + ([2] * (count // 2)), 0


    ''',
)
helpers = dedent(
    '''\
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


    ''',
)
if anchor not in text:
    raise SystemExit("delimiter helper anchor not found")
text = text.replace(anchor, anchor + helpers, 1)

old = (
    "    pieces: list[_InlinePiece] = []\n"
    "    delimiter_stack: list[_InlinePiece] = []\n"
    "    index = 0\n"
)
new = (
    "    pieces: list[_InlinePiece] = []\n"
    "    delimiter_stack: list[_InlinePiece] = []\n"
    "    next_pair_id = 0\n"
    "    index = 0\n"
)
if old not in text:
    raise SystemExit("render init anchor not found")
text = text.replace(old, new, 1)

old = dedent(
    '''\
                opening = delimiter_stack.pop()
                opening.structural = True
                closing = _InlinePiece(
                    "delimiter",
                    char * opening.size,
                    marker=char,
                    size=opening.size,
                    candidate=True,
                    structural=True,
                )
                pieces.append(closing)
                remaining -= opening.size
    ''',
)
new = dedent(
    '''\
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
    ''',
)
if old not in text:
    raise SystemExit("pairing anchor not found")
text = text.replace(old, new, 1)

old = "    output: list[str] = []\n    for piece_index, piece in enumerate(pieces):\n"
new = dedent(
    '''\
        unsafe_pairs = _unsafe_whatsapp_pair_ids(
            pieces,
            trailing_unmatched,
            streaming=streaming,
        )

        output: list[str] = []
        for piece_index, piece in enumerate(pieces):
    ''',
)
if old not in text:
    raise SystemExit("output anchor not found")
text = text.replace(old, new, 1)

old = dedent(
    '''\
            if piece.marker == "~":
                output.append("~")
            elif piece.size == 2:
                output.append("*")
            else:
                output.append("_")
    ''',
)
new = dedent(
    '''\
            if piece.pair_id in unsafe_pairs:
                continue
            output.append(_whatsapp_style_marker(piece))
    ''',
)
if old not in text:
    raise SystemExit("style rendering anchor not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")


test = Path("tests/test_whatsapp_markdown_cjk_boundaries.py")
test.write_text(
    dedent(
        '''\
        from __future__ import annotations

        import unittest

        from tests import test_whatsapp_markdown as base

        helpers = base.helpers


        class CJKWhatsAppBoundaryRegressionTests(unittest.TestCase):
            def test_screenshot_samples_degrade_unsafe_inline_bold_cleanly(self) -> None:
                cases = {
                    "只要有**正式診斷**，躁鬱症、PTSD": "只要有正式診斷，躁鬱症、PTSD",
                    "核心使命，為**殘疾人士提供職業訓練及就業機會**，推廣傷健共融": (
                        "核心使命，為殘疾人士提供職業訓練及就業機會，推廣傷健共融"
                    ),
                    "身體部分機能失常、畸形**或**外觀上的毀損": (
                        "身體部分機能失常、畸形或外觀上的毀損"
                    ),
                    "也包括**曾經存在、將來可能存在、或被認為存在**的。": (
                        "也包括曾經存在、將來可能存在、或被認為存在的。"
                    ),
                }
                for source, expected in cases.items():
                    with self.subTest(source=source):
                        self.assertEqual(helpers.format_whatsapp_markdown(source), expected)
                        self.assertEqual(
                            helpers.format_whatsapp_markdown(source, streaming=True),
                            expected,
                        )

            def test_unsafe_inline_italic_and_strike_also_degrade(self) -> None:
                cases = {
                    "中文*斜體*中文": "中文斜體中文",
                    "中文~~刪除~~中文": "中文刪除中文",
                    "pre**bold**post": "preboldpost",
                }
                for source, expected in cases.items():
                    with self.subTest(source=source):
                        self.assertEqual(helpers.format_whatsapp_markdown(source), expected)

            def test_safe_whitespace_and_punctuation_boundaries_keep_formatting(self) -> None:
                cases = {
                    "**正式診斷**，才算成立": "*正式診斷*，才算成立",
                    "重點：**正式診斷**；下一項": "重點：*正式診斷*；下一項",
                    "（**正式診斷**）": "（*正式診斷*）",
                    "文字 **正式診斷** 文字": "文字 *正式診斷* 文字",
                    "文字 *斜體* 文字": "文字 _斜體_ 文字",
                    "文字 ~~刪除~~ 文字": "文字 ~刪除~ 文字",
                }
                for source, expected in cases.items():
                    with self.subTest(source=source):
                        self.assertEqual(helpers.format_whatsapp_markdown(source), expected)

            def test_nested_styles_recheck_boundaries_after_outer_degrades(self) -> None:
                source = "中文**粗體 _斜體_**中文"
                self.assertEqual(
                    helpers.format_whatsapp_markdown(source),
                    "中文粗體 斜體中文",
                )

            def test_native_whatsapp_source_is_not_rewritten(self) -> None:
                native = "中文*使用者原生粗體*中文"
                self.assertEqual(
                    helpers.format_whatsapp_markdown(native, source_format="whatsapp"),
                    native,
                )


        if __name__ == "__main__":
            unittest.main()
        ''',
    ),
    encoding="utf-8",
)
