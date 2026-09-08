from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import gateway_stability as stability


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
