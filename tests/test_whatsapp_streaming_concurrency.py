from __future__ import annotations

import unittest

from whatsapp_streaming_concurrency import ResponsePresenceLeases


class ResponsePresenceLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.leases = ResponsePresenceLeases()
        self.client = object()

    def test_same_chat_waits_for_last_concurrent_response_before_pause(self) -> None:
        target = "123@s.whatsapp.net"

        self.assertEqual(self.leases.acquire(self.client, target), 1)
        self.assertTrue(self.leases.should_pause(self.client, target))

        self.assertEqual(self.leases.acquire(self.client, target), 2)
        self.assertFalse(self.leases.should_pause(self.client, target))

        self.assertEqual(self.leases.release(self.client, target), 1)
        self.assertTrue(self.leases.should_pause(self.client, target))

        self.assertEqual(self.leases.release(self.client, target), 0)
        self.assertEqual(self.leases.count(self.client, target), 0)

    def test_empty_and_unknown_leases_keep_legacy_pause_semantics(self) -> None:
        target = "123@s.whatsapp.net"

        self.assertEqual(self.leases.count(self.client, target), 0)
        self.assertTrue(self.leases.should_pause(self.client, target))
        self.assertEqual(self.leases.release(self.client, target), 0)
        self.assertEqual(self.leases.count(self.client, target), 0)

    def test_different_chats_do_not_share_presence_state(self) -> None:
        first = "111@s.whatsapp.net"
        second = "222@s.whatsapp.net"

        self.leases.acquire(self.client, first)
        self.leases.acquire(self.client, first)
        self.leases.acquire(self.client, second)

        self.assertEqual(self.leases.count(self.client, first), 2)
        self.assertEqual(self.leases.count(self.client, second), 1)
        self.assertFalse(self.leases.should_pause(self.client, first))
        self.assertTrue(self.leases.should_pause(self.client, second))

    def test_different_gateway_clients_do_not_share_chat_state(self) -> None:
        target = "123@s.whatsapp.net"
        other_client = object()

        self.leases.acquire(self.client, target)
        self.leases.acquire(self.client, target)
        self.leases.acquire(other_client, target)

        self.assertEqual(self.leases.count(self.client, target), 2)
        self.assertEqual(self.leases.count(other_client, target), 1)


if __name__ == "__main__":
    unittest.main()
