from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import mimetypes
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import astrbot.api.message_components as Comp
import httpx
from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.platform import (
    AstrBotMessage,
    AstrMessageEvent,
    Group,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.api.star import Context, Star, register
from pydantic import BaseModel, Field, ValidationError

try:
    from whatsapp_bridge.client import WhatsappClient
except ImportError:
    from whatsapp_bridge import WhatsappClient

try:
    from whatsapp_bridge.exceptions import (
        ApiError,
        BridgeError,
        DbError,
        PrerequisitesError,
        SetupError,
        WhatsappError,
    )
except ImportError:
    class WhatsappError(Exception):
        pass

    ApiError = BridgeError = DbError = PrerequisitesError = SetupError = WhatsappError


def _plugin_root() -> Path:
    return Path(__file__).resolve().parent


def _astrbot_data_root() -> Path:
    current = Path(__file__).resolve()
    parents = list(current.parents)
    if len(parents) >= 3 and parents[1].name == "plugins" and parents[2].name == "data":
        return parents[2]
    return _plugin_root() / "data"


PLUGIN_ROOT = _plugin_root()
ASTRBOT_DATA_ROOT = _astrbot_data_root()
WHATSAPP_CREDS_DIR = ASTRBOT_DATA_ROOT / "whatsapp_creds"
WHATSAPP_MEDIA_DIR = ASTRBOT_DATA_ROOT / "whatsapp_media"

DEFAULT_CONFIG: dict[str, Any] = {
    "id": "whatsapp",
    "type": "whatsapp",
    "enable": False,
    "mode": "gateway",
    "allowlist": [],
    "dm_policy": "allow",
    "send_read_receipts": True,
    "media_max_mb": 32,
    "polling_interval_sec": 1.0,
    "reconnect_initial_sec": 3.0,
    "reconnect_max_sec": 60.0,
    "bridge_timeout_sec": 180,
    "typing_indicator": True,
    "pre_reply_emoji": "💭",
}

CONFIG_METADATA: dict[str, Any] = {
    "mode": {
        "type": "string",
        "description": "運作模式",
        "hint": "固定使用 gateway，底層為 whatsapp-bridge 的非官方多裝置橋接。",
        "default": "gateway",
    },
    "allowlist": {
        "type": "list",
        "description": "白名單",
        "hint": "可填 chat_jid、sender、session_id。留空表示不限制。",
        "items": {"type": "string"},
        "default": [],
    },
    "dm_policy": {
        "type": "string",
        "description": "私聊策略",
        "hint": "allow=接受私聊，deny=忽略私聊，allowlist_only=僅允許白名單私聊。",
        "default": "allow",
    },
    "send_read_receipts": {
        "type": "bool",
        "description": "已讀回執",
        "hint": "收到並成功轉交 AstrBot 後，嘗試回傳已讀。",
        "default": True,
    },
    "media_max_mb": {
        "type": "float",
        "description": "媒體大小上限",
        "hint": "媒體自動下載與暫存的建議大小上限，單位 MB。",
        "default": 32,
    },
    "polling_interval_sec": {
        "type": "float",
        "description": "輪詢間隔",
        "hint": "輪詢 whatsapp-bridge 新訊息的秒數。",
        "default": 1.0,
    },
    "reconnect_initial_sec": {
        "type": "float",
        "description": "初始重連間隔",
        "hint": "連線失敗時的初始重試秒數。",
        "default": 3.0,
    },
    "reconnect_max_sec": {
        "type": "float",
        "description": "最大重連間隔",
        "hint": "指數退避的上限秒數。",
        "default": 60.0,
    },
    "bridge_timeout_sec": {
        "type": "int",
        "description": "橋接啟動逾時",
        "hint": "首次連線或重新登入等待 QR / 成功連線的秒數。",
        "default": 180,
    },
    "typing_indicator": {
        "type": "bool",
        "description": "Typing 指示",
        "hint": "正式回覆前，嘗試向 WhatsApp 使用者顯示 typing 狀態。",
        "default": True,
    },
    "pre_reply_emoji": {
        "type": "string",
        "description": "預回覆表情",
        "hint": "第一段正式回覆前先送出的自訂表情，留空可停用。",
        "default": "💭",
    },
}

WHATSAPP_PLATFORM_META = PlatformMetadata(
    name="whatsapp",
    description="基於 whatsapp-bridge 的 WhatsApp 非官方多裝置平台適配器",
    id="whatsapp",
    default_config_tmpl=DEFAULT_CONFIG,
    adapter_display_name="WhatsApp",
    support_streaming_message=True,
    support_proactive_message=True,
    config_metadata=CONFIG_METADATA,
)

_PLUGIN_CONTEXT: Context | None = None


class WhatsAppAdapterSettings(BaseModel):
    mode: Literal["gateway"] = "gateway"
    allowlist: list[str] = Field(default_factory=list)
    dm_policy: Literal["allow", "deny", "allowlist_only"] = "allow"
    send_read_receipts: bool = True
    media_max_mb: float = 32.0
    polling_interval_sec: float = 1.0
    reconnect_initial_sec: float = 3.0
    reconnect_max_sec: float = 60.0
    bridge_timeout_sec: int = 180
    typing_indicator: bool = True
    pre_reply_emoji: str = "💭"

    @property
    def allowlist_set(self) -> set[str]:
        return {item.strip() for item in self.allowlist if str(item).strip()}


@dataclass
class PreparedMedia:
    path: Path
    temporary: bool = False


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.startswith("http://") or value.startswith("https://")


def _safe_component_name(component: Any) -> str:
    return component.__class__.__name__.lower()


def _component_text(component: Any) -> str:
    for key in ("text", "content"):
        value = getattr(component, key, None)
        if isinstance(value, str):
            return value
    if _safe_component_name(component) == "at":
        mention_target = (
            getattr(component, "qq", None)
            or getattr(component, "user_id", None)
            or getattr(component, "id", None)
        )
        return f"@{mention_target}" if mention_target else "@"
    return ""


def _extract_chain_items(message: MessageChain | list[Any] | tuple[Any, ...] | Any) -> list[Any]:
    if message is None:
        return []
    if isinstance(message, list):
        return message
    if isinstance(message, tuple):
        return list(message)
    if hasattr(message, "chain"):
        chain = getattr(message, "chain")
        if isinstance(chain, list):
            return chain
        return list(chain)
    if isinstance(message, str):
        return [Comp.Plain(message)]
    try:
        return list(message)
    except TypeError:
        return [message]


def _make_image_component(path: Path) -> Any:
    image_cls = getattr(Comp, "Image")
    if hasattr(image_cls, "fromFileSystem"):
        return image_cls.fromFileSystem(path=str(path))
    return image_cls(file=str(path))


def _make_file_component(path: Path, name: str | None = None) -> Any:
    file_cls = getattr(Comp, "File")
    return file_cls(file=str(path), name=name or path.name)


def _make_record_component(path: Path) -> Any:
    record_cls = getattr(Comp, "Record")
    return record_cls(file=str(path), url=str(path))


def _make_audio_component(path: Path) -> Any:
    audio_cls = getattr(Comp, "Audio", None)
    if audio_cls is None:
        return _make_record_component(path)
    if hasattr(audio_cls, "fromFileSystem"):
        return audio_cls.fromFileSystem(path=str(path))
    return audio_cls(file=str(path))


def _parse_timestamp(value: Any) -> int:
    if value is None:
        return int(time.time())
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return int(time.time())
    return int(time.time())


class WhatsAppBridgeRuntime:
    """
    管理 whatsapp-bridge 的生命週期、輪詢、媒體下載與發送。

    第一次啟動時，whatsapp-bridge 會在平台日誌輸出 QR Code。
    掃碼成功後，憑證會保存在 AstrBot data/whatsapp_creds 目錄中。
    """

    def __init__(self, config: dict[str, Any], self_id: str) -> None:
        self.settings = WhatsAppAdapterSettings.model_validate(config)
        self.self_id = self_id
        self.creds_dir = WHATSAPP_CREDS_DIR
        self.media_dir = WHATSAPP_MEDIA_DIR
        self.client: Any | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._start_lock = asyncio.Lock()
        self._message_handler: Any | None = None

        self.creds_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    async def update_config(self, config: dict[str, Any]) -> None:
        self.settings = WhatsAppAdapterSettings.model_validate(config)

    async def start(self, on_message: Any) -> None:
        async with self._start_lock:
            self._message_handler = on_message
            if self._runner_task and not self._runner_task.done():
                return
            self._stop_event.clear()
            self._runner_task = asyncio.create_task(self._run_forever(), name="whatsapp-adapter-poll")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._runner_task:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
        self._runner_task = None
        await self._disconnect()
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _run_forever(self) -> None:
        backoff = max(1.0, self.settings.reconnect_initial_sec)
        while not self._stop_event.is_set():
            try:
                await self._ensure_connected()
                backoff = max(1.0, self.settings.reconnect_initial_sec)
                while not self._stop_event.is_set():
                    messages = await self._bridge_call("get_new_messages", download_media=False)
                    for message in messages or []:
                        if message.get("is_from_me"):
                            continue
                        if self._message_handler is not None:
                            result = self._message_handler(message)
                            if inspect.isawaitable(result):
                                await result
                    await asyncio.sleep(self.settings.polling_interval_sec)
            except asyncio.CancelledError:
                raise
            except (
                ApiError,
                BridgeError,
                DbError,
                PrerequisitesError,
                SetupError,
                WhatsappError,
                ValidationError,
                RuntimeError,
                OSError,
            ) as exc:
                logger.error(f"WhatsApp 橋接異常，將於 {backoff:.1f}s 後重連: {exc}")
                logger.debug(traceback.format_exc())
                await self._disconnect()
                await asyncio.sleep(backoff)
                backoff = min(self.settings.reconnect_max_sec, max(backoff * 2, 1.0))
            except Exception as exc:
                logger.error(f"WhatsApp 未預期錯誤，將於 {backoff:.1f}s 後重連: {exc}")
                logger.debug(traceback.format_exc())
                await self._disconnect()
                await asyncio.sleep(backoff)
                backoff = min(self.settings.reconnect_max_sec, max(backoff * 2, 1.0))

    async def _ensure_connected(self) -> None:
        if self.client is None:
            self.client = WhatsappClient(
                data_dir=str(self.creds_dir),
                auto_setup=True,
                auto_connect=False,
                bridge_timeout_sec=self.settings.bridge_timeout_sec,
            )
        is_alive = False
        if hasattr(self.client, "is_bridge_alive"):
            try:
                is_alive = bool(await self._bridge_call("is_bridge_alive"))
            except Exception:
                is_alive = False
        if not is_alive:
            await self._bridge_call("connect")
            logger.info("WhatsApp 橋接已啟動，若為首次登入請前往平台日誌掃描 QR Code。")

    async def _disconnect(self) -> None:
        if self.client is None:
            return
        if hasattr(self.client, "disconnect"):
            with contextlib.suppress(Exception):
                await self._bridge_call("disconnect")

    async def _bridge_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.client is None:
            raise RuntimeError("WhatsApp client 尚未初始化")
        method = getattr(self.client, method_name)
        try:
            if inspect.iscoroutinefunction(method):
                return await asyncio.wait_for(
                    method(*args, **kwargs),
                    timeout=self.settings.bridge_timeout_sec,
                )
            return await asyncio.wait_for(
                asyncio.to_thread(functools.partial(method, *args, **kwargs)),
                timeout=self.settings.bridge_timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            logger.warning(f"WhatsApp 橋接呼叫逾時: {method_name}")
            raise RuntimeError("WhatsApp 橋接呼叫逾時") from exc

    async def _ensure_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=120)
        return self._http_client

    async def _write_bytes_to_temp(self, payload: bytes | bytearray, filename: str) -> PreparedMedia:
        suffix = Path(filename).suffix or ".bin"
        temp_path = self.media_dir / f"{uuid.uuid4().hex}{suffix}"
        temp_path.write_bytes(bytes(payload))
        return PreparedMedia(path=temp_path, temporary=True)

    async def _download_url_to_temp(self, url: str, filename_hint: str | None = None) -> PreparedMedia:
        client = await self._ensure_http_client()
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        filename = filename_hint or Path(url.split("?")[0]).name or uuid.uuid4().hex
        if "." not in filename:
            guessed = mimetypes.guess_extension(response.headers.get("content-type", "").split(";")[0].strip())
            if guessed:
                filename = f"{filename}{guessed}"
        return await self._write_bytes_to_temp(response.content, filename)

    async def _prepare_component_media(self, component: Any) -> PreparedMedia:
        filename = getattr(component, "name", None) or getattr(component, "filename", None)
        for attr in ("file", "path", "url", "data", "bytes", "content"):
            value = getattr(component, attr, None)
            if value is None:
                continue
            if isinstance(value, Path) and value.exists():
                return PreparedMedia(path=value)
            if isinstance(value, (bytes, bytearray)):
                return await self._write_bytes_to_temp(value, filename or f"{uuid.uuid4().hex}.bin")
            if isinstance(value, str):
                if _is_http_url(value):
                    return await self._download_url_to_temp(value, filename)
                candidate = Path(value).expanduser()
                if candidate.exists():
                    return PreparedMedia(path=candidate)
        raise FileNotFoundError(f"無法解析可發送媒體來源: {component}")

    async def send_text(self, recipient: str, text: str) -> None:
        if not text:
            return
        await self._ensure_connected()
        await self._bridge_call("send_message", recipient, text)

    async def send_typing(self, recipient: str) -> None:
        await self._ensure_connected()
        for name in ("send_typing", "typing", "set_typing", "send_presence"):
            if self.client is None or not hasattr(self.client, name):
                continue
            try:
                await self._bridge_call(name, recipient)
                return
            except Exception as exc:
                logger.debug(f"WhatsApp typing 呼叫失敗 {name}: {exc}")

    async def send_read_receipt(self, raw_message: dict[str, Any]) -> None:
        await self._ensure_connected()
        for name, arguments in (
            ("mark_read", (raw_message.get("id"), raw_message.get("chat_jid"))),
            ("send_read_receipt", (raw_message.get("id"), raw_message.get("chat_jid"))),
            ("acknowledge_message", (raw_message.get("id"), raw_message.get("chat_jid"))),
        ):
            if self.client is None or not hasattr(self.client, name):
                continue
            try:
                await self._bridge_call(name, *arguments)
                return
            except Exception as exc:
                logger.debug(f"WhatsApp 已讀回執呼叫失敗 {name}: {exc}")

    async def send_media(
        self,
        recipient: str,
        media: PreparedMedia,
        caption: str = "",
        as_voice: bool = False,
    ) -> None:
        await self._ensure_connected()
        size_mb = media.path.stat().st_size / 1024 / 1024
        if size_mb > self.settings.media_max_mb:
            logger.warning(
                f"媒體 {media.path.name} 大小為 {size_mb:.2f} MB，超過配置上限 {self.settings.media_max_mb:.2f} MB。"
            )
        if as_voice:
            for name in ("send_audio_message", "send_voice_message", "send_voice"):
                if self.client is None or not hasattr(self.client, name):
                    continue
                try:
                    await self._bridge_call(name, recipient, str(media.path))
                    return
                except Exception as exc:
                    logger.debug(f"WhatsApp 語音發送呼叫失敗 {name}: {exc}")
        await self._bridge_call("send_media", recipient, str(media.path), caption or "")

    async def send_chain(self, recipient: str, message_chain: MessageChain | list[Any] | tuple[Any, ...] | Any) -> None:
        components = _extract_chain_items(message_chain)
        buffered_text: list[str] = []

        async def flush_text() -> None:
            if not buffered_text:
                return
            text = "".join(buffered_text).strip()
            buffered_text.clear()
            if text:
                await self.send_text(recipient, text)

        for component in components:
            name = _safe_component_name(component)
            if name == "plain" or name == "at":
                buffered_text.append(_component_text(component))
                continue

            caption = "".join(buffered_text).strip()
            buffered_text.clear()
            prepared: PreparedMedia | None = None
            try:
                if name in {"image", "file", "record", "audio", "voice", "video"}:
                    prepared = await self._prepare_component_media(component)
                    await self.send_media(
                        recipient=recipient,
                        media=prepared,
                        caption=caption,
                        as_voice=name in {"record", "voice"},
                    )
                else:
                    fallback = _component_text(component)
                    if fallback:
                        buffered_text.append(fallback)
            finally:
                if prepared and prepared.temporary:
                    with contextlib.suppress(FileNotFoundError):
                        prepared.path.unlink()

        await flush_text()

    async def ensure_local_media(self, raw_message: dict[str, Any]) -> Path | None:
        existing = raw_message.get("local_media_path")
        if existing and Path(existing).exists():
            return Path(existing)

        if self.client is None or not hasattr(self.client, "download_media_manual"):
            return None

        path = await self._bridge_call(
            "download_media_manual",
            raw_message.get("id"),
            raw_message.get("chat_jid"),
        )
        if not path:
            return None
        local_path = Path(path)
        raw_message["local_media_path"] = str(local_path)
        return local_path


class WhatsAppMessageEvent(AstrMessageEvent):
    """
    AstrBot 的 WhatsApp 事件包裝。

    send() 直接把 AstrBot 的 MessageChain 轉回 WhatsApp，
    並在第一次正式回覆前可選地發送 typing 與預回覆表情。
    """

    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        session_id: str,
        adapter: "WhatsAppPlatformAdapter",
    ) -> None:
        super().__init__(message_str, message_obj, WHATSAPP_PLATFORM_META, session_id)
        self.adapter = adapter
        self._prelude_sent = False

    async def _send_prelude_if_needed(self) -> None:
        if self._prelude_sent:
            return
        self._prelude_sent = True
        if self.adapter.settings.typing_indicator:
            await self.adapter.runtime.send_typing(self.message_obj.session_id)
        emoji = self.adapter.settings.pre_reply_emoji.strip()
        if emoji:
            await self.adapter.runtime.send_text(self.message_obj.session_id, emoji)

    async def send(self, message: MessageChain | list[Any] | tuple[Any, ...] | Any) -> None:
        await self._send_prelude_if_needed()
        await self.adapter.runtime.send_chain(self.message_obj.session_id, message)

    async def typing(self) -> None:
        await self.adapter.runtime.send_typing(self.message_obj.session_id)

    async def send_pre_reply(self) -> None:
        await self._send_prelude_if_needed()


@register_platform_adapter(
    "whatsapp",
    "基於 whatsapp-bridge 的 WhatsApp 非官方多裝置平台適配器",
    default_config_tmpl=DEFAULT_CONFIG,
    adapter_display_name="WhatsApp",
    support_streaming_message=True,
    config_metadata=CONFIG_METADATA,
)
class WhatsAppPlatformAdapter(Platform):
    def __init__(self, config: dict[str, Any], event_queue: asyncio.Queue) -> None:
        super().__init__(config, event_queue)
        self.settings = WhatsAppAdapterSettings.model_validate(config)
        self.runtime = WhatsAppBridgeRuntime(config, self.client_self_id)
        self._stop_event = asyncio.Event()

    def meta(self) -> PlatformMetadata:
        return WHATSAPP_PLATFORM_META

    def get_client(self) -> object:
        return self.runtime.client

    async def run(self) -> None:
        self._stop_event.clear()
        await self.runtime.update_config(self.config)
        await self.runtime.start(self._on_message)
        logger.info(
            f"WhatsApp 平台適配器已啟動，憑證目錄={self.runtime.creds_dir}，媒體目錄={self.runtime.media_dir}"
        )
        while not self._stop_event.is_set():
            await asyncio.sleep(3600)

    async def terminate(self) -> None:
        self._stop_event.set()
        await self.runtime.stop()

    async def send_by_session(self, session: Any, message_chain: MessageChain) -> None:
        await self.runtime.send_chain(session.session_id, message_chain)

    def _is_private_chat(self, raw_message: dict[str, Any]) -> bool:
        chat_jid = str(raw_message.get("chat_jid", ""))
        return not chat_jid.endswith("@g.us")

    def _allowlist_match(self, raw_message: dict[str, Any]) -> bool:
        if not self.settings.allowlist_set:
            return True
        candidates = {
            str(raw_message.get("chat_jid", "")),
            str(raw_message.get("sender", "")),
            str(raw_message.get("session_id", "")),
        }
        return any(candidate and candidate in self.settings.allowlist_set for candidate in candidates)

    def _should_accept(self, raw_message: dict[str, Any]) -> bool:
        is_private = self._is_private_chat(raw_message)
        if is_private and self.settings.dm_policy == "deny":
            return False
        if is_private and self.settings.dm_policy == "allowlist_only":
            return self._allowlist_match(raw_message)
        return self._allowlist_match(raw_message)

    async def _dispatch_event(self, event: WhatsAppMessageEvent) -> None:
        global _PLUGIN_CONTEXT
        if _PLUGIN_CONTEXT and hasattr(_PLUGIN_CONTEXT, "send_event"):
            try:
                result = _PLUGIN_CONTEXT.send_event(event)
                if inspect.isawaitable(result):
                    await result
                return
            except Exception as exc:
                logger.warning(f"context.send_event 失敗，改走平台事件佇列: {exc}")
        self.commit_event(event)

    async def _on_message(self, raw_message: dict[str, Any]) -> None:
        if not self._should_accept(raw_message):
            logger.debug(f"WhatsApp 訊息被策略過濾: {raw_message}")
            return

        event = await self._raw_message_to_event(raw_message)
        if event is None:
            return

        await self._dispatch_event(event)

        if self.settings.send_read_receipts:
            await self.runtime.send_read_receipt(raw_message)

    async def _raw_message_to_event(self, raw_message: dict[str, Any]) -> WhatsAppMessageEvent | None:
        chat_jid = str(raw_message.get("chat_jid", "")).strip()
        sender_id = str(raw_message.get("sender", "") or chat_jid).strip()
        if not chat_jid:
            logger.warning(f"忽略缺少 chat_jid 的 WhatsApp 訊息: {raw_message}")
            return None

        components: list[Any] = []
        text = str(raw_message.get("content") or "")
        if text:
            components.append(Comp.Plain(text))

        media_type = str(raw_message.get("media_type") or "").lower().strip()
        if media_type:
            local_path = await self.runtime.ensure_local_media(raw_message)
            if local_path and local_path.exists():
                size_mb = local_path.stat().st_size / 1024 / 1024
                if size_mb > self.settings.media_max_mb:
                    logger.warning(
                        f"接收媒體 {local_path.name} 大小為 {size_mb:.2f} MB，超過配置上限 {self.settings.media_max_mb:.2f} MB。"
                    )
                if media_type == "image":
                    components.append(_make_image_component(local_path))
                elif media_type in {"document", "file"}:
                    components.append(_make_file_component(local_path, raw_message.get("filename")))
                elif media_type == "audio":
                    filename = str(raw_message.get("filename") or local_path.name).lower()
                    if filename.endswith((".ogg", ".opus")):
                        components.append(_make_record_component(local_path))
                    else:
                        components.append(_make_audio_component(local_path))
                else:
                    components.append(_make_file_component(local_path, raw_message.get("filename")))

        message_obj = AstrBotMessage()
        message_obj.type = (
            MessageType.GROUP_MESSAGE if chat_jid.endswith("@g.us") else MessageType.FRIEND_MESSAGE
        )
        message_obj.self_id = self.client_self_id
        message_obj.session_id = chat_jid
        message_obj.message_id = str(raw_message.get("id") or uuid.uuid4().hex)
        message_obj.sender = MessageMember(user_id=sender_id, nickname=raw_message.get("sender_name") or sender_id)
        message_obj.message = components
        message_obj.message_str = text
        message_obj.raw_message = raw_message
        message_obj.timestamp = _parse_timestamp(raw_message.get("timestamp"))

        if chat_jid.endswith("@g.us"):
            message_obj.group = Group(group_id=chat_jid, group_name=raw_message.get("chat_name"))

        return WhatsAppMessageEvent(
            message_str=message_obj.message_str,
            message_obj=message_obj,
            session_id=chat_jid,
            adapter=self,
        )


@register(
    "astrbot_plugin_whatsapp_adapter",
    "casama233",
    "WhatsApp 平台適配器插件，使用 whatsapp-bridge 非官方多裝置 gateway。",
    "0.1.0",
    "https://github.com/casama233/astrbot_plugin_whatsapp_adapter",
)
class WhatsAppAdapterPlugin(Star):
    """
    這個 Star 插件負責把平台適配器模組注入 AstrBot，並保留 Context 供事件分發時使用。

    啟用方式：
    1. 安裝 requirements.txt 內依賴。
    2. 在 AstrBot WebUI 新增 WhatsApp 平台適配器。
    3. 保存並啟用後，前往平台日誌掃描 QR Code。
    4. 憑證將保存於 data/whatsapp_creds，熱重載後會自動復用。
    """

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        global _PLUGIN_CONTEXT
        _PLUGIN_CONTEXT = context
        WHATSAPP_CREDS_DIR.mkdir(parents=True, exist_ok=True)
        WHATSAPP_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        self.bridge_bootstrap = {
            "client_class": WhatsappClient,
            "creds_dir": str(WHATSAPP_CREDS_DIR),
            "media_dir": str(WHATSAPP_MEDIA_DIR),
        }

    async def initialize(self) -> None:
        logger.info(
            "WhatsApp 插件已載入。請在 WebUI 的平台頁新增 WhatsApp，首次啟用時到平台日誌掃碼登入。"
        )

    async def terminate(self) -> None:
        global _PLUGIN_CONTEXT
        if _PLUGIN_CONTEXT is self.context:
            _PLUGIN_CONTEXT = None
