from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _identity_module():
    spec = importlib.util.spec_from_file_location(
        "whatsapp_identity_under_test",
        ROOT / "whatsapp_identity.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class WhatsAppIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.identity = _identity_module()

    def test_device_suffix_and_hosted_domains_are_normalized(self) -> None:
        identity = self.identity
        self.assertEqual(identity.identity_user("85264362105:23@s.whatsapp.net"), "85264362105")
        self.assertEqual(identity.base_pn_jid("85264362105:23@s.whatsapp.net"), "85264362105@s.whatsapp.net")
        self.assertEqual(identity.base_pn_jid("85264362105:23@hosted"), "85264362105@hosted")
        self.assertEqual(identity.base_lid_jid("123456:7@lid"), "123456@lid")
        self.assertEqual(identity.base_lid_jid("123456:7@hosted.lid"), "123456@hosted.lid")
        self.assertEqual(identity.phone_from_identity("85264362105:23@hosted"), "+85264362105")

    def test_umo_sessions_use_qq_compatible_public_ids(self) -> None:
        identity = self.identity
        self.assertEqual(
            identity.build_umo_session_id(
                is_group=False,
                group_id=None,
                user_id="85264362105:23@s.whatsapp.net",
                unique_session=False,
            ),
            "85264362105",
        )
        self.assertEqual(
            identity.build_umo_session_id(
                is_group=True,
                group_id="120363000000000001@g.us",
                user_id="111@hosted",
                unique_session=False,
            ),
            "120363000000000001",
        )
        self.assertEqual(
            identity.build_umo_session_id(
                is_group=True,
                group_id="120363000000000001@g.us",
                user_id="111@hosted",
                unique_session=True,
            ),
            "111_120363000000000001",
        )

    def test_delivery_accepts_canonical_and_legacy_sessions(self) -> None:
        identity = self.identity
        for value in (
            "120363000000000001",
            "120363000000000001@g.us",
            "111_120363000000000001",
            "111_120363000000000001@g.us",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    identity.delivery_jid_from_session_id(value, is_group=True),
                    "120363000000000001@g.us",
                )
        self.assertEqual(
            identity.delivery_jid_from_session_id("111", is_group=False),
            "111@s.whatsapp.net",
        )
        self.assertEqual(
            identity.delivery_jid_from_session_id("123:7@hosted.lid", is_group=False),
            "123@hosted.lid",
        )

    def test_mapping_caches_are_isolated_by_adapter_account(self) -> None:
        identity = self.identity
        first = identity.IdentityMappingCache()
        second = identity.IdentityMappingCache()

        self.assertTrue(first.remember("123:7@hosted.lid", "111:4@hosted"))
        self.assertTrue(second.remember("123:9@hosted.lid", "222:5@hosted"))

        self.assertEqual(first.pn_for_lid("123:99@hosted.lid"), "111@hosted")
        self.assertEqual(second.pn_for_lid("123@hosted.lid"), "222@hosted")
        self.assertEqual(first.lid_for_pn("111:8@hosted"), "123@hosted.lid")
        self.assertEqual(second.lid_for_pn("222@hosted"), "123@hosted.lid")

    def test_mapping_lookup_falls_back_between_hosted_and_standard_domains(self) -> None:
        identity = self.identity
        cache = identity.IdentityMappingCache()
        cache.remember("123@lid", "85264362105@s.whatsapp.net")

        self.assertEqual(
            cache.pn_for_lid("123:7@hosted.lid"),
            "85264362105@s.whatsapp.net",
        )
        self.assertEqual(
            cache.lid_for_pn("85264362105:23@hosted"),
            "123@lid",
        )

    def test_active_auth_session_is_used_for_loading_and_saving(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            (auth_root / "lid-mapping-100_reverse.json").write_text(json.dumps("101"), "utf-8")
            active_dir = auth_root / ".sessions" / "session-2"
            active_dir.mkdir(parents=True)
            (active_dir / "lid-mapping-200_reverse.json").write_text(json.dumps("202"), "utf-8")
            (auth_root / ".active-session.json").write_text(
                json.dumps({"sessionId": "session-2"}),
                "utf-8",
            )

            cache = identity.IdentityMappingCache()
            cache.remember("999@lid", "998@s.whatsapp.net")
            self.assertEqual(identity.load_lid_mappings(auth_root, cache), 1)
            self.assertEqual(cache.pn_for_lid("200@lid"), "202@s.whatsapp.net")
            self.assertEqual(cache.pn_for_lid("100@lid"), "")
            self.assertEqual(cache.pn_for_lid("999@lid"), "")

            identity.save_lid_mapping(
                auth_root,
                "300:5@lid",
                "303:12@s.whatsapp.net",
            )
            saved = active_dir / "lid-mapping-300_reverse.json"
            self.assertEqual(json.loads(saved.read_text("utf-8")), "303")
            self.assertFalse((auth_root / saved.name).exists())

    def test_invalid_active_session_pointer_falls_back_to_legacy_root(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            (auth_root / "lid-mapping-400_reverse.json").write_text(json.dumps("404"), "utf-8")
            (auth_root / ".active-session.json").write_text(
                json.dumps({"sessionId": "../outside"}),
                "utf-8",
            )
            cache = identity.IdentityMappingCache()
            self.assertEqual(identity.load_lid_mappings(auth_root, cache), 1)
            self.assertEqual(cache.pn_for_lid("400@lid"), "404@s.whatsapp.net")


if __name__ == "__main__":
    unittest.main()
