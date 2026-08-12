from __future__ import annotations

import unittest

from whatsapp_multi_instance import (
    GatewayPortLeaseRegistry,
    ensure_gateway_endpoint,
    reallocate_after_bind_conflict,
)


class _Child:
    def __init__(self, returncode) -> None:
        self.returncode = returncode


class _GatewayProcess:
    def __init__(self, returncode) -> None:
        self.process = _Child(returncode)


class _Adapter:
    def __init__(self, instance_id: str = "whatsapp2") -> None:
        self.config = {
            "gateway_host": "127.0.0.1",
            "gateway_port": 18789,
            "auto_start_gateway": True,
            "id": instance_id,
        }
        self.gateway_process = None


class MultiInstanceRecoveryTests(unittest.TestCase):
    def test_confirmed_secondary_bind_conflict_moves_to_next_port(self) -> None:
        adapter = _Adapter()
        registry = GatewayPortLeaseRegistry()
        self.assertEqual(
            ensure_gateway_endpoint(
                adapter,
                registry=registry,
                port_probe=lambda _host, _port: True,
            ),
            ("127.0.0.1", 18790),
        )
        adapter.gateway_process = _GatewayProcess(returncode=1)

        replacement = reallocate_after_bind_conflict(
            adapter,
            port_probe=lambda _host, port: port != 18790,
        )
        self.assertEqual(replacement, ("127.0.0.1", 18791))
        self.assertEqual(registry.leased_ports(), {18791})

    def test_running_child_does_not_trigger_port_drift(self) -> None:
        adapter = _Adapter()
        registry = GatewayPortLeaseRegistry()
        original = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, _port: True,
        )
        adapter.gateway_process = _GatewayProcess(returncode=None)

        self.assertIsNone(
            reallocate_after_bind_conflict(
                adapter,
                port_probe=lambda _host, _port: False,
            )
        )
        self.assertEqual(adapter._gateway_runtime_endpoint, original)

    def test_non_bind_failure_keeps_stable_endpoint(self) -> None:
        adapter = _Adapter()
        registry = GatewayPortLeaseRegistry()
        original = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, _port: True,
        )
        adapter.gateway_process = _GatewayProcess(returncode=1)

        self.assertIsNone(
            reallocate_after_bind_conflict(
                adapter,
                port_probe=lambda _host, _port: True,
            )
        )
        self.assertEqual(adapter._gateway_runtime_endpoint, original)

    def test_default_instance_never_drifts_from_management_port(self) -> None:
        adapter = _Adapter(instance_id="whatsapp")
        registry = GatewayPortLeaseRegistry()
        self.assertEqual(
            ensure_gateway_endpoint(
                adapter,
                registry=registry,
                port_probe=lambda _host, _port: True,
            ),
            ("127.0.0.1", 18789),
        )
        adapter.gateway_process = _GatewayProcess(returncode=1)
        self.assertIsNone(
            reallocate_after_bind_conflict(
                adapter,
                port_probe=lambda _host, _port: False,
            )
        )
        self.assertEqual(adapter._gateway_runtime_endpoint, ("127.0.0.1", 18789))


if __name__ == "__main__":
    unittest.main()
