"""Shared Gateway requirements and staged-update preparation."""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from .gateway_dependencies import assert_dependency_directory_idle, dependencies_current
    from .gateway_stability import (
        MINIMUM_NODE_VERSION, _npm_command, _run_node_command,
        inspect_node_runtime, prepare_node_dependencies,
    )
except ImportError:  # Standalone tests and diagnostics.
    from gateway_dependencies import assert_dependency_directory_idle, dependencies_current
    from gateway_stability import (
        MINIMUM_NODE_VERSION, _npm_command, _run_node_command,
        inspect_node_runtime, prepare_node_dependencies,
    )


async def inspect_gateway_requirements(
    project_dir: Path, configured_node: str, *, managed: bool = True,
) -> dict[str, Any]:
    """Never install packages, start a Gateway, or read account credentials."""
    result: dict[str, Any] = {
        "ok": True, "ready": False, "canPrepare": False,
        "mode": "managed" if managed else "external",
        "minimumNodeMajor": MINIMUM_NODE_VERSION[0],
        "minimumNodeVersion": ".".join(map(str, MINIMUM_NODE_VERSION)),
        "node": {"configured": configured_node, "path": None, "version": None,
                 "major": None, "supported": False, "recommended": False, "error": None},
        "npm": {"path": None, "available": False, "error": None},
        "dependenciesInstalled": False,
    }
    if not managed:
        return {**result, "ready": True, "status": "external"}

    node_path = shutil.which(configured_node)
    result["node"]["path"] = node_path
    if not node_path:
        return {**result, "status": "node_missing"}
    try:
        runtime = await inspect_node_runtime(node_path)
    except RuntimeError as exc:
        result["node"]["error"] = str(exc)
        return {**result, "status": "node_unavailable"}
    result["node"].update({
        "version": runtime["version"], "major": runtime["identity"]["major"],
        "supported": runtime["supported"], "recommended": runtime["recommended"],
    })
    if not runtime["supported"]:
        return {**result, "status": "node_unsupported"}

    try:
        npm_command = _npm_command(node_path)
        result["npm"].update({"path": npm_command[1], "available": True})
    except RuntimeError as exc:
        result["npm"]["error"] = str(exc)

    current = await asyncio.to_thread(dependencies_current, project_dir, runtime["identity"])
    result["dependenciesInstalled"] = current
    if current:
        return {**result, "ready": True, "status": "dependencies_current"}
    try:
        assert_dependency_directory_idle(project_dir)
    except RuntimeError:
        return {**result, "status": "dependencies_blocked"}
    if not result["npm"]["available"]:
        return {**result, "status": "npm_unavailable"}
    return {**result, "canPrepare": True, "status": "dependencies_pending"}


async def prepare_staged_plugin(staged_dir: Path, node_executable: str) -> None:
    """Validate and prepare only the extracted candidate before quiescing live use."""
    staged_dir = staged_dir.resolve()
    for relative in ("package.json", "package-lock.json", "gateway/whatsapp-gateway.mjs"):
        if not (staged_dir / relative).is_file():
            raise RuntimeError(f"Release is missing {relative}")
    try:
        await prepare_node_dependencies(staged_dir, node_executable)
        await _run_node_command(
            sys.executable, "-m", "compileall", "-q", "-x",
            r"(^|[/\\])(node_modules|\.git)([/\\]|$)", ".",
            cwd=staged_dir, timeout=120,
        )
        await _run_node_command(
            node_executable, "--check", "gateway/whatsapp-gateway.mjs",
            cwd=staged_dir, timeout=60,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"Staged Gateway preparation failed: {exc}") from exc
