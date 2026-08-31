from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReactionEntry:
    """One observed WhatsApp reaction inside the arbitration window."""

    emoji: str
    observed_at: float


class ReactionJournal:
    """Keep short-lived reaction observations for cross-plugin arbitration.

    WhatsApp exposes only one current reaction per sender. During a distributed
    claim/confirm protocol, replacing the claim emoji with a confirmation emoji
    must not erase the already-observed claim before every participant has had a
    chance to read it. Entries therefore expire by TTL instead of mirroring only
    the final WhatsApp UI state.
    """

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._entries: dict[tuple[str, str, str, str], ReactionEntry] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _part(value: object) -> str:
        return str(value or "").strip()

    def _prune_locked(self, current: float) -> None:
        cutoff = current - self.ttl_seconds
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.observed_at < cutoff
        ]
        for key in stale:
            self._entries.pop(key, None)

    def record(
        self,
        *,
        chat_id: object,
        message_id: object,
        sender_id: object,
        emoji: object,
        now: float | None = None,
    ) -> bool:
        chat = self._part(chat_id)
        message = self._part(message_id)
        sender = self._part(sender_id)
        if not chat or not message or not sender:
            return False

        observed_at = time.monotonic() if now is None else float(now)
        normalized_emoji = self._part(emoji)
        with self._lock:
            self._prune_locked(observed_at)
            if normalized_emoji:
                key = (chat, message, sender, normalized_emoji)
                self._entries[key] = ReactionEntry(normalized_emoji, observed_at)
            else:
                # A native empty reaction means the sender removed its current
                # reaction. Clear every retained observation for that sender.
                keys = [
                    key
                    for key in self._entries
                    if key[:3] == (chat, message, sender)
                ]
                for key in keys:
                    self._entries.pop(key, None)
        return True

    def users(
        self,
        *,
        chat_id: object,
        message_id: object,
        emoji: object,
        now: float | None = None,
    ) -> list[str]:
        current = time.monotonic() if now is None else float(now)
        chat = self._part(chat_id)
        message = self._part(message_id)
        normalized_emoji = self._part(emoji)
        if not chat or not message or not normalized_emoji:
            return []

        with self._lock:
            self._prune_locked(current)
            return sorted(
                sender
                for (
                    entry_chat,
                    entry_message,
                    sender,
                    entry_emoji,
                ), _entry in self._entries.items()
                if entry_chat == chat
                and entry_message == message
                and entry_emoji == normalized_emoji
            )

    def prune(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            self._prune_locked(current)

    def clear(self) -> None:
        """Clear all observations, primarily for deterministic lifecycle/tests."""

        with self._lock:
            self._entries.clear()


reaction_journal = ReactionJournal()
