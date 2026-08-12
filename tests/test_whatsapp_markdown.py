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


def _install_stubs() -> tuple[type, type, type, type, type, type, type]:
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

    class MessageMember:
        def __init__(self, user_id: str = "", nickname: str = "") -> None:
            self.user_id = user_id
            self.nickname = nickname

    class Group:
        def __init__(
            self,
            group_id: str,
            group_name: str = "",
            group_avatar: str = "",
            group_owner: str | None = None,
            group_admins=None,
            members=None,
        ) -> None:
            self.group_id = group_id
            self.group_name = group_name
            self.group_avatar = group_avatar
            self.group_owner = group_owner
            self.group_admins = list(group_admins or [])
            self.members = list(members or [])

    platform.At = At
    platform.AstrBotMessage = AstrBotMessage
    platform.Group = Group
    platform.MessageMember = MessageMember
    platform.PlatformMetadata = PlatformMetadata
    sys.modules["astrbot.api.platform"] = platform

    components = types.ModuleType("astrbot.api.message_components")

    class Plain:
        def __init__(self, text: str = "") -> None:
            self.text = text

    class Location:
        def __init__(
            self,
            lat: float = 0,
            lon: float = 0,
            title: str = "",
            content: str = "",
        ) -> None:
            self.lat = lat
            self.lon = lon
            self.title = title
            self.content = content

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

    class Image:
        def __init__(self, file: str = "") -> None:
            self.file = file

    class DummyComponent:
        pass

    components.Plain = Plain
    components.Location = Location
    components.Reply = Reply
    components.File = type("File", (), {})
    components.Image = Image
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
            self.base_send_calls = 0

        async def send(self, _message) -> None:
            self.base_send_calls += 1
            self._has_send_oper = True

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
    return Plain, Reply, MessageChain, AstrBotMessage, PlatformMetadata, At, Image


Plain, Reply, MessageChain, AstrBotMessage, PlatformMetadata, At, Image = _install_stubs()
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
WhatsAppEdit = component_module.WhatsAppEdit

helpers = importlib.import_module(f"{PACKAGE}.whatsapp_helpers")
event_module = importlib.import_module(f"{PACKAGE}.whatsapp_event")
WhatsAppMessageEvent = event_module.WhatsAppMessageEvent


class GroupContractTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def group_info(self, group_jid: str):
            self.calls.append(group_jid)
            return {
                "groupId": 120363399820499653,
                "groupJid": group_jid,
                "subject": "Gateway Group",
                "owner": 15550001,
                "admins": [
                    15550001,
                    "15550002",
                    15550002,
                    "15550003@s.whatsapp.net",
                ],
                "participants": [
                    {"userId": 15550001, "name": "Owner"},
                    {"userId": "15550002", "name": "Admin"},
                    {
                        "jid": "15550003:7@s.whatsapp.net",
                        "name": "Member",
                    },
                ],
            }

    async def test_without_group_id_returns_only_current_snapshot(self) -> None:
        client = self.Client()
        current = types.SimpleNamespace(group_id="120363399820499653")
        event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(group=current),
            target_jid="120363399820499653@g.us",
            client=client,
        )
        self.assertIs(await event_module._get_group_compat(event), current)
        self.assertEqual(client.calls, [])

        private_event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(group=None),
            target_jid="15550009@s.whatsapp.net",
            client=client,
        )
        self.assertIsNone(await event_module._get_group_compat(private_event))
        self.assertEqual(client.calls, [])

    async def test_explicit_numeric_group_query_works_from_private_event(self) -> None:
        client = self.Client()
        event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(group=None),
            target_jid="15550009@s.whatsapp.net",
            client=client,
        )
        group = await event_module._get_group_compat(
            event,
            "120363399820499653",
        )

        self.assertEqual(client.calls, ["120363399820499653@g.us"])
        self.assertEqual(group.group_id, "120363399820499653")
        self.assertEqual(group.group_name, "Gateway Group")
        self.assertEqual(group.group_owner, "15550001")
        self.assertEqual(group.group_admins, ["15550002", "15550003"])
        self.assertTrue(all(isinstance(value, str) for value in group.group_admins))
        self.assertEqual(
            [(member.user_id, member.nickname) for member in group.members],
            [
                ("15550001", "Owner"),
                ("15550002", "Admin"),
                ("15550003", "Member"),
            ],
        )
        self.assertTrue(
            all(isinstance(member.user_id, str) for member in group.members),
        )

    async def test_explicit_group_jid_is_queried_and_invalid_id_is_rejected(self) -> None:
        client = self.Client()
        event = types.SimpleNamespace(
            message_obj=types.SimpleNamespace(group=None),
            target_jid="15550009@s.whatsapp.net",
            client=client,
        )
        group = await event_module._get_group_compat(
            event,
            "120363399820499653@g.us",
        )
        self.assertEqual(group.group_id, "120363399820499653")
        self.assertEqual(client.calls, ["120363399820499653@g.us"])

        self.assertIsNone(
            await event_module._get_group_compat(event, "not-a-group"),
        )
        self.assertEqual(client.calls, ["120363399820499653@g.us"])


class ConverterTests(unittest.TestCase):
    def test_mention_jids_normalize_device_suffixes_and_hosted_domains(self) -> None:
        self.assertEqual(
            helpers.mention_jid_from_at(At(qq="85264362105:23@s.whatsapp.net")),
            "85264362105@s.whatsapp.net",
        )
        self.assertEqual(
            helpers.mention_jid_from_at(At(qq="123:7@hosted.lid")),
            "123@hosted.lid",
        )
        self.assertEqual(
            helpers.mention_jid_from_at(At(qq="85264362105:9@hosted")),
            "85264362105@hosted",
        )

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
        self.assertEqual(renders, ["", "storm", "*storm*"])
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

    def test_weather_log_keeps_content_and_removes_only_orphan_tail_markers(self) -> None:
        source = (
            "大家定15号去深圳呀～小白帮你们查了深圳15号的天气☔\n\n"
            "*8月15日深圳：⛈️ 雷雨，25.7~27.5°C，降水概率94%！*\n\n"
            "不过雨天的深圳也别有一番风味，小白帮你们把天气预报都存好～~*"
        )
        expected = (
            "大家定15号去深圳呀～小白帮你们查了深圳15号的天气☔\n\n"
            "_8月15日深圳：⛈️ 雷雨，25.7~27.5°C，降水概率94%！_\n\n"
            "不过雨天的深圳也别有一番风味，小白帮你们把天气预报都存好～"
        )

        self.assertEqual(helpers.format_whatsapp_markdown(source), expected)
        self.assertEqual(
            helpers.format_whatsapp_markdown(source, streaming=True),
            expected,
        )

    def test_unmatched_backtick_in_kaomoji_does_not_swallow_later_markdown(self) -> None:
        source = "被點名啦～(´▽`ʃ♡ƪ) 小白來解釋：\n\n**粥底火鍋**係粥湯底。"
        expected = "被點名啦～(´▽`ʃ♡ƪ) 小白來解釋：\n\n*粥底火鍋*係粥湯底。"
        self.assertEqual(helpers.format_whatsapp_markdown(source), expected)
        self.assertNotIn("```", helpers.format_whatsapp_markdown(source))

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
            self.media: list[dict] = []
            self.edited: list[tuple[str, str]] = []

        async def send_text(self, _target, text, **kwargs):
            self.sent.append((text, kwargs.get("mentions") or []))
            return {"id": str(len(self.sent))}

        async def send_media(
            self,
            _target,
            media_type,
            path_or_url,
            caption=None,
            mentions=None,
            **kwargs,
        ):
            self.media.append(
                {
                    "type": media_type,
                    "path": path_or_url,
                    "caption": caption,
                    "mentions": mentions or [],
                    **kwargs,
                },
            )
            return {"id": f"media-{len(self.media)}"}

        async def edit_text(self, _target, message_id, text, **_kwargs):
            self.edited.append((message_id, text))
            return {"id": message_id}

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

    async def test_caption_keeps_native_mentions_on_media(self) -> None:
        client = self.Client()
        pending, mentions = await helpers.process_message_chain(
            client,
            "target",
            [
                At(qq="1@s.whatsapp.net", name="Alice"),
                Plain("hello"),
                Image(file="/tmp/test.jpg"),
            ],
            use_caption=True,
        )

        self.assertIsNone(pending)
        self.assertEqual(mentions, [])
        self.assertEqual(len(client.media), 1)
        self.assertIn("@Alice", client.media[0]["caption"])
        self.assertEqual(client.media[0]["mentions"], ["1@s.whatsapp.net"])

    async def test_media_resolution_failure_becomes_visible_text(self) -> None:
        async def fail_resolution(_value):
            raise ValueError("missing media")

        client = self.Client()
        pending, mentions = await helpers.process_message_chain(
            client,
            "target",
            [Image(file="/missing.jpg")],
            resolve_media_func=fail_resolution,
        )
        await helpers.flush_pending_text(client, "target", pending, mentions)

        self.assertEqual(client.media, [])
        self.assertEqual(client.sent, [("[Image unavailable]", [])])

    async def test_edit_component_counts_as_successful_transport(self) -> None:
        client = self.Client()
        component = WhatsAppEdit()
        component.message_id = "message-to-edit"
        component.text = "**final**"
        component.participant = None
        quote_state = helpers.QuoteState()

        pending, mentions = await helpers.process_message_chain(
            client,
            "target",
            [component],
            quote_state=quote_state,
        )

        self.assertIsNone(pending)
        self.assertEqual(mentions, [])
        self.assertEqual(client.edited, [("message-to-edit", "*final*")])
        self.assertEqual(quote_state.sent_count, 1)


class EventQuoteTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.media: list[dict] = []
            self.operations: list[dict] = []

        async def send_text(self, _target, text, **kwargs):
            payload = {"kind": "text", "text": text, **kwargs}
            self.sent.append(payload)
            self.operations.append(payload)
            return {"id": str(len(self.sent))}

        async def send_media(
            self,
            _target,
            media_type,
            path_or_url,
            caption=None,
            **kwargs,
        ):
            payload = {
                "kind": "media",
                "type": media_type,
                "path": path_or_url,
                "caption": caption,
                **kwargs,
            }
            self.media.append(payload)
            self.operations.append(payload)
            return {"id": f"media-{len(self.media)}"}

    def event(self, **kwargs) -> WhatsAppMessageEvent:
        message = AstrBotMessage()
        message.raw_message = {
            "senderJid": "15550001@s.whatsapp.net",
            "quoted": {"participant": "bot@s.whatsapp.net"},
        }
        options = {
            "source_message_id": "current-question",
            "typing_indicator": False,
        }
        options.update(kwargs)
        return WhatsAppMessageEvent(
            "",
            message,
            PlatformMetadata(),
            "session",
            self.client,
            "chat@g.us",
            **options,
        )

    async def asyncSetUp(self) -> None:
        self.client = self.Client()

    async def test_plain_outgoing_chain_does_not_quote_implicitly(self) -> None:
        await self.event().send(MessageChain([Plain("answer")]))
        self.assertIsNone(self.client.sent[0].get("quoted_message_id"))

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
        self.assertIsNone(self.client.sent[1].get("quoted_message_id"))

    async def test_long_reply_quotes_only_first_physical_chunk(self) -> None:
        await self.event(text_chunk_limit=12).send(
            MessageChain(
                [
                    Reply(id="current-question"),
                    Plain("first second third fourth fifth"),
                ],
            ),
        )

        self.assertGreater(len(self.client.sent), 1)
        self.assertEqual(
            self.client.sent[0].get("quoted_message_id"),
            "current-question",
        )
        self.assertTrue(
            all(item.get("quoted_message_id") is None for item in self.client.sent[1:]),
        )

    async def test_reply_plain_image_quotes_only_first_actual_send(self) -> None:
        await self.event().send(
            MessageChain(
                [
                    Reply(id="current-question"),
                    Plain("caption sent separately"),
                    Image(file="/tmp/test.jpg"),
                ],
            ),
        )

        self.assertEqual([item["kind"] for item in self.client.operations], ["text", "media"])
        self.assertEqual(
            self.client.operations[0].get("quoted_message_id"),
            "current-question",
        )
        self.assertIsNone(self.client.operations[1].get("quoted_message_id"))


class EventSendLifecycleTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self) -> None:
            self.reactions: list[tuple] = []

        async def send_text(self, *_args, **_kwargs):
            raise RuntimeError("transport failed")

        async def react(self, *args):
            self.reactions.append(args)

    async def test_transport_failure_is_not_marked_successful_or_reacted_done(self) -> None:
        message = AstrBotMessage()
        message.raw_message = {"senderJid": "15550001@s.whatsapp.net"}
        client = self.Client()
        event = WhatsAppMessageEvent(
            "",
            message,
            PlatformMetadata(),
            "session",
            client,
            "chat@g.us",
            source_message_id="current-question",
            typing_indicator=False,
        )
        event._pre_acked = True

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            await event.send(MessageChain([Plain("answer")]))

        self.assertEqual(event.base_send_calls, 0)
        self.assertFalse(event._has_send_oper)
        self.assertEqual(
            client.reactions,
            [("chat@g.us", "current-question", "", "15550001@s.whatsapp.net")],
        )
        self.assertFalse(event._pre_acked)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(
            self,
            *,
            fail_edit: bool = False,
            fail_media: bool = False,
            return_id: bool = True,
        ) -> None:
            self.operations: list[tuple[str, str, list[str]]] = []
            self.quote_ids: list[str | None] = []
            self.media_quote_ids: list[str | None] = []
            self.reactions: list[tuple] = []
            self.fail_edit = fail_edit
            self.fail_media = fail_media
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

        async def send_media(
            self,
            _target,
            _media_type,
            path_or_url,
            _caption=None,
            mentions=None,
            **kwargs,
        ):
            if self.fail_media:
                raise RuntimeError("media transport failed")
            self.operations.append(("media", path_or_url, mentions or []))
            self.media_quote_ids.append(kwargs.get("quoted_message_id"))
            return {"id": f"media-{len(self.operations)}"}

        async def react(self, *args):
            self.reactions.append(args)

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

    async def test_public_stream_restores_after_message_sent_hook_once(self) -> None:
        async def chunks():
            yield MessageChain([Plain("answer")])

        calls = []

        async def call_hook(event, hook_type):
            calls.append((event, hook_type))
            return False

        event_type = types.SimpleNamespace(OnAfterMessageSentEvent=object())
        client = self.Client()
        event = self.event(client)
        with (
            patch.object(event_module, "_call_event_hook", call_hook),
            patch.object(event_module, "_EventType", event_type),
        ):
            await event.send_streaming(chunks())
            await event._notify_streaming_after_message_sent()

        self.assertEqual(calls, [(event, event_type.OnAfterMessageSentEvent)])

    async def test_public_stream_does_not_duplicate_future_core_hook(self) -> None:
        async def chunks():
            yield MessageChain([Plain("answer")])

        calls = []

        async def call_hook(*args):
            calls.append(args)
            return False

        event_type = types.SimpleNamespace(OnAfterMessageSentEvent=object())
        with (
            patch.object(event_module, "_call_event_hook", call_hook),
            patch.object(event_module, "_EventType", event_type),
            patch.object(event_module, "_STREAMING_AFTER_HOOK_COMPAT", False),
        ):
            await self.event(self.Client()).send_streaming(chunks())

        self.assertEqual(calls, [])

    def test_streaming_hook_compat_follows_core_control_flow(self) -> None:
        affected_core = """
            async def process(event):
                await event.send_streaming(result.async_stream, False)
                return
                if await call_event_hook(event, EventType.OnAfterMessageSentEvent):
                    return
        """
        fixed_core = """
            async def process(event):
                await event.send_streaming(result.async_stream, False)
                if await call_event_hook(event, EventType.OnAfterMessageSentEvent):
                    return
                return
        """
        self.assertTrue(
            event_module._needs_streaming_after_hook_compat(affected_core),
        )
        self.assertFalse(
            event_module._needs_streaming_after_hook_compat(fixed_core),
        )

    async def test_empty_public_stream_does_not_emit_after_message_sent_hook(self) -> None:
        async def chunks():
            if False:
                yield MessageChain([Plain("unreachable")])

        calls = []

        async def call_hook(*args):
            calls.append(args)
            return False

        event_type = types.SimpleNamespace(OnAfterMessageSentEvent=object())
        with (
            patch.object(event_module, "_call_event_hook", call_hook),
            patch.object(event_module, "_EventType", event_type),
        ):
            await self.event(self.Client()).send_streaming(chunks())

        self.assertEqual(calls, [])

    async def test_marker_only_chunk_is_not_sent(self) -> None:
        async def chunks():
            yield MessageChain([Plain("**")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain("🥥 最新動態")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain("**")])

        client = self.Client()
        await self.event(client)._send_streaming_edit(chunks())
        self.assertEqual(client.operations[-1][1], "*🥥 最新動態*")
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

    async def test_realtime_edit_failure_keeps_the_failed_increment(self) -> None:
        async def chunks():
            yield MessageChain([Plain("hello")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" world")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" again.")])

        client = self.Client(fail_edit=True)
        event = self.event(client)
        await event.send_streaming(chunks(), use_fallback=True)
        sends = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertEqual(sends, ["hello", " world again."])
        self.assertEqual("".join(sends), "hello world again.")
        self.assertTrue(event._has_send_oper)

    async def test_false_use_fallback_waits_for_one_complete_final_recovery(self) -> None:
        async def chunks():
            yield MessageChain([Plain("hello")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" world")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" again.")])

        client = self.Client(fail_edit=True)
        event = self.event(
            client,
            # This legacy constructor value must not override AstrBot's
            # per-stream ``use_fallback`` decision.
            unsupported_streaming_strategy="realtime_segmenting",
        )
        await event.send_streaming(chunks(), use_fallback=False)

        sends = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertEqual(sends, ["hello", "hello world again."])
        self.assertTrue(event._has_send_oper)

    async def test_generator_failure_after_delivery_marks_sent_and_clears_pre_ack(self) -> None:
        async def chunks():
            yield MessageChain([Plain("partial answer")])
            raise RuntimeError("generator failed")

        calls = []

        async def call_hook(event, hook_type):
            calls.append((event, hook_type))
            return False

        event_type = types.SimpleNamespace(OnAfterMessageSentEvent=object())
        client = self.Client()
        event = self.event(client, source_message_id="question")
        event._pre_acked = True

        with (
            patch.object(event_module, "_call_event_hook", call_hook),
            patch.object(event_module, "_EventType", event_type),
            self.assertRaisesRegex(RuntimeError, "generator failed"),
        ):
            await event.send_streaming(chunks())

        self.assertTrue(event._has_send_oper)
        self.assertTrue(event._super_sent)
        self.assertEqual(client.reactions[-1][2], "")
        self.assertFalse(event._pre_acked)
        self.assertEqual(calls, [(event, event_type.OnAfterMessageSentEvent)])

    async def test_missing_message_id_does_not_duplicate_delivered_text(self) -> None:
        async def chunks():
            yield MessageChain([Plain("final text")])

        client = self.Client(return_id=False)
        await self.event(client)._send_streaming_edit(chunks())
        sends = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertEqual(sends, ["final text"])

    async def test_missing_message_id_fallback_sends_only_unseen_suffix(self) -> None:
        async def chunks():
            yield MessageChain([Plain("hello")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" world")])

        client = self.Client(return_id=False)
        await self.event(client)._send_streaming_edit(chunks())
        sends = [text for operation, text, _ in client.operations if operation == "send"]
        self.assertEqual(sends, ["hello", " world"])
        self.assertEqual("".join(sends), "hello world")

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

    async def test_reply_is_stream_metadata_and_does_not_reset_text_state(self) -> None:
        async def chunks():
            yield MessageChain([Reply(id="current-question"), Plain("hello")])
            await asyncio.sleep(0.11)
            yield MessageChain([Plain(" world")])

        client = self.Client()
        event = self.event(client, source_message_id="current-question")
        await event._send_streaming_edit(chunks())

        self.assertEqual(event.base_send_calls, 0)
        self.assertEqual(client.operations[-1][1], "hello world")
        self.assertEqual(client.quote_ids[0], "current-question")

    async def test_mixed_stream_preserves_chain_order_and_quotes_only_first_send(self) -> None:
        async def chunks():
            yield MessageChain(
                [
                    Reply(id="current-question"),
                    Plain("before"),
                    Image(file="/tmp/test.jpg"),
                    Plain("after"),
                ],
            )

        client = self.Client()
        event = self.event(client, source_message_id="current-question")
        delivered = await event._send_streaming_edit(chunks())

        self.assertTrue(delivered)
        self.assertEqual(
            [(kind, value) for kind, value, _mentions in client.operations],
            [("send", "before"), ("media", "/tmp/test.jpg"), ("send", "after")],
        )
        self.assertEqual(client.quote_ids, ["current-question", None])
        self.assertEqual(client.media_quote_ids, [None])

    async def test_empty_stream_clears_pre_ack_without_done_reaction(self) -> None:
        async def chunks():
            if False:
                yield MessageChain([])

        client = self.Client()
        event = self.event(client, source_message_id="question")
        event._pre_acked = True
        await event.send_streaming(chunks())

        self.assertEqual(client.reactions, [("target", "question", "", None)])
        self.assertFalse(event._has_send_oper)
        self.assertFalse(event._pre_acked)

    async def test_media_failure_does_not_emit_done_reaction(self) -> None:
        async def chunks():
            yield MessageChain([Image(file="/tmp/test.jpg")])

        client = self.Client(fail_media=True)
        event = self.event(client, source_message_id="question")
        event._pre_acked = True

        with self.assertRaisesRegex(RuntimeError, "media transport failed"):
            await event.send_streaming(chunks())

        self.assertEqual(client.reactions, [("target", "question", "", None)])
        self.assertFalse(event._has_send_oper)
        self.assertFalse(event._pre_acked)


if __name__ == "__main__":
    unittest.main()
