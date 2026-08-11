from __future__ import annotations


class ResponsePresenceLeases:
    """Track active outbound responses per WhatsApp chat.

    AstrBot may process multiple events from the same session concurrently.  Each
    response still owns its own streaming message state, but presence is shared
    at the chat transport level.  A response that finishes early must therefore
    not send ``paused`` while another response is still streaming.
    """

    def __init__(self) -> None:
        self._active: dict[tuple[int, str], int] = {}

    @staticmethod
    def _key(client: object, target_jid: str) -> tuple[int, str]:
        return id(client), str(target_jid or "")

    def acquire(self, client: object, target_jid: str) -> int:
        key = self._key(client, target_jid)
        count = self._active.get(key, 0) + 1
        self._active[key] = count
        return count

    def release(self, client: object, target_jid: str) -> int:
        key = self._key(client, target_jid)
        count = self._active.get(key, 0)
        if count <= 1:
            self._active.pop(key, None)
            return 0
        count -= 1
        self._active[key] = count
        return count

    def count(self, client: object, target_jid: str) -> int:
        return self._active.get(self._key(client, target_jid), 0)

    def should_pause(self, client: object, target_jid: str) -> bool:
        """Return whether the current response may clear composing presence.

        ``stop_typing`` runs before the current response releases its lease, so
        a count of one means this is the last active response for the chat.
        Calls made outside the lease wrapper (count zero) keep legacy behavior.
        """

        return self.count(client, target_jid) <= 1


response_presence_leases = ResponsePresenceLeases()
