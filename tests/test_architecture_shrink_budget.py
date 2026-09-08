from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

# Canonical main on 2026-08-31, measured as UTF-8 bytes with LF newlines.
# Lower these ceilings after an extraction; never raise them to fit new code.
SHRINK_ONLY_MAX_BYTES = {
    "_whatsapp_adapter_impl.py": 126,
    "whatsapp_adapter.py": 130_597,
    "_whatsapp_event_impl.py": 118,
    "whatsapp_event.py": 32_004,
    "_whatsapp_helpers_impl.py": 60_189,
}


def source_size(path: Path) -> int:
    """Ignore checkout-only CRLF expansion without discounting source changes."""
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


class HistoricalImplementationBudgetTests(unittest.TestCase):
    def test_historical_impl_modules_are_shrink_only(self) -> None:
        for relative_path, maximum in SHRINK_ONLY_MAX_BYTES.items():
            with self.subTest(path=relative_path):
                actual = source_size(ROOT / relative_path)
                self.assertLessEqual(
                    actual,
                    maximum,
                    f"{relative_path}: {actual:,} > {maximum:,} LF-normalized bytes. "
                    "Extract isolated behavior into a focused module with regression "
                    "coverage; lower the ceiling after shrinking the implementation.",
                )


class ShrinkBudgetGuardTests(unittest.TestCase):
    def _run_budget(self, content: bytes, maximum: int) -> unittest.TestResult:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.py").write_bytes(content)
            with (
                patch(f"{__name__}.ROOT", root),
                patch(f"{__name__}.SHRINK_ONLY_MAX_BYTES", {"fixture.py": maximum}),
            ):
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(
                    HistoricalImplementationBudgetTests
                )
                self.assertEqual(suite.countTestCases(), 1)
                result = unittest.TestResult()
                suite.run(result)
                return result

    def test_exact_budget_passes(self) -> None:
        result = self._run_budget(b"x\n", 2)
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(result.wasSuccessful())

    def test_one_byte_over_budget_fails(self) -> None:
        result = self._run_budget(b"xx\n", 2)
        self.assertEqual(result.testsRun, 1)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assertIn("fixture.py", result.failures[0][1])

    def test_crlf_checkout_has_the_same_budget(self) -> None:
        self.assertTrue(self._run_budget(b"x\r\n", 2).wasSuccessful())
        self.assertFalse(self._run_budget(b"xx\r\n", 2).wasSuccessful())

    def test_utf8_is_counted_in_bytes_not_characters(self) -> None:
        self.assertTrue(self._run_budget("豬\n".encode("utf-8"), 4).wasSuccessful())
        self.assertFalse(self._run_budget("豬\n".encode("utf-8"), 3).wasSuccessful())

    def test_missing_implementation_does_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(f"{__name__}.ROOT", Path(directory)):
                result = unittest.TestResult()
                unittest.defaultTestLoader.loadTestsFromTestCase(
                    HistoricalImplementationBudgetTests
                ).run(result)
                self.assertFalse(result.wasSuccessful())
                self.assertEqual(len(result.errors), len(SHRINK_ONLY_MAX_BYTES))


if __name__ == "__main__":
    unittest.main()
