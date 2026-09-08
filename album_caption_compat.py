from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


def _album_media(data: dict[str, Any]) -> list[dict[str, Any]]:
    media = data.get("media") or []
    if not isinstance(media, list):
        return []
    return [item for item in media if isinstance(item, dict)]


def _is_captioned_image_album(data: dict[str, Any]) -> bool:
    try:
        album_count = int(data.get("albumCount") or 0)
    except (TypeError, ValueError):
        album_count = 0
    media = _album_media(data)
    if album_count <= 1 or len(media) <= 1:
        return False
    if any(str(item.get("type") or "") != "image" for item in media):
        return False
    return any(str(item.get("caption") or "") for item in media)


def _caption_data(data: dict[str, Any], media: dict[str, Any]) -> dict[str, Any]:
    return {
        "mentionedJids": list(media.get("mentionedJids") or []),
        "mentionedNames": dict(media.get("mentionedNames") or {}),
        "mentionAll": bool(media.get("mentionAll")),
        "selfJid": data.get("selfJid"),
        "selfLid": data.get("selfLid"),
    }


def _format_caption(adapter: Any, caption: str) -> str:
    value = str(caption or "")
    if not value:
        return ""
    if bool((getattr(adapter, "config", {}) or {}).get("parse_inbound_formatting", True)):
        from .whatsapp_helpers import format_markdown_from_whatsapp

        value = format_markdown_from_whatsapp(value)
    return value


def album_caption_message_text(adapter: Any, data: dict[str, Any]) -> str:
    if not _is_captioned_image_album(data):
        return ""
    captions = [
        _format_caption(adapter, str(item.get("caption") or ""))
        for item in _album_media(data)
        if str(item.get("caption") or "")
    ]
    return "\n".join(captions).strip()


def album_caption_chain(adapter: Any, data: dict[str, Any]) -> list[Any] | None:
    """Interleave captions with their own image; None selects the normal chain."""
    if not _is_captioned_image_album(data):
        return None
    from astrbot.api.message_components import Image, Plain

    chain: list[Any] = []
    for media in _album_media(data):
        caption = _format_caption(adapter, str(media.get("caption") or ""))
        if caption:
            chain.extend(adapter._ordered_text_components(_caption_data(data, media), caption))
        path = str(media.get("path") or media.get("url") or "")
        chain.append(Image(file=path, path=path) if path else Plain(text="<media:image unavailable>"))
    return chain or None


def apply_album_caption_message(adapter: Any, message: Any, data: dict[str, Any]):
    """Expose every merged caption through message_str without changing transport text."""

    if message is None or not _is_captioned_image_album(data):
        return message
    caption_text = album_caption_message_text(adapter, data)
    if not caption_text:
        return message

    message.message_str = caption_text
    raw = getattr(message, "raw_message", None)
    if isinstance(raw, MutableMapping):
        raw["raw_message"] = caption_text
    return message
