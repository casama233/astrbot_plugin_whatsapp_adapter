from __future__ import annotations

from pathlib import Path as _Path

from .album_caption_compat import (
    apply_album_caption_message as _apply_album_caption_message,
    install_album_caption_compat as _install_album_caption_compat,
)
from .group_name_compat import apply_group_name as _apply_group_name
from .whatsapp_multi_instance import (
    ensure_gateway_endpoint as _ensure_gateway_endpoint,
    instance_auth_dir as _instance_auth_dir,
    reallocate_after_bind_conflict as _reallocate_after_bind_conflict,
    release_gateway_endpoint as _release_gateway_endpoint,
)

_impl_path = _Path(__file__).with_name("_whatsapp_adapter_impl.py")
exec(compile(_impl_path.read_text(encoding="utf-8"), str(_impl_path), "exec"), globals(), globals())


# Multi-instance runtime patch -------------------------------------------------
# Keep this layer outside the large implementation so the feature can follow
# main without reintroducing conflicts in unrelated adapter behaviour.
_original_init = WhatsAppPlatformAdapter.__init__
_original_run = WhatsAppPlatformAdapter.run
_original_terminate = WhatsAppPlatformAdapter.terminate
_original_connect_gateway = WhatsAppPlatformAdapter._connect_gateway
_original_ensure_gateway_running = WhatsAppPlatformAdapter._ensure_gateway_running


def _gateway_lifecycle_lock(self):
    # Plugin hot reload swaps ``inst.__class__`` without calling the new class's
    # __init__, so every patched async entry point must tolerate old instances.
    lock = getattr(self, "_gateway_endpoint_lifecycle_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        self._gateway_endpoint_lifecycle_lock = lock
    return lock


def _base_url_for_instance(self) -> str:
    host, port = _ensure_gateway_endpoint(self)
    requested_port = int(self.config.get("gateway_port") or 18789)
    if port != requested_port and not getattr(self, "_gateway_port_allocation_logged", False):
        logger.info(
            "WhatsApp multi-instance: allocated gateway port %s instead of %s for instance '%s'",
            port,
            requested_port,
            str(self.config.get("id") or "whatsapp"),
        )
        self._gateway_port_allocation_logged = True
    return f"http://{host}:{port}"


def _auth_dir_for_instance(self):
    return _instance_auth_dir(self._data_dir(), self.config)


def _create_gateway_process_for_instance(self):
    host, port = _ensure_gateway_endpoint(self)
    return GatewayProcess(
        node_executable=str(self.config["node_executable"]),
        script_path=PLUGIN_DIR / "gateway" / "whatsapp-gateway.mjs",
        host=host,
        port=port,
        auth_dir=self._auth_dir(),
        log_level=str(self.config["log_level"]),
        data_dir=self._data_dir(),
    )


def _init_with_gateway_lease(self, *args, **kwargs):
    _gateway_lifecycle_lock(self)
    try:
        _original_init(self, *args, **kwargs)
    except Exception:
        _release_gateway_endpoint(self)
        raise


def _recover_bind_race(self) -> bool:
    previous = getattr(self, "_gateway_runtime_endpoint", None)
    replacement = _reallocate_after_bind_conflict(self)
    if replacement is None:
        return False
    self.gateway_process = None
    self.client.update_base_url(f"http://{replacement[0]}:{replacement[1]}")
    self._gateway_port_allocation_logged = False
    logger.warning(
        "WhatsApp multi-instance: gateway bind race moved instance '%s' from %s to %s",
        str(self.config.get("id") or "whatsapp"),
        previous,
        replacement,
    )
    return True


async def _connect_gateway_with_gateway_lease(self):
    async with _gateway_lifecycle_lock(self):
        try:
            return await _original_connect_gateway(self)
        except Exception:
            _recover_bind_race(self)
            raise


async def _ensure_gateway_running_with_gateway_lease(self):
    async with _gateway_lifecycle_lock(self):
        try:
            return await _original_ensure_gateway_running(self)
        except Exception:
            _recover_bind_race(self)
            raise


async def _reload_with_gateway_lease(self, platform_config):
    """Atomically swap config and endpoint without exposing a live old port."""

    new_platform_config = sanitize_whatsapp_platform_config(platform_config or {})
    new_config = self._merged_config(new_platform_config)

    # The run loop and health monitor may otherwise reconnect while reload is
    # between releasing the old lease and publishing the new endpoint.
    async with _gateway_lifecycle_lock(self):
        self._reconnect_event.set()
        await self._stop_health_monitor()
        await self._shutdown_gateway_transport()
        self._release_runtime_owner()
        _release_gateway_endpoint(self)

        self._platform_config = new_platform_config
        self.config = new_config
        self._legacy_command_prefix = extract_legacy_command_prefix(new_platform_config)
        self._refresh_registered_commands()
        self._gateway_port_allocation_logged = False
        self.client.update_base_url(self._base_url)
        self._force_gateway_restart = True

        identity_auth_dir = self._auth_dir()
        _load_lid_mappings(identity_auth_dir, self._identity_mappings())
        self._identity_session_dir = _active_auth_session_dir(identity_auth_dir)
        if not self._stopped.is_set():
            await self._claim_runtime_owner()


async def _run_with_gateway_lease(self):
    try:
        return await _original_run(self)
    finally:
        # _original_run stops the Gateway in its own finally block first.
        _release_gateway_endpoint(self)


async def _terminate_with_gateway_lease(self):
    try:
        return await _original_terminate(self)
    finally:
        # terminate() has already stopped the Gateway transport here.
        _release_gateway_endpoint(self)


WhatsAppPlatformAdapter._base_url = property(_base_url_for_instance)
WhatsAppPlatformAdapter._auth_dir = _auth_dir_for_instance
WhatsAppPlatformAdapter._create_gateway_process = _create_gateway_process_for_instance
WhatsAppPlatformAdapter.__init__ = _init_with_gateway_lease
WhatsAppPlatformAdapter._connect_gateway = _connect_gateway_with_gateway_lease
WhatsAppPlatformAdapter._ensure_gateway_running = _ensure_gateway_running_with_gateway_lease
WhatsAppPlatformAdapter.reload = _reload_with_gateway_lease
WhatsAppPlatformAdapter.run = _run_with_gateway_lease
WhatsAppPlatformAdapter.terminate = _terminate_with_gateway_lease


# Existing compatibility patches ----------------------------------------------
_install_album_caption_compat(WhatsAppPlatformAdapter)
_original_convert_message = WhatsAppPlatformAdapter.convert_message


async def _convert_message_with_group_name(self, data):
    message = await _original_convert_message(self, data)
    message = _apply_group_name(message, data, self._project_public_user_id)
    return _apply_album_caption_message(self, message, data)


WhatsAppPlatformAdapter.convert_message = _convert_message_with_group_name
