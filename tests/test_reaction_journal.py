from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor

from whatsapp_reaction_journal import ReactionJournal


class ReactionJournalTests(unittest.TestCase):
    def test_tracks_and_sorts_reaction_users(self) -> None:
        journal = ReactionJournal(ttl_seconds=10)
        journal.record(
            chat_id="group",
            message_id="msg",
            sender_id="bot-b",
            emoji="⚙️",
            now=1,
        )
        journal.record(
            chat_id="group",
            message_id="msg",
            sender_id="bot-a",
            emoji="⚙️",
            now=1,
        )
        self.assertEqual(
            journal.users(chat_id="group", message_id="msg", emoji="⚙️", now=2),
            ["bot-a", "bot-b"],
        )

    def test_new_reaction_preserves_claim_during_protocol_window(self) -> None:
        journal = ReactionJournal(ttl_seconds=10)
        journal.record(
            chat_id="group",
            message_id="msg",
            sender_id="bot",
            emoji="⚙️",
            now=1,
        )
        journal.record(
            chat_id="group",
            message_id="msg",
            sender_id="bot",
            emoji="✅",
            now=2,
        )
        self.assertEqual(
            journal.users(chat_id="group", message_id="msg", emoji="⚙️", now=2),
            ["bot"],
        )
        self.assertEqual(
            journal.users(chat_id="group", message_id="msg", emoji="✅", now=2),
            ["bot"],
        )

    def test_empty_reaction_removes_all_sender_observations(self) -> None:
        journal = ReactionJournal(ttl_seconds=10)
        for emoji in ("⚙️", "✅"):
            journal.record(
                chat_id="group",
                message_id="msg",
                sender_id="bot",
                emoji=emoji,
                now=1,
            )
        journal.record(
            chat_id="group",
            message_id="msg",
            sender_id="bot",
            emoji="",
            now=2,
        )
        self.assertEqual(
            journal.users(chat_id="group", message_id="msg", emoji="⚙️", now=2),
            [],
        )
        self.assertEqual(
            journal.users(chat_id="group", message_id="msg", emoji="✅", now=2),
            [],
        )

    def test_ttl_prunes_old_entries(self) -> None:
        journal = ReactionJournal(ttl_seconds=10)
        journal.record(
            chat_id="group",
            message_id="msg",
            sender_id="bot",
            emoji="⚙️",
            now=3,
        )
        self.assertEqual(
            journal.users(chat_id="group", message_id="msg", emoji="⚙️", now=14),
            [],
        )

    def test_invalid_identity_is_not_recorded(self) -> None:
        journal = ReactionJournal()
        self.assertFalse(
            journal.record(chat_id="", message_id="msg", sender_id="bot", emoji="⚙️")
        )
        self.assertFalse(
            journal.record(chat_id="group", message_id="", sender_id="bot", emoji="⚙️")
        )
        self.assertFalse(
            journal.record(chat_id="group", message_id="msg", sender_id="", emoji="⚙️")
        )
        self.assertEqual(
            journal.users(chat_id="", message_id="msg", emoji="⚙️"),
            [],
        )

    def test_chat_message_and_emoji_namespaces_do_not_mix(self) -> None:
        journal = ReactionJournal(ttl_seconds=10)
        journal.record(
            chat_id="group-a",
            message_id="msg-a",
            sender_id="bot-a",
            emoji="⚙️",
            now=1,
        )
        journal.record(
            chat_id="group-b",
            message_id="msg-a",
            sender_id="bot-b",
            emoji="⚙️",
            now=1,
        )
        journal.record(
            chat_id="group-a",
            message_id="msg-b",
            sender_id="bot-c",
            emoji="⚙️",
            now=1,
        )
        journal.record(
            chat_id="group-a",
            message_id="msg-a",
            sender_id="bot-d",
            emoji="✅",
            now=1,
        )
        self.assertEqual(
            journal.users(chat_id="group-a", message_id="msg-a", emoji="⚙️", now=2),
            ["bot-a"],
        )

    def test_concurrent_writers_do_not_corrupt_iteration(self) -> None:
        journal = ReactionJournal(ttl_seconds=30)

        def record(index: int) -> None:
            journal.record(
                chat_id="group",
                message_id="msg",
                sender_id=f"bot-{index:03d}",
                emoji="⚙️",
                now=1,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(100)))

        users = journal.users(
            chat_id="group",
            message_id="msg",
            emoji="⚙️",
            now=2,
        )
        self.assertEqual(len(users), 100)
        self.assertEqual(users[0], "bot-000")
        self.assertEqual(users[-1], "bot-099")


if __name__ == "__main__":
    unittest.main()
