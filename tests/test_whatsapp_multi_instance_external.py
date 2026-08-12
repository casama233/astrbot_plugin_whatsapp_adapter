from __future__ import annotations

import unittest

from whatsapp_multi_instance import (
    GatewayPortLeaseRegistry,
    ensure_gateway_endpoint,
    release_gateway_endpoint,
)


class _Adapter:
    def __init__(self, instance_id: str, port: int = 19001) -> None:
        self.config = {
            "gateway_host": "127.0.0.1",
            "gateway_port": port,
            "auto_start_gateway": False,
            "id": instance_id,
        }


class MultiInstanceExternalGatewayTests(unittest.TestCase):
    def test_single_external_endpoint_claim_does_not_lease_local_port(self) -> None:
        registry = GatewayPortLeaseRegistry()
        adapter = _Adapter("whatsapp2")
        self.assertEqual(
            ensure_gateway_endpoint(adapter, registry=registry),
            ("127.0.0.1", 19001),
        )
        self.assertEqual(registry.leased_ports(), set())
        self.assertEqual(registry.external_endpoints(), {("127.0.0.1", 19001)})

    def test_duplicate_external_endpoint_claim_fails_closed(self) -> None:
        registry = GatewayPortLeaseRegistry()
        first = _Adapter("whatsapp")
        second = _Adapter("whatsapp2")
        ensure_gateway_endpoint(first, registry=registry)
        with self.assertRaisesRegex(RuntimeError, "distinct external Gateway"):
            ensure_gateway_endpoint(second, registry=registry)

    def test_external_endpoint_is_reusable_after_owner_terminates(self) -> None:
        registry = GatewayPortLeaseRegistry()
        first = _Adapter("whatsapp")
        second = _Adapter("whatsapp2")
        ensure_gateway_endpoint(first, registry=registry)
        release_gateway_endpoint(first)
        self.assertEqual(
            ensure_gateway_endpoint(second, registry=registry),
            ("127.0.0.1", 19001),
        )

    def test_distinct_external_endpoints_can_be_claimed_concurrently(self) -> None:
        registry = GatewayPortLeaseRegistry()
        first = _Adapter("whatsapp", port=19001)
        second = _Adapter("whatsapp2", port=19002)
        self.assertEqual(
            ensure_gateway_endpoint(first, registry=registry),
            ("127.0.0.1", 19001),
        )
        self.assertEqual(
            ensure_gateway_endpoint(second, registry=registry),
            ("127.0.0.1", 19002),
        )
        self.assertEqual(
            registry.external_endpoints(),
            {("127.0.0.1", 19001), ("127.0.0.1", 19002)},
        )


if __name__ == "__main__":
    unittest.main()
