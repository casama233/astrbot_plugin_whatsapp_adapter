from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gateway_stability as stability


class GatewayStabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_install_timeout_kills_installer_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            gateway_dir = root / "gateway"
            gateway_dir.mkdir()
            (root / "package.json").write_text(json.dumps({"dependencies": {}}), encoding="utf-8")

            class Error(RuntimeError):
                pass

            class Process:
                script_path = gateway_dir / "whatsapp-gateway.mjs"

                @staticmethod
                def _node_dependencies_current(_project_dir: Path) -> bool:
                    return False

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
                    "gateway_stability._npm_install_timeout_seconds",
                    return_value=0.01,
                ),
                patch(
                    "gateway_stability._terminate_process_tree",
                    side_effect=lambda process: self._mark_killed(process),
                ),
            ):
                with self.assertRaisesRegex(Error, "timed out"):
                    await stability._bounded_node_dependency_install(Process(), Error)
            self.assertTrue(installer.killed)

    @staticmethod
    async def _mark_killed(process) -> None:
        process.kill()

    async def test_auxiliary_presence_and_reaction_are_time_bounded(self) -> None:
        class Error(RuntimeError):
            pass

        class Client:
            async def send_presence(self, *_args, **_kwargs):
                await asyncio.Future()

            async def react(self, *_args, **_kwargs):
                await asyncio.Future()

        class Process:
            async def _ensure_node_dependencies(self):
                return None

            async def stop(self):
                return None

        stability.install_gateway_runtime_stability(Client, Process, Error)
        client = Client()
        with patch("gateway_stability._aux_request_timeout_seconds", return_value=0.01):
            with self.assertRaises(asyncio.TimeoutError):
                await client.send_presence("x", "composing")
            with self.assertRaises(asyncio.TimeoutError):
                await client.react("x", "m", "✅")

    async def test_process_stop_requests_authenticated_graceful_shutdown_first(self) -> None:
        calls: list[tuple[str, str]] = []

        class Error(RuntimeError):
            pass

        class Client:
            def __init__(self, base_url: str, timeout: float = 1.0) -> None:
                self.base_url = base_url
                self.timeout = timeout

            async def _request(self, method: str, path: str, json_data=None):
                calls.append((method, path))
                return {"ok": True}

            async def close(self) -> None:
                return None

        class Child:
            returncode = None

            async def wait(self) -> int:
                self.returncode = 0
                return 0

        class Process:
            def __init__(self) -> None:
                self.host = "127.0.0.1"
                self.port = 18789
                self.process = Child()
                self._gateway_auth_token = "token"
                self.original_stop_called = False

            async def _ensure_node_dependencies(self):
                return None

            async def stop(self):
                self.original_stop_called = True
                self.process = None

        stability.install_gateway_runtime_stability(Client, Process, Error)
        process = Process()
        await process.stop()
        self.assertIn(("POST", "/shutdown"), calls)
        self.assertTrue(process.original_stop_called)

    def test_adapter_installs_runtime_stability_after_transport_security(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "whatsapp_adapter.py").read_text(
            encoding="utf-8"
        )
        security_index = source.index("_install_gateway_transport_security(")
        stability_index = source.index("_install_gateway_runtime_stability(")
        self.assertGreater(stability_index, security_index)


if __name__ == "__main__":
    unittest.main()
