from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

_GROUP_NAME_KEYS = (
    "groupName",
    "group_name",
    "groupSubject",
    "group_subject",
)
_TRANSPORT_USER_PATTERN = re.compile(r"^(\d+)(?:_\d+)?(?::\d+)?$")


def _identity_parts(
    value: Any,
    *,
    jid: Any = "",
    pn_jid: Any = "",
    lid_jid: Any = "",
) -> tuple[str, str, str]:
    raw = str(value or jid or pn_jid or lid_jid or "").strip()
    pn = str(pn_jid or "").strip()
    lid = str(lid_jid or "").strip()
    for candidate in (str(jid or "").strip(), raw):
        if candidate.endswith(("@s.whatsapp.net", "@hosted")) and not pn:
            pn = candidate
        elif candidate.endswith(("@lid", "@hosted.lid")) and not lid:
            lid = candidate
    return raw, pn, lid


def _fallback_public_identity(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "@" not in raw:
        return raw if raw.isdigit() or re.fullmatch(r"lid-\d+", raw) else ""
    local, domain = raw.rsplit("@", 1)
    match = _TRANSPORT_USER_PATTERN.fullmatch(local)
    if not match:
        return ""
    user = match.group(1)
    normalized_domain = domain.lower()
    if normalized_domain in {"lid", "hosted.lid"}:
        return f"lid-{user}"
    if normalized_domain in {"s.whatsapp.net", "hosted"}:
        return user
    return ""


def _project_identity(
    value: Any,
    projector: Callable[..., str] | None,
    *,
    jid: Any = "",
    pn_jid: Any = "",
    lid_jid: Any = "",
) -> str:
    raw, pn, lid = _identity_parts(
        value,
        jid=jid,
        pn_jid=pn_jid,
        lid_jid=lid_jid,
    )
    if callable(projector):
        try:
            projected = projector(
                pn or lid or raw,
                lid_jid=lid or None,
                pn_jid=pn or None,
            )
        except TypeError:
            projected = projector(pn or lid or raw)
        if projected:
            return str(projected)
    return _fallback_public_identity(pn or lid or raw)


def _admin_identity_records(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    records = data.get("groupAdminIdentities")
    if isinstance(records, (list, tuple)):
        return [dict(item) for item in records if isinstance(item, Mapping)]

    jids = data.get("groupAdminJids") or []
    pn_jids = data.get("groupAdminPnJids") or []
    if isinstance(jids, (list, tuple)) or isinstance(pn_jids, (list, tuple)):
        jid_values = list(jids) if isinstance(jids, (list, tuple)) else []
        pn_values = list(pn_jids) if isinstance(pn_jids, (list, tuple)) else []
        return [
            {
                "jid": jid_values[index] if index < len(jid_values) else "",
                "pnJid": pn_values[index] if index < len(pn_values) else "",
            }
            for index in range(max(len(jid_values), len(pn_values)))
        ]
    return []


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


def apply_group_name(
    message: Any,
    data: dict[str, Any] | None,
    projector: Callable[..., str] | None = None,
) -> Any:
    """Populate AstrBot's Group model and legacy raw-message aliases."""
    if message is None:
        return None

    group_name = extract_group_name(data)
    group = getattr(message, "group", None)
    if group is not None:
        if group_name:
            group.group_name = group_name
        if isinstance(data, dict):
            owner = _project_identity(
                data.get("groupOwner"),
                projector,
                jid=data.get("groupOwnerJid"),
                pn_jid=data.get("groupOwnerPnJid"),
            )
            admin_records = _admin_identity_records(data)
            admins = data.get("groupAdmins") or []
            if admin_records:
                admins = [
                    _project_identity(
                        record.get("userId"),
                        projector,
                        jid=record.get("jid"),
                        pn_jid=record.get("pnJid"),
                        lid_jid=record.get("lidJid"),
                    )
                    for record in admin_records
                ]
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
    if isinstance(raw_message, dict) and group is not None:
        if getattr(group, "group_owner", None):
            raw_message["groupOwner"] = str(group.group_owner)
        if getattr(group, "group_admins", None) is not None:
            raw_message["groupAdmins"] = list(group.group_admins or [])

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
