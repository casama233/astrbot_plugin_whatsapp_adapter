from __future__ import annotations

import hashlib
import re
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Hashable


DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18789
PortProbe = Callable[[str, int], bool]
Endpoint = tuple[str, int]


class GatewayPortLeaseRegistry:
    """Process-local ownership for local ports and external Gateway endpoints."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ports: dict[int, Hashable] = {}
        self._owners: dict[Hashable, int] = {}
        self._external: dict[Endpoint, Hashable] = {}
        self._external_owners: dict[Hashable, Endpoint] = {}

    @staticmethod
    def _validate_port(port: int) -> int:
        value = int(port)
        if not 1 <= value <= 65535:
            raise ValueError(f"invalid gateway port: {port}")
        return value

    def acquire(
        self,
        owner: Hashable,
        host: str,
        requested_port: int,
        *,
        check_os: bool,
        port_probe: PortProbe,
    ) -> int:
        port = self._validate_port(requested_port)
        with self._lock:
            current = self._owners.get(owner)
            if current is not None:
                return current

            for candidate in range(port, 65536):
                if candidate in self._ports:
                    continue
                if check_os and not port_probe(host, candidate):
                    continue
                self._ports[candidate] = owner
                self._owners[owner] = candidate
                return candidate

        raise RuntimeError(f"no available gateway port at or above {port}")

    def reserve_external(self, owner: Hashable, host: str, port: int) -> Endpoint:
        endpoint = (str(host), self._validate_port(port))
        with self._lock:
            current = self._external_owners.get(owner)
            if current is not None:
                return current
            existing_owner = self._external.get(endpoint)
            if existing_owner is not None and existing_owner != owner:
                raise RuntimeError(
                    "external WhatsApp Gateway endpoint is already owned by another "
                    f"adapter instance: {endpoint[0]}:{endpoint[1]}; configure a distinct "
                    "external Gateway per account"
                )
            self._external[endpoint] = owner
            self._external_owners[owner] = endpoint
            return endpoint

    def release(self, owner: Hashable) -> None:
        with self._lock:
            port = self._owners.pop(owner, None)
            if port is not None and self._ports.get(port) == owner:
                self._ports.pop(port, None)
            endpoint = self._external_owners.pop(owner, None)
            if endpoint is not None and self._external.get(endpoint) == owner:
                self._external.pop(endpoint, None)

    def leased_ports(self) -> set[int]:
        with self._lock:
            return set(self._ports)

    def external_endpoints(self) -> set[Endpoint]:
        with self._lock:
            return set(self._external)


def is_port_available(host: str, port: int) -> bool:
    """Best-effort probe used when choosing a secondary local Gateway port."""

    bind_host = str(host or DEFAULT_GATEWAY_HOST).strip() or DEFAULT_GATEWAY_HOST
    if bind_host == "localhost":
        bind_host = DEFAULT_GATEWAY_HOST
    try:
        infos = socket.getaddrinfo(bind_host, int(port), type=socket.SOCK_STREAM)
    except OSError:
        # Leave invalid/unresolvable host reporting to GatewayProcess.
        return True

    for family, socktype, proto, _canonname, sockaddr in infos:
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.bind(sockaddr)
            return True
        except OSError:
            continue
    return False


def safe_instance_slug(instance_id: Any) -> str:
    """Return a filesystem-safe, collision-resistant instance identifier."""

    raw = str(instance_id or "whatsapp")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip(".") or "instance"
    cleaned = re.sub(r"\.{2,}", "_", cleaned).strip(".") or "instance"
    cleaned = cleaned[:48]
    if cleaned == raw and raw not in {".", ".."}:
        return cleaned
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"{cleaned}-{digest}"


def instance_auth_dir(data_dir: Path, config: dict[str, Any]) -> Path:
    raw_instance_id = str(config.get("id") or "whatsapp")
    configured = str(config.get("auth_dir") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser().resolve()
        if raw_instance_id == "whatsapp":
            return configured_path
        return configured_path.with_name(
            f"{configured_path.name}-{safe_instance_slug(raw_instance_id)}"
        )

    if raw_instance_id == "whatsapp":
        return data_dir / "whatsapp-auth"
    return data_dir / f"whatsapp-auth-{safe_instance_slug(raw_instance_id)}"


_GATEWAY_PORT_LEASES = GatewayPortLeaseRegistry()


def _owner_token(adapter: Any) -> Hashable:
    token = getattr(adapter, "_gateway_port_lease_token", None)
    if token is None:
        token = object()
        adapter._gateway_port_lease_token = token
    return token


def ensure_gateway_endpoint(
    adapter: Any,
    *,
    registry: GatewayPortLeaseRegistry | None = None,
    port_probe: PortProbe | None = None,
) -> Endpoint:
    """Resolve and pin one runtime endpoint for an adapter instance.

    The default ``whatsapp`` instance owns the configured local base port
    logically. Secondary local instances start at base+1. External endpoints
    are not locally leased, but duplicate runtime claims are rejected so two
    accounts cannot silently share one external Gateway/session.
    """

    current = getattr(adapter, "_gateway_runtime_endpoint", None)
    if current is not None:
        return current

    config = getattr(adapter, "config", {}) or {}
    host = str(config.get("gateway_host") or DEFAULT_GATEWAY_HOST)
    requested_port = int(config.get("gateway_port") or DEFAULT_GATEWAY_PORT)
    auto_start = bool(config.get("auto_start_gateway", True))
    instance_id = str(config.get("id") or "whatsapp")
    active_registry = registry or _GATEWAY_PORT_LEASES
    token = _owner_token(adapter)

    if not auto_start:
        endpoint = active_registry.reserve_external(token, host, requested_port)
        adapter._gateway_runtime_endpoint = endpoint
        adapter._gateway_runtime_port_leased = False
        adapter._gateway_runtime_external_reserved = True
        adapter._gateway_runtime_registry = active_registry
        return endpoint

    probe = port_probe or is_port_available
    allocation_start = requested_port if instance_id == "whatsapp" else requested_port + 1
    if allocation_start > 65535:
        raise RuntimeError(f"no available secondary gateway port above {requested_port}")

    port = active_registry.acquire(
        token,
        host,
        allocation_start,
        check_os=instance_id != "whatsapp",
        port_probe=probe,
    )
    endpoint = (host, port)
    adapter._gateway_runtime_endpoint = endpoint
    adapter._gateway_runtime_port_leased = True
    adapter._gateway_runtime_external_reserved = False
    adapter._gateway_runtime_registry = active_registry
    return endpoint


def release_gateway_endpoint(adapter: Any) -> None:
    token = getattr(adapter, "_gateway_port_lease_token", None)
    owns_runtime_slot = bool(getattr(adapter, "_gateway_runtime_port_leased", False)) or bool(
        getattr(adapter, "_gateway_runtime_external_reserved", False)
    )
    if token is not None and owns_runtime_slot:
        registry = getattr(adapter, "_gateway_runtime_registry", _GATEWAY_PORT_LEASES)
        registry.release(token)

    adapter._gateway_runtime_endpoint = None
    adapter._gateway_runtime_port_leased = False
    adapter._gateway_runtime_external_reserved = False
    adapter._gateway_runtime_registry = None


def reallocate_after_bind_conflict(
    adapter: Any,
    *,
    port_probe: PortProbe | None = None,
) -> Endpoint | None:
    """Move a failed secondary Gateway only for a confirmed bind race.

    Normal reconnect/login failures must keep the stable endpoint. Reallocation
    is permitted only when the spawned child has exited and the leased port is
    now observed as unavailable, which is the EADDRINUSE/TOCTOU shape.
    """

    config = getattr(adapter, "config", {}) or {}
    if not bool(config.get("auto_start_gateway", True)):
        return None
    if str(config.get("id") or "whatsapp") == "whatsapp":
        return None

    current = getattr(adapter, "_gateway_runtime_endpoint", None)
    if current is None:
        return None
    gateway_process = getattr(adapter, "gateway_process", None)
    child = getattr(gateway_process, "process", None)
    if child is None or getattr(child, "returncode", None) is None:
        return None

    host, port = current
    probe = port_probe or is_port_available
    if probe(host, port):
        return None

    registry = getattr(adapter, "_gateway_runtime_registry", _GATEWAY_PORT_LEASES)
    release_gateway_endpoint(adapter)
    return ensure_gateway_endpoint(adapter, registry=registry, port_probe=probe)
