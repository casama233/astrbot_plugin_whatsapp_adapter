import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "identity_migration_under_test",
        ROOT / "scripts" / "migrate_legacy_identities.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class IdentityMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.migration = _module()

    def test_merges_proven_lid_trailing_zero_and_group_sender_records(self):
        aliases = {"123": "85200000000", "lid-123": "85200000000", "123@lid": "85200000000"}
        users = [
            {"user_id": "123", "nickname": "Cary", "message_count": 2, "history": ["2026-08-01:2"], "first_message_time": 1, "last_message_time": 2},
            {"user_id": "85200000000", "nickname": "Cary", "message_count": 3, "history": ["2026-08-01:1", "2026-08-02:2"], "first_message_time": 3, "last_message_time": 4},
            {"user_id": "852000000000", "nickname": "Cary", "message_count": 1, "history": ["2026-08-03:1"], "first_message_time": 5, "last_message_time": 5},
            {"user_id": "1203631", "nickname": "Cary", "message_count": 4, "history": ["2026-08-04:4"], "first_message_time": 6, "last_message_time": 6},
        ]
        result = self.migration.merge_users(users, "1203631", aliases)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["user_id"], "85200000000")
        self.assertEqual(result[0]["message_count"], 10)
        self.assertEqual(result[0]["history"], [
            "2026-08-01:3", "2026-08-02:2", "2026-08-03:1", "2026-08-04:4"
        ])

    def test_trailing_zero_is_not_globally_treated_as_an_alias(self):
        aliases = {"123": "85200000000"}
        self.assertEqual(
            self.migration.canonical_id("852000000000", aliases),
            "852000000000",
        )
        self.assertEqual(
            self.migration.canonical_text("852000000000", aliases),
            "852000000000",
        )

    def test_generic_json_preserves_keys_and_unrelated_exact_numbers(self):
        aliases = {"123": "85200000000"}
        value = {
            "123": "123",
            "whatsapp:FriendMessage:123": "whatsapp:FriendMessage:123",
        }
        projected = self.migration.canonical_json(value, aliases)
        self.assertEqual(set(projected), set(value))
        self.assertEqual(projected["123"], "123")
        self.assertEqual(
            projected["whatsapp:FriendMessage:123"],
            "whatsapp:FriendMessage:85200000000",
        )

    def test_origin_map_keeps_disagreeing_collision_instead_of_overwriting(self):
        aliases = {"123": "85200000000"}
        projected = self.migration.canonical_origin_map(
            {"123": "first", "85200000000": "second"},
            aliases,
        )
        self.assertEqual(projected["123"], "first")
        self.assertEqual(projected["85200000000"], "second")

    def test_apply_backs_up_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root)
            mapping = data / "plugin_data" / "adapter" / "astrbot-lid-mappings-v1.json"
            mapping.parent.mkdir(parents=True)
            mapping.write_text(json.dumps({"lidToPn": {"123@lid": "85200000000@s.whatsapp.net"}}), "utf-8")
            target = data / "cmd_config.json"
            target.write_text(json.dumps({"admins": ["whatsapp:FriendMessage:123@lid"]}), "utf-8")
            aliases = self.migration.load_aliases(data)
            runner = self.migration.Migration(data, True)
            runner.migrate_json(target, aliases)
            self.assertEqual(json.loads(target.read_text("utf-8"))["admins"], ["whatsapp:FriendMessage:85200000000"])
            self.assertTrue((runner.backup_dir / "cmd_config.json").exists())
            self.assertEqual(list(data.glob(".cmd_config.json.*.tmp")), [])
            again = self.migration.Migration(data, True)
            again.migrate_json(target, aliases)
            self.assertEqual(again.changed, [])


if __name__ == "__main__":
    unittest.main()
