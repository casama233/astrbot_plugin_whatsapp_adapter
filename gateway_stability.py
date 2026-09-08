from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import subprocess
from pathlib import Path
from typing import Any

try:
    from .gateway_dependencies import (
        RECEIPT_NAME, assert_dependency_directory_idle, dependencies_current,
        dependency_fingerprint, record_dependency_install,
    )
except ImportError:  # Standalone tests.
    from gateway_dependencies import (
        RECEIPT_NAME, assert_dependency_directory_idle, dependencies_current,
        dependency_fingerprint, record_dependency_install,
    )

_PROCESS_PATCH_MARKER = "_astrbot_gateway_stability_process_installed"
_CLIENT_PATCH_MARKER = "_astrbot_gateway_stability_client_installed"
_NODE_DEPENDENCY_INSTALL_LOCK = asyncio.Lock()
MINIMUM_NODE_VERSION = (20, 9, 0)
RECOMMENDED_NODE_MAJORS = (22, 24)


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
        if pgid is not None and pgid != os.getpgrp() and hasattr(os, "killpg"):
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


async def _run_node_command(*command: str, cwd: Path | None = None, timeout: float = 5.0) -> bytes:
    extra: dict[str, Any] = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        extra["start_new_session"] = True
    try:
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **extra,
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot start {command[0]}: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except BaseException as exc:
        await _terminate_process_tree(process)
        if isinstance(exc, asyncio.TimeoutError):
            raise RuntimeError(f"{command[0]} timed out after {timeout:g}s") from exc
        raise
    if process.returncode:
        detail = (stdout + stderr).decode(errors="replace")[-6000:]
        raise RuntimeError(f"{command[0]} exited with code {process.returncode}: {detail}")
    return stdout


async def inspect_node_runtime(node: str) -> dict[str, Any]:
    output = await _run_node_command(
        node, "--eval",
        "console.log(JSON.stringify({version:process.versions.node,"
        "abi:process.versions.modules,platform:process.platform,arch:process.arch}))",
    )
    try:
        info = json.loads(output.decode().strip())
        version = tuple(int(part) for part in info["version"].split("."))
        if len(version) != 3 or any(part < 0 for part in version):
            raise ValueError("invalid Node version")
        if any(not isinstance(info.get(key), str) or not info[key] for key in ("abi", "platform", "arch")):
            raise ValueError("incomplete Node identity")
        # Patch updates with an unchanged ABI need not reinstall native modules.
        return {
            "version": info["version"],
            "supported": version >= MINIMUM_NODE_VERSION,
            "recommended": version[0] in RECOMMENDED_NODE_MAJORS,
            "identity": {"major": version[0], "abi": info["abi"],
                         "platform": info["platform"], "arch": info["arch"]},
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("Node.js >=20.9.0 is required; Node 22/24 LTS is recommended") from exc


async def probe_node_runtime(node: str) -> dict[str, Any]:
    info = await inspect_node_runtime(node)
    if not info["supported"]:
        raise RuntimeError("Node.js >=20.9.0 is required; Node 22/24 LTS is recommended")
    return info["identity"]


def _npm_command(node: str) -> list[str]:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found; install Node.js/npm before starting the Gateway")
    path = Path(npm)
    # Run npm with the configured Node, including npm.cmd installations on Windows.
    candidates = [path.resolve(), path.parent / "node_modules/npm/bin/npm-cli.js",
                  path.parent.parent / "lib/node_modules/npm/bin/npm-cli.js"]
    for candidate in candidates:
        if candidate.is_file() and candidate.name == "npm-cli.js":
            return [node, str(candidate)]
    raise RuntimeError("Cannot locate npm-cli.js beside npm; use a standard Node.js/npm installation")


async def prepare_node_dependencies(
    project_dir: Path, node_executable: str, *, node_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prepare a managed or staged tree using the same completion contract."""
    try:
        identity = node_identity if node_identity is not None else await probe_node_runtime(node_executable)
        if dependencies_current(project_dir, identity):
            return identity
        async with _NODE_DEPENDENCY_INSTALL_LOCK:
            if dependencies_current(project_dir, identity):
                return identity
            # Never use npm ci's destructive cleanup on a live managed runtime.
            assert_dependency_directory_idle(project_dir)
            fingerprint = dependency_fingerprint(project_dir)
            timeout = _npm_install_timeout_seconds()
            npm_command = _npm_command(node_executable)
            (project_dir / "node_modules" / RECEIPT_NAME).unlink(missing_ok=True)
            await _run_node_command(
                *npm_command, "ci", "--omit=dev",
                "--no-audit", "--no-fund", "--ignore-scripts=false",
                cwd=project_dir, timeout=timeout,
            )
            # Explicit verification also handles installations influenced by npm config.
            await _run_node_command(
                node_executable, "scripts/patch-baileys-ephemeral.mjs",
                cwd=project_dir, timeout=30,
            )
            await _run_node_command(
                node_executable, "--input-type=module", "--eval",
                "await Promise.all(['@whiskeysockets/baileys','sharp','qrcode',"
                "'qrcode-terminal','pino','https-proxy-agent'].map(x => import(x)))",
                cwd=project_dir, timeout=30,
            )
            record_dependency_install(project_dir, identity, fingerprint)
            return identity
    except (RuntimeError, OSError, ValueError) as exc:
        raise RuntimeError(f"Gateway dependency preparation failed: {exc}") from exc


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
