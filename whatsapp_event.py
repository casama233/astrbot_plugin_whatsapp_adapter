from __future__ import annotations

from pathlib import Path as _Path

from astrbot.api.platform import Group as _Group
from astrbot.api.platform import MessageMember as _MessageMember

from .group_name_compat import current_event_group as _current_event_group
from .member_tag_compat import apply_group_member_tag as _apply_group_member_tag
from .whatsapp_identity import (
    normalize_group_session_id as _normalize_group_session_id,
    strict_public_id as _strict_public_id,
)
from .whatsapp_streaming_concurrency import (
    response_presence_leases as _response_presence_leases,
)

_impl_path = _Path(__file__).with_name("_whatsapp_event_impl.py")
exec(compile(_impl_path.read_text(encoding="utf-8"), str(_impl_path), "exec"), globals(), globals())


def _public_id(value) -> str:
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text


def _project_member_identity(item, projector=None, *, defer_persist: bool = False) -> str:
    if isinstance(item, dict):
        jid = str(item.get("jid") or "")
        pn_jid = str(item.get("pnJid") or "")
        lid_jid = str(item.get("lidJid") or "")
        value = item.get("userId") or pn_jid or lid_jid or jid
    else:
        jid = ""
        pn_jid = ""
        lid_jid = ""
        value = item

    raw = str(value or "").strip()
    for candidate in (jid, raw):
        if candidate.endswith(("@s.whatsapp.net", "@hosted")) and not pn_jid:
            pn_jid = candidate
        elif candidate.endswith(("@lid", "@hosted.lid")) and not lid_jid:
            lid_jid = candidate
    if callable(projector):
        kwargs = {
            "lid_jid": lid_jid or None,
            "pn_jid": pn_jid or None,
        }
        if defer_persist:
            kwargs["persist"] = False
        projected = projector(pn_jid or lid_jid or raw, **kwargs)
        if projected:
            return str(projected)
    return _strict_public_id(pn_jid or lid_jid or raw)


def _group_lookup(group_id) -> tuple[str, str] | None:
    requested = str(group_id or "").strip()
    public_id = _normalize_group_session_id(requested)
    return (public_id, f"{public_id}@g.us") if public_id else None


def _group_from_gateway(
    group_jid: str,
    info: dict,
    projector=None,
    *,
    defer_persist: bool = False,
) -> _Group:
    owner = _project_member_identity(
        {
            "userId": info.get("owner"),
            "jid": info.get("ownerJid"),
            "pnJid": info.get("ownerPnJid"),
        },
        projector,
        defer_persist=defer_persist,
    )
    admins: list[str] = []
    admin_records = info.get("adminIdentities") or []
    if not admin_records:
        admin_jids = info.get("adminJids") or []
        admin_pn_jids = info.get("adminPnJids") or []
        if admin_jids or admin_pn_jids:
            admin_records = [
                {
                    "jid": admin_jids[index] if index < len(admin_jids) else "",
                    "pnJid": admin_pn_jids[index] if index < len(admin_pn_jids) else "",
                }
                for index in range(max(len(admin_jids), len(admin_pn_jids)))
            ]
        else:
            admin_records = info.get("admins") or []
    for value in admin_records:
        admin_id = _project_member_identity(
            value,
            projector,
            defer_persist=defer_persist,
        )
        if not admin_id or admin_id == owner or admin_id in admins:
            continue
        admins.append(admin_id)

    members: list[_MessageMember] = []
    seen_members: set[str] = set()
    for item in info.get("participants") or []:
        if not isinstance(item, dict):
            continue
        user_id = _project_member_identity(
            item,
            projector,
            defer_persist=defer_persist,
        )
        if not user_id or user_id in seen_members:
            continue
        seen_members.add(user_id)
        member = _MessageMember(
            user_id=user_id,
            nickname=str(item.get("name") or user_id),
        )
        members.append(_apply_group_member_tag(member, item))

        role = str(item.get("role") or "")
        if role == "owner" and not owner:
            owner = user_id
        elif role == "admin" and user_id not in admins:
            admins.append(user_id)

    if owner in admins:
        admins.remove(owner)

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
    projector = getattr(self, "identity_projector", None)
    projector_owner = getattr(projector, "__self__", None)
    persist = getattr(projector_owner, "_persist_identity_projections", None)
    defer_persist = callable(persist)
    try:
        return _group_from_gateway(
            group_jid,
            dict(info or {}),
            projector,
            defer_persist=defer_persist,
        )
    finally:
        if defer_persist:
            persist()


_original_send = WhatsAppMessageEvent.send
_original_send_streaming = WhatsAppMessageEvent.send_streaming
_original_stop_typing = WhatsAppMessageEvent.stop_typing


def _acquire_response_presence(self) -> None:
    _response_presence_leases.acquire(self.client, self.target_jid)


def _release_response_presence(self) -> None:
    _response_presence_leases.release(self.client, self.target_jid)


async def _send_with_response_presence(self, *args, **kwargs):
    _acquire_response_presence(self)
    try:
        return await _original_send(self, *args, **kwargs)
    finally:
        _release_response_presence(self)


async def _send_streaming_with_response_presence(self, *args, **kwargs):
    _acquire_response_presence(self)
    try:
        return await _original_send_streaming(self, *args, **kwargs)
    finally:
        _release_response_presence(self)


async def _stop_typing_when_chat_idle(self) -> None:
    if not _response_presence_leases.should_pause(self.client, self.target_jid):
        return
    await _original_stop_typing(self)


WhatsAppMessageEvent.get_group = _get_group_compat
WhatsAppMessageEvent.send = _send_with_response_presence
WhatsAppMessageEvent.send_streaming = _send_streaming_with_response_presence
WhatsAppMessageEvent.stop_typing = _stop_typing_when_chat_idle
