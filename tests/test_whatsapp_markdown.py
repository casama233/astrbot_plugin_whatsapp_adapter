from __future__ import annotations

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _install_astrbot_stubs() -> tuple[type, type, type, type]:
    astrbot = types.ModuleType("astrbot")
    astrbot.logger = _Logger()
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", types.ModuleType("astrbot.api"))

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
    sys.modules.setdefault("astrbot.api.platform", platform)

    components = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text: str = "") -> None:
            self.text = text

    class DummyComponent:
        pass

    components.Plain = Plain
    components.File = DummyComponent
    components.Image = DummyComponent
    components.Record = DummyComponent
    components.Video = DummyComponent
    sys.modules.setdefault("astrbot.api.message_components", components)

    event_module = types.ModuleType("astrbot.api.event")

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

    event_module.MessageChain = MessageChain
    event_module.AstrMessageEvent = AstrMessageEvent
    sys.modules.setdefault("astrbot.api.event", event_module)

    sys.modules.setdefault("astrbot.core", types.ModuleType("astrbot.core"))
    sys.modules.setdefault("astrbot.core.utils", types.ModuleType("astrbot.core.utils"))

    io_module = types.ModuleType("astrbot.core.utils.io")

    async def download_image_by_url(_url: str) -> str:
        return "/tmp/whatsapp-test-media"

    io_module.download_image_by_url = download_image_by_url
    sys.modules.setdefault("astrbot.core.utils.io", io_module)

    metrics_module = types.ModuleType("astrbot.core.utils.metrics")

    class Metric:
        @staticmethod
        async def upload(**_kwargs) -> None:
            return None

    metrics_module.Metric = Metric
    sys.modules.setdefault("astrbot.core.utils.metrics", metrics_module)
    return Plain, MessageChain, AstrBotMessage, PlatformMetadata


Plain, MessageChain, AstrBotMessage, PlatformMetadata = _install_astrbot_stubs()

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = ROOT.name
sys.path.insert(0, str(ROOT.parent))

client_module = types.ModuleType(f"{PACKAGE_NAME}.whatsapp_client")
client_module.WhatsAppGatewayClient = type("WhatsAppGatewayClient", (), {})
sys.modules.setdefault(f"{PACKAGE_NAME}.whatsapp_client", client_module)

component_module = types.ModuleType(f"{PACKAGE_NAME}.whatsapp_components")
for component_name in ("WhatsAppButtons", "WhatsAppEdit", "WhatsAppList", "WhatsAppPoll"):
    setattr(component_module, component_name, type(component_name, (), {}))
sys.modules.setdefault(f"{PACKAGE_NAME}.whatsapp_components", component_module)

WhatsAppMessageEvent = importlib.import_module(f"{PACKAGE_NAME}.whatsapp_event").WhatsAppMessageEvent
helpers = importlib.import_module(f"{PACKAGE_NAME}.whatsapp_helpers")
format_whatsapp_markdown = helpers.format_whatsapp_markdown
format_markdown_from_whatsapp = helpers.format_markdown_from_whatsapp


class MarkdownFormattingTests(unittest.TestCase):
    def test_whatsapp_native_formats(self) -> None:
        cases = {
            "**bold**": "*bold*",
            "__bold__": "*bold*",
            "*italic*": "_italic_",
            "_italic_": "_italic_",
            "~~strike~~": "~strike~",
            "~strike~": "~strike~",
            "`code`": "`code`",
            "```code```": "```code```",
            "- item": "- item",
            "> quote": "> quote",
            "　indented with full-width space": "　indented with full-width space",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(format_whatsapp_markdown(source), expected)

    def test_code_spans_and_fences_are_not_affected(self) -> None:
        cases = {
            "`**bold** inside code`": "`**bold** inside code`",
            "`*italic* inside code`": "`*italic* inside code`",
            "`~~strike~~ inside code`": "`~~strike~~ inside code`",
            "`mixed *italic* and **bold** and ~~strike~~`":
                "`mixed *italic* and **bold** and ~~strike~~`",
            "`` `nested` ``": "`` `nested` ``",
            "```code with `inline` backticks```": "```code with `inline` backticks```",
            "```\n**bold**\n*italic*\n~~strike~~\n```":
                "```\n**bold**\n*italic*\n~~strike~~\n```",
            "```python\nvalue = '**bold** *italic* ~~strike~~'\n```":
                "```python\nvalue = '**bold** *italic* ~~strike~~'\n```",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(format_whatsapp_markdown(source), expected)

    def test_unclosed_code_is_preserved_conservatively(self) -> None:
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                source = "```python\nvalue = '**not formatting yet**'"
                self.assertEqual(
                    format_whatsapp_markdown(source, streaming=streaming),
                    source,
                )

    def test_combined_and_unmatched_markers_degrade_safely(self) -> None:
        cases = {
            "***both***": "*_both_*",
            "___both___": "*_both_*",
            "**unfinished": "*unfinished",
            "unfinished**": "unfinished*",
            "__unfinished": "*unfinished",
            "unfinished__": "unfinished*",
            "~~unfinished": "~unfinished",
            "unfinished~~": "unfinished~",
            "foo__bar": "foo__bar",
            "foo~~bar": "foo~~bar",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(format_whatsapp_markdown(source), expected)

    def test_inbound_code_is_not_rewritten(self) -> None:
        source = "```python\n*bold* _italic_ ~strike~\n```"
        self.assertEqual(format_markdown_from_whatsapp(source), source)
        self.assertEqual(
            format_markdown_from_whatsapp("*bold* _italic_ ~strike~"),
            "**bold** *italic* ~~strike~~",
        )

    def _fragmented(self, marker: str) -> list[str]:
        raw = ""
        rendered: list[str] = []
        for part in (marker, "storm update", marker):
            raw += part
            rendered.append(format_whatsapp_markdown(raw, streaming=True))
        return rendered

    def test_fragmented_bold_markers_are_collapsed_during_streaming(self) -> None:
        self.assertEqual(
            self._fragmented("**"),
            ["*", "*storm update", "*storm update*"],
        )
        self.assertEqual(
            self._fragmented("__"),
            ["*", "*storm update", "*storm update*"],
        )

    def test_fragmented_strike_marker_is_collapsed_during_streaming(self) -> None:
        self.assertEqual(
            self._fragmented("~~"),
            ["~", "~storm update", "~storm update~"],
        )


class StreamingMessageTests(unittest.IsolatedAsyncioTestCase):
    class FakeClient:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.edited: list[str] = []

        async def send_text(self, _target, text, **_kwargs):
            self.sent.append(text)
            return {"id": f"message-{len(self.sent)}"}

        async def edit_text(self, _target, _message_id, text, **_kwargs):
            self.edited.append(text)
            return {"id": _message_id}

    async def test_streaming_reformats_the_complete_raw_buffer(self) -> None:
        async def chunks():
            yield MessageChain([Plain("**")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain("🥥 熱帶風暴「諾盧」(Noul) 最新動態")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain("**\n\n")])

        client = self.FakeClient()
        event = WhatsAppMessageEvent(
            "",
            AstrBotMessage(),
            PlatformMetadata(),
            "session",
            client,
            "target",
            typing_indicator=False,
            streaming_edit_throttle=0.1,
        )
        await event._send_streaming_edit(chunks())

        self.assertEqual(client.sent[0], "*")
        self.assertEqual(
            client.edited[-1],
            "*🥥 熱帶風暴「諾盧」(Noul) 最新動態*\n\n",
        )
        self.assertNotIn("**", client.edited[-1])

    async def test_long_stream_does_not_repeat_identical_edits(self) -> None:
        async def chunks():
            for part in ("12345678", "90", "abcdefgh", "ij", "KLMN"):
                yield MessageChain([Plain(part)])
                await asyncio.sleep(0.02)

        client = self.FakeClient()
        event = WhatsAppMessageEvent(
            "",
            AstrBotMessage(),
            PlatformMetadata(),
            "session",
            client,
            "target",
            text_chunk_limit=10,
            typing_indicator=False,
            streaming_edit_throttle=0.01,
        )
        await event._send_streaming_edit(chunks())

        operations = client.sent + client.edited
        for previous, current in zip(operations, operations[1:]):
            self.assertNotEqual(previous, current)


if __name__ == "__main__":
    unittest.main()
