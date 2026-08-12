from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import whatsapp_config_policy as policy


def setup_function() -> None:
    policy._reset_multi_instance_registry_for_tests()


def _runtime_defaults() -> dict:
    return {
        "gateway_host": "127.0.0.1",
        "gateway_port": 18789,
        "auto_start_gateway": True,
        "auth_dir": "",
    }


def test_default_instance_keeps_legacy_endpoint_and_auth_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_port_available", lambda host, port: True)
    monkeypatch.setattr(policy, "_plugin_data_dir", lambda: tmp_path)

    merged = policy.merge_runtime_config(
        _runtime_defaults(),
        {},
        {"id": "whatsapp"},
    )

    assert merged["gateway_port"] == 18789
    assert merged["auth_dir"] == ""


def test_auto_start_instances_get_stable_unique_ports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_port_available", lambda host, port: True)
    monkeypatch.setattr(policy, "_plugin_data_dir", lambda: tmp_path)

    first = policy.merge_runtime_config(_runtime_defaults(), {}, {"id": "whatsapp2"})
    second = policy.merge_runtime_config(_runtime_defaults(), {}, {"id": "whatsapp3"})
    reloaded = policy.merge_runtime_config(_runtime_defaults(), {}, {"id": "whatsapp2"})

    assert first["gateway_port"] == 18790
    assert second["gateway_port"] == 18791
    assert reloaded["gateway_port"] == first["gateway_port"]
    assert first["auth_dir"] == str((tmp_path / "whatsapp-auth-whatsapp2").resolve())
    assert second["auth_dir"] == str((tmp_path / "whatsapp-auth-whatsapp3").resolve())


def test_allocator_skips_ports_already_owned_by_other_processes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_plugin_data_dir", lambda: tmp_path)
    monkeypatch.setattr(policy, "_port_available", lambda host, port: port != 18790)

    merged = policy.merge_runtime_config(_runtime_defaults(), {}, {"id": "whatsapp2"})

    assert merged["gateway_port"] == 18791


def test_external_gateway_endpoint_is_never_rewritten(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_plugin_data_dir", lambda: tmp_path)
    monkeypatch.setattr(policy, "_port_available", lambda host, port: False)

    merged = policy.merge_runtime_config(
        _runtime_defaults(),
        {"gateway_host": "10.0.0.20", "gateway_port": 19999, "auto_start_gateway": False},
        {"id": "remote-2"},
    )

    assert merged["gateway_host"] == "10.0.0.20"
    assert merged["gateway_port"] == 19999
    assert merged["auto_start_gateway"] is False


def test_explicit_auth_dir_is_suffixed_per_instance(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_port_available", lambda host, port: True)
    configured = tmp_path / "shared-auth"

    merged = policy.merge_runtime_config(
        _runtime_defaults(),
        {"auth_dir": str(configured)},
        {"id": "whatsapp2"},
    )

    assert merged["auth_dir"] == str(configured.with_name("shared-auth-whatsapp2").resolve())


def test_unsafe_instance_id_cannot_escape_auth_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_port_available", lambda host, port: True)
    monkeypatch.setattr(policy, "_plugin_data_dir", lambda: tmp_path)

    merged = policy.merge_runtime_config(
        _runtime_defaults(),
        {},
        {"id": "../../team/a"},
    )

    auth_dir = Path(merged["auth_dir"])
    assert auth_dir.parent == tmp_path.resolve()
    assert ".." not in auth_dir.name
    assert "/" not in auth_dir.name
    assert auth_dir.name.startswith("whatsapp-auth-")


def test_concurrent_initialization_does_not_duplicate_ports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(policy, "_port_available", lambda host, port: True)
    monkeypatch.setattr(policy, "_plugin_data_dir", lambda: tmp_path)

    ids = [f"whatsapp{i}" for i in range(2, 12)]

    def resolve(instance_id: str) -> int:
        merged = policy.merge_runtime_config(_runtime_defaults(), {}, {"id": instance_id})
        return int(merged["gateway_port"])

    with ThreadPoolExecutor(max_workers=len(ids)) as executor:
        ports = list(executor.map(resolve, ids))

    assert len(set(ports)) == len(ids)
    assert min(ports) >= 18790
