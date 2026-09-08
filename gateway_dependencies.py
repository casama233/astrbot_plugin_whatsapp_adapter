"""Dependency provenance and process-local installation ownership.

The receipt is a completion marker, not an authenticity guarantee. npm ci owns
package integrity; this module detects stale inputs, missing packages and ABI
changes without reaching the network on every Gateway startup.
"""
from __future__ import annotations

import asyncio
import hashlib
import gc
import json
import os
import platform
import sys
import types
from pathlib import Path, PurePosixPath
from typing import Any

RECEIPT_NAME = ".astrbot-install.json"
# AstrBot unloads the plugin package on hot reload. Keep live child references
# outside that package so the next generation cannot overwrite their modules.
_STATE = sys.modules.setdefault(
    "_astrbot_whatsapp_dependency_ownership_v1",
    types.ModuleType("_astrbot_whatsapp_dependency_ownership_v1"),
)
if not hasattr(_STATE, "children"):
    _STATE.children = {}
    _STATE.locks = {}
    _STATE.legacy_adopted = False


def project_start_lock(project_dir: Path) -> asyncio.Lock:
    key = (str(project_dir.resolve()), asyncio.get_running_loop())
    return _STATE.locks.setdefault(key, asyncio.Lock())


def register_gateway_child(project_dir: Path, child: Any) -> None:
    key = str(project_dir.resolve())
    _STATE.children[key] = [p for p in _STATE.children.get(key, []) if p.returncode is None]
    _STATE.children[key].append(child)


def _adopt_legacy_children() -> None:
    if _STATE.legacy_adopted:
        return
    # One-time migration for live GatewayProcess instances created before the
    # ownership registry existed. Inspect only this plugin's concrete class.
    for value in gc.get_objects():
        kind = type(value)
        if kind.__name__ != "GatewayProcess" or not kind.__module__.endswith("whatsapp_client"):
            continue
        attributes = vars(value)
        child = attributes.get("process")
        script = attributes.get("script_path")
        if child is not None and child.returncode is None and isinstance(script, Path):
            register_gateway_child(script.parent.parent, child)
    _STATE.legacy_adopted = True


def assert_dependency_directory_idle(project_dir: Path) -> None:
    _adopt_legacy_children()
    key = str(project_dir.resolve())
    children = [p for p in _STATE.children.get(key, []) if p.returncode is None]
    _STATE.children[key] = children
    if children:
        raise RuntimeError(
            "Gateway dependencies changed while another managed Gateway is running; "
            "stop all WhatsApp Gateway instances or restart AstrBot before retrying. "
            "Existing node_modules has not been modified."
        )


def dependency_fingerprint(project_dir: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("package.json", "package-lock.json", "scripts/patch-baileys-ephemeral.mjs"):
        content = (project_dir / relative).read_bytes().replace(b"\r\n", b"\n")
        digest.update(relative.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def host_identity() -> dict[str, str]:
    return {"os": sys.platform, "machine": platform.machine().lower()}


def installed_tree_matches(project_dir: Path) -> bool:
    """Check every locked installed package, not a hand-picked dependency list.

    Absent optional packages are allowed (native packages are platform-specific).
    If an optional package is installed, its version must still match the lock.
    npm ci and the runtime import smoke test validate the optional/native graph.
    """
    try:
        manifest = json.loads((project_dir / "package.json").read_text(encoding="utf-8-sig"))
        lock = json.loads((project_dir / "package-lock.json").read_text(encoding="utf-8-sig"))
        packages = lock["packages"]
        if not isinstance(packages, dict) or not packages:
            return False
        root = packages[""]
        for field in ("dependencies", "optionalDependencies", "devDependencies"):
            if manifest.get(field, {}) != root.get(field, {}):
                return False
        for name in manifest.get("dependencies", {}):
            if f"node_modules/{name}" not in packages:
                return False
        checked = 0
        for relative, desired in packages.items():
            if relative == "":
                continue
            parts = PurePosixPath(relative).parts
            if (
                not parts or parts[0] != "node_modules" or ".." in parts
                or "\\" in relative or ":" in relative or not isinstance(desired, dict)
                or desired.get("link")
            ):
                return False
            if desired.get("dev"):
                continue
            package = project_dir.joinpath(*parts, "package.json")
            if not package.exists() and (desired.get("optional") or desired.get("devOptional")):
                continue
            installed = json.loads(package.read_text(encoding="utf-8-sig"))
            if not desired.get("version") or installed.get("version") != desired["version"]:
                return False
            checked += 1
        return checked > 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def dependencies_current(project_dir: Path, node_identity: dict[str, Any] | None = None) -> bool:
    try:
        receipt = json.loads((project_dir / "node_modules" / RECEIPT_NAME).read_text(encoding="utf-8"))
        return bool(
            receipt.get("schema") == 1
            and receipt.get("fingerprint") == dependency_fingerprint(project_dir)
            and receipt.get("host") == host_identity()
            and isinstance(receipt.get("node"), dict)
            and (node_identity is None or receipt["node"] == node_identity)
            and installed_tree_matches(project_dir)
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def record_dependency_install(project_dir: Path, node_identity: dict[str, Any], fingerprint: str) -> None:
    if dependency_fingerprint(project_dir) != fingerprint or not installed_tree_matches(project_dir):
        raise RuntimeError("Dependency inputs changed during installation or the installed tree is incomplete")
    target = project_dir / "node_modules" / RECEIPT_NAME
    payload = {"schema": 1, "fingerprint": fingerprint, "host": host_identity(), "node": node_identity}
    # This method is called only after npm, the Baileys patch and imports pass.
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
