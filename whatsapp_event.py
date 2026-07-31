from __future__ import annotations

from pathlib import Path as _Path

from .group_name_compat import current_event_group as _current_event_group

_impl_path = _Path(__file__).with_name("_whatsapp_event_impl.py")
exec(compile(_impl_path.read_text(encoding="utf-8"), str(_impl_path), "exec"), globals(), globals())


async def _get_group_compat(self, group_id=None, **kwargs):
    del kwargs
    return _current_event_group(self, group_id)


WhatsAppMessageEvent.get_group = _get_group_compat
