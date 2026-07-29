from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))

from whatsapp_client import GatewayProcess


class _Installer:
    returncode = 0

    def __init__(self, dependency_dir: Path) -> None:
        self.dependency_dir = dependency_dir

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(0.01)
        self.dependency_dir.mkdir(parents=True)
        return b"", b""


class GatewayProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_dependency_checks_install_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            gateway_dir = project_dir / "gateway"
            gateway_dir.mkdir()
            (project_dir / "package.json").write_text("{}", encoding="utf-8")
            dependency_dir = (
                project_dir / "node_modules" / "@whiskeysockets" / "baileys"
            )
            calls = 0

            async def create_installer(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                return _Installer(dependency_dir)

            processes = [
                GatewayProcess(
                    node_executable="node",
                    script_path=gateway_dir / "whatsapp-gateway.mjs",
                    host="127.0.0.1",
                    port=18789,
                    auth_dir=project_dir / "auth",
                    log_level="info",
                )
                for _ in range(2)
            ]
            with patch(
                "whatsapp_client.asyncio.create_subprocess_exec",
                side_effect=create_installer,
            ):
                await asyncio.gather(
                    *(process._ensure_node_dependencies() for process in processes)
                )

            self.assertEqual(calls, 1)
            self.assertTrue(dependency_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
