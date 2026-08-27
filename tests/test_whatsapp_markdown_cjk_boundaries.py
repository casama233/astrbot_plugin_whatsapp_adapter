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

    def test_unsafe_inline_emphasis_degrades_cleanly(self) -> None:
        cases = {
            "中文*斜體*中文": "中文斜體中文",
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

    def test_safe_nested_style_survives_outer_degrade(self) -> None:
        source = "中文**粗體 _斜體_**，"
        self.assertEqual(
            helpers.format_whatsapp_markdown(source),
            "中文粗體 _斜體_，",
        )

    def test_escaped_marker_before_safe_style_keeps_style(self) -> None:
        word_joiner = "\u2060"
        cases = {
            r"\***bold**": f"*{word_joiner}*bold*",
            r"\_**bold**": f"_{word_joiner}*bold*",
            r"\~**bold**": f"~{word_joiner}*bold*",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(helpers.format_whatsapp_markdown(source), expected)
                self.assertEqual(
                    helpers.format_whatsapp_markdown(source, streaming=True),
                    expected,
                )

    def test_native_whatsapp_source_is_not_rewritten(self) -> None:
        native = "中文*使用者原生粗體*中文"
        self.assertEqual(
            helpers.format_whatsapp_markdown(native, source_format="whatsapp"),
            native,
        )


if __name__ == "__main__":
    unittest.main()
