from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_whatsapp_adapter_compat import _adapter_module
from test_whatsapp_markdown import event_module
from whatsapp_reaction_journal import ReactionJournal


class ReactionArbitrationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_inbound_reactions_are_observed_without_entering_message_delivery(self):
        module = _adapter_module()
        adapter = object.__new__(module.WhatsAppPlatformAdapter)
        adapter.config = {"ignore_self_messages": False}
        adapter._is_reaction_only = lambda _raw: True
        message = SimpleNamespace(
            raw_message={"chatJid": "group@g.us", "extras": {"reaction": {
                "key": {"id": "original"}, "text": "✅"}}},
            session_id="group", sender=SimpleNamespace(user_id="bot-b"),
        )
        journal = ReactionJournal()
        with patch.object(module, "_reaction_journal", journal):
            await adapter.handle_msg(message)
        self.assertEqual(journal.users(chat_id="group@g.us", message_id="original", emoji="✅"), ["bot-b"])

    async def test_send_failure_has_no_observation_and_journal_failure_does_not_report_send_failure(self):
        event = SimpleNamespace(
            source_message_id="original", target_jid="group@g.us", source_participant=None,
            get_self_id=lambda: "bot-a", client=SimpleNamespace(react=AsyncMock()),
        )
        journal = ReactionJournal()
        with patch.object(event_module, "_reaction_journal", journal):
            await event_module.WhatsAppMessageEvent.react(event, "✅")
            self.assertEqual(await event_module.WhatsAppMessageEvent.get_arbiter_reaction_users(event, "✅"), ["bot-a"])
            event.client.react.side_effect = RuntimeError("Gateway rejected")
            await event_module.WhatsAppMessageEvent.react(event, "❌")
            self.assertEqual(journal.users(chat_id="group@g.us", message_id="original", emoji="❌"), [])
        event.client.react.side_effect = None
        logger = Mock()
        with patch.object(event_module, "logger", logger), patch.object(event_module, "_reaction_journal", Mock(record=Mock(side_effect=RuntimeError("journal unavailable")))):
            await event_module.WhatsAppMessageEvent.react(event, "⚙️")
        logger.warning.assert_not_called()
        logger.debug.assert_called_once()

    async def test_accounts_in_one_process_share_observations_with_chat_and_message_boundaries(self):
        journal = ReactionJournal()
        accounts = [SimpleNamespace(source_message_id="original", target_jid="group@g.us", source_participant=None,
                    get_self_id=lambda account=account: account, client=SimpleNamespace(react=AsyncMock()))
                    for account in ("bot-a", "bot-b")]
        with patch.object(event_module, "_reaction_journal", journal):
            for event in accounts:
                await event_module.WhatsAppMessageEvent.react(event, "⚙️")
            self.assertEqual(await event_module.WhatsAppMessageEvent.get_arbiter_reaction_users(accounts[0], "⚙️"), ["bot-a", "bot-b"])
            accounts[0].target_jid = "another@g.us"
            self.assertEqual(await event_module.WhatsAppMessageEvent.get_arbiter_reaction_users(accounts[0], "⚙️"), [])

    def test_capacity_evicts_oldest_observation_and_refresh_keeps_recent_claim(self):
        journal = ReactionJournal(max_entries=2)
        for sender, now in [("a", 1), ("b", 2), ("a", 3), ("c", 4)]:
            journal.record(chat_id="g", message_id="m", sender_id=sender, emoji="✅", now=now)
        self.assertEqual(journal.users(chat_id="g", message_id="m", emoji="✅", now=4), ["a", "c"])
