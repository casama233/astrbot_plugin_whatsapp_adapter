from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _install_stubs() -> tuple[type, type, type, type, type, type]:
    astrbot = types.ModuleType("astrbot")
    astrbot.logger = _Logger()
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = types.ModuleType("astrbot.api")

    platform = types.ModuleType("astrbot.api.platform")

    class At:
        def __init__(self, qq: str = "", name: str = "") -> None:
            self.qq = qq
            self.name = name

    class AstrBotMessage:
        def __init__(self) -> None:
            self.raw_message = {}

    class PlatformMetadata:
        def __init__(self, name: str = "whatsapp") -> None:
            self.name = name

    platform.At = At
    platform.AstrBotMessage = AstrBotMessage
    platform.PlatformMetadata = PlatformMetadata
    sys.modules["astrbot.api.platform"] = platform

    components = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text: str = "") -> None:
            self.text = text

    class Reply:
        def __init__(
            self,
            id: str = "",
            chain=None,
            sender_id: str = "",
            qq: str = "",
        ) -> None:
            self.id = id
            self.chain = list(chain or [])
            self.sender_id = sender_id
            self.qq = qq

    class DummyComponent:
        pass

    components.Plain = Plain
    components.Reply = Reply
    components.File = type("File", (), {})
    components.Image = type("Image", (), {})
    components.Record = type("Record", (), {})
    components.Video = type("Video", (), {})
    sys.modules["astrbot.api.message_components"] = components

    events = types.ModuleType("astrbot.api.event")

    class MessageChain:
        def __init__(self, chain=None) -> None:
            self.chain = list(chain or [])
            self.type = None

    class AstrMessageEvent:
        def __init__(self, _message_str, _message_obj, platform_meta, _session_id) -> None:
            self.platform_meta = platform_meta
            self._has_send_oper = False

        async def send(self, _message) -> None:
            return None

    events.MessageChain = MessageChain
    events.AstrMessageEvent = AstrMessageEvent
    sys.modules["astrbot.api.event"] = events

    sys.modules["astrbot.core"] = types.ModuleType("astrbot.core")
    sys.modules["astrbot.core.utils"] = types.ModuleType("astrbot.core.utils")
    io_module = types.ModuleType("astrbot.core.utils.io")

    async def download_image_by_url(_url: str) -> str:
        return "/tmp/test-media"

    io_module.download_image_by_url = download_image_by_url
    sys.modules["astrbot.core.utils.io"] = io_module

    metrics = types.ModuleType("astrbot.core.utils.metrics")

    class Metric:
        @staticmethod
        async def upload(**_kwargs) -> None:
            return None

    metrics.Metric = Metric
    sys.modules["astrbot.core.utils.metrics"] = metrics
    return Plain, Reply, MessageChain, AstrBotMessage, PlatformMetadata, At


Plain, Reply, MessageChain, AstrBotMessage, PlatformMetadata, At = _install_stubs()
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
PACKAGE = ROOT.name

client_module = types.ModuleType(f"{PACKAGE}.whatsapp_client")
client_module.WhatsAppGatewayClient = type("WhatsAppGatewayClient", (), {})
sys.modules[f"{PACKAGE}.whatsapp_client"] = client_module

component_module = types.ModuleType(f"{PACKAGE}.whatsapp_components")
for name in ("WhatsAppButtons", "WhatsAppEdit", "WhatsAppList", "WhatsAppPoll"):
    setattr(component_module, name, type(name, (), {}))
sys.modules[f"{PACKAGE}.whatsapp_components"] = component_module

helpers = importlib.import_module(f"{PACKAGE}.whatsapp_helpers")
event_module = importlib.import_module(f"{PACKAGE}.whatsapp_event")
WhatsAppMessageEvent = event_module.WhatsAppMessageEvent


class ConverterTests(unittest.TestCase):
    def test_official_whatsapp_output_syntax(self) -> None:
        cases = {
            "**bold**": "*bold*",
            "__bold__": "*bold*",
            "*italic*": "_italic_",
            "_italic_": "_italic_",
            "~~strike~~": "~strike~",
            "`inline`": "`inline`",
            "```python\nprint('x')\n```": "```print('x')\n```",
            "- item": "- item",
            "* item": "- item",
            "1) item": "1. item",
            ">quote": "> quote",
            "# Heading": "*Heading*",
            "[OpenAI](https://openai.com)": "OpenAI (https://openai.com)",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(helpers.format_whatsapp_markdown(source), expected)

    def test_table_is_degraded_to_official_list_and_bold(self) -> None:
        source = "| Time | Strength |\n|---|---|\n| Today | Typhoon |\n"
        result = helpers.format_whatsapp_markdown(source)
        self.assertEqual(result, "- *Time:* Today | *Strength:* Typhoon\n\n")
        self.assertNotIn("|---", result)

    def test_code_is_never_reformatted(self) -> None:
        cases = [
            "`**bold** _italic_ ~~strike~~`",
            "`` `nested` **bold** ``",
            "```\n**bold**\n*italic*\n~~strike~~\n```",
            "```python\nvalue = '**bold**'\n",
        ]
        for source in cases:
            with self.subTest(source=source):
                result = helpers.format_whatsapp_markdown(source)
                self.assertIn("**bold**", result)

    def test_streaming_markers_are_balanced(self) -> None:
        raw = ""
        renders = []
        for part in ("**", "storm", "**"):
            raw += part
            renders.append(helpers.format_whatsapp_markdown(raw, streaming=True))
        self.assertEqual(renders, ["*", "*storm*", "*storm*"])
        self.assertFalse(helpers.has_visible_whatsapp_content(renders[0]))

    def test_source_format_contract_prevents_double_conversion(self) -> None:
        native = "*bold* _italic_ ~strike~ `code`"
        self.assertEqual(
            helpers.format_whatsapp_markdown(native, source_format="whatsapp"),
            native,
        )
        markdown = "**bold**"
        rendered = helpers.format_whatsapp_markdown(markdown)
        self.assertEqual(
            helpers.format_whatsapp_markdown(rendered, source_format="whatsapp"),
            rendered,
        )

    def test_split_balances_formatting_and_preserves_graphemes(self) -> None:
        text = "*" + ("A" * 40) + "* 👨‍👩‍👧‍👦"
        chunks = helpers.split_whatsapp_text(text, 20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))
        self.assertTrue(all(chunk.count("*") % 2 == 0 for chunk in chunks))
        self.assertTrue(any("👨‍👩‍👧‍👦" in chunk for chunk in chunks))

    def test_mentions_are_filtered_per_chunk(self) -> None:
        refs = [
            helpers.MentionRef("1@s.whatsapp.net", "@Alice"),
            helpers.MentionRef("2@s.whatsapp.net", "@Bob"),
        ]

        async def run() -> list[str]:
            return await helpers.mentions_for_text(None, "", "hello @Bob", refs)

        self.assertEqual(asyncio.run(run()), ["2@s.whatsapp.net"])


class ProcessChainTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self) -> None:
            self.sent: list[tuple[str, list[str]]] = []

        async def send_text(self, _target, text, **kwargs):
            self.sent.append((text, kwargs.get("mentions") or []))
            return {"id": str(len(self.sent))}

    async def test_adjacent_plain_components_are_converted_once(self) -> None:
        client = self.Client()
        pending, mentions = await helpers.process_message_chain(
            client,
            "target",
            [Plain("**bo"), Plain("ld**")],
        )
        await helpers.flush_pending_text(client, "target", pending, mentions)
        self.assertEqual(client.sent, [("*bold*", [])])

    async def test_reply_component_is_transport_metadata_not_nested_output(self) -> None:
        client = self.Client()
        pending, mentions = await helpers.process_message_chain(
            client,
            "target",
            [Reply(id="old-question", chain=[Plain("unrelated question")]), Plain("answer")],
        )
        await helpers.flush_pending_text(client, "target", pending, mentions)
        self.assertEqual(client.sent, [("answer", [])])


class EventQuoteTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send_text(self, _target, text, **kwargs):
            self.sent.append({"text": text, **kwargs})
            return {"id": str(len(self.sent))}

    def event(self) -> WhatsAppMessageEvent:
        message = AstrBotMessage()
        message.raw_message = {
            "senderJid": "15550001@s.whatsapp.net",
            "quoted": {"participant": "bot@s.whatsapp.net"},
        }
        return WhatsAppMessageEvent(
            "",
            message,
            PlatformMetadata(),
            "session",
            self.client,
            "chat@g.us",
            source_message_id="current-question",
            typing_indicator=False,
        )

    async def asyncSetUp(self) -> None:
        self.client = self.Client()

    async def test_plain_outgoing_chain_does_not_quote_implicitly(self) -> None:
        await self.event().send(MessageChain([Plain("answer")]))
        self.assertIsNone(self.client.sent[0]["quoted_message_id"])

    async def test_only_reply_segment_quotes_current_source_message(self) -> None:
        event = self.event()
        await event.send(
            MessageChain([Reply(id="current-question"), Plain("first segment")]),
        )
        await event.send(MessageChain([Plain("second segment")]))

        self.assertEqual(
            self.client.sent[0]["quoted_message_id"],
            "current-question",
        )
        self.assertEqual(
            self.client.sent[0]["quoted_participant"],
            "15550001@s.whatsapp.net",
        )
        self.assertIsNone(self.client.sent[1]["quoted_message_id"])


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self, *, fail_edit: bool = False, return_id: bool = True) -> None:
            self.operations: list[tuple[str, str, list[str]]] = []
            self.quote_ids: list[str | None] = []
            self.fail_edit = fail_edit
            self.return_id = return_id

        async def send_text(self, _target, text, **kwargs):
            self.operations.append(("send", text, kwargs.get("mentions") or []))
            self.quote_ids.append(kwargs.get("quoted_message_id"))
            return {"id": f"m{len(self.operations)}" if self.return_id else ""}

        async def edit_text(self, _target, _message_id, text, **kwargs):
            self.operations.append(("edit", text, kwargs.get("mentions") or []))
            if self.fail_edit:
                raise RuntimeError("edit unsupported")
            return {"id": _message_id}

    def event(self, client, **kwargs):
        return WhatsAppMessageEvent(
            "",
            AstrBotMessage(),
            PlatformMetadata(),
            "session",
            client,
            "target",
            typing_indicator=False,
            streaming_edit_throttle=0.1,
            **kwargs,
        )

    async def test_marker_only_chunk_is_not_sent(self) -> None:
        async def chunks():
            yield MessageChain([Plain("**")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain("🥥 最新動態")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain("**")])

        client = self.Client()
        await self.event(client)._send_streaming_edit(chunks())
        self.assertEqual(client.operations[0][1], "*🥥 最新動態*")
        self.assertNotIn("**", client.operations[-1][1])

    async def test_streaming_does_not_force_quote_without_reply_component(self) -> None:
        async def chunks():
            yield MessageChain([Plain("streamed answer")])

        client = self.Client()
        await self.event(
            client,
            source_message_id="current-question",
        )._send_streaming_edit(chunks())
        self.assertEqual(client.quote_ids, [None])

    async def test_throttle_happens_before_render(self) -> None:
        original = event_module.format_whatsapp_markdown
        calls = 0

        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        async def chunks():
            for char in "abcdefghij":
                yield MessageChain([Plain(char)])

        client = self.Client()
        with patch.object(event_module, "format_whatsapp_markdown", counted):
            await self.event(client)._send_streaming_edit(chunks())
        self.assertLessEqual(calls, 2)

    async def test_long_bold_stream_is_split_into_balanced_messages(self) -> None:
        async def chunks():
            yield MessageChain([Plain("**" + "A" * 60 + "**")])

        client = self.Client()
        await self.event(client, text_chunk_limit=20)._send_streaming_edit(chunks())
        texts = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertGreater(len(texts), 1)
        self.assertTrue(all(len(text) <= 20 for text in texts))
        self.assertTrue(all(text.count("*") % 2 == 0 for text in texts))

    async def test_edit_failure_sends_one_complete_final_fallback(self) -> None:
        async def chunks():
            yield MessageChain([Plain("hello")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" world")])

        client = self.Client(fail_edit=True)
        await self.event(client)._send_streaming_edit(chunks())
        sends = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertEqual(sends[-1], "hello world")
        self.assertEqual(sends.count("hello world"), 1)

    async def test_missing_message_id_falls_back_at_end(self) -> None:
        async def chunks():
            yield MessageChain([Plain("final text")])

        client = self.Client(return_id=False)
        await self.event(client)._send_streaming_edit(chunks())
        sends = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertEqual(sends[-1], "final text")
        self.assertEqual(len(sends), 2)

    async def test_mentions_only_attach_to_matching_chunk(self) -> None:
        async def chunks():
            yield MessageChain([At(qq="1@s.whatsapp.net", name="Alice")])
            yield MessageChain([Plain(" " + "x" * 30)])
            yield MessageChain([At(qq="2@s.whatsapp.net", name="Bob")])

        client = self.Client()
        await self.event(client, text_chunk_limit=20)._send_streaming_edit(chunks())
        for _operation, text, mentions in client.operations:
            if "@Alice" not in text:
                self.assertNotIn("1@s.whatsapp.net", mentions)
            if "@Bob" not in text:
                self.assertNotIn("2@s.whatsapp.net", mentions)


if __name__ == "__main__":
    unittest.main()
