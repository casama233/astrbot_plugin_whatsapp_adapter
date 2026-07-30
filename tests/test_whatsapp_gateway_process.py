from __future__ import annotations

import asyncio
import json
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

    def __init__(self, project_dir: Path, versions: dict[str, str]) -> None:
        self.project_dir = project_dir
        self.versions = versions

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(0.01)
        for name, version in self.versions.items():
            dependency_dir = self.project_dir / "node_modules" / Path(*name.split("/"))
            dependency_dir.mkdir(parents=True, exist_ok=True)
            (dependency_dir / "package.json").write_text(
                json.dumps({"name": name, "version": version}),
                encoding="utf-8",
            )
        return b"", b""


class GatewayProcessTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _write_project(
        project_dir: Path,
        desired_versions: dict[str, str],
        installed_versions: dict[str, str] | None = None,
    ) -> None:
        direct_dependencies = {
            name: version
            for name, version in desired_versions.items()
            if name not in {"protobufjs", "sharp"}
        }
        (project_dir / "package.json").write_text(
            json.dumps({"dependencies": direct_dependencies}),
            encoding="utf-8",
        )
        packages: dict[str, object] = {
            "": {"dependencies": direct_dependencies},
        }
        for name, version in desired_versions.items():
            packages[f"node_modules/{name}"] = {"version": version}
        (project_dir / "package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": packages}),
            encoding="utf-8",
        )
        for name, version in (installed_versions or {}).items():
            dependency_dir = project_dir / "node_modules" / Path(*name.split("/"))
            dependency_dir.mkdir(parents=True, exist_ok=True)
            (dependency_dir / "package.json").write_text(
                json.dumps({"name": name, "version": version}),
                encoding="utf-8",
            )

    @staticmethod
    def _process(project_dir: Path) -> GatewayProcess:
        gateway_dir = project_dir / "gateway"
        gateway_dir.mkdir(exist_ok=True)
        return GatewayProcess(
            node_executable="node",
            script_path=gateway_dir / "whatsapp-gateway.mjs",
            host="127.0.0.1",
            port=18789,
            auth_dir=project_dir / "auth",
            log_level="info",
        )

    async def test_concurrent_dependency_checks_install_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            versions = {
                "@whiskeysockets/baileys": "7.0.0-rc14",
                "protobufjs": "7.6.5",
                "sharp": "0.35.3",
            }
            self._write_project(project_dir, versions)
            calls = 0

            async def create_installer(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                return _Installer(project_dir, versions)

            processes = [self._process(project_dir) for _ in range(2)]
            with patch(
                "whatsapp_client.asyncio.create_subprocess_exec",
                side_effect=create_installer,
            ):
                await asyncio.gather(
                    *(process._ensure_node_dependencies() for process in processes)
                )

            self.assertEqual(calls, 1)
            self.assertTrue(GatewayProcess._node_dependencies_current(project_dir))

    async def test_stale_baileys_or_security_dependency_triggers_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            desired = {
                "@whiskeysockets/baileys": "7.0.0-rc14",
                "protobufjs": "7.6.5",
                "sharp": "0.35.3",
            }
            installed = {
                "@whiskeysockets/baileys": "7.0.0-rc13",
                "protobufjs": "7.6.1",
                "sharp": "0.34.5",
            }
            self._write_project(project_dir, desired, installed)
            calls = 0

            async def create_installer(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                return _Installer(project_dir, desired)

            with patch(
                "whatsapp_client.asyncio.create_subprocess_exec",
                side_effect=create_installer,
            ):
                await self._process(project_dir)._ensure_node_dependencies()

            self.assertEqual(calls, 1)
            self.assertTrue(GatewayProcess._node_dependencies_current(project_dir))

    async def test_matching_locked_dependencies_skip_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            versions = {
                "@whiskeysockets/baileys": "7.0.0-rc14",
                "protobufjs": "7.6.5",
                "sharp": "0.35.3",
            }
            self._write_project(project_dir, versions, versions)

            with patch(
                "whatsapp_client.asyncio.create_subprocess_exec"
            ) as create_installer:
                await self._process(project_dir)._ensure_node_dependencies()

            create_installer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
