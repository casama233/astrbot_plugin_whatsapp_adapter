from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

_GATEWAY_TOKENS: dict[tuple[str, int], str] = {}
_PROCESS_START_ENV_LOCK = asyncio.Lock()
_CLASS_PATCH_MARKER = "_astrbot_gateway_security_installed"


def _endpoint_key(host: str, port: int) -> tuple[str, int]:
    return (str(host or "").strip().strip("[]").lower(), int(port))


def _client_endpoint_key(base_url: str) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(str(base_url or ""))
        if not parsed.hostname:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return _endpoint_key(parsed.hostname, port)
    except (TypeError, ValueError):
        return None


def _token_for_client(client: Any) -> str:
    explicit = str(getattr(client, "_gateway_auth_token", "") or "").strip()
    if explicit:
        return explicit
    env_token = str(os.environ.get("WA_GATEWAY_TOKEN") or "").strip()
    if env_token:
        return env_token
    key = _client_endpoint_key(getattr(client, "base_url", ""))
    return _GATEWAY_TOKENS.get(key, "") if key else ""


def _apply_client_auth(client: Any) -> None:
    session = getattr(client, "_session", None)
    if session is None:
        return
    headers = getattr(session, "headers", None)
    if headers is None:
        return
    token = _token_for_client(client)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers.pop("Authorization", None)


def install_gateway_transport_security(
    client_cls: type[Any],
    process_cls: type[Any],
) -> None:
    """Install per-process Bearer authentication without changing public APIs."""

    if getattr(client_cls, _CLASS_PATCH_MARKER, False) and getattr(
        process_cls,
        _CLASS_PATCH_MARKER,
        False,
    ):
        return

    original_process_init = process_cls.__init__
    original_process_start = process_cls.start
    original_client_request = client_cls._request
    original_client_events = client_cls.events

    def secure_process_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_process_init(self, *args, **kwargs)
        token = secrets.token_urlsafe(32)
        self._gateway_auth_token = token
        _GATEWAY_TOKENS[_endpoint_key(self.host, self.port)] = token

    async def secure_process_start(self: Any) -> None:
        token = str(getattr(self, "_gateway_auth_token", "") or "").strip()
        if not token:
            token = secrets.token_urlsafe(32)
            self._gateway_auth_token = token
        _GATEWAY_TOKENS[_endpoint_key(self.host, self.port)] = token
        async with _PROCESS_START_ENV_LOCK:
            previous = os.environ.get("WA_GATEWAY_TOKEN")
            os.environ["WA_GATEWAY_TOKEN"] = token
            try:
                await original_process_start(self)
            finally:
                if previous is None:
                    os.environ.pop("WA_GATEWAY_TOKEN", None)
                else:
                    os.environ["WA_GATEWAY_TOKEN"] = previous

    async def secure_client_request(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        await self.start()
        _apply_client_auth(self)
        return await original_client_request(self, *args, **kwargs)

    async def secure_client_events(self: Any) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        _apply_client_auth(self)
        async for item in original_client_events(self):
            yield item

    process_cls.__init__ = secure_process_init
    process_cls.start = secure_process_start
    client_cls._request = secure_client_request
    client_cls.events = secure_client_events
    setattr(process_cls, _CLASS_PATCH_MARKER, True)
    setattr(client_cls, _CLASS_PATCH_MARKER, True)
