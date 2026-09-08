from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
from whatsapp_client import GatewayProcess, WhatsAppGatewayError
import gateway_dependencies as dependencies
import gateway_stability as stability

NODE = {"major": 22, "abi": "127", "platform": "linux", "arch": "x64"}


def write_project(root: Path, versions=None, installed=True):
    versions = versions or {"@whiskeysockets/baileys": "7.0.0-rc14", "ws": "8.21.0"}
    direct = {"@whiskeysockets/baileys": versions["@whiskeysockets/baileys"]}
    (root / "package.json").write_text(json.dumps({"dependencies": direct}), encoding="utf-8")
    packages = {"": {"dependencies": direct}}
    for name, version in versions.items():
        packages[f"node_modules/{name}"] = {"version": version}
        if installed:
            target = root / "node_modules" / name / "package.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"version": version}), encoding="utf-8")
    (root / "package-lock.json").write_text(json.dumps({"packages": packages}), encoding="utf-8")
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts/patch-baileys-ephemeral.mjs").write_text("// fixture\n", encoding="utf-8")
    return packages


def receipt(root):
    dependencies.record_dependency_install(root, NODE, dependencies.dependency_fingerprint(root))


def process(root):
    result = GatewayProcess("node", root / "gateway/whatsapp-gateway.mjs", "127.0.0.1", 18789, root / "auth", "info")
    result._node_identity = NODE
    return result


class DependencyReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packages = write_project(self.root)
        receipt(self.root)

    def current(self):
        return dependencies.dependencies_current(self.root, NODE)

    def test_matching_locked_dependencies_skip_install(self):
        self.assertTrue(self.current())

    def test_indirect_dependency_change_is_not_missed(self):
        target = self.root / "node_modules/ws/package.json"
        target.write_text('{"version":"8.20.0"}', encoding="utf-8")
        self.assertFalse(self.current())

    def test_nested_indirect_dependency_is_checked(self):
        self.packages['node_modules/a/node_modules/b'] = {"version": "1.0.0"}
        (self.root / "package-lock.json").write_text(json.dumps({"packages": self.packages}), encoding="utf-8")
        self.assertFalse(dependencies.installed_tree_matches(self.root))

    def test_lockfile_only_change_invalidates_receipt(self):
        target = self.root / "package-lock.json"
        target.write_text(target.read_text() + "\n", encoding="utf-8")
        self.assertFalse(self.current())

    def test_patch_only_change_invalidates_receipt(self):
        (self.root / "scripts/patch-baileys-ephemeral.mjs").write_text("// revised\n", encoding="utf-8")
        self.assertFalse(self.current())

    def test_crlf_checkout_does_not_force_reinstall(self):
        for relative in ("package.json", "package-lock.json", "scripts/patch-baileys-ephemeral.mjs"):
            p = self.root / relative
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.assertTrue(self.current())

    def test_missing_required_package_is_stale(self):
        (self.root / "node_modules/ws/package.json").unlink()
        self.assertFalse(self.current())

    def test_absent_optional_package_is_allowed_but_stale_installed_one_is_not(self):
        self.packages['node_modules/native-other-platform'] = {"version": "1.0.0", "optional": True}
        (self.root / "package-lock.json").write_text(json.dumps({"packages": self.packages}), encoding="utf-8")
        receipt(self.root)
        self.assertTrue(self.current())
        target = self.root / "node_modules/native-other-platform/package.json"
        target.parent.mkdir()
        target.write_text('{"version":"0.9.0"}', encoding="utf-8")
        self.assertFalse(self.current())

    def test_old_install_without_receipt_is_not_claimed_verified(self):
        (self.root / "node_modules" / dependencies.RECEIPT_NAME).unlink()
        self.assertFalse(self.current())

    def test_corrupt_receipt_does_not_pass(self):
        (self.root / "node_modules" / dependencies.RECEIPT_NAME).write_text('[]', encoding="utf-8")
        self.assertFalse(self.current())

    def test_node_abi_or_arch_change_requires_repair(self):
        for field in ("major", "abi", "arch"):
            self.assertFalse(dependencies.dependencies_current(self.root, {**NODE, field: 'other'}))

    def test_source_change_during_install_cannot_receive_receipt(self):
        before = dependencies.dependency_fingerprint(self.root)
        (self.root / "package.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            dependencies.record_dependency_install(self.root, NODE, before)

    def test_lock_missing_direct_dependency_is_not_current(self):
        del self.packages['node_modules/@whiskeysockets/baileys']
        (self.root / "package-lock.json").write_text(json.dumps({"packages": self.packages}), encoding="utf-8")
        self.assertFalse(dependencies.installed_tree_matches(self.root))


class GatewayProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_project(self.root, installed=False)
        self.command_patch = patch('gateway_stability._npm_command', return_value=['node', 'npm-cli.js'])
        self.command_patch.start()
        self.addCleanup(self.command_patch.stop)

    async def successful_command(self, *command, **kwargs):
        if 'ci' in command:
            await asyncio.sleep(0.01)
            write_project(self.root)
        return b''

    async def test_concurrent_dependency_checks_install_once(self):
        with patch('gateway_stability._run_node_command', side_effect=self.successful_command) as run:
            await asyncio.gather(*(process(self.root)._ensure_node_dependencies() for _ in range(2)))
        self.assertEqual(sum('ci' in c.args for c in run.call_args_list), 1)
        self.assertEqual(run.call_count, 3)  # npm ci, explicit patch, import smoke.
        self.assertIn('--ignore-scripts=false', run.call_args_list[0].args)
        self.assertTrue(GatewayProcess._node_dependencies_current(self.root))

    async def test_matching_receipt_skips_all_install_commands(self):
        write_project(self.root)
        receipt(self.root)
        with patch('gateway_stability._run_node_command') as run:
            await process(self.root)._ensure_node_dependencies()
        run.assert_not_called()

    async def test_failed_patch_or_smoke_never_writes_success_receipt(self):
        for failed_stage in (1, 2):
            with self.subTest(stage=failed_stage):
                (self.root / 'node_modules' / dependencies.RECEIPT_NAME).unlink(missing_ok=True)
                count = 0
                async def command(*args, **kwargs):
                    nonlocal count
                    stage, count = count, count + 1
                    if stage == failed_stage:
                        raise RuntimeError('stage failure')
                    return await self.successful_command(*args, **kwargs)
                with patch('gateway_stability._run_node_command', side_effect=command):
                    with self.assertRaisesRegex(WhatsAppGatewayError, 'stage failure'):
                        await process(self.root)._ensure_node_dependencies()
                self.assertFalse(GatewayProcess._node_dependencies_current(self.root))

    async def test_live_gateway_prevents_destructive_install(self):
        child = SimpleNamespace(returncode=None)
        dependencies.register_gateway_child(self.root, child)
        with patch('gateway_stability._run_node_command') as run:
            with self.assertRaisesRegex(WhatsAppGatewayError, 'another managed Gateway'):
                await process(self.root)._ensure_node_dependencies()
        run.assert_not_called()
        child.returncode = 0
        with patch('gateway_stability._run_node_command', side_effect=self.successful_command):
            await process(self.root)._ensure_node_dependencies()
        self.assertTrue(GatewayProcess._node_dependencies_current(self.root))

    async def test_valid_dependencies_may_be_shared_by_live_gateway(self):
        write_project(self.root)
        receipt(self.root)
        dependencies.register_gateway_child(self.root, SimpleNamespace(returncode=None))
        with patch('gateway_stability._run_node_command') as run:
            await process(self.root)._ensure_node_dependencies()
        run.assert_not_called()

    async def test_directory_ownership_survives_plugin_module_reimport(self):
        import importlib.util
        dependencies.register_gateway_child(self.root, SimpleNamespace(returncode=None))
        spec = importlib.util.spec_from_file_location('reloaded_dependencies', Path(dependencies.__file__))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(RuntimeError, 'another managed Gateway'):
            module.assert_dependency_directory_idle(self.root)
        self.assertIs(module.project_start_lock(self.root), dependencies.project_start_lock(self.root))

    async def test_first_upgrade_detects_unregistered_legacy_gateway(self):
        legacy = process(self.root)
        legacy.process = SimpleNamespace(returncode=None)
        with patch.object(dependencies._STATE, 'legacy_adopted', False):
            with self.assertRaisesRegex(RuntimeError, 'another managed Gateway'):
                dependencies.assert_dependency_directory_idle(self.root)
        legacy.process.returncode = 0

    async def test_dependency_install_implementation_is_not_monkeypatched(self):
        method = GatewayProcess._ensure_node_dependencies
        self.assertEqual(method.__qualname__, "GatewayProcess._ensure_node_dependencies")
        self.assertIs(GatewayProcess._ensure_node_dependencies, method)


class NodeCommandLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_installer_is_terminated_before_cancellation_escapes(self):
        started = asyncio.Event()
        async def communicate():
            started.set()
            await asyncio.Future()
        child = SimpleNamespace(communicate=communicate, returncode=None)
        with (patch('gateway_stability.asyncio.create_subprocess_exec', AsyncMock(return_value=child)),
              patch('gateway_stability._terminate_process_tree', AsyncMock()) as terminate):
            task = asyncio.create_task(stability._run_node_command('node', timeout=20))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            terminate.assert_awaited_once_with(child)

    async def test_unsupported_node_patch_version_is_rejected(self):
        for version in ('18.20.0', '20.8.9', 'garbled'):
            output = json.dumps({'version': version, 'abi': 'x', 'platform': 'linux', 'arch': 'x64'}).encode()
            with patch('gateway_stability._run_node_command', AsyncMock(return_value=output)):
                with self.assertRaisesRegex(RuntimeError, '20.9.0'):
                    await stability.probe_node_runtime('node')

    async def test_node_identity_accepts_legacy_floor_and_current_lts(self):
        for version in ('20.9.0', '22.23.2', '24.20.0'):
            output = json.dumps({'version': version, 'abi': 'x', 'platform': 'linux', 'arch': 'x64'}).encode()
            with patch('gateway_stability._run_node_command', AsyncMock(return_value=output)):
                identity = await stability.probe_node_runtime('node')
                self.assertEqual(identity['major'], int(version.split('.')[0]))


if __name__ == '__main__':
    unittest.main()
