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
        self.assertEqual(identity.base_pn_jid("85264362105_128:23@hosted"), "85264362105@hosted")
        self.assertEqual(identity.base_lid_jid("123456:7@lid"), "123456@lid")
        self.assertEqual(identity.base_lid_jid("123456:7@hosted.lid"), "123456@hosted.lid")
        self.assertEqual(identity.base_lid_jid("123456_129:7@hosted.lid"), "123456@hosted.lid")
        self.assertEqual(identity.phone_from_identity("85264362105:23@hosted"), "+85264362105")

    def test_public_ids_are_strict_and_keep_pn_and_lid_namespaces_separate(self) -> None:
        identity = self.identity
        self.assertEqual(identity.public_numeric_id("111@s.whatsapp.net"), "111")
        self.assertEqual(identity.public_numeric_id("+111"), "111")
        self.assertEqual(identity.public_numeric_id("123:7@lid"), "lid-123")
        self.assertEqual(identity.public_numeric_id("123:7@hosted.lid"), "lid-123")
        self.assertEqual(identity.public_numeric_id("lid-123"), "lid-123")
        self.assertEqual(identity.public_numeric_id("120363000000000001@g.us"), "120363000000000001")

        for malformed in (
            "abc123",
            "abc123@s.whatsapp.net",
            "abc123@lid",
            "123:device@s.whatsapp.net",
            "123:7:8@s.whatsapp.net",
            "123_agent:7@s.whatsapp.net",
            "123_128:7:8@s.whatsapp.net",
            "123:device@lid",
            "123:7:8@hosted.lid",
            "123@example.net",
            "lid-abc123",
            "123:7",
            "120363000000000001:7@g.us",
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(identity.public_numeric_id(malformed), "")
        self.assertEqual(identity.base_pn_jid("abc123"), "")
        self.assertEqual(identity.base_lid_jid("abc123"), "")
        self.assertEqual(identity.base_pn_jid("123:7"), "")
        self.assertEqual(identity.base_lid_jid("123:7"), "")
        self.assertEqual(identity.base_pn_jid("123:device@hosted"), "")
        self.assertEqual(identity.base_lid_jid("123:device@hosted.lid"), "")
        self.assertEqual(identity.phone_from_identity("abc123"), "")

    def test_group_session_normalization_only_accepts_canonical_legacy_shapes(self) -> None:
        identity = self.identity
        group_id = "120363000000000001"
        self.assertEqual(identity.normalize_group_session_id(group_id), group_id)
        self.assertEqual(identity.normalize_group_session_id(f"{group_id}@g.us"), group_id)
        self.assertEqual(identity.normalize_group_session_id(f"111_{group_id}"), group_id)
        self.assertEqual(identity.normalize_group_session_id(f"lid-123_{group_id}@g.us"), group_id)

        for malformed in (
            "abc123@g.us",
            f"alice_{group_id}",
            f"111_extra_{group_id}",
            "111_abc123",
            f"{group_id}@example.net",
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(identity.normalize_group_session_id(malformed), "")
                self.assertEqual(
                    identity.delivery_jid_from_session_id(malformed, is_group=True),
                    "",
                )

    def test_legacy_hyphenated_group_sessions_round_trip_without_rewriting(self) -> None:
        identity = self.identity
        group_id = "123456789-123345"
        for value in (
            group_id,
            f"{group_id}@g.us",
            f"111_{group_id}",
            f"lid-123_{group_id}@g.us",
        ):
            with self.subTest(value=value):
                self.assertEqual(identity.normalize_group_session_id(value), group_id)
                self.assertEqual(
                    identity.delivery_jid_from_session_id(value, is_group=True),
                    f"{group_id}@g.us",
                )
        self.assertEqual(
            identity.public_numeric_id(f"{group_id}@g.us"),
            group_id,
        )
        self.assertEqual(
            identity.build_umo_session_id(
                is_group=True,
                group_id=f"{group_id}@g.us",
                user_id="111@s.whatsapp.net",
                unique_session=True,
            ),
            f"111_{group_id}",
        )

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
        self.assertEqual(
            identity.delivery_jid_from_session_id("lid-123", is_group=False),
            "123@lid",
        )
        self.assertEqual(
            identity.delivery_jid_from_session_id("abc123", is_group=False),
            "",
        )

    def test_numeric_public_delivery_preserves_observed_hosted_pn_after_reload(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as tmp:
            auth_root = Path(tmp)
            cache = identity.IdentityMappingCache()
            self.assertEqual(cache.project_public_id("111:99@hosted"), "111")
            self.assertEqual(
                identity.delivery_jid_from_session_id(
                    "111",
                    is_group=False,
                    cache=cache,
                ),
                "111@hosted",
            )
            self.assertTrue(identity.save_identity_projections(auth_root, cache))

            reloaded = identity.IdentityMappingCache()
            identity.load_identity_state(auth_root, reloaded)
            self.assertEqual(
                identity.delivery_jid_from_session_id(
                    "111",
                    is_group=False,
                    cache=reloaded,
                ),
                "111@hosted",
            )

    def test_same_identity_is_domain_aware_and_uses_only_proven_aliases(self) -> None:
        identity = self.identity
        self.assertTrue(
            identity.same_whatsapp_identity(
                "111:7@s.whatsapp.net",
                "111@hosted",
            ),
        )
        self.assertTrue(
            identity.same_whatsapp_identity(
                "123:7@lid",
                "123@hosted.lid",
            ),
        )
        self.assertTrue(identity.same_whatsapp_identity("+111", "111@s.whatsapp.net"))
        self.assertFalse(identity.same_whatsapp_identity("123@lid", "123@s.whatsapp.net"))
        self.assertFalse(identity.same_whatsapp_identity("abc123", "123@s.whatsapp.net"))

        cache = identity.IdentityMappingCache()
        cache.remember("123@hosted.lid", "111@hosted")
        self.assertTrue(
            identity.same_whatsapp_identity(
                "lid-123",
                "111@s.whatsapp.net",
                cache,
            ),
        )
        self.assertFalse(
            identity.same_whatsapp_identity(
                "lid-123",
                "222@s.whatsapp.net",
                cache,
            ),
        )

    def test_public_projection_preserves_whichever_identity_was_exposed_first(self) -> None:
        identity = self.identity

        lid_first = identity.IdentityMappingCache()
        self.assertEqual(lid_first.project_public_id("123@hosted.lid"), "lid-123")
        self.assertTrue(lid_first.projections_dirty)
        self.assertTrue(lid_first.remember("123@hosted.lid", "111@hosted"))
        self.assertEqual(lid_first.project_public_id("111@s.whatsapp.net"), "lid-123")
        self.assertEqual(lid_first.public_by_identity["pn:111"], "lid-123")

        pn_first = identity.IdentityMappingCache()
        self.assertEqual(pn_first.project_public_id("+111"), "111")
        self.assertTrue(pn_first.remember("123@lid", "111@s.whatsapp.net"))
        self.assertEqual(pn_first.project_public_id("123@hosted.lid"), "111")
        self.assertEqual(pn_first.public_by_identity["lid:123"], "111")

        mapping_first = identity.IdentityMappingCache()
        mapping_first.remember("123@lid", "111@s.whatsapp.net")
        self.assertEqual(mapping_first.project_public_id("123@lid"), "111")

    def test_projection_conflict_uses_first_exposure_order(self) -> None:
        identity = self.identity
        cache = identity.IdentityMappingCache()
        self.assertEqual(cache.project_public_id("123@lid"), "lid-123")
        self.assertEqual(cache.project_public_id("111@s.whatsapp.net"), "111")
        cache.remember("123@lid", "111@s.whatsapp.net")
        self.assertEqual(cache.project_public_id("123@lid"), "lid-123")
        self.assertEqual(cache.project_public_id("111@s.whatsapp.net"), "lid-123")

    def test_hot_reload_projection_writers_merge_without_losing_first_exposure(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as tmp:
            auth_root = Path(tmp)
            lid_writer = identity.IdentityMappingCache()
            pn_writer = identity.IdentityMappingCache()
            self.assertEqual(
                lid_writer.project_public_id("123:7@hosted.lid"),
                "lid-123",
            )
            self.assertEqual(pn_writer.project_public_id("111:9@hosted"), "111")

            self.assertTrue(identity.save_identity_projections(auth_root, lid_writer))
            self.assertTrue(identity.save_identity_projections(auth_root, pn_writer))

            payload = json.loads(
                (auth_root / identity.IDENTITY_PROJECTIONS_FILENAME).read_text("utf-8"),
            )
            self.assertEqual(
                payload["lidToPublic"],
                {"123@hosted.lid": "lid-123"},
            )
            self.assertEqual(payload["pnToPublic"], {"111@hosted": "111"})
            self.assertLess(
                payload["projectionOrder"]["123@hosted.lid"],
                payload["projectionOrder"]["111@hosted"],
            )

            (auth_root / identity.LID_MAPPINGS_FILENAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "lidToPn": {"123@hosted.lid": "111@hosted"},
                    },
                ),
                "utf-8",
            )
            restarted = identity.IdentityMappingCache()
            identity.load_identity_state(auth_root, restarted)
            self.assertEqual(
                restarted.project_public_id("111@s.whatsapp.net"),
                "lid-123",
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

    def test_multiple_lids_can_alias_one_pn_without_being_dropped(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            cache = identity.IdentityMappingCache()
            cache.remember("123@lid", "111@s.whatsapp.net")
            cache.remember("456@hosted.lid", "111@hosted")

            self.assertEqual(cache.pn_for_lid("123@hosted.lid"), "111@s.whatsapp.net")
            self.assertEqual(cache.pn_for_lid("456@lid"), "111@hosted")
            self.assertEqual(cache.lid_for_pn("111@s.whatsapp.net"), "123@lid")
            self.assertTrue(
                identity.same_whatsapp_identity(
                    "456@lid",
                    "111@s.whatsapp.net",
                    cache,
                ),
            )
            gateway_payload = {
                "version": 1,
                "lidToPn": {
                    "123@lid": "111@s.whatsapp.net",
                    "456@hosted.lid": "111@hosted",
                },
            }
            (auth_root / identity.LID_MAPPINGS_FILENAME).write_text(
                json.dumps(gateway_payload),
                "utf-8",
            )

            restarted = identity.IdentityMappingCache()
            self.assertEqual(identity.load_identity_state(auth_root, restarted), 2)
            self.assertEqual(restarted.pn_for_lid("123@lid"), "111@s.whatsapp.net")
            self.assertEqual(restarted.pn_for_lid("456@lid"), "111@hosted")

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

            self.assertFalse((active_dir / identity.LID_MAPPINGS_FILENAME).exists())
            self.assertFalse((active_dir / identity.IDENTITY_PROJECTIONS_FILENAME).exists())

    def test_two_file_state_preserves_hosted_lid_projection_across_restart(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            cache = identity.IdentityMappingCache()
            self.assertEqual(cache.project_public_id("123:7@hosted.lid"), "lid-123")
            self.assertTrue(identity.save_identity_projections(auth_root, cache))
            self.assertFalse(identity.save_identity_projections(auth_root, cache))

            projection_path = auth_root / identity.IDENTITY_PROJECTIONS_FILENAME
            projection_payload = json.loads(projection_path.read_text("utf-8"))
            self.assertEqual(projection_payload["version"], 1)
            self.assertEqual(
                projection_payload["lidToPublic"],
                {"123@hosted.lid": "lid-123"},
            )
            self.assertEqual(projection_payload["pnToPublic"], {})
            self.assertFalse((auth_root / identity.LID_MAPPINGS_FILENAME).exists())
            self.assertEqual(
                list(auth_root.glob(f".{identity.IDENTITY_PROJECTIONS_FILENAME}.*.tmp")),
                [],
            )

            restarted = identity.IdentityMappingCache()
            self.assertEqual(identity.load_identity_state(auth_root, restarted), 0)
            self.assertEqual(restarted.project_public_id("123@hosted.lid"), "lid-123")
            self.assertEqual(
                identity.delivery_jid_from_session_id(
                    "lid-123",
                    is_group=False,
                    cache=restarted,
                ),
                "123@hosted.lid",
            )

            identity.save_lid_mapping(
                auth_root,
                "123@hosted.lid",
                "111@hosted",
                cache=restarted,
            )
            self.assertFalse((auth_root / identity.LID_MAPPINGS_FILENAME).exists())
            gateway_mapping_payload = {
                "version": 1,
                "lidToPn": {"123@hosted.lid": "111@hosted"},
            }
            (auth_root / identity.LID_MAPPINGS_FILENAME).write_text(
                json.dumps(gateway_mapping_payload),
                "utf-8",
            )

            after_mapping = identity.IdentityMappingCache()
            self.assertEqual(identity.load_identity_state(auth_root, after_mapping), 1)
            self.assertEqual(after_mapping.pn_for_lid("123@lid"), "111@hosted")
            self.assertEqual(after_mapping.project_public_id("111@s.whatsapp.net"), "lid-123")
            self.assertEqual(
                identity.delivery_jid_from_session_id(
                    "lid-123",
                    is_group=False,
                    cache=after_mapping,
                ),
                "123@hosted.lid",
            )

    def test_pn_first_projection_remains_numeric_across_restart(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            cache = identity.IdentityMappingCache()
            self.assertEqual(cache.project_public_id("111@hosted"), "111")
            cache.remember("123@hosted.lid", "111@hosted")
            self.assertEqual(cache.project_public_id("123@hosted.lid"), "111")
            (auth_root / identity.LID_MAPPINGS_FILENAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "lidToPn": {"123@hosted.lid": "111@hosted"},
                    },
                ),
                "utf-8",
            )
            self.assertTrue(identity.save_identity_projections(auth_root, cache))

            restarted = identity.IdentityMappingCache()
            self.assertEqual(identity.load_identity_state(auth_root, restarted), 1)
            self.assertEqual(restarted.project_public_id("123@lid"), "111")
            self.assertEqual(restarted.project_public_id("111@s.whatsapp.net"), "111")

    def test_supplemental_full_jids_override_but_do_not_modify_legacy_files(self) -> None:
        identity = self.identity
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            legacy = auth_root / "lid-mapping-123_reverse.json"
            legacy.write_text(json.dumps("111"), "utf-8")
            shared_path = auth_root / identity.LID_MAPPINGS_FILENAME
            shared_content = json.dumps(
                {
                    "version": 1,
                    "lidToPn": {"123@hosted.lid": "222@hosted"},
                    "gatewayOwned": True,
                },
            )
            shared_path.write_text(shared_content, "utf-8")

            identity.save_lid_mapping(
                auth_root,
                "123@hosted.lid",
                "222@hosted",
            )
            self.assertEqual(json.loads(legacy.read_text("utf-8")), "111")
            self.assertEqual(shared_path.read_text("utf-8"), shared_content)

            shared = json.loads(shared_path.read_text("utf-8"))
            self.assertEqual(shared["lidToPn"], {"123@hosted.lid": "222@hosted"})

            restarted = identity.IdentityMappingCache()
            self.assertEqual(identity.load_lid_mappings(auth_root, restarted), 1)
            self.assertEqual(restarted.pn_for_lid("123@lid"), "222@hosted")
            self.assertEqual(restarted.lid_for_pn("222@s.whatsapp.net"), "123@hosted.lid")

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
