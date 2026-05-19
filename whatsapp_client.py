from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

import aiohttp


class WhatsAppGatewayError(RuntimeError):
    pass


class WhatsAppGatewayClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "WhatsAppGatewayClient":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/status")

    async def qr(self) -> dict[str, Any]:
        return await self._request("GET", "/qr")

    async def configure(self, config: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/config", json_data=config)

    async def send_text(
        self,
        to: str,
        text: str,
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "text": text}
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        if quoted_participant:
            payload["quotedParticipant"] = quoted_participant
        return await self._request("POST", "/send/text", json_data=payload)

    async def send_media(
        self,
        to: str,
        media_type: str,
        path_or_url: str,
        caption: str | None = None,
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "to": to,
            "type": media_type,
            "pathOrUrl": path_or_url,
        }
        if caption:
            payload["caption"] = caption
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        if quoted_participant:
            payload["quotedParticipant"] = quoted_participant
        return await self._request("POST", "/send/media", json_data=payload)

    async def react(self, to: str, message_id: str, emoji: str, participant: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "messageId": message_id, "emoji": emoji}
        if participant:
            payload["participant"] = participant
        return await self._request(
            "POST", "/send/reaction", json_data=payload
        )

    async def restart(self) -> dict[str, Any]:
        return await self._request("POST", "/restart", json_data={})

    async def logout(self) -> dict[str, Any]:
        return await self._request("POST", "/logout", json_data={})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        assert self._session is not None
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.timeout, sock_read=None)
        async with self._session.get(f"{self.base_url}/events", timeout=timeout) as response:
            response.raise_for_status()
            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                yield json.loads(data)

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.start()
        assert self._session is not None
        async with self._session.request(
            method,
            f"{self.base_url}{path}",
            json=json_data,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise WhatsAppGatewayError(f"Gateway {method} {path} failed: {response.status} {text}")
            if not text:
                return {}
            return json.loads(text)


class GatewayProcess:
    def __init__(
        self,
        node_executable: str,
        script_path: Path,
        host: str,
        port: int,
        auth_dir: Path,
        log_level: str,
        data_dir: Path | None = None,
    ) -> None:
        self.node_executable = node_executable
        self.script_path = script_path
        self.host = host
        self.port = port
        self.auth_dir = auth_dir
        self.log_level = log_level
        self.data_dir = data_dir or auth_dir.parent
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "WA_GATEWAY_HOST": self.host,
                "WA_GATEWAY_PORT": str(self.port),
                "WA_AUTH_DIR": str(self.auth_dir),
                "WA_DATA_DIR": str(self.data_dir),
                "WA_LOG_LEVEL": self.log_level,
            }
        )
        self.process = await asyncio.create_subprocess_exec(
            self.node_executable,
            str(self.script_path),
            cwd=str(self.script_path.parent.parent),
            env=env,
        )

    async def stop(self) -> None:
        if not self.process or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
