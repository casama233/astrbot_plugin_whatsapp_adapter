from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import album_caption_compat as compat


class _Plain:
    def __init__(self, text: str = "", **_kwargs) -> None:
        self.text = text


class _Image:
    def __init__(self, file: str = "", path: str = "", **_kwargs) -> None:
        self.file = file
        self.path = path


class _Adapter:
    config = {"parse_inbound_formatting": False}

    def __init__(self) -> None:
        self.caption_projections = []

    def _ordered_text_components(self, data, text):
        self.caption_projections.append((data, text))
        return [_Plain(text=text)]

    def _message_chain(self, _data, _text):
        return [_Plain(text="legacy")]


def _component_modules():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    components = types.ModuleType("astrbot.api.message_components")
    components.Image = _Image
    components.Plain = _Plain
    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.message_components": components,
    }


def _album_data():
    return {
        "albumCount": 2,
        "selfJid": "999@s.whatsapp.net",
        "media": [
            {
                "type": "image",
                "path": "/tmp/one.jpg",
                "caption": "first caption",
                "mentionedJids": ["111@s.whatsapp.net"],
                "mentionedNames": {"111@s.whatsapp.net": "Alice"},
                "mentionAll": False,
            },
            {
                "type": "image",
                "path": "/tmp/two.jpg",
                "caption": "second caption",
                "mentionedJids": [],
                "mentionedNames": {},
                "mentionAll": False,
            },
        ],
    }


class AlbumCaptionCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test uses a fresh subclass so the installer marker cannot leak
        # across test cases.
        self.adapter_cls = type("AdapterUnderTest", (_Adapter,), {})

    def test_captioned_album_interleaves_each_caption_with_its_image(self) -> None:
        with patch.dict(sys.modules, _component_modules(), clear=False):
            compat.install_album_caption_compat(self.adapter_cls)
            adapter = self.adapter_cls()
            chain = adapter._message_chain(_album_data(), "first caption")

        self.assertEqual(
            [type(item).__name__ for item in chain],
            ["_Plain", "_Image", "_Plain", "_Image"],
        )
        self.assertEqual(chain[0].text, "first caption")
        self.assertEqual(chain[1].path, "/tmp/one.jpg")
        self.assertEqual(chain[2].text, "second caption")
        self.assertEqual(chain[3].path, "/tmp/two.jpg")
        self.assertEqual(
            adapter.caption_projections[0][0]["mentionedJids"],
            ["111@s.whatsapp.net"],
        )

    def test_all_captions_are_exposed_through_message_str(self) -> None:
        adapter = self.adapter_cls()
        message = SimpleNamespace(message_str="first caption", raw_message={"raw_message": "first caption"})

        result = compat.apply_album_caption_message(adapter, message, _album_data())

        self.assertIs(result, message)
        self.assertEqual(message.message_str, "first caption\nsecond caption")
        self.assertEqual(message.raw_message["raw_message"], "first caption\nsecond caption")

    def test_uncaptioned_album_keeps_legacy_chain(self) -> None:
        data = _album_data()
        for media in data["media"]:
            media["caption"] = ""
        with patch.dict(sys.modules, _component_modules(), clear=False):
            compat.install_album_caption_compat(self.adapter_cls)
            chain = self.adapter_cls()._message_chain(data, "")

        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0].text, "legacy")


if __name__ == "__main__":
    unittest.main()
