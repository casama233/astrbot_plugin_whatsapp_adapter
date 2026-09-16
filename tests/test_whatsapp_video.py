from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

_astrbot = types.ModuleType("astrbot")
_astrbot.logger = logging.getLogger("whatsapp-video-tests")
sys.modules.setdefault("astrbot", _astrbot)

import whatsapp_video  # noqa: E402

SIZE_LIMIT = 4096
BITRATE_BUDGET = 256 * 1024


class _FakeProcess:
    def __init__(self, *, stdout=b"", returncode=0, on_start=None) -> None:
        self._stdout = stdout
        self._on_start = on_start
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        if self._on_start is not None:
            self._on_start()
        return self._stdout, b""

    async def wait(self):
        if self._on_start is not None:
            self._on_start()
        return self.returncode

    def kill(self):
        self.killed = True


class _RecordingClient:
    def __init__(self) -> None:
        self.calls = []

    async def send_media(self, target, media_type, source, caption, **kwargs):
        self.calls.append((target, media_type, source, caption, kwargs))


class InlineVideoTestBase(unittest.IsolatedAsyncioTestCase):
    """Behavior of the oversized local video inline-send path.

    All subprocesses are test doubles: the suite never requires ffmpeg and
    stays portable across the shared Windows/Linux runtime matrix. The module
    limits are patched to byte scale so doubles stay tiny.
    """

    def setUp(self) -> None:
        self._stack = ExitStack()
        self.addCleanup(self._stack.close)
        # Discovery may bind the module logger to another test module's stub
        # (a silent recorder); bind a real stdlib logger so assertLogs works
        # regardless of import order.
        self._stack.enter_context(
            patch.object(whatsapp_video, "logger", logging.getLogger("whatsapp-video-tests"))
        )
        self._stack.enter_context(
            patch.object(whatsapp_video, "INLINE_VIDEO_SIZE_LIMIT", SIZE_LIMIT)
        )
        self._stack.enter_context(
            patch.object(whatsapp_video, "INLINE_VIDEO_BITRATE_BUDGET", BITRATE_BUDGET)
        )
        self.spawns: list[tuple] = []

    def install_spawns(self, *, duration="1.5", probe_returncode=0, encode_bytes=1024,
                       encode_returncode=0, spawn_error=None, probe_stdout=b"") -> None:
        async def spawn(*argv, **kwargs):
            self.spawns.append(argv)
            if spawn_error is not None:
                raise spawn_error
            if argv[0] == "ffprobe":
                stdout = probe_stdout or json.dumps({"format": {"duration": duration}}).encode()
                return _FakeProcess(stdout=stdout, returncode=probe_returncode)
            assert argv[0] == "ffmpeg"
            output = Path(argv[-1])

            def _write_output():
                output.write_bytes(b"x" * encode_bytes)

            return _FakeProcess(returncode=encode_returncode, on_start=_write_output)

        self._stack.enter_context(patch.object(asyncio, "create_subprocess_exec", spawn))

    def make_source(self, directory: str, *, size=10_000) -> Path:
        source = Path(directory) / "huge.mp4"
        source.write_bytes(b"x" * size)
        return source


class InlineVideoTests(InlineVideoTestBase):
    async def test_remote_url_passes_through_without_spawning(self):
        async with whatsapp_video.inline_video("https://example.com/video.mp4") as prepared:
            self.assertEqual(prepared, "https://example.com/video.mp4")
        self.assertEqual(self.spawns, [])

    async def test_small_local_file_passes_through_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory, size=64)
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))
        self.assertEqual(self.spawns, [])

    async def test_missing_local_file_passes_through(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gone.mp4"
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))
        self.assertEqual(self.spawns, [])

    async def test_oversized_local_video_is_transcoded_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(encode_bytes=2048)
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertNotEqual(prepared, str(source))
                self.assertTrue(Path(prepared).exists())
                self.assertEqual(Path(prepared).stat().st_size, 2048)
            self.assertEqual(self.spawns[0][0], "ffprobe")
            self.assertEqual(self.spawns[1][0], "ffmpeg")
            self.assertFalse(Path(prepared).exists())

    async def test_file_uri_source_is_transcoded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns()
            async with whatsapp_video.inline_video(source.as_uri()) as prepared:
                self.assertNotEqual(prepared, source.as_uri())
                self.assertEqual(Path(self.spawns[0][-1]).resolve(), source.resolve())

    async def test_missing_ffmpeg_falls_back_to_original_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(spawn_error=FileNotFoundError("ffprobe"))
            with self.assertLogs(level=logging.WARNING):
                async with whatsapp_video.inline_video(str(source)) as prepared:
                    self.assertEqual(prepared, str(source))
            self.assertEqual([argv[0] for argv in self.spawns], ["ffprobe"])

    async def test_failed_probe_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(probe_returncode=1)
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))
            self.assertEqual(len(self.spawns), 1)

    async def test_unreadable_duration_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(probe_stdout=b"not-json")
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))

    async def test_too_long_video_falls_back_before_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(duration="1000000")
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))
            self.assertEqual([argv[0] for argv in self.spawns], ["ffprobe"])

    async def test_oversized_encoded_output_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(encode_bytes=SIZE_LIMIT * 2)
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))
            self.assertEqual([argv[0] for argv in self.spawns], ["ffprobe", "ffmpeg"])

    async def test_failed_encoding_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            self.install_spawns(encode_returncode=1)
            async with whatsapp_video.inline_video(str(source)) as prepared:
                self.assertEqual(prepared, str(source))
            self.assertEqual([argv[0] for argv in self.spawns], ["ffprobe", "ffmpeg"])

    async def test_send_inline_video_forwards_media_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "small.mp4"
            source.write_bytes(b"tiny")
            client = _RecordingClient()
            await whatsapp_video.send_inline_video(
                client, "8613800138000@s.whatsapp.net", str(source), "看看", mentions=["8613800138000"]
            )
            self.assertEqual(
                client.calls,
                [
                    (
                        "8613800138000@s.whatsapp.net",
                        "video",
                        str(source),
                        "看看",
                        {"mentions": ["8613800138000"]},
                    )
                ],
            )


class InlineVideoWiringContractTests(unittest.TestCase):
    """The dispatcher must keep routing oversized videos through the helper
    and the Gateway must keep letting Baileys build native media previews."""

    def test_helpers_dispatch_video_through_inline_helper(self):
        text = (ROOT / "_whatsapp_helpers_impl.py").read_text(encoding="utf-8")
        self.assertIn("from .whatsapp_video import send_inline_video", text)
        self.assertEqual(text.count("await send_inline_video("), 1)

    def test_gateway_keeps_native_media_thumbnail(self):
        text = (ROOT / "gateway" / "whatsapp-gateway-impl.mjs").read_text(encoding="utf-8")
        self.assertNotIn("payload.jpegThumbnail = null", text)
        self.assertIn(
            "Leave jpegThumbnail unset so Baileys generates the native media preview", text
        )


if __name__ == "__main__":
    unittest.main()
