from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gateway_stability as stability


class NpmDiscoveryTests(unittest.TestCase):
    def assert_npm_command(self, node: str, cli: Path) -> None:
        command = stability._npm_command(node)
        self.assertEqual(len(command), 2)
        self.assertEqual(command[0], node)
        # Windows may expose Temp through an 8.3 alias; npm's identity matters.
        self.assertTrue(Path(command[1]).samefile(cli), command)

    def test_node_on_path_finds_its_adjacent_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = "node.exe" if os.name == "nt" else "node"
            node = root / name
            node.touch()
            node.chmod(0o755)
            cli = root / "node_modules/npm/bin/npm-cli.js"
            cli.parent.mkdir(parents=True)
            cli.touch()
            with patch.dict(os.environ, {"PATH": str(root)}):
                self.assert_npm_command(name, cli)

    def test_configured_node_finds_its_npm_without_path(self):
        for node_name, relative in (("node.exe", "node_modules/npm/bin/npm-cli.js"),
                                    ("bin/node", "lib/node_modules/npm/bin/npm-cli.js")):
            with self.subTest(layout=node_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                node = root / node_name
                node.parent.mkdir(parents=True, exist_ok=True)
                node.touch()
                cli = root / relative
                cli.parent.mkdir(parents=True)
                cli.write_text("// npm entrypoint\n", encoding="utf-8")
                with patch.dict(os.environ, {"PATH": ""}):
                    self.assert_npm_command(str(node), cli)

    def test_configured_node_takes_precedence_over_an_unusable_path_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = root / "selected/node.exe"
            node.parent.mkdir()
            node.touch()
            cli = node.parent / "node_modules/npm/bin/npm-cli.js"
            cli.parent.mkdir(parents=True)
            cli.touch()
            shims = root / "shims"
            shims.mkdir()
            for name in ("npm", "npm.cmd"):
                wrapper = shims / name
                wrapper.write_text("unrelated wrapper\n", encoding="utf-8")
                wrapper.chmod(0o755)
            with patch.dict(os.environ, {"PATH": str(shims)}):
                self.assert_npm_command(str(node), cli)

    def test_node_symlink_prefers_npm_bundled_with_its_target(self):
        for node_name, relative in (("node.exe", "node_modules/npm/bin/npm-cli.js"),
                                    ("bin/node", "lib/node_modules/npm/bin/npm-cli.js")):
            with self.subTest(layout=node_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "selected" / node_name
                target.parent.mkdir(parents=True)
                target.touch()
                node = root / "other" / node_name
                node.parent.mkdir(parents=True)
                try:
                    node.symlink_to(target)
                except OSError as exc:
                    self.skipTest(f"File symlinks are unavailable: {exc}")
                cli = root / "selected" / relative
                other_cli = root / "other" / relative
                for candidate in (cli, other_cli):
                    candidate.parent.mkdir(parents=True)
                    candidate.touch()
                with patch.dict(os.environ, {"PATH": ""}):
                    self.assert_npm_command(str(node), cli)
                # Keep the symlink-directory fallback when the target has no npm.
                cli.unlink()
                with patch.dict(os.environ, {"PATH": ""}):
                    self.assert_npm_command(str(node), other_cli)

    def test_node_without_adjacent_npm_falls_back_to_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = root / "selected/node"
            node.parent.mkdir()
            node.touch()
            npm = root / "fallback/npm"
            cli = npm.parent / "node_modules/npm/bin/npm-cli.js"
            cli.parent.mkdir(parents=True)
            cli.touch()
            with patch("gateway_stability.shutil.which", side_effect=lambda name: str(npm) if name == "npm" else None):
                self.assert_npm_command(str(node), cli)

    def test_missing_npm_still_reports_the_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"PATH": ""}):
            with self.assertRaisesRegex(RuntimeError, "npm not found"):
                stability._npm_command(str(Path(tmp) / "node"))


class GatewayStabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_node_command_timeout_kills_child_and_fails(self) -> None:
        class Installer:
            returncode = None
            pid = 12345

            def __init__(self) -> None:
                self.killed = False

            async def communicate(self):
                await asyncio.Future()

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

            async def wait(self) -> int:
                return int(self.returncode or 0)

        installer = Installer()
        with (
            patch(
                "gateway_stability.asyncio.create_subprocess_exec",
                return_value=installer,
            ),
            patch(
                "gateway_stability._terminate_process_tree",
                side_effect=self._mark_killed,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                await stability._run_node_command("npm", timeout=0.01)
        self.assertTrue(installer.killed)

    @staticmethod
    async def _mark_killed(process) -> None:
        process.kill()

    async def test_auxiliary_presence_and_reaction_are_time_bounded(self) -> None:
        from whatsapp_client import WhatsAppGatewayClient
        from unittest.mock import AsyncMock
        async def stalled(*_args, **_kwargs):
            await asyncio.Future()
        client = WhatsAppGatewayClient("http://127.0.0.1:18789")
        client._request = AsyncMock(side_effect=stalled)
        with patch("gateway_stability._aux_request_timeout_seconds", return_value=0.01):
            with self.assertRaises(asyncio.TimeoutError):
                await client.send_presence("x", "composing")
            with self.assertRaises(asyncio.TimeoutError):
                await client.react("x", "m", "✅")

    async def test_process_stop_requests_authenticated_graceful_shutdown_first(self) -> None:
        from whatsapp_client import GatewayProcess
        calls = []
        class Client:
            def __init__(self, *_args, **_kwargs): pass
            async def _request(self, method, path, json_data=None):
                calls.append((method, path, self._gateway_auth_token))
                return {"ok": True}
            async def close(self): pass
        class Child:
            returncode = None
            async def wait(self):
                self.returncode = 0
                return 0
            def terminate(self):
                raise AssertionError("graceful exit must not send terminate")
        process = GatewayProcess("node", Path("gateway/whatsapp-gateway.mjs"),
                                 "127.0.0.1", 18789, Path("auth"), "info")
        process.process = Child()
        with patch("whatsapp_client.WhatsAppGatewayClient", Client):
            await process.stop()
        self.assertEqual(calls, [("POST", "/shutdown", process._gateway_auth_token)])
        self.assertIsNone(process.process)


if __name__ == "__main__":
    unittest.main()
