from __future__ import annotations

import unittest

# Reuse the AstrBot compatibility stubs and already-imported helper facade from
# the main Markdown suite so these regressions exercise the same public API.
from test_whatsapp_markdown import helpers


class WhatsAppMarkdownHardeningTests(unittest.TestCase):
    def test_real_closing_marker_at_chunk_boundary_is_not_duplicated(self) -> None:
        for marker in ("*", "_", "~"):
            with self.subTest(marker=marker):
                source = marker + ("A" * 14) + marker + " rest"
                chunks = helpers.split_whatsapp_text(source, 16)
                self.assertEqual(chunks, [marker + ("A" * 14) + marker, " rest"])
                self.assertTrue(all(len(chunk) <= 16 for chunk in chunks))

    def test_code_closer_at_chunk_boundary_is_not_duplicated(self) -> None:
        inline = "`" + ("A" * 14) + "` rest"
        self.assertEqual(
            helpers.split_whatsapp_text(inline, 16),
            ["`" + ("A" * 14) + "`", " rest"],
        )

        fenced = "```" + ("A" * 10) + "``` rest"
        chunks = helpers.split_whatsapp_text(fenced, 16)
        self.assertEqual(chunks, ["```" + ("A" * 10) + "```", " rest"])
        self.assertNotIn("``````", chunks)

    def test_literal_delimiters_are_not_silently_deleted(self) -> None:
        cases = {
            "2 ** 3": "2 ** 3",
            "literal ~~ text": "literal ~~ text",
            "pattern*": "pattern*",
            "foo_": "foo_",
            "abc~": "abc~",
            "trailing **": "trailing **",
            "trailing ~~": "trailing ~~",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(
                    helpers.format_whatsapp_markdown(source),
                    expected,
                )

    def test_streaming_unmatched_openers_are_hidden_without_global_deletion(self) -> None:
        self.assertEqual(
            helpers.format_whatsapp_markdown("**", streaming=True),
            "",
        )
        self.assertEqual(
            helpers.format_whatsapp_markdown("**storm", streaming=True),
            "storm",
        )
        self.assertEqual(
            helpers.format_whatsapp_markdown("2 ** 3", streaming=True),
            "2 ** 3",
        )

    def test_mixed_stale_tail_is_removed_but_literal_runs_survive(self) -> None:
        self.assertEqual(
            helpers.format_whatsapp_markdown("done~*"),
            "done",
        )
        self.assertEqual(
            helpers.format_whatsapp_markdown(r"done\~*"),
            r"done\~*",
        )
        self.assertEqual(
            helpers.format_whatsapp_markdown("done**"),
            "done**",
        )
        self.assertEqual(
            helpers.format_whatsapp_markdown("done~~"),
            "done~~",
        )

    def test_escaped_markdown_keeps_escape_sequence(self) -> None:
        source = (
            r"\*literal\* "
            r"\_name\_ "
            r"\~wave\~ "
            r"\`code\` "
            r"\[link\] "
            r"\|pipe\| "
            r"\#heading \>quote \-dash \!bang"
        )
        self.assertEqual(helpers.format_whatsapp_markdown(source), source)

    def test_complex_heading_does_not_create_adjacent_bold_markers(self) -> None:
        rendered = helpers.format_whatsapp_markdown("# **Bold** heading")
        self.assertEqual(rendered, "*Bold* heading")
        self.assertNotIn("**Bold", rendered)
        self.assertEqual(
            helpers.format_whatsapp_markdown("# Heading"),
            "*Heading*",
        )

    def test_header_only_table_is_not_dropped(self) -> None:
        source = "| A | B |\n|---|---|\n"
        rendered = helpers.format_whatsapp_markdown(source)
        self.assertEqual(rendered, "- *A* | *B*\n")
        self.assertTrue(rendered.strip())

    def test_table_inline_markdown_does_not_generate_marker_collisions(self) -> None:
        source = (
            "| **Time** | _Strength_ |\n"
            "|---|---|\n"
            "| Today | **High** |\n"
        )
        rendered = helpers.format_whatsapp_markdown(source)
        self.assertIn("Today", rendered)
        self.assertIn("High", rendered)
        self.assertNotIn("****", rendered)
        self.assertNotIn("|---", rendered)

    def test_final_unmatched_inline_backtick_does_not_swallow_tail(self) -> None:
        source = "Use `foo and **important**"
        rendered = helpers.format_whatsapp_markdown(source, streaming=False)
        self.assertEqual(rendered, "Use `foo and *important*")
        self.assertNotIn("```foo", rendered)

        streaming = helpers.format_whatsapp_markdown(source, streaming=True)
        self.assertEqual(streaming, "Use `foo and **important**`")
        self.assertIn("**important**", streaming)

    def test_keycap_emoji_is_not_split_inside_grapheme(self) -> None:
        source = ("A" * 14) + "1️⃣" + "B"
        chunks = helpers.split_whatsapp_text(source, 16)
        self.assertTrue(any("1️⃣" in chunk for chunk in chunks))
        self.assertFalse(any(chunk.startswith("⃣") for chunk in chunks))
        self.assertEqual("".join(chunks), source)

    def test_emoji_tag_sequence_is_not_split_inside_grapheme(self) -> None:
        # England subdivision flag = black flag + Unicode tag letters + cancel tag.
        # Use explicit code points so the test cannot silently degrade to a plain
        # black flag if an editor hides the tag characters.
        england = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
        source = ("A" * 14) + england + "B"
        chunks = helpers.split_whatsapp_text(source, 16)
        self.assertTrue(any(england in chunk for chunk in chunks))
        self.assertFalse(
            any(
                chunk and 0xE0020 <= ord(chunk[0]) <= 0xE007F
                for chunk in chunks
            ),
        )
        self.assertEqual("".join(chunks), source)


if __name__ == "__main__":
    unittest.main()
