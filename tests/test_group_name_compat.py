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


if __name__ == "__main__":
    unittest.main()
