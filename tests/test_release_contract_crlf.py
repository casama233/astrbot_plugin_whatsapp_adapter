from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_contract_crlf_test",
    ROOT / "scripts" / "release_contract.py",
)
assert SPEC and SPEC.loader
release_contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_contract
SPEC.loader.exec_module(release_contract)


class ReleaseContractCrlfTests(unittest.TestCase):
    def test_main_identity_accepts_lf_and_crlf_without_weakening_uniqueness(self) -> None:
        lf = (
            'PLUGIN_NAME = "astrbot_plugin_whatsapp_adapter"\n'
            'PLUGIN_VERSION = "0.2.35"\n'
        )
        crlf = lf.replace("\n", "\r\n")

        self.assertEqual(
            release_contract._extract_main_identity(lf),
            ("astrbot_plugin_whatsapp_adapter", "0.2.35"),
        )
        self.assertEqual(
            release_contract._extract_main_identity(crlf),
            ("astrbot_plugin_whatsapp_adapter", "0.2.35"),
        )

        duplicate = crlf + 'PLUGIN_VERSION = "9.9.9"\r\n'
        with self.assertRaises(release_contract.ReleaseContractError):
            release_contract._extract_main_identity(duplicate)


if __name__ == "__main__":
    unittest.main()
