from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any

_PROCESS_PATCH_MARKER = "_astrbot_gateway_stability_process_installed"
_CLIENT_PATCH_MARKER = "_astrbot_gateway_stability_client_installed"
_NODE_DEPENDENCY_INSTALL_LOCK = asyncio.Lock()


def _env_seconds(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else default
    except (TypeError, ValueError):
        value = default
    if not value > 0:
        value = default
    return min(max(value, minimum), maximum)


def _npm_install_timeout_seconds() -> float:
    return _env_seconds("WA_NPM_INSTALL_TIMEOUT_SECONDS", 180.0, 1.0, 900.0)


def _aux_request_timeout_seconds() -> float:
    return _env_seconds("WA_AUX_REQUEST_TIMEOUT_SECONDS", 6.0, 1.0, 30.0)


def _graceful_shutdown_timeout_seconds() -> float:
    return _env_seconds("WA_GATEWAY_GRACEFUL_SHUTDOWN_SECONDS", 5.0, 1.0, 15.0)


async def _terminate_process_tree(process: Any) -> None:
    if process is None or getattr(process, "returncode", None) is not None:
        return

    if os.name == "nt":
        pid = getattr(process, "pid", None)
        if pid:
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(killer.wait(), timeout=5)
            except (FileNotFoundError, OSError, asyncio.TimeoutError):
                pass
    else:
        pid = getattr(process, "pid", None)
        pgid = None
        if pid and hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, OSError):
                pgid = None
        if pgid is not None and hasattr(os, "killpg"):
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
                return
            except (asyncio.TimeoutError, ProcessLookupError, AttributeError):
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass

    if getattr(process, "returncode", None) is None:
        try:
            process.kill()
        except (ProcessLookupError, AttributeError, OSError):
            pass
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            await asyncio.wait_for(wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            pass


async def _bounded_node_dependency_install(self: Any, error_cls: type[BaseException]) -> None:
    project_dir = self.script_path.parent.parent
    if self._node_dependencies_current(project_dir):
        return

    async with _NODE_DEPENDENCY_INSTALL_LOCK:
        if self._node_dependencies_current(project_dir):
            return
        package_json = project_dir / "package.json"
        if not package_json.exists():
            raise error_cls(f"Gateway package.json not found: {package_json}")

        extra_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creation_flags:
                extra_kwargs["creationflags"] = creation_flags
        else:
            extra_kwargs["start_new_session"] = True

        try:
            installer = await asyncio.create_subprocess_exec(
                "npm",
                "install",
                "--omit=dev",
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **extra_kwargs,
            )
        except FileNotFoundError as exc:
            raise error_cls(
                "npm not found; please install Node.js/npm or run npm install manually"
            ) from exc

        timeout = _npm_install_timeout_seconds()
        try:
            stdout, stderr = await asyncio.wait_for(
                installer.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            await _terminate_process_tree(installer)
            raise error_cls(
                f"npm install --omit=dev timed out after {timeout:g}s; "
                "check npm registry, DNS, proxy, and postinstall scripts"
            ) from exc

        if installer.returncode != 0:
            out = stdout.decode(errors="replace").strip()
            err = stderr.decode(errors="replace").strip()
            detail = "\n".join(part for part in [out, err] if part)[-6000:]
            raise error_cls(
                f"npm install --omit=dev failed with code {installer.returncode}: {detail}"
            )
        if not self._node_dependencies_current(project_dir):
            raise error_cls(
                "npm install --omit=dev completed but installed dependency versions "
                "do not match package-lock.json"
            )


async def _request_graceful_shutdown(client_cls: type[Any], process: Any) -> bool:
    child = getattr(process, "process", None)
    if child is None or getattr(child, "returncode", None) is not None:
        return False

    base_url = f"http://{process.host}:{process.port}"
    timeout = min(2.5, _graceful_shutdown_timeout_seconds())
    try:
        try:
            client = client_cls(base_url, timeout=timeout)
        except TypeError:
            client = client_cls(base_url)
        token = str(getattr(process, "_gateway_auth_token", "") or "").strip()
        if token:
            client._gateway_auth_token = token
        try:
            await asyncio.wait_for(
                client._request("POST", "/shutdown", json_data={}),
                timeout=timeout,
            )
            return True
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                await close()
    except Exception:
        return False


def install_gateway_runtime_stability(
    client_cls: type[Any],
    process_cls: type[Any],
    error_cls: type[BaseException],
) -> None:
    """Install bounded I/O and graceful-stop behavior on Gateway classes."""

    if not getattr(process_cls, _PROCESS_PATCH_MARKER, False):
        original_stop = process_cls.stop

        async def stable_ensure_node_dependencies(self: Any) -> None:
            await _bounded_node_dependency_install(self, error_cls)

        async def stable_process_stop(self: Any) -> None:
            child = getattr(self, "process", None)
            requested = await _request_graceful_shutdown(client_cls, self)
            if requested and child is not None and getattr(child, "returncode", None) is None:
                wait = getattr(child, "wait", None)
                if callable(wait):
                    try:
                        await asyncio.wait_for(
                            wait(),
                            timeout=_graceful_shutdown_timeout_seconds(),
                        )
                    except asyncio.TimeoutError:
                        pass
            await original_stop(self)

        process_cls._ensure_node_dependencies = stable_ensure_node_dependencies
        process_cls.stop = stable_process_stop
        setattr(process_cls, _PROCESS_PATCH_MARKER, True)

    if not getattr(client_cls, _CLIENT_PATCH_MARKER, False):
        if hasattr(client_cls, "send_presence"):
            original_send_presence = client_cls.send_presence

            async def stable_send_presence(self: Any, *args: Any, **kwargs: Any):
                return await asyncio.wait_for(
                    original_send_presence(self, *args, **kwargs),
                    timeout=_aux_request_timeout_seconds(),
                )

            client_cls.send_presence = stable_send_presence

        if hasattr(client_cls, "react"):
            original_react = client_cls.react

            async def stable_react(self: Any, *args: Any, **kwargs: Any):
                return await asyncio.wait_for(
                    original_react(self, *args, **kwargs),
                    timeout=_aux_request_timeout_seconds(),
                )

            client_cls.react = stable_react

        setattr(client_cls, _CLIENT_PATCH_MARKER, True)
