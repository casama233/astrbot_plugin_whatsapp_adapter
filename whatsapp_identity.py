"""WhatsApp PN/LID identity normalization and isolated mapping storage.

WhatsApp exposes two user-address namespaces: phone-number JIDs (PN) and
opaque linked-device JIDs (LID).  Their numeric user portions are not
interchangeable.  This module therefore keeps transport JIDs domain-aware and
projects them to an explicit public grammar:

* ``<digits>`` for a phone-number identity;
* ``lid-<digits>`` for an unresolved LID identity; and
* ``<digits>`` for a WhatsApp group.

The public projection is persistent.  In particular, an LID that was exposed
before its PN mapping became available keeps its ``lid-`` ID after resolution,
while an LID learned after its PN identity reuses the existing numeric ID.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


PN_DOMAINS = ("@s.whatsapp.net", "@hosted")
LID_DOMAINS = ("@lid", "@hosted.lid")
GROUP_DOMAIN = "@g.us"

IDENTITY_STATE_VERSION = 1
LID_MAPPINGS_FILENAME = "astrbot-lid-mappings-v1.json"
IDENTITY_PROJECTIONS_FILENAME = "astrbot-identity-projections-v1.json"

_SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAPPING_FILE_PATTERN = re.compile(r"^lid-mapping-(\d+)_reverse\.json$")
_DIGITS_PATTERN = re.compile(r"^\d+$")
_DEVICE_USER_PATTERN = re.compile(r"^(\d+)(?:_\d+)?(?::\d+)?$")
_GROUP_LOCAL_PATTERN = re.compile(r"^\d+(?:-\d+)?$")
_PHONE_TEXT_PATTERN = re.compile(r"^\+?[\d\s().-]+$")
_LID_PUBLIC_ID_PATTERN = re.compile(r"^lid-(\d+)$")
_IDENTITY_KEY_PATTERN = re.compile(r"^(pn|lid):(\d+)$")
_UNIQUE_GROUP_SESSION_PATTERN = re.compile(
    r"^(?:\d+|lid-\d+)_(\d+(?:-\d+)?)$",
)


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


def _device_independent_user(value: str | None) -> str:
    """Return a numeric user only for ``digits[:device]`` transport shapes."""

    match = _DEVICE_USER_PATTERN.fullmatch(str(value or ""))
    return match.group(1) if match else ""


def is_pn_jid(value: str | None) -> bool:
    _user, domain = _split_jid(value)
    return domain in PN_DOMAINS


def is_lid_jid(value: str | None) -> bool:
    _user, domain = _split_jid(value)
    return domain in LID_DOMAINS


def normalize_user_jid(value: str | None) -> str:
    """Strip a Baileys device suffix while preserving the identity domain."""

    user, domain = _split_jid(value)
    base_user = _device_independent_user(user)
    return f"{base_user}{domain}" if base_user and domain else str(value or "").strip()


def _strict_digits(value: str | None) -> str:
    text = str(value or "").strip()
    return text if _DIGITS_PATTERN.fullmatch(text) else ""


def base_pn_jid(value: str | None) -> str:
    """Return one strict, device-independent phone-number JID.

    A bare number (optionally prefixed with ``+``) is interpreted as a PN.
    Arbitrary alphanumeric input is rejected instead of having its digits
    extracted, which prevents values such as ``abc123`` colliding with ``123``.
    """

    raw = str(value or "").strip()
    user, domain = _split_jid(raw)
    if domain:
        if domain not in PN_DOMAINS:
            return ""
        user = _device_independent_user(user)
        if not user:
            return ""
        return f"{user}{domain}"
    if user.startswith("+"):
        user = user[1:]
    return f"{user}@s.whatsapp.net" if _DIGITS_PATTERN.fullmatch(user) else ""


def base_lid_jid(value: str | None) -> str:
    """Return one strict, device-independent linked-device JID."""

    raw = str(value or "").strip()
    public_match = _LID_PUBLIC_ID_PATTERN.fullmatch(raw)
    if public_match:
        return f"{public_match.group(1)}@lid"
    user, domain = _split_jid(raw)
    if domain:
        if domain not in LID_DOMAINS:
            return ""
        user = _device_independent_user(user)
        if not user:
            return ""
        return f"{user}{domain}"
    return f"{user}@lid" if _DIGITS_PATTERN.fullmatch(user) else ""


def phone_from_identity(value: str | None) -> str:
    """Return an E.164-like number without confusing a LID for a phone."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    if "@" in raw:
        pn_jid = base_pn_jid(raw)
        return f"+{identity_user(pn_jid)}" if pn_jid else ""
    if not _PHONE_TEXT_PATTERN.fullmatch(raw):
        return ""
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if digits else ""


def _identity_key(value: str | None) -> str:
    """Return a namespace-qualified identity key for comparisons/storage."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    lid_public = _LID_PUBLIC_ID_PATTERN.fullmatch(raw)
    if lid_public:
        return f"lid:{lid_public.group(1)}"
    if _DIGITS_PATTERN.fullmatch(raw):
        return f"pn:{raw}"
    if raw.startswith("+") and _DIGITS_PATTERN.fullmatch(raw[1:]):
        return f"pn:{raw[1:]}"

    user, domain = _split_jid(raw)
    if domain in PN_DOMAINS or domain in LID_DOMAINS:
        user = _device_independent_user(user)
        if not user:
            return ""
    elif domain == GROUP_DOMAIN:
        return f"group:{user}" if _GROUP_LOCAL_PATTERN.fullmatch(user) else ""
    elif not _DIGITS_PATTERN.fullmatch(user):
        return ""
    if domain in PN_DOMAINS:
        return f"pn:{user}"
    if domain in LID_DOMAINS:
        return f"lid:{user}"
    return ""


def strict_public_id(value: str | None) -> str:
    """Project a valid transport/public identity without lossy digit scraping."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    lid_public = _LID_PUBLIC_ID_PATTERN.fullmatch(raw)
    if lid_public:
        return f"lid-{lid_public.group(1)}"
    if _DIGITS_PATTERN.fullmatch(raw):
        return raw
    if raw.startswith("+") and _DIGITS_PATTERN.fullmatch(raw[1:]):
        return raw[1:]

    key = _identity_key(raw)
    if key.startswith("pn:") or key.startswith("group:"):
        return key.split(":", 1)[1]
    if key.startswith("lid:"):
        return f"lid-{key.split(':', 1)[1]}"
    return ""


def public_numeric_id(value: str | None) -> str:
    """Compatibility name for :func:`strict_public_id`.

    The result is numeric for PN/group identities and explicitly ``lid-``
    prefixed for LIDs.  Despite the historical function name, an unresolved LID
    must never masquerade as a phone number.
    """

    return strict_public_id(value)


def normalize_group_session_id(value: str | None) -> str:
    """Normalize canonical and QQ-style legacy group sessions strictly."""

    raw = str(value or "").strip()
    user, domain = _split_jid(raw)
    if domain:
        if domain != GROUP_DOMAIN:
            return ""
        raw = user

    if _GROUP_LOCAL_PATTERN.fullmatch(raw):
        return raw
    unique_match = _UNIQUE_GROUP_SESSION_PATTERN.fullmatch(raw)
    return unique_match.group(1) if unique_match else ""


def build_umo_session_id(
    *,
    is_group: bool,
    group_id: str | None,
    user_id: str | None,
    unique_session: bool,
) -> str:
    """Build one canonical AstrBot session ID using the QQ adapter contract."""

    public_user_id = strict_public_id(user_id)
    if not is_group:
        return public_user_id

    public_group_id = normalize_group_session_id(group_id)
    if unique_session and public_user_id and public_group_id:
        return f"{public_user_id}_{public_group_id}"
    return public_group_id


def delivery_jid_from_session_id(
    value: str | None,
    *,
    is_group: bool,
    cache: IdentityMappingCache | None = None,
) -> str:
    """Resolve canonical or legacy UMO session IDs back to transport JIDs."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_group:
        group_id = normalize_group_session_id(raw)
        return f"{group_id}{GROUP_DOMAIN}" if group_id else ""
    if is_pn_jid(raw) or is_lid_jid(raw):
        normalized = normalize_user_jid(raw)
        return normalized if _identity_key(normalized) else ""

    lid_public = _LID_PUBLIC_ID_PATTERN.fullmatch(raw)
    if lid_public:
        if cache is not None:
            cached_lid = cache.lid_for_public_id(raw)
            if cached_lid:
                return cached_lid
        return f"{lid_public.group(1)}@lid"
    if cache is not None:
        cached_pn = cache.pn_for_public_id(raw)
        if cached_pn:
            return cached_pn
    pn_jid = base_pn_jid(raw)
    return pn_jid or ""


@dataclass(slots=True)
class IdentityMappingCache:
    """PN/LID aliases and stable public projections for one adapter account."""

    lid_to_pn: dict[str, str] = field(default_factory=dict)
    pn_to_lid: dict[str, str] = field(default_factory=dict)
    public_by_identity: dict[str, str] = field(default_factory=dict)
    projection_order: dict[str, int] = field(default_factory=dict)
    jid_by_identity: dict[str, str] = field(default_factory=dict)
    _next_projection_order: int = 0
    _projections_dirty: bool = False

    @property
    def dirty(self) -> bool:
        return self._projections_dirty

    @property
    def projections_dirty(self) -> bool:
        return self._projections_dirty

    def clear(self) -> None:
        self.lid_to_pn.clear()
        self.pn_to_lid.clear()
        self.public_by_identity.clear()
        self.projection_order.clear()
        self.jid_by_identity.clear()
        self._next_projection_order = 0
        self._projections_dirty = False

    @staticmethod
    def _matching_key(values: dict[str, str], identity_key: str) -> str:
        for candidate in values:
            if _identity_key(candidate) == identity_key:
                return candidate
        return ""

    def _remember_observed_jid(
        self,
        identity_key: str,
        jid: str,
        *,
        mark_dirty: bool = True,
    ) -> None:
        if not _IDENTITY_KEY_PATTERN.fullmatch(identity_key) or not jid:
            return
        previous = self.jid_by_identity.get(identity_key)
        self.jid_by_identity[identity_key] = jid
        if previous != jid and identity_key in self.public_by_identity and mark_dirty:
            self._projections_dirty = True

    def _set_projection(
        self,
        identity_key: str,
        public_id: str,
        *,
        order: int | None = None,
        mark_dirty: bool = True,
    ) -> bool:
        if not _IDENTITY_KEY_PATTERN.fullmatch(identity_key):
            return False
        if not (_DIGITS_PATTERN.fullmatch(public_id) or _LID_PUBLIC_ID_PATTERN.fullmatch(public_id)):
            return False

        existing_public = self.public_by_identity.get(identity_key)
        existing_order = self.projection_order.get(identity_key)
        if order is None:
            order = existing_order
        if order is None or order <= 0:
            self._next_projection_order += 1
            order = self._next_projection_order
        else:
            self._next_projection_order = max(self._next_projection_order, order)

        changed = existing_public != public_id or existing_order != order
        self.public_by_identity[identity_key] = public_id
        self.projection_order[identity_key] = order
        if changed and mark_dirty:
            self._projections_dirty = True
        return changed

    def _bind_mapping_projections(
        self,
        lid_key: str,
        pn_key: str,
        *,
        mark_dirty: bool = True,
    ) -> None:
        candidates: list[tuple[int, str]] = []
        for key in (lid_key, pn_key):
            public_id = self.public_by_identity.get(key)
            if public_id:
                candidates.append((self.projection_order.get(key, 2**63 - 1), public_id))
        if not candidates:
            return
        order, public_id = min(candidates, key=lambda item: (item[0], item[1]))
        self._set_projection(lid_key, public_id, order=order, mark_dirty=mark_dirty)
        self._set_projection(pn_key, public_id, order=order, mark_dirty=mark_dirty)

    def _rebuild_reverse_mappings(self) -> None:
        """Keep one deterministic delivery LID per PN without dropping aliases."""

        self.pn_to_lid.clear()
        seen_pn_keys: set[str] = set()
        for lid, pn in sorted(
            self.lid_to_pn.items(),
            key=lambda pair: (_identity_key(pair[1]), _identity_key(pair[0]), pair),
        ):
            pn_key = _identity_key(pn)
            if pn_key in seen_pn_keys:
                continue
            seen_pn_keys.add(pn_key)
            self.pn_to_lid[pn] = lid

    def _remember(
        self,
        lid_jid: str | None,
        pn_jid: str | None,
        *,
        mark_dirty: bool,
    ) -> bool:
        lid = base_lid_jid(lid_jid)
        pn = base_pn_jid(pn_jid)
        lid_key = _identity_key(lid)
        pn_key = _identity_key(pn)
        if not lid or not pn or not lid_key.startswith("lid:") or not pn_key.startswith("pn:"):
            return False

        stale_pairs = [
            (stored_lid, stored_pn)
            for stored_lid, stored_pn in self.lid_to_pn.items()
            if _identity_key(stored_lid) == lid_key
        ]
        already_exact = stale_pairs == [(lid, pn)]
        if not already_exact:
            for stored_lid, _stored_pn in stale_pairs:
                self.lid_to_pn.pop(stored_lid, None)
            self.lid_to_pn[lid] = pn
        self._rebuild_reverse_mappings()

        self._remember_observed_jid(lid_key, lid, mark_dirty=mark_dirty)
        self._remember_observed_jid(pn_key, pn, mark_dirty=mark_dirty)

        self._bind_mapping_projections(lid_key, pn_key, mark_dirty=mark_dirty)
        return True

    def remember(self, lid_jid: str | None, pn_jid: str | None) -> bool:
        """Remember one domain-preserving PN/LID alias relation."""

        return self._remember(lid_jid, pn_jid, mark_dirty=True)

    def pn_for_lid(self, lid_jid: str | None) -> str:
        lid = base_lid_jid(lid_jid)
        lid_key = _identity_key(lid)
        if not lid_key.startswith("lid:"):
            return ""
        exact = self.lid_to_pn.get(lid)
        if exact:
            return exact
        stored_lid = self._matching_key(self.lid_to_pn, lid_key)
        return self.lid_to_pn.get(stored_lid, "")

    def lid_for_pn(self, pn_jid: str | None) -> str:
        pn = base_pn_jid(pn_jid)
        pn_key = _identity_key(pn)
        if not pn_key.startswith("pn:"):
            return ""
        exact = self.pn_to_lid.get(pn)
        if exact:
            return exact
        stored_pn = self._matching_key(self.pn_to_lid, pn_key)
        if stored_pn:
            return self.pn_to_lid.get(stored_pn, "")
        candidates = sorted(
            lid
            for lid, mapped_pn in self.lid_to_pn.items()
            if _identity_key(mapped_pn) == pn_key
        )
        return candidates[0] if candidates else ""

    def project_public_id(
        self,
        value: str | None = None,
        *,
        lid_jid: str | None = None,
        pn_jid: str | None = None,
    ) -> str:
        """Return and remember the stable public ID for one identity.

        Supplying both ``lid_jid`` and ``pn_jid`` records their alias relation.
        If neither side was projected before, the presence of a PN makes the
        numeric PN ID canonical.  If the LID already had a ``lid-`` projection,
        that earlier public ID wins and is retained for both aliases.
        """

        explicit_lid_public = _LID_PUBLIC_ID_PATTERN.fullmatch(str(value or "").strip())
        value_key = _identity_key(value)
        normalized_lid = base_lid_jid(lid_jid)
        normalized_pn = base_pn_jid(pn_jid)

        if value_key.startswith("lid:") and not normalized_lid:
            normalized_lid = base_lid_jid(value)
        elif value_key.startswith("pn:") and not normalized_pn:
            normalized_pn = base_pn_jid(value)

        if normalized_lid:
            self._remember_observed_jid(_identity_key(normalized_lid), normalized_lid)
        if normalized_pn:
            self._remember_observed_jid(_identity_key(normalized_pn), normalized_pn)

        if normalized_lid and normalized_pn:
            self.remember(normalized_lid, normalized_pn)
        elif normalized_lid and not normalized_pn:
            normalized_pn = self.pn_for_lid(normalized_lid)
        elif normalized_pn and not normalized_lid:
            normalized_lid = self.lid_for_pn(normalized_pn)

        lid_key = _identity_key(normalized_lid)
        pn_key = _identity_key(normalized_pn)
        keys = [key for key in (lid_key, pn_key) if _IDENTITY_KEY_PATTERN.fullmatch(key)]
        if not keys and _IDENTITY_KEY_PATTERN.fullmatch(value_key):
            keys = [value_key]
        if not keys:
            return ""

        existing = [
            (self.projection_order.get(key, 2**63 - 1), self.public_by_identity[key])
            for key in keys
            if key in self.public_by_identity
        ]
        if existing:
            order, public_id = min(existing, key=lambda item: (item[0], item[1]))
        elif explicit_lid_public:
            order = None
            public_id = f"lid-{explicit_lid_public.group(1)}"
        elif pn_key:
            order = None
            public_id = pn_key.split(":", 1)[1]
        else:
            order = None
            public_id = f"lid-{lid_key.split(':', 1)[1]}"

        for key in keys:
            self._set_projection(key, public_id, order=order)
            if order is None:
                order = self.projection_order[key]
        return public_id

    def lid_for_public_id(self, public_id: str | None) -> str:
        """Resolve a persisted public projection to its best LID transport JID."""

        public = strict_public_id(public_id)
        if not public:
            return ""
        lid_keys = [
            key
            for key, projected in self.public_by_identity.items()
            if key.startswith("lid:") and projected == public
        ]
        direct_key = _identity_key(public_id)
        if direct_key.startswith("lid:") and direct_key not in lid_keys:
            lid_keys.append(direct_key)
        if not lid_keys:
            return ""
        lid_key = min(lid_keys, key=lambda key: self.projection_order.get(key, 2**63 - 1))
        stored = self._matching_key(self.lid_to_pn, lid_key)
        return stored or self.jid_by_identity.get(lid_key, "") or f"{lid_key.split(':', 1)[1]}@lid"

    def pn_for_public_id(self, public_id: str | None) -> str:
        """Resolve a numeric public projection to its observed PN transport JID."""

        public = strict_public_id(public_id)
        if not _DIGITS_PATTERN.fullmatch(public):
            return ""
        pn_keys = [
            key
            for key, projected in self.public_by_identity.items()
            if key.startswith("pn:") and projected == public
        ]
        direct_key = f"pn:{public}"
        if direct_key not in pn_keys:
            pn_keys.append(direct_key)
        pn_key = min(
            pn_keys,
            key=lambda key: self.projection_order.get(key, 2**63 - 1),
        )
        observed = self.jid_by_identity.get(pn_key, "")
        if base_pn_jid(observed):
            return base_pn_jid(observed)
        stored = self._matching_key(self.pn_to_lid, pn_key)
        return stored or f"{public}@s.whatsapp.net"

    def mark_projections_saved(self) -> None:
        self._projections_dirty = False

    def mark_saved(self) -> None:
        self.mark_projections_saved()

    def __len__(self) -> int:
        return len(self.lid_to_pn)


def same_whatsapp_identity(
    left: str | None,
    right: str | None,
    cache: IdentityMappingCache | None = None,
) -> bool:
    """Compare identities without equating equal PN/LID numeric portions."""

    left_key = _identity_key(left)
    right_key = _identity_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if cache is None:
        return False

    keys = {left_key.split(":", 1)[0], right_key.split(":", 1)[0]}
    if keys != {"pn", "lid"}:
        return False
    lid_key = left_key if left_key.startswith("lid:") else right_key
    pn_key = left_key if left_key.startswith("pn:") else right_key
    lid = cache._matching_key(cache.lid_to_pn, lid_key) or f"{lid_key.split(':', 1)[1]}@lid"
    mapped_pn = cache.pn_for_lid(lid)
    return _identity_key(mapped_pn) == pn_key


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


def _load_legacy_lid_mappings(directory: Path, cache: IdentityMappingCache) -> None:
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return
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
        pn = base_pn_jid(payload)
        if pn:
            cache._remember(f"{match.group(1)}@lid", pn, mark_dirty=False)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_supplemental_lid_mappings(directory: Path, cache: IdentityMappingCache) -> None:
    payload = _read_json_object(directory / LID_MAPPINGS_FILENAME)
    if payload.get("version") != IDENTITY_STATE_VERSION:
        return

    mappings = payload.get("lidToPn")
    if not isinstance(mappings, dict):
        return
    for lid, pn in sorted(mappings.items(), key=lambda item: str(item[0])):
        cache._remember(str(lid), str(pn), mark_dirty=False)


def _load_identity_projections(directory: Path, cache: IdentityMappingCache) -> None:
    payload = _read_json_object(directory / IDENTITY_PROJECTIONS_FILENAME)
    if payload.get("version") != IDENTITY_STATE_VERSION:
        return

    order_by_jid = payload.get("projectionOrder")
    if not isinstance(order_by_jid, dict):
        order_by_jid = {}

    for field_name, expected_kind in (("lidToPublic", "lid"), ("pnToPublic", "pn")):
        projections = payload.get(field_name)
        if not isinstance(projections, dict):
            continue
        for raw_jid, raw_public_id in sorted(projections.items(), key=lambda item: str(item[0])):
            jid = base_lid_jid(raw_jid) if expected_kind == "lid" else base_pn_jid(raw_jid)
            identity_key = _identity_key(jid)
            if not jid or not identity_key.startswith(f"{expected_kind}:"):
                continue
            try:
                order = int(order_by_jid.get(str(raw_jid), 0) or 0)
            except (TypeError, ValueError):
                order = 0
            cache._remember_observed_jid(identity_key, jid, mark_dirty=False)
            cache._set_projection(
                identity_key,
                str(raw_public_id),
                order=order,
                mark_dirty=False,
            )

    for lid, pn in list(cache.lid_to_pn.items()):
        cache._bind_mapping_projections(
            _identity_key(lid),
            _identity_key(pn),
            mark_dirty=False,
        )


def load_identity_state(auth_root: Path, cache: IdentityMappingCache) -> int:
    """Load legacy Baileys mappings plus AstrBot's supplemental identity state."""

    cache.clear()
    directory = active_auth_session_dir(auth_root)
    if not directory.is_dir():
        return 0
    _load_legacy_lid_mappings(directory, cache)
    _load_supplemental_lid_mappings(directory, cache)
    _load_identity_projections(directory, cache)
    cache.mark_saved()
    return len(cache)


def load_lid_mappings(auth_root: Path, cache: IdentityMappingCache) -> int:
    """Backward-compatible alias for :func:`load_identity_state`."""

    return load_identity_state(auth_root, cache)


def _projection_jid(cache: IdentityMappingCache, identity_key: str) -> str:
    observed = cache.jid_by_identity.get(identity_key, "")
    if observed:
        return observed
    if identity_key.startswith("lid:"):
        stored = cache._matching_key(cache.lid_to_pn, identity_key)
        return stored or f"{identity_key.split(':', 1)[1]}@lid"
    if identity_key.startswith("pn:"):
        stored = cache._matching_key(cache.pn_to_lid, identity_key)
        return stored or f"{identity_key.split(':', 1)[1]}@s.whatsapp.net"
    return ""


def _projection_payload(
    cache: IdentityMappingCache,
    existing: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = dict(existing or {})
    existing_order = payload.get("projectionOrder")
    if not isinstance(existing_order, dict):
        existing_order = {}

    # Hot reload can briefly leave two adapter objects with independently
    # loaded caches. Merge by namespace-qualified identity instead of replacing
    # the file wholesale, otherwise the last old cache to save would erase a
    # projection first learned by the new adapter.
    candidates: dict[str, tuple[str, str, int]] = {}
    fallback_order = 0
    for raw_order in existing_order.values():
        try:
            fallback_order = max(fallback_order, int(raw_order or 0))
        except (TypeError, ValueError):
            continue
    for field_name, expected_kind in (("lidToPublic", "lid"), ("pnToPublic", "pn")):
        values = payload.get(field_name)
        if not isinstance(values, dict):
            continue
        for raw_jid, raw_public in sorted(values.items(), key=lambda item: str(item[0])):
            jid = base_lid_jid(raw_jid) if expected_kind == "lid" else base_pn_jid(raw_jid)
            identity_key = _identity_key(jid)
            public_id = strict_public_id(raw_public)
            if (
                not jid
                or not identity_key.startswith(f"{expected_kind}:")
                or not public_id
            ):
                continue
            try:
                order = int(existing_order.get(str(raw_jid), 0) or 0)
            except (TypeError, ValueError):
                order = 0
            if order <= 0:
                fallback_order += 1
                order = fallback_order
            previous = candidates.get(identity_key)
            if previous is None or order < previous[2]:
                candidates[identity_key] = (jid, public_id, order)
            elif order == previous[2] and jid.endswith(("@hosted", "@hosted.lid")):
                candidates[identity_key] = (jid, previous[1], previous[2])

    max_existing_order = max((item[2] for item in candidates.values()), default=0)
    next_new_order = max_existing_order
    current_order_groups: dict[tuple[int, str], int] = {}
    existing_public_orders: dict[str, int] = {}
    for _jid, public_id, order in candidates.values():
        existing_public_orders[public_id] = min(
            existing_public_orders.get(public_id, order),
            order,
        )

    current_items = sorted(
        cache.public_by_identity.items(),
        key=lambda item: (
            cache.projection_order.get(item[0], 2**63 - 1),
            item[0],
        ),
    )
    for identity_key, raw_public_id in current_items:
        public_id = strict_public_id(raw_public_id)
        jid = _projection_jid(cache, identity_key)
        if not jid or not public_id:
            continue
        order = cache.projection_order.get(identity_key, 0)
        previous = candidates.get(identity_key)
        if previous is not None:
            previous_jid, previous_public, previous_order = previous
            if order > 0 and order < previous_order:
                chosen_public = public_id
                chosen_order = order
            else:
                # A committed projection wins ties; this makes concurrent first
                # exposure deterministic rather than dependent on last writer.
                chosen_public = previous_public
                chosen_order = previous_order
            hosted_suffix = "@hosted.lid" if identity_key.startswith("lid:") else "@hosted"
            chosen_jid = (
                jid
                if jid.endswith(hosted_suffix)
                else previous_jid
            )
            candidates[identity_key] = (chosen_jid, chosen_public, chosen_order)
            continue

        alias_order = existing_public_orders.get(public_id)
        if alias_order is not None:
            chosen_order = alias_order
        else:
            group = (order, public_id)
            chosen_order = current_order_groups.get(group, 0)
            if not chosen_order:
                next_new_order += 1
                chosen_order = next_new_order
                current_order_groups[group] = chosen_order
            existing_public_orders[public_id] = chosen_order
        candidates[identity_key] = (jid, public_id, chosen_order)

    lid_to_public: dict[str, str] = {}
    pn_to_public: dict[str, str] = {}
    projection_order: dict[str, int] = {}
    for identity_key, (jid, public_id, order) in sorted(candidates.items()):
        target = lid_to_public if identity_key.startswith("lid:") else pn_to_public
        target[jid] = public_id
        projection_order[jid] = order

    payload["version"] = IDENTITY_STATE_VERSION
    payload["lidToPublic"] = dict(sorted(lid_to_public.items()))
    payload["pnToPublic"] = dict(sorted(pn_to_public.items()))
    payload["projectionOrder"] = dict(sorted(projection_order.items()))
    return payload


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise


def save_identity_projections(auth_root: Path, cache: IdentityMappingCache) -> bool:
    """Atomically persist Python-owned stable public identity projections."""

    directory = active_auth_session_dir(auth_root)
    path = directory / IDENTITY_PROJECTIONS_FILENAME
    if not cache.public_by_identity:
        return False
    if not cache.projections_dirty and path.exists():
        return False
    existing = _read_json_object(path)
    if existing and existing.get("version") not in (None, IDENTITY_STATE_VERSION):
        return False
    try:
        payload = _projection_payload(cache, existing)
        _atomic_write_json(path, payload)
    except OSError:
        return False
    # Keep the in-memory writer synchronized with projections merged from a
    # concurrent/hot-reload cache so later mappings use the same exposure order.
    _load_identity_projections(directory, cache)
    cache.mark_projections_saved()
    return True


def save_lid_mapping(
    auth_root: Path,
    lid_jid: str | None,
    pn_jid: str | None,
    *,
    cache: IdentityMappingCache | None = None,
) -> None:
    """Persist legacy compatibility plus any affected public projection.

    The digits-only reverse file is retained for Gateway/Baileys compatibility.
    The Gateway is the sole writer of ``astrbot-lid-mappings-v1.json``; Python
    only reads that shared full-JID mapping file, avoiding a cross-process
    read/modify/write race.  Existing legacy reverse files are never replaced.
    """

    lid = base_lid_jid(lid_jid)
    pn = base_pn_jid(pn_jid)
    lid_user = identity_user(lid)
    pn_user = identity_user(pn)
    if not lid or not pn or not lid_user.isdigit() or not pn_user.isdigit():
        return

    directory = active_auth_session_dir(auth_root)
    legacy_path = directory / f"lid-mapping-{lid_user}_reverse.json"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not legacy_path.exists():
            legacy_path.write_text(json.dumps(pn_user), "utf-8")
    except OSError:
        pass

    state_cache = cache
    if state_cache is None:
        state_cache = IdentityMappingCache()
        load_identity_state(auth_root, state_cache)
    state_cache.remember(lid, pn)
    save_identity_projections(auth_root, state_cache)
