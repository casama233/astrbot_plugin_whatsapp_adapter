from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gateway_security as security


def _fake_classes(root: Path):
    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

    class Client:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url
            self._session = None

        async def start(self) -> None:
            if self._session is None:
                self._session = Session()

        async def _request(self, *_args, **_kwargs):
            return {"authorization": self._session.headers.get("Authorization")}

        async def events(self):
            yield {"authorization": self._session.headers.get("Authorization")}

    class Process:
        def __init__(self, host="127.0.0.1", port=18789) -> None:
            self.host = host
            self.port = port
            self.node_executable = "node"
            self.script_path = root / "gateway" / "whatsapp-gateway.mjs"
            self.auth_dir = root / f"auth-{port}"
            self.data_dir = root / "plugin_data" / f"adapter-{port}"
            self.log_level = "info"
            self.process = None
            self.stopped = False

        async def _ensure_node_runtime(self) -> None:
            await asyncio.sleep(0)

        async def _ensure_node_dependencies(self) -> None:
            await asyncio.sleep(0)

        async def start(self) -> None:
            raise AssertionError("original start must be replaced")

        async def stop(self) -> None:
            self.stopped = True
            self.process = None

    return Client, Process


class _ChildProcess:
    returncode = None


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
        security.install_gateway_transport_security(client_cls, process_cls)
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
            "gateway_security.asyncio.create_subprocess_exec",
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
        security.install_gateway_transport_security(client_cls, process_cls)
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
        security.install_gateway_transport_security(client_cls, process_cls)
        process = process_cls(port=18800)
        client = client_cls("http://127.0.0.1:18800")
        security.bind_gateway_client(client, process)

        events = [event async for event in client.events()]
        self.assertTrue(events[0]["authorization"].startswith("Bearer "))

    async def test_patch_is_idempotent_per_class_and_can_patch_reloaded_classes(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        security.install_gateway_transport_security(client_cls, process_cls)
        request_method = client_cls._request
        security.install_gateway_transport_security(client_cls, process_cls)
        self.assertIs(client_cls._request, request_method)

        reloaded_client_cls, reloaded_process_cls = _fake_classes(self.root)
        security.install_gateway_transport_security(
            reloaded_client_cls,
            reloaded_process_cls,
        )
        process = reloaded_process_cls(port=18801)
        client = reloaded_client_cls("http://127.0.0.1:18801")
        security.bind_gateway_client(client, process)
        result = await client._request("GET", "/health")
        self.assertTrue(result["authorization"].startswith("Bearer "))

    async def test_failed_spawn_does_not_replace_live_endpoint_token(self) -> None:
        client_cls, process_cls = _fake_classes(self.root)
        security.install_gateway_transport_security(client_cls, process_cls)
        security._GATEWAY_TOKENS[("127.0.0.1", 18789)] = "live-token"
        replacement = process_cls()

        with patch(
            "gateway_security.asyncio.create_subprocess_exec",
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
        security.install_gateway_transport_security(client_cls, process_cls)
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
        security.install_gateway_transport_security(client_cls, process_cls)
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

    async def test_partial_reload_patches_only_the_unpatched_class(self) -> None:
        old_client_cls, old_process_cls = _fake_classes(self.root)
        security.install_gateway_transport_security(old_client_cls, old_process_cls)
        old_process_start = old_process_cls.start

        new_client_cls, _unused_process_cls = _fake_classes(self.root)
        security.install_gateway_transport_security(new_client_cls, old_process_cls)

        self.assertIs(old_process_cls.start, old_process_start)
        process = old_process_cls(port=18803)
        client = new_client_cls("http://127.0.0.1:18803")
        token = security.bind_gateway_client(client, process)
        self.assertEqual(
            (await client._request("GET", "/health"))["authorization"],
            f"Bearer {token}",
        )


if __name__ == "__main__":
    unittest.main()
