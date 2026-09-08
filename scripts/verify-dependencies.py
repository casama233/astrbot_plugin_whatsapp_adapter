"""CI smoke test for the real managed dependency installer; never logs in."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway_dependencies import dependencies_current, dependency_fingerprint
from gateway_runtime import inspect_gateway_requirements, prepare_staged_plugin
from gateway_stability import prepare_node_dependencies, probe_node_runtime


async def main() -> None:
    node = shutil.which("node") or "node"
    await prepare_staged_plugin(ROOT, node)
    identity = await probe_node_runtime(node)
    assert dependencies_current(ROOT, identity)
    with patch("gateway_stability._npm_command", side_effect=AssertionError("unexpected second install")):
        await prepare_node_dependencies(ROOT, node)
    # A receipt must remain usable after the updater moves the prepared tree.
    with tempfile.TemporaryDirectory(prefix="whatsapp-dependency-smoke-") as temporary:
        staged = Path(temporary) / "staged"
        for relative in ("package.json", "package-lock.json", "scripts/patch-baileys-ephemeral.mjs"):
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        shutil.copytree(ROOT / "node_modules", staged / "node_modules", symlinks=True)
        installed = staged.rename(Path(temporary) / "installed")
        runtime = await inspect_gateway_requirements(installed, node)
        assert runtime["ready"] and runtime["dependenciesInstalled"], runtime
        with patch("gateway_stability._npm_command", side_effect=AssertionError("unexpected post-swap install")):
            await prepare_node_dependencies(installed, node)
    print(json.dumps({"dependencySmoke": "passed", "stagedReceiptReuse": True, "node": identity,
                      "fingerprint": dependency_fingerprint(ROOT)}))


if __name__ == "__main__":
    asyncio.run(main())
