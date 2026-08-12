"""WhatsApp PN/LID identity normalization and isolated mapping storage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


PN_DOMAINS = ("@s.whatsapp.net", "@hosted")
LID_DOMAINS = ("@lid", "@hosted.lid")
_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAPPING_FILE_PATTERN = re.compile(r"^lid-mapping-(\d+)_reverse\.json$")


def _split_jid(value: str | None) -> tuple[str, str]:
    raw = str(value or "").strip()
    if "@" not in raw:
        return raw, ""
    user, domain = raw.rsplit("@", 1)
    return user, f"@{domain.lower()}"


def identity_user(value: str | None) -> str:
    """Return a device-independent WhatsApp user portion."""

    user, _domain = _split_jid(value)
    return user.split(":", 1)[0]


def is_pn_jid(value: str | None) -> bool:
    _user, domain = _split_jid(value)
    return domain in PN_DOMAINS


def is_lid_jid(value: str | None) -> bool:
    _user, domain = _split_jid(value)
    return domain in LID_DOMAINS


def normalize_user_jid(value: str | None) -> str:
    """Strip a Baileys device suffix while preserving the identity domain."""

    user, domain = _split_jid(value)
    base_user = user.split(":", 1)[0]
    return f"{base_user}{domain}" if base_user and domain else str(value or "").strip()


def base_pn_jid(value: str | None) -> str:
    raw = str(value or "").strip()
    user, domain = _split_jid(raw)
    user = user.split(":", 1)[0]
    if not user:
        return ""
    if domain in PN_DOMAINS:
        return f"{user}{domain}"
    if domain:
        return ""
    digits = re.sub(r"\D", "", user)
    return f"{digits}@s.whatsapp.net" if digits else ""


def base_lid_jid(value: str | None) -> str:
    raw = str(value or "").strip()
    user, domain = _split_jid(raw)
    user = user.split(":", 1)[0]
    if not user:
        return ""
    if domain in LID_DOMAINS:
        return f"{user}{domain}"
    if domain:
        return ""
    digits = re.sub(r"\D", "", user)
    return f"{digits}@lid" if digits else ""


def phone_from_identity(value: str | None) -> str:
    """Return an E.164-like number without appending a JID device suffix."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    source = identity_user(raw) if "@" in raw else raw
    digits = re.sub(r"\D", "", source)
    return f"+{digits}" if digits else ""


def public_numeric_id(value: str | None) -> str:
    """Return the QQ-compatible public ID for a WhatsApp identity.

    Transport domains and Baileys device suffixes must never become part of an
    AstrBot ``session_id``.  Keep the original JID in ``raw_message`` and use
    only this public projection for UMO, sender, self and group identifiers.
    """

    user = identity_user(value)
    digits = re.sub(r"\D", "", user)
    return digits or user


def normalize_group_session_id(value: str | None) -> str:
    """Normalize current and legacy group session IDs to a public group ID.

    Accepted legacy forms include ``group@g.us`` and
    ``sender_group@g.us``.  New canonical group sessions contain neither the
    sender nor WhatsApp's transport domain.
    """

    raw = str(value or "").strip()
    if raw.endswith("@g.us"):
        raw = raw[: -len("@g.us")]
    if "_" in raw:
        raw = raw.rsplit("_", 1)[-1]
    return public_numeric_id(raw)


def build_umo_session_id(
    *,
    is_group: bool,
    group_id: str | None,
    user_id: str | None,
    unique_session: bool,
) -> str:
    """Build one canonical AstrBot session ID using the QQ adapter contract."""

    public_user_id = public_numeric_id(user_id)
    if not is_group:
        return public_user_id

    public_group_id = normalize_group_session_id(group_id)
    if unique_session and public_user_id:
        return f"{public_user_id}_{public_group_id}"
    return public_group_id


def delivery_jid_from_session_id(
    value: str | None,
    *,
    is_group: bool,
) -> str:
    """Resolve canonical or legacy UMO session IDs back to transport JIDs."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_group:
        group_id = normalize_group_session_id(raw)
        return f"{group_id}@g.us" if group_id else raw
    if is_pn_jid(raw) or is_lid_jid(raw):
        return normalize_user_jid(raw)
    public_id = public_numeric_id(raw)
    return base_pn_jid(public_id) or raw


@dataclass(slots=True)
class IdentityMappingCache:
    """A mapping cache owned by one adapter/account runtime."""

    lid_to_pn: dict[str, str] = field(default_factory=dict)
    pn_to_lid: dict[str, str] = field(default_factory=dict)

    def clear(self) -> None:
        self.lid_to_pn.clear()
        self.pn_to_lid.clear()

    def remember(self, lid_jid: str | None, pn_jid: str | None) -> bool:
        lid = base_lid_jid(lid_jid)
        pn = base_pn_jid(pn_jid)
        if not lid or not pn or not is_lid_jid(lid) or not is_pn_jid(pn):
            return False
        previous_pn = self.lid_to_pn.get(lid)
        if previous_pn and previous_pn != pn and self.pn_to_lid.get(previous_pn) == lid:
            self.pn_to_lid.pop(previous_pn, None)
        previous_lid = self.pn_to_lid.get(pn)
        if previous_lid and previous_lid != lid and self.lid_to_pn.get(previous_lid) == pn:
            self.lid_to_pn.pop(previous_lid, None)
        self.lid_to_pn[lid] = pn
        self.pn_to_lid[pn] = lid
        return True

    def pn_for_lid(self, lid_jid: str | None) -> str:
        lid = base_lid_jid(lid_jid)
        if not lid:
            return ""
        exact = self.lid_to_pn.get(lid)
        if exact:
            return exact
        user = identity_user(lid)
        for domain in LID_DOMAINS:
            mapped = self.lid_to_pn.get(f"{user}{domain}")
            if mapped:
                return mapped
        return ""

    def lid_for_pn(self, pn_jid: str | None) -> str:
        pn = base_pn_jid(pn_jid)
        if not pn:
            return ""
        exact = self.pn_to_lid.get(pn)
        if exact:
            return exact
        user = identity_user(pn)
        for domain in PN_DOMAINS:
            mapped = self.pn_to_lid.get(f"{user}{domain}")
            if mapped:
                return mapped
        return ""

    def __len__(self) -> int:
        return len(self.lid_to_pn)


def active_auth_session_dir(auth_root: Path) -> Path:
    """Resolve the Gateway's active isolated auth directory, or legacy root."""

    root = Path(auth_root)
    pointer = root / ".active-session.json"
    try:
        payload = json.loads(pointer.read_text("utf-8"))
        session_id = str(payload.get("sessionId") or "")
        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            return root
        sessions_root = (root / ".sessions").resolve()
        candidate = (sessions_root / session_id).resolve()
        candidate.relative_to(sessions_root)
        if candidate.is_dir():
            return candidate
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return root


def load_lid_mappings(auth_root: Path, cache: IdentityMappingCache) -> int:
    """Load only the active account's persisted reverse mappings."""

    cache.clear()
    directory = active_auth_session_dir(auth_root)
    if not directory.is_dir():
        return 0
    loaded = 0
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    for entry in entries:
        match = _MAPPING_FILE_PATTERN.fullmatch(entry.name)
        if not match:
            continue
        try:
            payload = json.loads(entry.read_text("utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if not isinstance(payload, str):
            continue
        pn_user = identity_user(payload) if "@" in payload else re.sub(r"\D", "", payload)
        if pn_user and cache.remember(
            f"{match.group(1)}@lid",
            f"{pn_user}@s.whatsapp.net",
        ):
            loaded += 1
    return loaded


def save_lid_mapping(
    auth_root: Path,
    lid_jid: str | None,
    pn_jid: str | None,
) -> None:
    """Persist a mapping in the Gateway's active auth session layout."""

    lid_user = identity_user(lid_jid)
    pn_user = identity_user(pn_jid)
    if not lid_user.isdigit() or not pn_user.isdigit():
        return
    directory = active_auth_session_dir(auth_root)
    path = directory / f"lid-mapping-{lid_user}_reverse.json"
    if path.exists():
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pn_user), "utf-8")
    except OSError:
        pass
