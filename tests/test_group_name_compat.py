from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "group_name_compat.py"
spec = importlib.util.spec_from_file_location("group_name_compat", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class _Group:
    def __init__(self, group_id: str):
        self.group_id = group_id
        self.group_name = None
        self.group_owner = None
        self.group_admins = None


class _Message:
    def __init__(self):
        self.group = _Group("120363399820499653")
        self.raw_message = {}


class _Event:
    def __init__(self, message):
        self.message_obj = message
        self.target_jid = "120363399820499653@g.us"


class GroupNameCompatibilityTests(unittest.TestCase):
    def test_applies_standard_group_name_and_raw_aliases(self):
        message = _Message()
        module.apply_group_name(message, {"groupName": "測試群組"})
        self.assertEqual(message.group.group_name, "測試群組")
        self.assertEqual(message.raw_message["groupName"], "測試群組")
        self.assertEqual(message.raw_message["group_name"], "測試群組")
        self.assertEqual(message.raw_message["groupSubject"], "測試群組")

    def test_accepts_legacy_group_subject_alias(self):
        self.assertEqual(
            module.extract_group_name({"groupSubject": "舊插件兼容群"}),
            "舊插件兼容群",
        )

    def test_applies_group_owner_and_admin_snapshot(self):
        message = _Message()
        module.apply_group_name(
            message,
            {
                "groupName": "群資料測試",
                "groupOwner": "15550001",
                "groupAdmins": ["15550001", 15550002, ""],
            },
        )
        self.assertEqual(message.group.group_owner, "15550001")
        self.assertEqual(message.group.group_admins, ["15550002"])

    def test_group_admin_snapshot_is_string_deduplicated_and_excludes_owner(self):
        message = _Message()
        module.apply_group_name(
            message,
            {
                "groupOwner": 15550001,
                "groupAdmins": [
                    15550001,
                    15550002,
                    "15550002",
                    " 15550003 ",
                    "",
                ],
            },
        )
        self.assertEqual(message.group.group_owner, "15550001")
        self.assertEqual(
            message.group.group_admins,
            ["15550002", "15550003"],
        )

    def test_full_group_identities_use_stable_projector_without_bare_lid_collision(self):
        message = _Message()
        calls = []

        def projector(value, *, lid_jid=None, pn_jid=None):
            calls.append((value, lid_jid, pn_jid))
            if pn_jid:
                return str(pn_jid).split("@", 1)[0].split(":", 1)[0]
            return f"lid-{str(lid_jid or value).split('@', 1)[0].split(':', 1)[0]}"

        module.apply_group_name(
            message,
            {
                "groupOwner": "999999",
                "groupOwnerJid": "700:4@lid",
                "groupOwnerPnJid": "15550001:7@s.whatsapp.net",
                "groupAdminIdentities": [
                    {
                        "jid": "701:8@hosted.lid",
                        "lidJid": "701:8@hosted.lid",
                    },
                    {
                        "jid": "702:9@lid",
                        "pnJid": "15550002:3@hosted",
                        "lidJid": "702:9@lid",
                    },
                ],
            },
            projector,
        )

        self.assertEqual(message.group.group_owner, "15550001")
        self.assertEqual(message.group.group_admins, ["lid-701", "15550002"])
        self.assertEqual(message.raw_message["groupOwner"], "15550001")
        self.assertEqual(
            message.raw_message["groupAdmins"],
            ["lid-701", "15550002"],
        )
        self.assertTrue(calls)

    def test_admin_update_still_excludes_owner_from_existing_snapshot(self):
        message = _Message()
        message.group.group_owner = "15550001"
        module.apply_group_name(
            message,
            {"groupAdmins": ["15550001", "15550002"]},
        )
        self.assertEqual(message.group.group_admins, ["15550002"])

    def test_event_group_lookup_accepts_numeric_and_jid_ids(self):
        message = _Message()
        event = _Event(message)
        self.assertIs(module.current_event_group(event), message.group)
        self.assertIs(
            module.current_event_group(event, "120363399820499653"),
            message.group,
        )
        self.assertIs(
            module.current_event_group(event, "120363399820499653@g.us"),
            message.group,
        )
        self.assertIsNone(module.current_event_group(event, "another-group"))

    def test_event_group_lookup_preserves_legacy_hyphenated_id(self):
        message = _Message()
        message.group.group_id = "123456789-123345"
        event = _Event(message)
        event.target_jid = "123456789-123345@g.us"
        self.assertIs(
            module.current_event_group(event, "123456789-123345"),
            message.group,
        )
        self.assertIs(
            module.current_event_group(event, "123456789-123345@g.us"),
            message.group,
        )


if __name__ == "__main__":
    unittest.main()
