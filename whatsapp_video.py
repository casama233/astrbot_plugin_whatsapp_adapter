"""Prepare oversized local videos for WhatsApp's inline media path."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from astrbot import logger

# The Gateway rejects inline media above 100 MiB (shrink-only limit). The
# transcode budget leaves room for audio and MP4 overhead under that limit.
INLINE_VIDEO_SIZE_LIMIT = 100 * 1024**2
INLINE_VIDEO_BITRATE_BUDGET = 94 * 1024**2


def _local_path(source: str, parsed) -> Path | None:
    """Resolve a local/file-URI source to a filesystem path.

    ``file:///C:/...`` URIs carry a leading slash before the drive letter,
    which Windows reads as a root path on the current drive; strip it so the
    drive form stays valid. Remote URLs yield ``None``.
    """
    if parsed.scheme in {"http", "https"}:
        return None
    if parsed.scheme == "file":
        raw = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            raw = f"//{parsed.netloc}{raw}"
        elif re.fullmatch(r"/[A-Za-z]:/.+", raw):
            raw = raw[1:]
        return Path(raw)
    return Path(source)


@asynccontextmanager
async def inline_video(source: str):
    """Yield an H.264/AAC copy for oversized local videos, then remove it.

    Args:
        source: Local file, file URI, or remote URL from a Video component.

    Yields:
        A compatible local copy, or the original source if preparation fails.
    """
    parsed = urlparse(source)
    path = _local_path(source, parsed)
    if (
        parsed.scheme in {"http", "https"}
        or path is None
        or not path.is_file()
        or path.stat().st_size <= INLINE_VIDEO_SIZE_LIMIT
    ):
        yield source
        return

    with tempfile.TemporaryDirectory(prefix="astrbot-wa-video-") as temporary:
        output = Path(temporary) / "video.mp4"
        process = None
        prepared = source
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            raw, _ = await asyncio.wait_for(process.communicate(), 30)
            if process.returncode:
                raise ValueError("video duration unavailable")
            duration = float(json.loads(raw)["format"]["duration"])
            if duration <= 0:
                raise ValueError("invalid video duration")
            # Capped bitrate avoids an extra analysis pass.
            bitrate = int(INLINE_VIDEO_BITRATE_BUDGET * 8 / duration) - 128000
            if bitrate < 100000:
                raise ValueError("video too long for an inline copy")
            args = [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-vf",
                "scale='min(1280,iw)':-2,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-threads",
                "4",
                "-b:v",
                str(bitrate),
                "-maxrate",
                str(bitrate),
                "-bufsize",
                str(bitrate * 2),
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output),
            ]
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), 900)
            if process.returncode:
                raise ValueError("video encoding failed")
            if not 0 < output.stat().st_size <= INLINE_VIDEO_SIZE_LIMIT:
                raise ValueError("encoded video exceeds inline limit")
            prepared = str(output)
        except (OSError, ValueError, KeyError, asyncio.TimeoutError) as exc:
            logger.warning(
                "WhatsApp inline video preparation failed: %s", type(exc).__name__
            )
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        yield prepared


async def send_inline_video(client, target, source, caption, **kwargs):
    """Keep the prepared copy alive until the Gateway consumes it."""
    async with inline_video(source) as prepared:
        return await client.send_media(target, "video", prepared, caption, **kwargs)
