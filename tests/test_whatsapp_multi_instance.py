from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from whatsapp_multi_instance import (
    GatewayPortLeaseRegistry,
    ensure_gateway_endpoint,
    instance_auth_dir,
    release_gateway_endpoint,
    safe_instance_slug,
)


class _Adapter:
    def __init__(self, **config) -> None:
        self.config = {
            "gateway_host": "127.0.0.1",
            "gateway_port": 18789,
            "auto_start_gateway": True,
            "id": "whatsapp",
            **config,
        }


class MultiInstanceRuntimeTests(unittest.TestCase):
    def test_registry_allocates_unique_stable_ports(self) -> None:
        registry = GatewayPortLeaseRegistry()
        owner_a = object()
        owner_b = object()
        probe = lambda _host, _port: True

        self.assertEqual(
            registry.acquire(owner_a, "127.0.0.1", 18789, check_os=False, port_probe=probe),
            18789,
        )
        self.assertEqual(
            registry.acquire(owner_b, "127.0.0.1", 18789, check_os=False, port_probe=probe),
            18790,
        )
        self.assertEqual(
            registry.acquire(owner_b, "127.0.0.1", 18789, check_os=False, port_probe=probe),
            18790,
        )

    def test_release_makes_port_reusable(self) -> None:
        registry = GatewayPortLeaseRegistry()
        probe = lambda _host, _port: True
        owner_a = object()
        owner_b = object()
        registry.acquire(owner_a, "127.0.0.1", 18789, check_os=False, port_probe=probe)
        registry.release(owner_a)
        self.assertEqual(
            registry.acquire(owner_b, "127.0.0.1", 18789, check_os=False, port_probe=probe),
            18789,
        )

    def test_secondary_reserves_default_port_even_if_it_starts_first(self) -> None:
        registry = GatewayPortLeaseRegistry()
        secondary = _Adapter(id="whatsapp2")
        default = _Adapter(id="whatsapp")

        secondary_endpoint = ensure_gateway_endpoint(
            secondary,
            registry=registry,
            port_probe=lambda _host, _port: True,
        )
        default_endpoint = ensure_gateway_endpoint(
            default,
            registry=registry,
            port_probe=lambda _host, _port: True,
        )

        self.assertEqual(secondary_endpoint, ("127.0.0.1", 18790))
        self.assertEqual(default_endpoint, ("127.0.0.1", 18789))

    def test_secondary_skips_already_bound_candidate_port(self) -> None:
        adapter = _Adapter(id="whatsapp2")
        registry = GatewayPortLeaseRegistry()
        endpoint = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, port: port != 18790,
        )
        self.assertEqual(endpoint, ("127.0.0.1", 18791))

    def test_default_instance_keeps_management_page_compatibility(self) -> None:
        adapter = _Adapter(id="whatsapp")
        registry = GatewayPortLeaseRegistry()
        endpoint = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, _port: False,
        )
        self.assertEqual(endpoint, ("127.0.0.1", 18789))

    def test_external_gateway_mode_never_takes_a_local_lease(self) -> None:
        adapter = _Adapter(id="whatsapp2", auto_start_gateway=False, gateway_port=19001)
        registry = GatewayPortLeaseRegistry()
        endpoint = ensure_gateway_endpoint(adapter, registry=registry)
        self.assertEqual(endpoint, ("127.0.0.1", 19001))
        self.assertEqual(registry.leased_ports(), set())

    def test_endpoint_does_not_drift_across_reconnect_style_reads(self) -> None:
        adapter = _Adapter(id="whatsapp2")
        registry = GatewayPortLeaseRegistry()
        first = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, port: port == 18790,
        )
        second = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, _port: False,
        )
        self.assertEqual(first, second)
        self.assertEqual(first[1], 18790)

    def test_release_allows_reload_to_resolve_a_new_endpoint(self) -> None:
        adapter = _Adapter(id="whatsapp2")
        registry = GatewayPortLeaseRegistry()
        first = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, _port: True,
        )
        release_gateway_endpoint(adapter)
        adapter.config["gateway_port"] = 19000
        second = ensure_gateway_endpoint(
            adapter,
            registry=registry,
            port_probe=lambda _host, _port: True,
        )
        self.assertEqual(first[1], 18790)
        self.assertEqual(second[1], 19001)

    def test_concurrent_secondary_allocations_are_unique(self) -> None:
        registry = GatewayPortLeaseRegistry()
        adapters = [_Adapter(id=f"whatsapp{index}") for index in range(2, 10)]

        def allocate(adapter: _Adapter) -> int:
            return ensure_gateway_endpoint(
                adapter,
                registry=registry,
                port_probe=lambda _host, _port: True,
            )[1]

        with ThreadPoolExecutor(max_workers=len(adapters)) as pool:
            ports = list(pool.map(allocate, adapters))

        self.assertEqual(len(set(ports)), len(adapters))
        self.assertEqual(set(ports), set(range(18790, 18798)))

    def test_instance_auth_dir_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth_dir = instance_auth_dir(root, {"id": "../../evil"})
            self.assertEqual(auth_dir.parent, root)
            self.assertTrue(auth_dir.name.startswith("whatsapp-auth-"))
            self.assertNotIn("..", auth_dir.name)

    def test_unsafe_ids_that_normalize_the_same_get_distinct_slugs(self) -> None:
        self.assertNotEqual(safe_instance_slug("a/b"), safe_instance_slug("a?b"))

    def test_invalid_gateway_port_is_rejected(self) -> None:
        registry = GatewayPortLeaseRegistry()
        with self.assertRaises(ValueError):
            registry.acquire(
                object(),
                "127.0.0.1",
                70000,
                check_os=False,
                port_probe=lambda _host, _port: True,
            )


if __name__ == "__main__":
    unittest.main()
