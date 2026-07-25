from __future__ import annotations

import unittest

# unittest discovery adds the tests directory to sys.path. This name reuses the
# already-loaded main suite instead of importing a second copy under tests.*.
from test_whatsapp_markdown import helpers


class WhatsAppChunkingFollowUpTests(unittest.TestCase):
    def test_code_block_split_has_no_marker_only_chunk(self) -> None:
        source = "```multiline code\nline 2 long enough to split here\n```"
        chunks = helpers.split_whatsapp_text(source, 16)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.strip("`\n\r\t ") for chunk in chunks))
        self.assertNotIn("``````", chunks)

    def test_inline_markers_are_balanced_across_chunks(self) -> None:
        for marker in ("*", "_", "~"):
            with self.subTest(marker=marker):
                chunks = helpers.split_whatsapp_text(
                    marker + ("streaming payload " * 6) + marker,
                    32,
                )
                self.assertGreater(len(chunks), 1)
                self.assertTrue(all(chunk.startswith(marker) for chunk in chunks))
                self.assertTrue(all(chunk.endswith(marker) for chunk in chunks))
                self.assertTrue(all(chunk.strip("*_~\n\r\t ") for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
