from __future__ import annotations

import unittest

from markdown_it_whatsapp_prototype import available, render_markdown_it_whatsapp
from test_whatsapp_markdown import helpers
from whatsapp_markdown_corpus import CASES


_CASES_BY_NAME = {case.name: case for case in CASES}


@unittest.skipUnless(available(), "markdown-it-py research dependency is not installed")
class MarkdownItDifferentialPrototypeTests(unittest.TestCase):
    def test_reference_parser_matches_stable_supported_subset(self) -> None:
        # Keep this subset deliberately conservative.  Exact equality here says
        # the token renderer can reproduce existing public behaviour without
        # forcing the production plugin to depend on markdown-it-py yet.
        names = (
            "strong",
            "underscore-strong",
            "emphasis",
            "underscore-emphasis",
            "strike",
            "inline-code-protects-markdown",
            "plain-heading",
            "blockquote-formatting",
            "unordered-formatting",
            "ordered-formatting",
            "link-formatting",
            "formatted-link-label",
            "horizontal-rule",
        )
        for name in names:
            case = _CASES_BY_NAME[name]
            with self.subTest(case=name):
                production = helpers.format_whatsapp_markdown(case.source)
                reference = render_markdown_it_whatsapp(case.source)
                self.assertEqual(reference, production)

    def test_reference_parser_preserves_payload_across_full_corpus(self) -> None:
        for case in CASES:
            with self.subTest(case=case.name):
                reference = render_markdown_it_whatsapp(case.source)
                self.assertNotIn("\x00WA", reference)
                for required in case.required:
                    self.assertIn(required, reference)

    def test_differential_corpus_has_documented_nontrivial_overlap(self) -> None:
        equal: list[str] = []
        different: list[str] = []
        for case in CASES:
            production = helpers.format_whatsapp_markdown(case.source)
            reference = render_markdown_it_whatsapp(case.source)
            (equal if production == reference else different).append(case.name)

        # This is a research gate rather than a migration gate: the prototype
        # must already agree on a meaningful portion of behaviour, while at
        # least one divergence should remain visible for future design work.
        self.assertGreaterEqual(len(equal), 12, (equal, different))
        self.assertTrue(different, "prototype unexpectedly became a drop-in replacement")

    def test_known_parser_advantages_remain_observable(self) -> None:
        # Nested link labels and balanced parentheses are cases where a real
        # parser has structural information that the production regex pipeline
        # does not.  Do not require production to change in this PR; merely
        # ensure the prototype preserves those inputs for later evaluation.
        for name in ("nested-link-label", "parenthesized-link-target"):
            case = _CASES_BY_NAME[name]
            with self.subTest(case=name):
                reference = render_markdown_it_whatsapp(case.source)
                for required in case.required:
                    self.assertIn(required, reference)


if __name__ == "__main__":
    unittest.main()
