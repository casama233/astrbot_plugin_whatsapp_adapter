from __future__ import annotations

import asyncio
import os
import json
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
from whatsapp_client import WhatsAppGatewayClient, GatewayProcess
import gateway_security as security


def _fake_classes(root: Path):
    class Response:
        status = 200
        def __init__(self, session):
            self.session = session
            self.content = self.events()
        async def text(self):
            return json.dumps({"authorization": self.session.headers.get("Authorization")})
        async def events(self):
            yield ("data: " + await self.text() + "\n").encode()
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        def raise_for_status(self): pass
        def release(self): pass

    class Session:
        closed = False
        def __init__(self): self.headers = {}
        def request(self, *_args, **_kwargs): return Response(self)
        async def get(self, *_args, **_kwargs): return Response(self)
        async def close(self): self.closed = True

    class Client(WhatsAppGatewayClient):
        async def start(self):
            if self._session is None: self._session = Session()

    class Process(GatewayProcess):
        def __init__(self, host="127.0.0.1", port=18789):
            super().__init__("node", root / "gateway/whatsapp-gateway.mjs", host, port,
                             root / f"auth-{port}", "info", root / "plugin_data" / f"adapter-{port}")
        async def _ensure_node_runtime(self): await asyncio.sleep(0)
        async def _ensure_node_dependencies(self): await asyncio.sleep(0)
    return Client, Process


class _ChildProcess:
    returncode = None
    pid = 2147483647
    def __init__(self): self.exited = asyncio.Event()
    def terminate(self):
        self.returncode = 0
        self.exited.set()
    def kill(self): self.terminate()
    async def wait(self):
        await self.exited.wait()
        return self.returncode


class GatewaySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        security._GATEWAY_TOKENS.clear()
        os.environ.pop("WA_GATEWAY_TOKEN", None)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)

    async def asyncTearDown(self) -> None:
        os.environ.pop("WA_GATEWAY_TOKEN", None)
        self._temp_dir.cleanup()

    async def test_process_token_is_random_registered_and_child_only(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        process = process_cls()
        client = client_cls("http://127.0.0.1:18789")

        self.assertNotIn(("127.0.0.1", 18789), security._GATEWAY_TOKENS)
        token = security.bind_gateway_client(client, process)

        result = await client._request("GET", "/health")
        self.assertEqual(result["authorization"], f"Bearer {token}")
        self.assertGreaterEqual(len(token), 40)

        os.environ["WA_GATEWAY_TOKEN"] = "external-parent-token"
        captured_env: dict[str, str] = {}

        async def create_child(*_args, **kwargs):
            captured_env.update(kwargs["env"])
            return _ChildProcess()

        with patch(
            "whatsapp_client.asyncio.create_subprocess_exec",
            side_effect=create_child,
        ):
            await process.start()

        self.assertEqual(captured_env["WA_GATEWAY_TOKEN"], token)
        self.assertEqual(
            captured_env["WA_MEDIA_ALLOWED_ROOTS"],
            str(self.root.resolve()),
        )
        self.assertEqual(os.environ["WA_GATEWAY_TOKEN"], "external-parent-token")
        self.assertEqual(
            (await client._request("GET", "/health"))["authorization"],
            f"Bearer {token}",
        )

    async def test_external_token_is_used_only_without_managed_endpoint(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        os.environ["WA_GATEWAY_TOKEN"] = "external-token"
        external_client = client_cls("http://127.0.0.1:19000")
        self.assertEqual(
            (await external_client._request("GET", "/health"))["authorization"],
            "Bearer external-token",
        )

        managed = process_cls(port=19001)
        managed_client = client_cls("http://127.0.0.1:19001")
        self.assertEqual(
            (await managed_client._request("GET", "/health"))["authorization"],
            "Bearer external-token",
        )
        managed_token = security.bind_gateway_client(managed_client, managed)
        self.assertEqual(
            (await managed_client._request("GET", "/health"))["authorization"],
            f"Bearer {managed_token}",
        )

        await managed.stop()
        replacement_client = client_cls("http://127.0.0.1:19001")
        self.assertEqual(
            (await replacement_client._request("GET", "/health"))["authorization"],
            "Bearer external-token",
        )

    async def test_events_receive_authorization_header(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        process = process_cls(port=18800)
        client = client_cls("http://127.0.0.1:18800")
        security.bind_gateway_client(client, process)

        with patch("whatsapp_client.aiohttp.ClientTimeout", create=True):
            events = [event async for event in client.events()]
        self.assertTrue(events[0]["authorization"].startswith("Bearer "))


    async def test_failed_spawn_does_not_replace_live_endpoint_token(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        security._GATEWAY_TOKENS[("127.0.0.1", 18789)] = "live-token"
        replacement = process_cls()

        with patch(
            "whatsapp_client.asyncio.create_subprocess_exec",
            side_effect=OSError("spawn failed"),
        ):
            with self.assertRaisesRegex(OSError, "spawn failed"):
                await replacement.start()

        self.assertEqual(
            security._GATEWAY_TOKENS[("127.0.0.1", 18789)],
            "live-token",
        )

    async def test_explicit_binding_survives_registry_generation_loss(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        process = process_cls(port=18802)
        client = client_cls("http://127.0.0.1:18802")
        token = security.bind_gateway_client(client, process)
        security._GATEWAY_TOKENS.clear()

        self.assertEqual(
            (await client._request("GET", "/health"))["authorization"],
            f"Bearer {token}",
        )

    async def test_binding_is_scoped_to_endpoint_and_can_be_cleared(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        os.environ["WA_GATEWAY_TOKEN"] = "external-token"
        process = process_cls(port=18804)
        client = client_cls("http://127.0.0.1:18804")
        token = security.bind_gateway_client(client, process)
        self.assertGreaterEqual(len(token), 40)

        client.base_url = "http://127.0.0.1:18805"
        self.assertEqual(
            (await client._request("GET", "/health"))["authorization"],
            "Bearer external-token",
        )
        client.base_url = "http://127.0.0.1:18804"
        self.assertTrue(security.clear_gateway_client_binding(client, process))
        self.assertEqual(
            (await client._request("GET", "/health"))["authorization"],
            "Bearer external-token",
        )



if __name__ == "__main__":
    unittest.main()
