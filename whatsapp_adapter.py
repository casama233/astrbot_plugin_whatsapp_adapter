from __future__ import annotations

from pathlib import Path as _Path

from .group_name_compat import apply_group_name as _apply_group_name

_impl_path = _Path(__file__).with_name("_whatsapp_adapter_impl.py")
exec(compile(_impl_path.read_text(encoding="utf-8"), str(_impl_path), "exec"), globals(), globals())

_original_convert_message = WhatsAppPlatformAdapter.convert_message


async def _convert_message_with_group_name(self, data):
    message = await _original_convert_message(self, data)
    return _apply_group_name(message, data)


WhatsAppPlatformAdapter.convert_message = _convert_message_with_group_name
