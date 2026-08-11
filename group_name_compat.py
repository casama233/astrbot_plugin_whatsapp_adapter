from __future__ import annotations

from typing import Any

_GROUP_NAME_KEYS = (
    "groupName",
    "group_name",
    "groupSubject",
    "group_subject",
)


def extract_group_name(data: dict[str, Any] | None) -> str:
    """Return the first non-empty group-name alias from a Gateway event."""
    if not isinstance(data, dict):
        return ""
    for key in _GROUP_NAME_KEYS:
        value = data.get(key)
        if value is None:
            continue
        name = str(value).strip()
        if name:
            return name
    return ""


def apply_group_name(message: Any, data: dict[str, Any] | None) -> Any:
    """Populate AstrBot's Group model and legacy raw-message aliases."""
    if message is None:
        return None

    group_name = extract_group_name(data)
    group = getattr(message, "group", None)
    if group is not None:
        if group_name:
            group.group_name = group_name
        if isinstance(data, dict):
            owner = str(data.get("groupOwner") or "").strip()
            admins = data.get("groupAdmins") or []
            if owner:
                group.group_owner = owner
            effective_owner = owner or str(
                getattr(group, "group_owner", "") or "",
            ).strip()
            if isinstance(admins, (list, tuple, set)):
                normalized_admins: list[str] = []
                for item in admins:
                    admin_id = str(item).strip()
                    if (
                        not admin_id
                        or admin_id == effective_owner
                        or admin_id in normalized_admins
                    ):
                        continue
                    normalized_admins.append(admin_id)
                group.group_admins = normalized_admins

    raw_message = getattr(message, "raw_message", None)
    if isinstance(raw_message, dict) and group_name:
        raw_message.setdefault("groupName", group_name)
        raw_message.setdefault("group_name", group_name)
        raw_message.setdefault("groupSubject", group_name)

    return message


def current_event_group(event: Any, group_id: str | int | None = None) -> Any | None:
    """Return the current AstrBot Group for the standard event.get_group API."""
    message = getattr(event, "message_obj", None)
    group = getattr(message, "group", None)
    if group is None:
        return None

    if group_id is None or not str(group_id).strip():
        return group

    requested = str(group_id).strip()
    target_jid = str(getattr(event, "target_jid", "") or "").strip()
    candidates = {str(getattr(group, "group_id", "") or "").strip()}
    if target_jid:
        candidates.add(target_jid)
        candidates.add(target_jid.split("@", 1)[0])
    candidates.discard("")
    return group if requested in candidates else None
