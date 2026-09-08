"""CI smoke test for the real managed dependency installer; never logs in."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway_dependencies import dependencies_current, dependency_fingerprint
from gateway_stability import _bounded_node_dependency_install


async def main() -> None:
    process = SimpleNamespace(
        script_path=ROOT / "gateway/whatsapp-gateway.mjs",
        node_executable=shutil.which("node") or "node",
    )
    await _bounded_node_dependency_install(process, RuntimeError)
    assert dependencies_current(ROOT, process._node_identity)
    with patch("gateway_stability._npm_command", side_effect=AssertionError("unexpected second install")):
        await _bounded_node_dependency_install(process, RuntimeError)
    print(json.dumps({"dependencySmoke": "passed", "node": process._node_identity,
                      "fingerprint": dependency_fingerprint(ROOT)}))


if __name__ == "__main__":
    asyncio.run(main())
