from __future__ import annotations

import random
import re
import unittest

from test_whatsapp_markdown import helpers
from whatsapp_markdown_corpus import CASES, COMPLETE_CASES, EXACT_CASES


class WhatsAppMarkdownCorpusTests(unittest.TestCase):
    def test_exact_reference_cases(self) -> None:
        for case in EXACT_CASES:
            with self.subTest(case=case.name):
                self.assertEqual(
                    helpers.format_whatsapp_markdown(case.source),
                    case.expected,
                )

    def test_adversarial_cases_preserve_required_payload(self) -> None:
        for case in CASES:
            with self.subTest(case=case.name):
                rendered = helpers.format_whatsapp_markdown(case.source)
                for required in case.required:
                    self.assertIn(required, rendered)
                for forbidden in case.forbidden:
                    self.assertNotIn(forbidden, rendered)

    def test_complete_input_has_same_streaming_and_final_render(self) -> None:
        for case in COMPLETE_CASES:
            with self.subTest(case=case.name):
                self.assertEqual(
                    helpers.format_whatsapp_markdown(case.source, streaming=True),
                    helpers.format_whatsapp_markdown(case.source, streaming=False),
                )

    def test_rendered_whatsapp_source_is_idempotent(self) -> None:
        for case in CASES:
            with self.subTest(case=case.name):
                rendered = helpers.format_whatsapp_markdown(case.source)
                self.assertEqual(
                    helpers.format_whatsapp_markdown(
                        rendered,
                        source_format="whatsapp",
                    ),
                    rendered,
                )

    def test_reference_corpus_chunks_stay_bounded_and_visible(self) -> None:
        for case in CASES:
            rendered = helpers.format_whatsapp_markdown(case.source)
            if not helpers.has_visible_whatsapp_content(rendered):
                continue
            for limit in (16, 32, 64):
                with self.subTest(case=case.name, limit=limit):
                    chunks = helpers.split_whatsapp_text(rendered, limit)
                    self.assertTrue(chunks)
                    self.assertTrue(all(len(chunk) <= limit for chunk in chunks))
                    self.assertTrue(
                        all(helpers.has_visible_whatsapp_content(chunk) for chunk in chunks),
                    )
                    self.assertTrue(all("\x00WA" not in chunk for chunk in chunks))

    def test_deterministic_delimiter_fuzz_never_drops_word_payload(self) -> None:
        rng = random.Random(0x5A17B07)
        atoms = (
            "*",
            "**",
            "***",
            "_",
            "__",
            "___",
            "~",
            "~~",
            "`",
            "``",
            "\\*",
            "\\_",
            "[",
            "]",
            "(",
            ")",
            "> ",
            "- ",
            " ",
        )

        for index in range(750):
            words = [f"alpha{index}", "beta", "中"]
            source_parts = [words[0]]
            for word in words[1:]:
                source_parts.extend(
                    (
                        rng.choice(atoms),
                        word,
                        rng.choice(atoms),
                    ),
                )
            source = "".join(source_parts)

            with self.subTest(index=index, source=source):
                rendered = helpers.format_whatsapp_markdown(source)
                self.assertNotIn("\x00WA", rendered)
                for word in words:
                    self.assertIn(word, rendered)

                # Source-format contracts must make a rendered WhatsApp string
                # stable even if its literal punctuation resembles Markdown.
                self.assertEqual(
                    helpers.format_whatsapp_markdown(
                        rendered,
                        source_format="whatsapp",
                    ),
                    rendered,
                )

                chunks = helpers.split_whatsapp_text(rendered, 32)
                self.assertTrue(chunks)
                self.assertTrue(all(len(chunk) <= 32 for chunk in chunks))
                self.assertTrue(
                    all(helpers.has_visible_whatsapp_content(chunk) for chunk in chunks),
                )

                # The formatter may change delimiters but never the actual word
                # payload.  This catches destructive cleanup without trying to
                # make regex output a full CommonMark conformance oracle.
                rendered_words = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", rendered))
                for word in words:
                    self.assertIn(word, rendered_words)


if __name__ == "__main__":
    unittest.main()
