from __future__ import annotations

import asyncio
import os
import unittest

import gateway_security as security


def _fake_classes():
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
            self.started_with = None

        async def start(self) -> None:
            await asyncio.sleep(0)
            self.started_with = os.environ.get("WA_GATEWAY_TOKEN")

    return Client, Process


class GatewaySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        security._GATEWAY_TOKENS.clear()
        os.environ.pop("WA_GATEWAY_TOKEN", None)

    async def test_process_token_is_random_registered_and_forwarded_to_client(self) -> None:
        client_cls, process_cls = _fake_classes()
        security.install_gateway_transport_security(client_cls, process_cls)
        process = process_cls()
        client = client_cls("http://127.0.0.1:18789")

        result = await client._request("GET", "/health")
        self.assertTrue(result["authorization"].startswith("Bearer "))
        token = result["authorization"].removeprefix("Bearer ")
        self.assertGreaterEqual(len(token), 40)

        await process.start()
        self.assertEqual(process.started_with, token)
        self.assertIsNone(os.environ.get("WA_GATEWAY_TOKEN"))

    async def test_events_receive_authorization_header(self) -> None:
        client_cls, process_cls = _fake_classes()
        security.install_gateway_transport_security(client_cls, process_cls)
        process_cls(port=18800)
        client = client_cls("http://127.0.0.1:18800")

        events = [event async for event in client.events()]
        self.assertTrue(events[0]["authorization"].startswith("Bearer "))

    async def test_patch_is_idempotent_per_class_and_can_patch_reloaded_classes(self) -> None:
        client_cls, process_cls = _fake_classes()
        security.install_gateway_transport_security(client_cls, process_cls)
        request_method = client_cls._request
        security.install_gateway_transport_security(client_cls, process_cls)
        self.assertIs(client_cls._request, request_method)

        reloaded_client_cls, reloaded_process_cls = _fake_classes()
        security.install_gateway_transport_security(reloaded_client_cls, reloaded_process_cls)
        reloaded_process_cls(port=18801)
        client = reloaded_client_cls("http://127.0.0.1:18801")
        result = await client._request("GET", "/health")
        self.assertTrue(result["authorization"].startswith("Bearer "))


if __name__ == "__main__":
    unittest.main()
