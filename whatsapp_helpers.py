"""Public WhatsApp helper facade.

The legacy implementation remains in ``_whatsapp_helpers_impl`` while focused
formatting and chunking helpers live in dedicated modules.  Core send/caption
helpers resolve these globals at call time, so the hardened implementations are
installed once here before re-exporting the public API.
"""

from __future__ import annotations

from . import _whatsapp_helpers_impl as _impl
from .whatsapp_chunking import split_whatsapp_text
from .whatsapp_markdown import (
    format_markdown_from_whatsapp,
    format_whatsapp_markdown,
)

_impl.split_whatsapp_text = split_whatsapp_text
_impl.format_whatsapp_markdown = format_whatsapp_markdown
_impl.format_markdown_from_whatsapp = format_markdown_from_whatsapp

__all__ = list(_impl.__all__)
for _name in __all__:
    globals()[_name] = getattr(_impl, _name)

globals()["split_whatsapp_text"] = split_whatsapp_text
globals()["format_whatsapp_markdown"] = format_whatsapp_markdown
globals()["format_markdown_from_whatsapp"] = format_markdown_from_whatsapp

del _name
