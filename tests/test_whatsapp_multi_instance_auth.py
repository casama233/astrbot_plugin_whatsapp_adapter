from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from whatsapp_multi_instance import instance_auth_dir


class MultiInstanceAuthDirTests(unittest.TestCase):
    def test_default_instance_preserves_explicit_auth_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configured = Path(tmp) / "custom-auth"
            self.assertEqual(
                instance_auth_dir(
                    Path(tmp),
                    {"id": "whatsapp", "auth_dir": str(configured)},
                ),
                configured.resolve(),
            )

    def test_secondary_instance_derives_sibling_from_explicit_auth_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configured = Path(tmp) / "custom-auth"
            secondary = instance_auth_dir(
                Path(tmp),
                {"id": "whatsapp2", "auth_dir": str(configured)},
            )
            self.assertEqual(secondary.parent, configured.parent.resolve())
            self.assertEqual(secondary.name, "custom-auth-whatsapp2")
            self.assertNotEqual(secondary, configured.resolve())


if __name__ == "__main__":
    unittest.main()
