from __future__ import annotations

import asyncio
from enum import Enum
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Component:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Plain(_Component):
    def __init__(self, text: str = "", **kwargs) -> None:
        super().__init__(text=text, **kwargs)


class _At(_Component):
    def __init__(self, qq: str = "", name: str = "", **kwargs) -> None:
        super().__init__(qq=qq, name=name, **kwargs)


class _AtAll(_At):
    def __init__(self, qq: str = "all", name: str = "", **kwargs) -> None:
        super().__init__(qq=qq, name=name, **kwargs)


class _Reply(_Component):
    def __init__(self, id: str = "", chain=None, **kwargs) -> None:
        super().__init__(id=id, chain=chain, **kwargs)


class _Image(_Component):
    def __init__(self, file: str = "", **kwargs) -> None:
        super().__init__(file=file, **kwargs)


class _File(_Component):
    def __init__(self, name: str = "", file: str = "", **kwargs) -> None:
        super().__init__(name=name, file_=file, **kwargs)


class _Location(_Component):
    def __init__(
        self,
        lat: float = 0,
        lon: float = 0,
        title: str = "",
        content: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            lat=lat,
            lon=lon,
            title=title,
            content=content,
            **kwargs,
        )


class _Group:
    def __init__(self, group_id: str) -> None:
        self.group_id = group_id
        self.group_name = None


class _AstrBotMessage:
    def __init__(self) -> None:
        self.group = None

    @property
    def group_id(self) -> str:
        return self.group.group_id if self.group else ""

    @group_id.setter
    def group_id(self, value) -> None:
        self.group = _Group(str(value)) if value else None


class _MessageMember:
    def __init__(self, user_id: str, nickname: str | None = None) -> None:
        self.user_id = user_id
        self.nickname = nickname


class _MessageType(Enum):
    GROUP_MESSAGE = "GroupMessage"
    FRIEND_MESSAGE = "FriendMessage"


class _Platform:
    async def send_by_session(self, _session, _message_chain) -> None:
        self.base_session_sends = getattr(self, "base_session_sends", 0) + 1


class _PlatformMetadata:
    def __init__(self, *args, **kwargs) -> None:
        self.name = kwargs.get("name") or (args[0] if args else "")


def _register_platform_adapter(*_args, **_kwargs):
    return lambda cls: cls


def _adapter_module():
    package_name = "_whatsapp_adapter_compat_target"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]

    astrbot = types.ModuleType("astrbot")
    astrbot.logger = _Logger()
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")

    class _MessageChain:
        def __init__(self, chain=None) -> None:
            self.chain = list(chain or [])

    event.MessageChain = _MessageChain

    components = types.ModuleType("astrbot.api.message_components")
    components.AtAll = _AtAll
    components.File = _File
    components.Image = _Image
    components.Location = _Location
    components.Plain = _Plain
    components.Record = type("Record", (_Component,), {})
    components.Reply = _Reply
    components.Video = type("Video", (_Component,), {})

    platform = types.ModuleType("astrbot.api.platform")
    platform.AstrBotMessage = _AstrBotMessage
    platform.At = _At
    platform.MessageMember = _MessageMember
    platform.MessageType = _MessageType
    platform.Platform = _Platform
    platform.PlatformMetadata = _PlatformMetadata
    platform.register_platform_adapter = _register_platform_adapter

    core = types.ModuleType("astrbot.core")
    core_utils = types.ModuleType("astrbot.core.utils")
    path_utils = types.ModuleType("astrbot.core.utils.astrbot_path")
    path_utils.get_astrbot_data_path = lambda: str(ROOT)
    core_platform = types.ModuleType("astrbot.core.platform")
    event_core = types.ModuleType("astrbot.core.platform.astr_message_event")
    event_core.MessageSesion = type("MessageSesion", (), {})
    platform_core = types.ModuleType("astrbot.core.platform.platform")
    platform_core.PlatformStatus = types.SimpleNamespace(STOPPED="stopped")

    client = types.ModuleType(f"{package_name}.whatsapp_client")
    client.GatewayProcess = type("GatewayProcess", (), {})
    client.WhatsAppGatewayClient = type("WhatsAppGatewayClient", (), {})
    client.WhatsAppGatewayError = type("WhatsAppGatewayError", (RuntimeError,), {})

    commands = types.ModuleType(f"{package_name}.whatsapp_commands")
    commands.collect_registered_commands = lambda: []
    commands.message_matches_command = lambda *_args, **_kwargs: False

    event_module = types.ModuleType(f"{package_name}.whatsapp_event")
    event_module.WhatsAppMessageEvent = type("WhatsAppMessageEvent", (), {})

    helpers = types.ModuleType(f"{package_name}.whatsapp_helpers")
    helpers.format_markdown_from_whatsapp = lambda value: value

    class _QuoteState:
        def __init__(self, message_id=None, participant=None) -> None:
            self.message_id = message_id
            self.participant = participant
            self.sent_count = 0

    helpers.QuoteState = _QuoteState

    async def _noop(*_args, **_kwargs):
        return None, []

    helpers.flush_pending_text = _noop
    helpers.process_message_chain = _noop

    modules = {
        package_name: package,
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.message_components": components,
        "astrbot.api.platform": platform,
        "astrbot.core": core,
        "astrbot.core.utils": core_utils,
        "astrbot.core.utils.astrbot_path": path_utils,
        "astrbot.core.platform": core_platform,
        "astrbot.core.platform.astr_message_event": event_core,
        "astrbot.core.platform.platform": platform_core,
        f"{package_name}.whatsapp_client": client,
        f"{package_name}.whatsapp_commands": commands,
        f"{package_name}.whatsapp_event": event_module,
        f"{package_name}.whatsapp_helpers": helpers,
    }
    module_path = ROOT / "_whatsapp_adapter_impl.py"
    spec = importlib.util.spec_from_file_location(
        f"{package_name}._whatsapp_adapter_impl",
        module_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules, clear=False):
        spec.loader.exec_module(module)
    return module


class WhatsAppAdapterCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _adapter_module()
        cls._save_mapping_patcher = patch.object(cls.module, "_save_lid_mapping")
        cls._save_projection_patcher = patch.object(
            cls.module,
            "_save_identity_projections",
        )
        cls._save_mapping_patcher.start()
        cls._save_projection_patcher.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._save_projection_patcher.stop()
        cls._save_mapping_patcher.stop()

    def test_numeric_ids_ordered_mentions_reply_and_raw_projection(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        adapter._platform_settings = {"unique_session": False}
        data = {
            "chatJid": "120363000000000001@g.us",
            "senderJid": "111@s.whatsapp.net",
            "senderPn": "111@s.whatsapp.net",
            "senderName": "Alice",
            "selfJid": "999@s.whatsapp.net",
            "messageId": "msg-1",
            "timestamp": 1775721600,
            "text": "hello @999 middle @222 end",
            "mentionedJids": [
                "999@s.whatsapp.net",
                "222@s.whatsapp.net",
            ],
            "mentionedNames": {
                "999@s.whatsapp.net": "Bot",
                "222@s.whatsapp.net": "Bob",
            },
            "senderRole": "admin",
            "quoted": {
                "stanzaId": "old-1",
                "participant": "999@s.whatsapp.net",
                "participantName": "Bot",
                "timestamp": 1775721500,
                "text": "old question",
            },
        }

        message = asyncio.run(adapter.convert_message(data))
        self.assertIsNotNone(message)
        self.assertEqual(message.self_id, "999")
        self.assertEqual(message.sender.user_id, "111")
        self.assertEqual(message.session_id, "120363000000000001")

        chain = message.message
        self.assertIsInstance(chain[0], _Reply)
        self.assertEqual(chain[0].sender_id, "")
        self.assertEqual(chain[0].qq, "999")
        self.assertEqual(
            [type(item) for item in chain[1:]],
            [_Plain, _At, _Plain, _At, _Plain],
        )
        self.assertEqual(
            [item.qq for item in chain if isinstance(item, _At)],
            ["999", "222"],
        )
        self.assertNotIn("@Bot", message.message_str)
        self.assertIn("@Bob(222)", message.message_str)

        raw = message.raw_message
        self.assertEqual(raw["self_id"], "999")
        self.assertEqual(raw["sub_type"], "normal")
        self.assertEqual(raw["font"], 0)
        self.assertEqual(raw["raw_message"], "hello @999 middle @222 end")
        self.assertEqual(raw["sender"]["role"], "admin")
        self.assertEqual(raw.sender.user_id, "111")
        self.assertEqual(raw.sender.role, "admin")
        self.assertIsNone(raw.not_present)
        self.assertEqual(raw.message[0].type, "reply")
        self.assertEqual(raw.message[0].data.id, "old-1")
        for public_id in (raw.self_id, raw.user_id, raw.group_id, raw.message_id):
            self.assertIsInstance(public_id, str)
        self.assertEqual(
            [segment["type"] for segment in raw["message"]],
            ["reply", "text", "at", "text", "at", "text"],
        )

    def test_private_raw_projection_uses_friend_subtype_and_attribute_access(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "111@s.whatsapp.net",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": 12345,
                    "text": "hello",
                },
            ),
        )

        raw = message.raw_message
        self.assertIsInstance(raw, dict)
        self.assertEqual(raw["sub_type"], "friend")
        self.assertEqual(raw.sub_type, "friend")
        self.assertEqual(raw.sender.nickname, "Alice")
        self.assertEqual(raw.message[0].data.text, "hello")
        self.assertEqual(raw.message_id, "12345")
        self.assertIsInstance(raw.user_id, str)
        self.assertIsInstance(raw.self_id, str)
        self.assertEqual(message.session_id, "111")

    def test_failed_inbound_media_remains_visible_even_with_a_caption(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "111@s.whatsapp.net",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "failed-media",
                    "text": "caption",
                    "media": [
                        {"type": "sticker", "error": "download failed"},
                    ],
                },
            ),
        )

        visible = [item.text for item in message.message if isinstance(item, _Plain)]
        self.assertEqual(visible, ["caption", "<media:sticker unavailable>"])
        self.assertIn("<media:sticker unavailable>", message.message_str)

    def test_failed_inbound_media_without_caption_has_one_placeholder(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "111@s.whatsapp.net",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "failed-media-no-caption",
                    "text": "<media:sticker>",
                    "media": [
                        {"type": "sticker", "error": "download failed"},
                    ],
                },
            ),
        )

        visible = [item.text for item in message.message if isinstance(item, _Plain)]
        self.assertEqual(visible, ["<media:sticker unavailable>"])
        self.assertEqual(message.message_str, "<media:sticker unavailable>")

    def test_global_unique_session_matches_qq_sender_and_group_ids(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        adapter._platform_settings = {"unique_session": True}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "120363000000000001@g.us",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "unique-session",
                    "text": "hello",
                },
            ),
        )

        self.assertEqual(message.sender.user_id, "111")
        self.assertEqual(message.group_id, "120363000000000001")
        self.assertEqual(message.session_id, "111_120363000000000001")

    def test_legacy_hyphenated_group_id_is_stable_across_public_fields(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        adapter._platform_settings = {"unique_session": True}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "123456789-123345@g.us",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "legacy-group",
                    "text": "hello",
                },
            ),
        )

        self.assertEqual(message.group_id, "123456789-123345")
        self.assertEqual(message.session_id, "111_123456789-123345")
        self.assertEqual(message.raw_message["group_id"], "123456789-123345")
        self.assertEqual(
            adapter._delivery_target_from_session_id(
                message.session_id,
                is_group=True,
            ),
            "123456789-123345@g.us",
        )

    def test_native_event_is_projected_as_detailed_plain_text(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "111@s.whatsapp.net",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "native-event",
                    "text": "Shenzhen trip: Meet at the station",
                    "extras": {
                        "event": {
                            "name": "Shenzhen trip",
                            "description": "Meet at the station",
                            "startTime": 1_786_755_600,
                            "endTime": 1_786_766_400,
                            "location": {
                                "name": "Shenzhen",
                                "address": "Guangdong",
                            },
                            "extraGuestsAllowed": True,
                            "isCanceled": False,
                        },
                    },
                },
            ),
        )

        visible = [item.text for item in message.message if isinstance(item, _Plain)]
        self.assertEqual(visible, [message.message_str])
        self.assertIn("[Event] Shenzhen trip", message.message_str)
        self.assertIn("2026-08-15T01:00:00Z", message.message_str)
        self.assertIn("Shenzhen — Guangdong", message.message_str)
        self.assertIn("extra guests allowed", message.message_str)

    def test_platform_group_role_does_not_expand_astrbot_admin_permissions(self) -> None:
        source = (ROOT / "_whatsapp_adapter_impl.py").read_text("utf-8")
        self.assertNotRegex(source, r"(?m)^\s*event\.role\s*=")

    def test_mention_all_matches_qq_chain_and_message_str_semantics(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "120363000000000001@g.us",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "mention-all",
                    "text": "announcement",
                    "mentionedJids": [],
                    "mentionAll": True,
                },
            ),
        )

        self.assertIsInstance(message.message[0], _AtAll)
        self.assertEqual(message.message[0].qq, "all")
        self.assertEqual(message.message[0].name, "全体成员")
        self.assertEqual(message.message_str, "announcement")
        self.assertEqual(message.raw_message["message"][0]["data"]["qq"], "all")

    def test_mention_all_pre_wake_and_ack_respect_global_ignore_setting(self) -> None:
        class FakeEvent:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)
                self.is_at_or_wake_command = False
                self.is_wake = False
                self._pre_acked = False
                self.reactions = []

            async def react(self, emoji: str) -> None:
                self.reactions.append(emoji)

        async def allowed(_raw, _is_private) -> bool:
            return True

        for ignore_at_all, expected_wake, expected_reactions in (
            (True, False, []),
            (False, True, ["👀"]),
        ):
            with self.subTest(ignore_at_all=ignore_at_all):
                adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
                adapter.config = {
                    "parse_inbound_formatting": False,
                    "ignore_self_messages": False,
                    "pre_ack_emoji": True,
                    "pre_ack_public": "mentions",
                    "pre_ack_emojis": "👀",
                    "text_chunk_limit": 4000,
                    "media_caption_mode": "separate",
                    "link_preview_single_url": True,
                    "typing_indicator": False,
                    "pre_ack_done_emoji": "✅",
                    "streaming_edit_throttle": 1.0,
                }
                adapter._platform_settings = {"ignore_at_all": ignore_at_all}
                adapter._legacy_command_prefix = ""
                adapter._registered_commands = []
                adapter.client = object()
                adapter._is_sender_allowed = allowed
                adapter._message_matches_known_command = lambda _text: False
                committed = []
                adapter.commit_event = committed.append
                message = asyncio.run(
                    adapter.convert_message(
                        {
                            "chatJid": "120363000000000001@g.us",
                            "senderJid": "111@s.whatsapp.net",
                            "senderPn": "111@s.whatsapp.net",
                            "senderName": "Alice",
                            "selfJid": "999@s.whatsapp.net",
                            "messageId": f"mention-all-{ignore_at_all}",
                            "text": "announcement",
                            "mentionedJids": [],
                            "mentionAll": True,
                        },
                    ),
                )

                with patch.object(self.module, "WhatsAppMessageEvent", FakeEvent):
                    asyncio.run(adapter.handle_msg(message))

                self.assertEqual(len(committed), 1)
                event = committed[0]
                self.assertEqual(event.is_wake, expected_wake)
                self.assertEqual(event.is_at_or_wake_command, expected_wake)
                self.assertEqual(event.reactions, expected_reactions)
                self.assertNotIn("unsupported_streaming_strategy", event.__dict__)
                self.assertTrue(callable(event.mention_resolver))
                self.assertTrue(any(isinstance(item, _AtAll) for item in message.message))

    def test_quoting_bot_without_explicit_mention_does_not_wake_or_ack(self) -> None:
        class FakeEvent:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)
                self.is_at_or_wake_command = False
                self.is_wake = False
                self._pre_acked = False
                self.reactions = []

            async def react(self, emoji: str) -> None:
                self.reactions.append(emoji)

        async def allowed(_raw, _is_private) -> bool:
            return True

        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {
            "parse_inbound_formatting": False,
            "ignore_self_messages": False,
            "pre_ack_emoji": True,
            "pre_ack_public": "mentions",
            "pre_ack_emojis": "👀",
            "text_chunk_limit": 4000,
            "media_caption_mode": "separate",
            "link_preview_single_url": True,
            "typing_indicator": False,
            "pre_ack_done_emoji": "✅",
            "streaming_edit_throttle": 1.0,
        }
        adapter._platform_settings = {"ignore_at_all": False}
        adapter._legacy_command_prefix = ""
        adapter._registered_commands = []
        adapter.client = object()
        adapter._is_sender_allowed = allowed
        adapter._message_matches_known_command = lambda _text: False
        committed = []
        adapter.commit_event = committed.append
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "120363000000000001@g.us",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "senderName": "Alice",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "quote-only",
                    "text": "follow-up without at",
                    "mentionedJids": [],
                    "quoted": {
                        "stanzaId": "bot-answer",
                        "participant": "999@s.whatsapp.net",
                        "participantName": "Bot",
                        "text": "previous answer",
                    },
                },
            ),
        )

        quoted = message.message[0]
        self.assertIsInstance(quoted, _Reply)
        self.assertEqual(quoted.sender_id, "")
        self.assertEqual(quoted.qq, "999")
        self.assertFalse(any(isinstance(item, _At) for item in message.message))

        with patch.object(self.module, "WhatsAppMessageEvent", FakeEvent):
            asyncio.run(adapter.handle_msg(message))

        self.assertEqual(len(committed), 1)
        event = committed[0]
        self.assertFalse(event.is_wake)
        self.assertFalse(event.is_at_or_wake_command)
        self.assertEqual(event.reactions, [])

    def test_send_by_group_sessions_recovers_target_and_reports_success(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {
            "link_preview_single_url": True,
            "text_chunk_limit": 4000,
            "media_caption_mode": "separate",
            "typing_indicator": False,
        }
        adapter.client = object()
        adapter._platform_settings = {"segmented_reply": {"enable": False}}
        adapter._identity_cache = self.module.IdentityMappingCache()
        captured = {"targets": [], "states": []}

        async def process(_client, target, _chain, **kwargs):
            captured["targets"].append(target)
            captured["states"].append(kwargs["quote_state"])
            kwargs["quote_state"].sent_count += 1
            return None, []

        async def flush(_client, target, _pending, _mentions, **kwargs):
            self.assertEqual(target, captured["targets"][-1])
            self.assertIs(kwargs["quote_state"], captured["states"][-1])
            return None, []

        chain = self.module.MessageChain(
            [_Reply(id="quoted-message"), _Plain("answer")],
        )
        with (
            patch.object(self.module, "process_message_chain", process),
            patch.object(self.module, "flush_pending_text", flush),
        ):
            for session_id in (
                "120363000000000001",
                "111_120363000000000001",
                "111_120363000000000001@g.us",
            ):
                session = types.SimpleNamespace(
                    session_id=session_id,
                    message_type=_MessageType.GROUP_MESSAGE,
                )
                asyncio.run(adapter.send_by_session(session, chain))

        self.assertEqual(
            captured["targets"],
            [
                "120363000000000001@g.us",
                "120363000000000001@g.us",
                "120363000000000001@g.us",
            ],
        )
        self.assertTrue(
            all(state.message_id == "quoted-message" for state in captured["states"]),
        )
        self.assertEqual(adapter.base_session_sends, 3)

    def test_private_numeric_session_resolves_back_to_cached_lid(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {
            "link_preview_single_url": True,
            "text_chunk_limit": 4000,
            "media_caption_mode": "separate",
            "typing_indicator": False,
        }
        adapter.client = object()
        adapter._identity_cache = self.module.IdentityMappingCache()
        adapter._identity_cache.remember("123@hosted.lid", "111@hosted")
        captured = []
        mention_targets = []

        async def process(_client, target, _chain, **kwargs):
            captured.append(target)
            mention_targets.append(kwargs["mention_resolver"]("111"))
            kwargs["quote_state"].sent_count += 1
            return None, []

        async def flush(*_args, **_kwargs):
            return None, []

        session = types.SimpleNamespace(
            session_id="111",
            message_type=_MessageType.FRIEND_MESSAGE,
        )
        with (
            patch.object(self.module, "process_message_chain", process),
            patch.object(self.module, "flush_pending_text", flush),
        ):
            asyncio.run(
                adapter.send_by_session(
                    session,
                    self.module.MessageChain([_Plain("hello")]),
                ),
            )

        self.assertEqual(captured, ["123@hosted.lid"])
        self.assertEqual(mention_targets, ["111@hosted"])

    def test_private_pn_and_lid_addresses_share_one_numeric_session(self) -> None:
        with tempfile.TemporaryDirectory() as auth_dir:
            adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
            adapter.config = {
                "parse_inbound_formatting": False,
                "auth_dir": auth_dir,
            }

            pn_message = asyncio.run(
                adapter.convert_message(
                    {
                        "chatJid": "111@s.whatsapp.net",
                        "senderJid": "111@s.whatsapp.net",
                        "senderPn": "111@s.whatsapp.net",
                        "senderName": "Alice",
                        "selfJid": "999@s.whatsapp.net",
                        "messageId": "pn-address",
                        "text": "one",
                    },
                ),
            )
            lid_message = asyncio.run(
                adapter.convert_message(
                    {
                        "chatJid": "123@lid",
                        "senderJid": "123@lid",
                        "senderPn": "111@s.whatsapp.net",
                        "canonicalSessionJid": "123@lid",
                        "canonicalSessionPn": "111@s.whatsapp.net",
                        "senderName": "Alice",
                        "selfJid": "999@s.whatsapp.net",
                        "messageId": "lid-address",
                        "text": "two",
                    },
                ),
            )

        self.assertEqual(pn_message.sender.user_id, "111")
        self.assertEqual(lid_message.sender.user_id, "111")
        self.assertEqual(pn_message.session_id, "111")
        self.assertEqual(lid_message.session_id, "111")

    def test_malformed_transport_ids_never_create_empty_umo_sessions(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}

        malformed_group = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "abc123@g.us",
                    "senderJid": "111@s.whatsapp.net",
                    "senderPn": "111@s.whatsapp.net",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "invalid-group",
                    "text": "hello",
                },
            ),
        )
        malformed_user = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "abc123@lid",
                    "senderJid": "abc123@lid",
                    "selfJid": "999@s.whatsapp.net",
                    "messageId": "invalid-user",
                    "text": "hello",
                },
            ),
        )

        self.assertIsNone(malformed_group)
        self.assertIsNone(malformed_user)

    def test_send_by_session_rejects_a_malformed_target_before_transport(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {}
        session = types.SimpleNamespace(
            session_id="abc123",
            message_type=_MessageType.FRIEND_MESSAGE,
        )

        with self.assertRaisesRegex(ValueError, "ID 格式无效"):
            asyncio.run(
                adapter.send_by_session(
                    session,
                    self.module.MessageChain([_Plain("hello")]),
                ),
            )

    def test_phone_originated_direct_message_uses_remote_session_identity(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        message = asyncio.run(
            adapter.convert_message(
                {
                    "chatJid": "123@lid",
                    "senderJid": "999@s.whatsapp.net",
                    "senderPn": "999@s.whatsapp.net",
                    "canonicalSessionJid": "123@lid",
                    "canonicalSessionPn": "111@s.whatsapp.net",
                    "senderName": "Bot owner",
                    "selfJid": "999@s.whatsapp.net",
                    "fromMe": True,
                    "messageId": "phone-originated",
                    "text": "hello",
                },
            ),
        )

        self.assertEqual(message.sender.user_id, "999")
        self.assertEqual(message.session_id, "111")

    def test_unknown_lid_is_resolved_before_first_public_session_is_built(self) -> None:
        class Client:
            async def resolve_lid(self, lid_jid):
                self.lid_jid = lid_jid
                return "111:4@s.whatsapp.net"

        with tempfile.TemporaryDirectory() as auth_dir:
            adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
            adapter.config = {
                "parse_inbound_formatting": False,
                "auth_dir": auth_dir,
            }
            adapter.client = Client()
            message = asyncio.run(
                adapter.convert_message(
                    {
                        "chatJid": "123:8@lid",
                        "senderJid": "123:8@lid",
                        "canonicalSessionJid": "123:8@lid",
                        "senderName": "Alice",
                        "selfJid": "999@s.whatsapp.net",
                        "messageId": "resolve-first",
                        "text": "hello",
                    },
                ),
            )

        self.assertEqual(adapter.client.lid_jid, "123@lid")
        self.assertEqual(message.sender.user_id, "111")
        self.assertEqual(message.session_id, "111")

    def test_send_by_session_never_delays_plugin_messages_for_segmented_reply(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {
            "link_preview_single_url": True,
            "text_chunk_limit": 4000,
            "media_caption_mode": "separate",
            "typing_indicator": False,
        }
        adapter.client = object()
        adapter._platform_settings = {"segmented_reply": {"enable": True}}
        adapter._identity_cache = self.module.IdentityMappingCache()
        delivered = []

        async def process(_client, target, chain, **kwargs):
            delivered.append((target, chain[0].text))
            kwargs["quote_state"].sent_count += 1
            return None, []

        async def flush(*_args, **_kwargs):
            return None, []

        session = types.SimpleNamespace(
            session_id="120363000000000001@g.us",
            message_type=_MessageType.GROUP_MESSAGE,
        )
        chain = self.module.MessageChain([_Plain("请稍等")])
        with (
            patch.object(self.module, "process_message_chain", process),
            patch.object(self.module, "flush_pending_text", flush),
        ):
            asyncio.run(adapter.send_by_session(session, chain))

        self.assertEqual(
            delivered,
            [("120363000000000001@g.us", "请稍等")],
        )
        self.assertEqual(adapter.base_session_sends, 1)
        self.assertFalse(hasattr(adapter, "_send_text_tasks"))

    def test_device_suffixes_never_leak_into_astrbot_identity_components(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {"parse_inbound_formatting": False}
        data = {
            "chatJid": "120363000000000001@g.us",
            "senderJid": "111:8@hosted",
            "senderPn": "111:8@hosted",
            "senderName": "Alice",
            "selfJid": "85264362105:23@s.whatsapp.net",
            "messageId": "msg-device",
            "text": "hello @85264362105 and @222",
            "mentionedJids": [
                "85264362105:23@s.whatsapp.net",
                "222:6@hosted",
            ],
            "mentionedNames": {
                "85264362105:23@s.whatsapp.net": "Bot",
                "222:6@hosted": "Bob",
            },
            "quoted": {
                "stanzaId": "quoted-device",
                "participant": "85264362105:23@s.whatsapp.net",
                "text": "old",
            },
        }

        message = asyncio.run(adapter.convert_message(data))
        self.assertEqual(message.self_id, "85264362105")
        self.assertEqual(message.sender.user_id, "111")
        self.assertEqual(message.message[0].sender_id, "")
        self.assertEqual(message.message[0].qq, "85264362105")
        self.assertEqual(
            [item.qq for item in message.message if isinstance(item, _At)],
            ["85264362105", "222"],
        )

    def test_adapter_mapping_state_is_isolated_and_supports_hosted_lids(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_dir,
            tempfile.TemporaryDirectory() as second_dir,
        ):
            first = object.__new__(self.module.WhatsAppPlatformAdapter)
            first.config = {
                "parse_inbound_formatting": False,
                "auth_dir": first_dir,
            }
            second = object.__new__(self.module.WhatsAppPlatformAdapter)
            second.config = {
                "parse_inbound_formatting": False,
                "auth_dir": second_dir,
            }

            def payload(pn: str | None) -> dict:
                value = {
                    "chatJid": "123:9@hosted.lid",
                    "senderJid": "123:9@hosted.lid",
                    "senderName": "Hosted user",
                    "messageId": "hosted-message",
                    "text": "hello",
                }
                if pn:
                    value["senderPn"] = pn
                return value

            first_message = asyncio.run(first.convert_message(payload("111:4@hosted")))
            second_message = asyncio.run(second.convert_message(payload("222:5@hosted")))
            first_follow_up = asyncio.run(first.convert_message(payload(None)))

            self.assertEqual(first_message.sender.user_id, "111")
            self.assertEqual(second_message.sender.user_id, "222")
            self.assertEqual(first_follow_up.sender.user_id, "111")
            self.assertEqual(first_message.session_id, "111")
            self.assertEqual(second_message.session_id, "222")
            self.assertEqual(first_follow_up.session_id, "111")
            self.assertEqual(
                first._identity_mappings().pn_for_lid("123:10@hosted.lid"),
                "111@hosted",
            )
            self.assertEqual(
                second._identity_mappings().pn_for_lid("123@hosted.lid"),
                "222@hosted",
            )

    def test_allowlist_normalizes_device_suffix_and_uses_private_lid_cache(self) -> None:
        adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
        adapter.config = {
            "dm_policy": "allowlist",
            "allow_from": ["+85264362105"],
        }

        self.assertTrue(
            asyncio.run(
                adapter._is_sender_allowed(
                    {"senderPn": "85264362105:23@s.whatsapp.net"},
                    True,
                ),
            ),
        )

        adapter._identity_mappings().remember(
            "123:7@hosted.lid",
            "85264362105:4@hosted",
        )
        self.assertTrue(
            asyncio.run(
                adapter._is_sender_allowed(
                    {"senderJid": "123:99@hosted.lid"},
                    True,
                ),
            ),
        )

    def test_runtime_cache_switches_with_gateway_active_auth_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_root = Path(temp_dir)
            (auth_root / "lid-mapping-123_reverse.json").write_text(
                json.dumps("111"),
                "utf-8",
            )
            adapter = object.__new__(self.module.WhatsAppPlatformAdapter)
            adapter.config = {"auth_dir": temp_dir}
            cache = adapter._identity_mappings(refresh_session=True)
            self.assertEqual(cache.pn_for_lid("123@lid"), "111@s.whatsapp.net")

            active_dir = auth_root / ".sessions" / "fresh-account"
            active_dir.mkdir(parents=True)
            (active_dir / "lid-mapping-123_reverse.json").write_text(
                json.dumps("222"),
                "utf-8",
            )
            (auth_root / ".active-session.json").write_text(
                json.dumps({"sessionId": "fresh-account"}),
                "utf-8",
            )

            refreshed = adapter._identity_mappings(refresh_session=True)
            self.assertIs(refreshed, cache)
            self.assertEqual(refreshed.pn_for_lid("123@lid"), "222@s.whatsapp.net")
            self.assertEqual(refreshed.lid_for_pn("111@s.whatsapp.net"), "")


if __name__ == "__main__":
    unittest.main()
