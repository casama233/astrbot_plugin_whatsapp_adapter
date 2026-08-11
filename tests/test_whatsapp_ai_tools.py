from __future__ import annotations

import asyncio
import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import whatsapp_ai_tools as tools


ROOT = Path(__file__).resolve().parents[1]


class _MessageChain:
    def __init__(self) -> None:
        self.chain = []


class _AstrMessageEvent:
    async def send(self, _chain) -> None:
        self.base_send_calls += 1
        self._has_send_oper = True


def _astrbot_bookkeeping_modules() -> dict[str, types.ModuleType]:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = _AstrMessageEvent
    event.MessageChain = _MessageChain
    astrbot.api = api
    api.event = event
    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
    }


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.fail = fail

    async def _send(self, kind: str, target: str, **kwargs):
        self.calls.append((kind, target, kwargs))
        if self.fail:
            raise RuntimeError("transport failed")
        return {"ok": True, "id": f"{kind}-id"}

    async def send_poll(self, target: str, **kwargs):
        return await self._send("poll", target, **kwargs)

    async def send_contact(self, target: str, **kwargs):
        return await self._send("contact", target, **kwargs)

    async def send_event(self, target: str, **kwargs):
        return await self._send("event", target, **kwargs)


class _Event:
    def __init__(
        self,
        *,
        platform: str = "whatsapp",
        target: str = "120363000000000001@g.us",
        raw_target: str | None = None,
        fail: bool = False,
    ) -> None:
        self.platform = platform
        self.target_jid = target
        self.message_obj = types.SimpleNamespace(
            raw_message={"chatJid": raw_target if raw_target is not None else target},
        )
        self.client = _Client(fail=fail)
        self.platform_meta = types.SimpleNamespace(name="whatsapp")
        self.base_send_calls = 0
        self._has_send_oper = False
        self._super_sent = False
        self.completed_pre_ack = 0
        self.cleared_pre_ack = 0

    def get_platform_name(self) -> str:
        return self.platform

    async def _complete_pre_ack(self) -> None:
        self.completed_pre_ack += 1

    async def _clear_pre_ack(self) -> None:
        self.cleared_pre_ack += 1


class WhatsAppAiToolTests(unittest.IsolatedAsyncioTestCase):
    async def _with_bookkeeping(self, awaitable):
        with patch.dict(sys.modules, _astrbot_bookkeeping_modules()):
            return await awaitable

    async def test_poll_is_bound_to_current_event_and_marks_success(self) -> None:
        event = _Event()
        result = await self._with_bookkeeping(
            tools.create_poll(event, "午餐？", ["点心", "面"], 1),
        )

        self.assertEqual(result["id"], "poll-id")
        self.assertEqual(
            event.client.calls,
            [
                (
                    "poll",
                    "120363000000000001@g.us",
                    {
                        "name": "午餐？",
                        "options": ["点心", "面"],
                        "selectable_count": 1,
                    },
                ),
            ],
        )
        self.assertEqual(event.base_send_calls, 1)
        self.assertTrue(event._has_send_oper)
        self.assertTrue(event._super_sent)
        self.assertEqual(event.completed_pre_ack, 1)

    async def test_non_whatsapp_and_mismatched_target_are_explicitly_rejected(self) -> None:
        for event in (
            _Event(platform="aiocqhttp"),
            _Event(raw_target="999@s.whatsapp.net"),
        ):
            with self.assertRaises(tools.WhatsAppToolRejected):
                await tools.create_poll(event, "Question", ["A", "B"], 1)
            self.assertEqual(event.client.calls, [])
            self.assertEqual(event.base_send_calls, 0)

    async def test_transport_failure_is_not_bookkept(self) -> None:
        event = _Event(fail=True)
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            await tools.create_poll(event, "Question", ["A", "B"], 1)
        self.assertEqual(event.base_send_calls, 0)
        self.assertFalse(event._has_send_oper)
        self.assertEqual(event.cleared_pre_ack, 1)

    async def test_contact_is_normalized_without_exposing_a_target_argument(self) -> None:
        event = _Event(target="85212345678@s.whatsapp.net")
        await self._with_bookkeeping(
            tools.share_contact(event, " Alice ", "+852 9876-5432", " Example, Inc. "),
        )
        self.assertEqual(
            event.client.calls[0],
            (
                "contact",
                "85212345678@s.whatsapp.net",
                {
                    "display_name": "Alice",
                    "phone_number": "+85298765432",
                    "organization": "Example, Inc.",
                },
            ),
        )
        self.assertNotIn("target", inspect.signature(tools.share_contact).parameters)
        self.assertNotIn("jid", inspect.signature(tools.share_contact).parameters)

    async def test_event_requires_timezone_and_preserves_the_instant(self) -> None:
        event = _Event()
        await self._with_bookkeeping(
            tools.create_event(
                event,
                "深圳行程",
                "2026-08-15T09:00:00+08:00",
                "2026-08-15T12:00:00+08:00",
                "集合",
                "深圳",
                "广东省深圳市",
                False,
            ),
        )
        payload = event.client.calls[0][2]
        self.assertEqual(payload["start_timestamp_ms"], 1_786_755_600_000)
        self.assertEqual(payload["end_timestamp_ms"], 1_786_766_400_000)
        self.assertFalse(payload["extra_guests_allowed"])

        with self.assertRaisesRegex(tools.WhatsAppToolRejected, "时区"):
            tools.normalize_event("行程", "2026-08-15T09:00:00")
        with self.assertRaisesRegex(tools.WhatsAppToolRejected, "366 天"):
            tools.normalize_event(
                "超长活动",
                "2026-08-15T09:00:00+08:00",
                "2027-08-16T10:00:00+08:00",
            )

    def test_poll_validation_rejects_duplicates_and_invalid_selection_count(self) -> None:
        with self.assertRaisesRegex(tools.WhatsAppToolRejected, "不能重复"):
            tools.normalize_poll("Question", ["Yes", "yes"], 1)
        with self.assertRaisesRegex(tools.WhatsAppToolRejected, "必须在"):
            tools.normalize_poll("Question", ["A", "B"], 3)

    def test_main_registers_documented_current_session_tools(self) -> None:
        source = (ROOT / "main.py").read_text("utf-8")
        for name in (
            "whatsapp_create_poll",
            "whatsapp_share_contact",
            "whatsapp_create_event",
        ):
            self.assertIn(f'@filter.llm_tool(name="{name}")', source)
        self.assertIn("options(array[string])", source)
        self.assertNotIn("whatsapp_create_sticker", source)


if __name__ == "__main__":
    unittest.main()
