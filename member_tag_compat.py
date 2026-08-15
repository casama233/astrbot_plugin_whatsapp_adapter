from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_member_tag(value: Any) -> str:
    """Normalize a WhatsApp group member tag for public adapter fields."""
    return str(value or "").strip()


def extract_sender_member_tag(data: Mapping[str, Any] | None) -> str:
    """Read the sender's group member tag from Gateway or raw sender aliases."""
    if not isinstance(data, Mapping):
        return ""

    if "senderMemberTag" in data:
        return normalize_member_tag(data.get("senderMemberTag"))

    sender = data.get("sender")
    if isinstance(sender, Mapping):
        if "member_tag" in sender:
            return normalize_member_tag(sender.get("member_tag"))
        if "memberTag" in sender:
            return normalize_member_tag(sender.get("memberTag"))
    return ""


def apply_sender_member_tag(message: Any, data: Mapping[str, Any] | None) -> Any:
    """Expose a group sender tag without mixing it with nickname or permissions."""
    if message is None:
        return None

    group = getattr(message, "group", None)
    has_transport_field = isinstance(data, Mapping) and "senderMemberTag" in data
    if group is None and not has_transport_field:
        return message

    member_tag = extract_sender_member_tag(data)
    sender = getattr(message, "sender", None)
    if sender is not None:
        setattr(sender, "member_tag", member_tag)

    raw_message = getattr(message, "raw_message", None)
    if isinstance(raw_message, dict):
        raw_message["senderMemberTag"] = member_tag
        raw_sender = raw_message.get("sender")
        if isinstance(raw_sender, dict):
            raw_sender["member_tag"] = member_tag
            raw_sender["memberTag"] = member_tag

    return message


def apply_group_member_tag(member: Any, item: Mapping[str, Any] | None) -> Any:
    """Attach a Gateway participant tag to an AstrBot MessageMember instance."""
    if member is None:
        return None
    value = item.get("memberTag") if isinstance(item, Mapping) else ""
    setattr(member, "member_tag", normalize_member_tag(value))
    return member
