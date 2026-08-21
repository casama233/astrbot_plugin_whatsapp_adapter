from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "member_tag_compat.py"
spec = importlib.util.spec_from_file_location("member_tag_compat", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class _Sender:
    def __init__(self):
        self.user_id = "10001"
        self.nickname = "Test User"


class _Group:
    group_id = "120363000000000001"


class _Message:
    def __init__(self):
        self.group = _Group()
        self.sender = _Sender()
        self.raw_message = {
            "sender": {
                "user_id": "10001",
                "nickname": "Test User",
                "role": "member",
            },
        }


class _Member:
    def __init__(self):
        self.user_id = "10002"
        self.nickname = "Another User"


class MemberTagCompatibilityTests(unittest.TestCase):
    def test_sender_tag_is_separate_from_nickname_and_role(self):
        message = _Message()
        module.apply_sender_member_tag(
            message,
            {"senderMemberTag": "test-tag"},
        )

        self.assertEqual(message.sender.nickname, "Test User")
        self.assertEqual(message.sender.member_tag, "test-tag")
        self.assertEqual(message.raw_message["sender"]["role"], "member")
        self.assertEqual(message.raw_message["sender"]["member_tag"], "test-tag")
        self.assertEqual(message.raw_message["sender"]["memberTag"], "test-tag")
        self.assertEqual(message.raw_message["senderMemberTag"], "test-tag")

    def test_empty_sender_tag_clears_previous_value(self):
        message = _Message()
        message.sender.member_tag = "old-tag"
        message.raw_message["sender"]["member_tag"] = "old-tag"

        module.apply_sender_member_tag(message, {"senderMemberTag": ""})

        self.assertEqual(message.sender.member_tag, "")
        self.assertEqual(message.raw_message["sender"]["member_tag"], "")

    def test_group_member_tag_is_exposed_on_message_member(self):
        member = _Member()
        returned = module.apply_group_member_tag(member, {"memberTag": "test-tag"})
        self.assertIs(returned, member)
        self.assertEqual(member.member_tag, "test-tag")

    def test_private_message_without_transport_tag_is_unchanged(self):
        message = _Message()
        message.group = None
        module.apply_sender_member_tag(message, {})
        self.assertFalse(hasattr(message.sender, "member_tag"))


if __name__ == "__main__":
    unittest.main()
