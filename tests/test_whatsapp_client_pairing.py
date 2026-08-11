from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_client_module():
    aiohttp = types.ModuleType("aiohttp")
    spec = importlib.util.spec_from_file_location(
        "_whatsapp_client_pairing_target",
        ROOT / "whatsapp_client.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"aiohttp": aiohttp}):
        spec.loader.exec_module(module)
    return module


CLIENT_MODULE = _load_client_module()
WhatsAppGatewayClient = CLIENT_MODULE.WhatsAppGatewayClient
WhatsAppGatewayError = CLIENT_MODULE.WhatsAppGatewayError


class WhatsAppClientPairingTests(unittest.IsolatedAsyncioTestCase):
    async def test_pair_code_posts_only_the_supplied_phone(self) -> None:
        client = WhatsAppGatewayClient("http://127.0.0.1:18789")
        calls: list[tuple[str, str, dict | None]] = []

        async def request(method: str, path: str, json_data=None):
            calls.append((method, path, json_data))
            return {"ok": True, "code": "AB12-CD34"}

        client._request = request  # type: ignore[method-assign]
        result = await client.pair_code("8613800138000")

        self.assertEqual(result, {"ok": True, "code": "AB12-CD34"})
        self.assertEqual(
            calls,
            [("POST", "/pair-code", {"phone": "8613800138000"})],
        )

    async def test_gateway_error_can_carry_http_status_without_changing_text(self) -> None:
        error = WhatsAppGatewayError("gateway rejected request", status_code=409)
        self.assertEqual(str(error), "gateway rejected request")
        self.assertEqual(error.status_code, 409)


if __name__ == "__main__":
    unittest.main()
