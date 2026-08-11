"""Validated, current-conversation-only transports for WhatsApp LLM tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable


class WhatsAppToolRejected(ValueError):
    """The requested tool call is invalid or is not bound to a WhatsApp event."""


_DIRECT_JID_SUFFIXES = (
    "@s.whatsapp.net",
    "@lid",
    "@hosted",
    "@hosted.lid",
)
_ALLOWED_TARGET_SUFFIXES = ("@g.us", *_DIRECT_JID_SUFFIXES)


def _clean_text(
    value: Any,
    label: str,
    *,
    maximum: int,
    required: bool = True,
) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if required and not text:
        raise WhatsAppToolRejected(f"{label}不能为空")
    if len(text) > maximum:
        raise WhatsAppToolRejected(f"{label}不能超过 {maximum} 个字符")
    return text


def _current_transport(
    event: Any,
    method_name: str,
) -> tuple[str, Callable[..., Awaitable[dict[str, Any]]]]:
    try:
        platform_name = str(event.get_platform_name() or "")
    except Exception as exc:
        raise WhatsAppToolRejected("只能在当前 WhatsApp 会话中使用此工具") from exc
    if platform_name != "whatsapp":
        raise WhatsAppToolRejected("只能在当前 WhatsApp 会话中使用此工具")

    target = str(getattr(event, "target_jid", "") or "").strip()
    message = getattr(event, "message_obj", None)
    raw = getattr(message, "raw_message", None)
    raw_target = str(raw.get("chatJid") or "").strip() if isinstance(raw, dict) else ""
    if (
        not target
        or target != raw_target
        or any(character.isspace() for character in target)
        or not target.endswith(_ALLOWED_TARGET_SUFFIXES)
    ):
        raise WhatsAppToolRejected("无法确认当前 WhatsApp 会话目标，已拒绝发送")

    client = getattr(event, "client", None)
    sender = getattr(client, method_name, None)
    if not callable(sender):
        raise WhatsAppToolRejected("当前 WhatsApp 适配器不支持此原生能力")
    return target, sender


async def _finish_success(event: Any) -> None:
    # Invoke the public AstrBot base bookkeeping only after the Gateway call
    # succeeds. Calling the base implementation explicitly avoids sending a
    # second visible message while retaining metrics and _has_send_oper.
    from astrbot.api.event import AstrMessageEvent, MessageChain

    await AstrMessageEvent.send(event, MessageChain())
    if hasattr(event, "_super_sent"):
        event._super_sent = True
    complete_pre_ack = getattr(event, "_complete_pre_ack", None)
    if callable(complete_pre_ack):
        await complete_pre_ack()


async def _clear_failed_pre_ack(event: Any) -> None:
    clear_pre_ack = getattr(event, "_clear_pre_ack", None)
    if callable(clear_pre_ack):
        await clear_pre_ack()


async def _deliver(
    event: Any,
    method_name: str,
    **payload: Any,
) -> dict[str, Any]:
    target, sender = _current_transport(event, method_name)
    try:
        result = await sender(target, **payload)
        if not isinstance(result, dict) or result.get("ok") is False:
            raise RuntimeError("WhatsApp Gateway 未确认原生消息投递成功")
    except Exception:
        await _clear_failed_pre_ack(event)
        raise
    await _finish_success(event)
    return result


def normalize_poll(
    question: Any,
    options: Any,
    selectable_count: Any = 1,
) -> tuple[str, list[str], int]:
    name = _clean_text(question, "投票问题", maximum=255)
    if not isinstance(options, (list, tuple)):
        raise WhatsAppToolRejected("投票选项必须是字符串数组")
    values = [_clean_text(item, "投票选项", maximum=100) for item in options]
    if not 2 <= len(values) <= 12:
        raise WhatsAppToolRejected("投票选项数量必须在 2 到 12 之间")
    if len({value.casefold() for value in values}) != len(values):
        raise WhatsAppToolRejected("投票选项不能重复")
    if isinstance(selectable_count, bool):
        raise WhatsAppToolRejected("可选数量必须是整数")
    try:
        count = int(selectable_count)
    except (TypeError, ValueError) as exc:
        raise WhatsAppToolRejected("可选数量必须是整数") from exc
    if count != selectable_count or not 0 <= count <= len(values):
        raise WhatsAppToolRejected(
            f"可选数量必须在 0 到 {len(values)} 之间（0 表示多选）",
        )
    return name, values, count


async def create_poll(
    event: Any,
    question: Any,
    options: Any,
    selectable_count: Any = 1,
) -> dict[str, Any]:
    name, values, count = normalize_poll(question, options, selectable_count)
    return await _deliver(
        event,
        "send_poll",
        name=name,
        options=values,
        selectable_count=count,
    )


def normalize_contact(
    display_name: Any,
    phone_number: Any,
    organization: Any = "",
) -> tuple[str, str, str]:
    name = _clean_text(display_name, "联系人姓名", maximum=100)
    raw_phone = str(phone_number or "").strip()
    if raw_phone.startswith("+"):
        raw_phone = raw_phone[1:]
    digits = "".join(character for character in raw_phone if character.isdigit())
    # Reject punctuation-heavy or otherwise ambiguous phone strings instead of
    # silently turning arbitrary model output into a different number.
    allowed_phone_chars = set("0123456789 +-()")
    if any(character not in allowed_phone_chars for character in str(phone_number or "")):
        raise WhatsAppToolRejected("联系人号码格式无效")
    if not 7 <= len(digits) <= 15:
        raise WhatsAppToolRejected("联系人号码必须包含 7 到 15 位数字")
    org = _clean_text(
        organization,
        "联系人组织",
        maximum=100,
        required=False,
    )
    return name, f"+{digits}", org


async def share_contact(
    event: Any,
    display_name: Any,
    phone_number: Any,
    organization: Any = "",
) -> dict[str, Any]:
    name, phone, org = normalize_contact(display_name, phone_number, organization)
    return await _deliver(
        event,
        "send_contact",
        display_name=name,
        phone_number=phone,
        organization=org,
    )


def _parse_datetime(value: Any, label: str) -> datetime:
    raw = _clean_text(value, label, maximum=64)
    if raw.endswith(("Z", "z")):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WhatsAppToolRejected(f"{label}必须是 ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WhatsAppToolRejected(f"{label}必须包含时区偏移")
    return parsed.astimezone(timezone.utc)


def normalize_event(
    name: Any,
    start_time: Any,
    end_time: Any = "",
    description: Any = "",
    location_name: Any = "",
    location_address: Any = "",
    extra_guests_allowed: Any = False,
) -> dict[str, Any]:
    event_name = _clean_text(name, "活动名称", maximum=100)
    start = _parse_datetime(start_time, "开始时间")
    end = _parse_datetime(end_time, "结束时间") if str(end_time or "").strip() else None
    if end is not None and end <= start:
        raise WhatsAppToolRejected("结束时间必须晚于开始时间")
    if end is not None and end - start > timedelta(days=366):
        raise WhatsAppToolRejected("活动持续时间不能超过 366 天")
    if not isinstance(extra_guests_allowed, bool):
        raise WhatsAppToolRejected("是否允许额外来宾必须是布尔值")
    return {
        "name": event_name,
        "start_timestamp_ms": int(start.timestamp() * 1000),
        "end_timestamp_ms": int(end.timestamp() * 1000) if end else None,
        "description": _clean_text(
            description,
            "活动说明",
            maximum=2048,
            required=False,
        ),
        "location_name": _clean_text(
            location_name,
            "地点名称",
            maximum=200,
            required=False,
        ),
        "location_address": _clean_text(
            location_address,
            "地点地址",
            maximum=500,
            required=False,
        ),
        "extra_guests_allowed": extra_guests_allowed,
    }


async def create_event(
    event: Any,
    name: Any,
    start_time: Any,
    end_time: Any = "",
    description: Any = "",
    location_name: Any = "",
    location_address: Any = "",
    extra_guests_allowed: Any = False,
) -> dict[str, Any]:
    payload = normalize_event(
        name,
        start_time,
        end_time,
        description,
        location_name,
        location_address,
        extra_guests_allowed,
    )
    return await _deliver(event, "send_event", **payload)


__all__ = [
    "WhatsAppToolRejected",
    "create_event",
    "create_poll",
    "normalize_contact",
    "normalize_event",
    "normalize_poll",
    "share_contact",
]
