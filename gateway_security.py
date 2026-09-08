from __future__ import annotations

import asyncio
import os
import secrets
from typing import Any
from urllib.parse import urlsplit

_GATEWAY_TOKENS: dict[tuple[str, int], str] = {}


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
    key = _client_endpoint_key(getattr(client, "base_url", ""))
    explicit = str(getattr(client, "_gateway_auth_token", "") or "").strip()
    explicit_key = getattr(client, "_gateway_auth_endpoint_key", None)
    if explicit and (explicit_key is None or explicit_key == key):
        return explicit
    if key:
        managed_token = _GATEWAY_TOKENS.get(key, "")
        if managed_token:
            return managed_token
    return str(os.environ.get("WA_GATEWAY_TOKEN") or "").strip()


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


def bind_gateway_client(client: Any, process: Any) -> str:
    """Bind a client to the exact managed Gateway process it talks to.

    The explicit client credential deliberately takes precedence over the
    module-level endpoint registry.  AstrBot hot reload can temporarily leave
    objects created by different module generations alive; storing the token
    on both objects keeps those mixed-generation pairs authenticated.
    """

    token = str(getattr(process, "_gateway_auth_token", "") or "").strip()
    if not token:
        token = secrets.token_urlsafe(32)
        process._gateway_auth_token = token
    client._gateway_auth_token = token
    client._gateway_auth_endpoint_key = _client_endpoint_key(
        getattr(client, "base_url", "")
    )
    _apply_client_auth(client)
    return token


def clear_gateway_client_binding(client: Any, process: Any | None = None) -> bool:
    """Remove a managed binding, optionally only when it belongs to process."""

    explicit = str(getattr(client, "_gateway_auth_token", "") or "")
    if process is not None:
        process_token = str(getattr(process, "_gateway_auth_token", "") or "")
        if explicit and explicit != process_token:
            return False
    client.__dict__.pop("_gateway_auth_token", None)
    client.__dict__.pop("_gateway_auth_endpoint_key", None)
    _apply_client_auth(client)
    return True


def register_gateway_token(process: Any) -> None:
    """Publish endpoint ownership only after a child was successfully spawned."""
    key = _endpoint_key(process.host, process.port)
    token = process._gateway_auth_token
    _GATEWAY_TOKENS[key] = token
    child = process.process
    wait = getattr(child, "wait", None)
    previous_watch = getattr(process, "_gateway_token_watch_task", None)
    if callable(wait) and (previous_watch is None or previous_watch.done()):
        async def discard_after_exit() -> None:
            try:
                await wait()
            finally:
                if _GATEWAY_TOKENS.get(key) == token:
                    _GATEWAY_TOKENS.pop(key, None)
        process._gateway_token_watch_task = asyncio.create_task(discard_after_exit())


def release_gateway_token(process: Any) -> None:
    key = _endpoint_key(process.host, process.port)
    token = getattr(process, "_gateway_auth_token", "")
    if token and _GATEWAY_TOKENS.get(key) == token:
        _GATEWAY_TOKENS.pop(key, None)
