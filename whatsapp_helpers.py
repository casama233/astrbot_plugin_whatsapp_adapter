"""Public WhatsApp helper facade.

The implementation remains in ``_whatsapp_helpers_impl`` while formatting-aware
chunking lives in its own focused module.
"""

from __future__ import annotations

from . import _whatsapp_helpers_impl as _impl
from .whatsapp_chunking import split_whatsapp_text

# Core send/caption helpers resolve this global at call time, so install the
# hardened splitter once before re-exporting the public API.
_impl.split_whatsapp_text = split_whatsapp_text

__all__ = list(_impl.__all__)
for _name in __all__:
    globals()[_name] = getattr(_impl, _name)
globals()["split_whatsapp_text"] = split_whatsapp_text

del _name
