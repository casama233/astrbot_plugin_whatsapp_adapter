"""Task ownership for cancellation-safe update transactions."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

_T = TypeVar("_T")


async def await_update_operation(operation: Awaitable[_T]) -> _T:
    """Defer caller cancellation until an owned update operation has settled.

    In particular, cancelling to_thread does not stop filesystem writes. Keep
    the task strongly referenced and shield repeated cancellation, then propagate
    the original cancellation only after observing the operation's outcome.
    The caller must keep its transaction lock until this function returns.
    """
    task = asyncio.ensure_future(operation)
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancellation = exc
        except Exception:
            break
    if cancellation is not None:
        try:
            task.result()
        except Exception as exc:
            raise cancellation from exc
        raise cancellation
    return task.result()
