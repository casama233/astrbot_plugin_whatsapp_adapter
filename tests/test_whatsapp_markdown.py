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

# These local modules are only needed to import the two files under test without
# installing AstrBot or starting the Node.js gateway.
client_module = types.ModuleType(f"{PACKAGE_NAME}.whatsapp_client")
client_module.WhatsAppGatewayClient = type("WhatsAppGatewayClient", (), {})
sys.modules.setdefault(f"{PACKAGE_NAME}.whatsapp_client", client_module)

component_module = types.ModuleType(f"{PACKAGE_NAME}.whatsapp_components")
for component_name in ("WhatsAppButtons", "WhatsAppEdit", "WhatsAppList", "WhatsAppPoll"):
    setattr(component_module, component_name, type(component_name, (), {}))
sys.modules.setdefault(f"{PACKAGE_NAME}.whatsapp_components", component_module)

WhatsAppMessageEvent = importlib.import_module(f"{PACKAGE_NAME}.whatsapp_event").WhatsAppMessageEvent
format_whatsapp_markdown = importlib.import_module(
    f"{PACKAGE_NAME}.whatsapp_helpers"
).format_whatsapp_markdown


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
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(format_whatsapp_markdown(source), expected)

    def test_fragmented_bold_marker_is_collapsed_during_streaming(self) -> None:
        raw = ""
        rendered = []
        for part in ("**", "🥥 熱帶風暴「諾盧」(Noul) 最新動態", "**"):
            raw += part
            rendered.append(format_whatsapp_markdown(raw, streaming=True))
        self.assertEqual(
            rendered,
            [
                "*",
                "*🥥 熱帶風暴「諾盧」(Noul) 最新動態",
                "*🥥 熱帶風暴「諾盧」(Noul) 最新動態*",
            ],
        )


class StreamingMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_reformats_the_complete_raw_buffer(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.sent: list[str] = []
                self.edited: list[str] = []

            async def send_text(self, _target, text, **_kwargs):
                self.sent.append(text)
                return {"id": "message-1"}

            async def edit_text(self, _target, _message_id, text, **_kwargs):
                self.edited.append(text)
                return {"id": "message-1"}

        async def chunks():
            yield MessageChain([Plain("**")])
            yield MessageChain([Plain("🥥 熱帶風暴「諾盧」(Noul) 最新動態")])
            yield MessageChain([Plain("**\n\n")])

        client = FakeClient()
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


if __name__ == "__main__":
    unittest.main()
