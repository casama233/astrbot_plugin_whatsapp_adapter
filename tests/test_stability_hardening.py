from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StabilityHardeningSourceTests(unittest.TestCase):
    def test_adapter_fails_fast_on_terminal_gateway_health(self) -> None:
        source = (ROOT / "whatsapp_adapter.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("_wait_for_gateway_with_fatal_health", source)
        self.assertIn("exc.status_code in {401, 503}", source)
        self.assertIn("child.returncode is not None", source)
        self.assertIn(
            "WhatsAppPlatformAdapter._wait_for_gateway = _wait_for_gateway_with_fatal_health",
            source,
        )


if __name__ == "__main__":
    unittest.main()
