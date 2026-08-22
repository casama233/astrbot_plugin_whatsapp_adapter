from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_GATEWAY_TOKENS: dict[tuple[str, int], str] = {}
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


def install_gateway_transport_security(
    client_cls: type[Any],
    process_cls: type[Any],
) -> None:
    """Install per-process Bearer authentication without changing public APIs."""

    if not getattr(process_cls, _CLASS_PATCH_MARKER, False):
        original_process_init = process_cls.__init__
        original_process_stop = process_cls.stop

        def secure_process_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_process_init(self, *args, **kwargs)
            # Merely constructing a possible replacement must not take
            # ownership of a live endpoint's credential.
            self._gateway_auth_token = secrets.token_urlsafe(32)

        async def secure_process_start(self: Any) -> None:
            token = str(getattr(self, "_gateway_auth_token", "") or "").strip()
            if not token:
                token = secrets.token_urlsafe(32)
                self._gateway_auth_token = token
            key = _endpoint_key(self.host, self.port)
            if self.process and self.process.returncode is None:
                _GATEWAY_TOKENS[key] = token
                return

            # Mirror GatewayProcess.start while injecting the secret only into
            # the child env. Never mutate os.environ: unrelated concurrent
            # subprocesses must not inherit the Gateway credential.
            await self._ensure_node_runtime()
            await self._ensure_node_dependencies()
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            astrbot_data_dir = Path(self.data_dir).parent.parent.resolve()
            temp_dir = str(astrbot_data_dir / "temp")
            env.update(
                {
                    "WA_GATEWAY_HOST": self.host,
                    "WA_GATEWAY_PORT": str(self.port),
                    "WA_AUTH_DIR": str(self.auth_dir),
                    "WA_DATA_DIR": str(self.data_dir),
                    "WA_TEMP_DIR": temp_dir,
                    # AstrBot plugins normally create outbound media under
                    # data/ (for example temp/, temp_images/ or plugin_data/).
                    "WA_MEDIA_ALLOWED_ROOTS": str(astrbot_data_dir),
                    "WA_LOG_LEVEL": self.log_level,
                    "WA_GATEWAY_TOKEN": token,
                }
            )
            extra_kwargs: dict[str, Any] = {}
            if os.name == "nt":
                creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if creation_flags:
                    extra_kwargs["creationflags"] = creation_flags
            else:
                extra_kwargs["start_new_session"] = True
            child = await asyncio.create_subprocess_exec(
                self.node_executable,
                str(self.script_path),
                cwd=str(self.script_path.parent.parent),
                env=env,
                **extra_kwargs,
            )
            self.process = child
            # Publish only after spawn succeeds. A failed replacement must not
            # invalidate clients of the process already bound to this port.
            _GATEWAY_TOKENS[key] = token
            wait_for_exit = getattr(child, "wait", None)
            if callable(wait_for_exit):
                async def discard_token_after_exit() -> None:
                    try:
                        await wait_for_exit()
                    finally:
                        if _GATEWAY_TOKENS.get(key) == token:
                            _GATEWAY_TOKENS.pop(key, None)

                self._gateway_token_watch_task = asyncio.create_task(
                    discard_token_after_exit()
                )

        async def secure_process_stop(self: Any) -> None:
            try:
                await original_process_stop(self)
            finally:
                key = _endpoint_key(self.host, self.port)
                token = str(getattr(self, "_gateway_auth_token", "") or "")
                if token and _GATEWAY_TOKENS.get(key) == token:
                    _GATEWAY_TOKENS.pop(key, None)

        process_cls.__init__ = secure_process_init
        process_cls.start = secure_process_start
        process_cls.stop = secure_process_stop
        setattr(process_cls, _CLASS_PATCH_MARKER, True)

    if not getattr(client_cls, _CLASS_PATCH_MARKER, False):
        original_client_request = client_cls._request
        original_client_events = client_cls.events

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

        client_cls._request = secure_client_request
        client_cls.events = secure_client_events
        setattr(client_cls, _CLASS_PATCH_MARKER, True)
