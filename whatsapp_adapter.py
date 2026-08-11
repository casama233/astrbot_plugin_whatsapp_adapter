from __future__ import annotations

from pathlib import Path as _Path

from .album_caption_compat import (
    apply_album_caption_message as _apply_album_caption_message,
    install_album_caption_compat as _install_album_caption_compat,
)
from .group_name_compat import apply_group_name as _apply_group_name

_impl_path = _Path(__file__).with_name("_whatsapp_adapter_impl.py")
exec(compile(_impl_path.read_text(encoding="utf-8"), str(_impl_path), "exec"), globals(), globals())

_install_album_caption_compat(WhatsAppPlatformAdapter)
_original_convert_message = WhatsAppPlatformAdapter.convert_message


async def _convert_message_with_compat(self, data):
    message = await _original_convert_message(self, data)
    message = _apply_group_name(message, data)
    return _apply_album_caption_message(self, message, data)


WhatsAppPlatformAdapter.convert_message = _convert_message_with_compat
