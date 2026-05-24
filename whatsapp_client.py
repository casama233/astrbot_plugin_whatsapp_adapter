from __future__ import annotations

import asyncio
import json
import os
import subprocess
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
        self._start_lock = asyncio.Lock()
        self._events_response: aiohttp.ClientResponse | None = None

    async def __aenter__(self) -> "WhatsAppGatewayClient":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session and not self._session.closed:
            return
        async with self._start_lock:
            if self._session and not self._session.closed:
                return
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )

    async def close(self) -> None:
        if self._events_response:
            self._events_response.release()
            self._events_response = None
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def update_base_url(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/status")

    async def qr(self) -> dict[str, Any]:
        return await self._request("GET", "/qr")

    async def configure(self, config: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/config", json_data=config)

    async def resolve_lid(self, lid_jid: str) -> str | None:
        result = await self._request("POST", "/lid/resolve", json_data={"lidJid": lid_jid})
        return result.get("pnJid") or None

    async def send_text(
        self,
        to: str,
        text: str,
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
        link_preview: bool = False,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "text": text, "linkPreview": link_preview}
        if mentions:
            payload["mentions"] = mentions
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        if quoted_participant:
            payload["quotedParticipant"] = quoted_participant
        return await self._request("POST", "/send/text", json_data=payload)

    async def edit_text(
        self,
        to: str,
        message_id: str,
        text: str,
        mentions: list[str] | None = None,
        participant: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "messageId": message_id, "text": text}
        if mentions:
            payload["mentions"] = mentions
        if participant:
            payload["participant"] = participant
        return await self._request("POST", "/edit/text", json_data=payload)

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

    async def send_sticker(
        self,
        to: str,
        path_or_url: str,
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        return await self.send_media(
            to,
            "sticker",
            path_or_url,
            quoted_message_id=quoted_message_id,
            quoted_participant=quoted_participant,
        )

    async def send_presence(self, to: str, state: str) -> dict[str, Any]:
        return await self._request("POST", "/presence", json_data={"to": to, "state": state})

    async def react(self, to: str, message_id: str, emoji: str, participant: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "messageId": message_id, "emoji": emoji}
        if participant:
            payload["participant"] = participant
        return await self._request(
            "POST", "/send/reaction", json_data=payload
        )

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict[str, str]],
        footer: str = "",
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"to": to, "text": body, "body": body, "buttons": buttons, "footer": footer}
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        if quoted_participant:
            payload["quotedParticipant"] = quoted_participant
        return await self._request("POST", "/send/buttons", json_data=payload)

    async def send_list(
        self,
        to: str,
        title: str,
        sections: list[dict[str, Any]],
        description: str = "",
        button_text: str = "選項",
        footer: str = "",
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "to": to,
            "title": title,
            "description": description,
            "buttonText": button_text,
            "sections": sections,
            "footer": footer,
        }
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        if quoted_participant:
            payload["quotedParticipant"] = quoted_participant
        return await self._request("POST", "/send/list", json_data=payload)

    async def send_poll(
        self,
        to: str,
        name: str,
        options: list[str],
        selectable_count: int = 0,
        quoted_message_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "to": to,
            "name": name,
            "options": options,
            "selectableCount": selectable_count,
        }
        if quoted_message_id:
            payload["quotedMessageId"] = quoted_message_id
        if quoted_participant:
            payload["quotedParticipant"] = quoted_participant
        return await self._request("POST", "/send/poll", json_data=payload)

    async def restart(self) -> dict[str, Any]:
        return await self._request("POST", "/restart", json_data={})

    async def logout(self) -> dict[str, Any]:
        return await self._request("POST", "/logout", json_data={})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        await self.start()
        if self._session is None:
            raise RuntimeError("WhatsApp gateway client session not started")
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.timeout, sock_read=300.0)
        response = None
        try:
            response = await self._session.get(f"{self.base_url}/events", timeout=timeout)
            response.raise_for_status()
            self._events_response = response
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
        finally:
            if response is not None:
                response.release()
            if getattr(self, '_events_response', None) is response:
                self._events_response = None

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.start()
        if self._session is None:
            raise RuntimeError("WhatsApp gateway client session not started")
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
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise WhatsAppGatewayError(f"Gateway {method} {path} returned invalid JSON: {exc}") from exc


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
        # 全域暫存目錄（給 astrbot TempDirCleaner 自動清理）
        temp_dir = str(Path(self.data_dir).parent.parent / "temp")
        env.update(
            {
                "WA_GATEWAY_HOST": self.host,
                "WA_GATEWAY_PORT": str(self.port),
                "WA_AUTH_DIR": str(self.auth_dir),
                "WA_DATA_DIR": str(self.data_dir),
                "WA_TEMP_DIR": temp_dir,
                "WA_LOG_LEVEL": self.log_level,
            }
        )
        creation_flags = 0
        extra_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creation_flags:
                extra_kwargs["creationflags"] = creation_flags
        else:
            extra_kwargs["start_new_session"] = True
        self.process = await asyncio.create_subprocess_exec(
            self.node_executable,
            str(self.script_path),
            cwd=str(self.script_path.parent.parent),
            env=env,
            **extra_kwargs,
        )

    async def stop(self) -> None:
        if not self.process or self.process.returncode is not None:
            self.process = None
            return
        pgid = None
        current_pgid = None
        if os.name != "nt" and hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(self.process.pid)
            except (ProcessLookupError, OSError):
                pgid = None
            try:
                current_pgid = os.getpgid(os.getpid())
            except (ProcessLookupError, OSError):
                current_pgid = None
        if os.name == "nt":
            if not await self._taskkill_process_tree(force=False):
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    self.process = None
                    return
        elif pgid is not None and current_pgid is not None and pgid != current_pgid and hasattr(os, "killpg"):
            try:
                os.killpg(pgid, 15)
            except (ProcessLookupError, OSError):
                pass
        else:
            try:
                self.process.terminate()
            except ProcessLookupError:
                self.process = None
                return
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            if os.name == "nt":
                await self._taskkill_process_tree(force=True)
            elif pgid is not None and current_pgid is not None and pgid != current_pgid and hasattr(os, "killpg"):
                try:
                    os.killpg(pgid, 9)
                except (ProcessLookupError, OSError):
                    pass
            else:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
            try:
                await self.process.wait()
            except Exception:
                pass
        self.process = None

    async def _taskkill_process_tree(self, force: bool) -> bool:
        if os.name != "nt" or not self.process or self.process.returncode is not None:
            return False
        args = ["taskkill", "/PID", str(self.process.pid), "/T"]
        if force:
            args.append("/F")
        try:
            killer = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False
        try:
            await asyncio.wait_for(killer.wait(), timeout=5)
        except asyncio.TimeoutError:
            killer.kill()
            await killer.wait()
        return True
