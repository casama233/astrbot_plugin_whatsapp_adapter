from __future__ import annotations

from pathlib import Path as _Path

from astrbot.api.platform import Group as _Group
from astrbot.api.platform import MessageMember as _MessageMember

from .group_name_compat import current_event_group as _current_event_group

_impl_path = _Path(__file__).with_name("_whatsapp_event_impl.py")
exec(compile(_impl_path.read_text(encoding="utf-8"), str(_impl_path), "exec"), globals(), globals())


def _public_id(value) -> str:
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text


def _group_lookup(group_id) -> tuple[str, str] | None:
    requested = str(group_id or "").strip()
    if requested.endswith("@g.us"):
        public_id = _public_id(requested)
        return (public_id, f"{public_id}@g.us") if public_id else None
    if requested.isdigit():
        return requested, f"{requested}@g.us"
    return None


def _group_from_gateway(group_jid: str, info: dict) -> _Group:
    owner = _public_id(info.get("owner"))
    admins: list[str] = []
    for value in info.get("admins") or []:
        admin_id = _public_id(value)
        if not admin_id or admin_id == owner or admin_id in admins:
            continue
        admins.append(admin_id)

    members: list[_MessageMember] = []
    seen_members: set[str] = set()
    for item in info.get("participants") or []:
        if not isinstance(item, dict):
            continue
        user_id = _public_id(
            item.get("userId")
            or item.get("pnJid")
            or item.get("jid"),
        )
        if not user_id or user_id in seen_members:
            continue
        seen_members.add(user_id)
        members.append(
            _MessageMember(
                user_id=user_id,
                nickname=str(item.get("name") or user_id),
            ),
        )

    return _Group(
        group_id=_public_id(info.get("groupId") or group_jid),
        group_name=str(info.get("subject") or ""),
        group_avatar="",
        group_owner=owner or None,
        group_admins=admins,
        members=members,
    )


async def _get_group_compat(self, group_id=None, **kwargs):
    del kwargs
    if group_id is None or not str(group_id).strip():
        return _current_event_group(self)

    lookup = _group_lookup(group_id)
    if lookup is None:
        return None
    _public_group_id, group_jid = lookup
    info = await self.client.group_info(group_jid)
    return _group_from_gateway(group_jid, dict(info or {}))


WhatsAppMessageEvent.get_group = _get_group_compat
