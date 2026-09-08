from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import gateway_dependencies as dependencies
import gateway_runtime as runtime
import gateway_stability as stability
from tests.test_whatsapp_gateway_process import NODE, process, receipt, write_project


def node_output(version="22.23.2", **identity):
    return json.dumps({"version": version, "abi": "127", "platform": "linux", "arch": "x64", **identity}).encode()


class GatewayPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write_project(self.root)
        self.node = patch("gateway_runtime.shutil.which", return_value="configured-node")
        self.command = patch("gateway_stability._run_node_command", AsyncMock(return_value=node_output()))
        self.npm = patch("gateway_runtime._npm_command", return_value=["configured-node", "npm-cli.js"])
        self.node.start()
        self.run = self.command.start()
        self.npm.start()
        for mock in (self.node, self.command, self.npm):
            self.addCleanup(mock.stop)

    async def check(self):
        return await runtime.inspect_gateway_requirements(self.root, "configured-node")

    async def test_baileys_directory_without_receipt_is_pending_not_ready(self):
        before = sorted(p.relative_to(self.root) for p in self.root.rglob("*"))
        result = await self.check()
        self.assertEqual(result["status"], "dependencies_pending")
        self.assertFalse(result["ready"])
        self.assertFalse(result["dependenciesInstalled"])
        self.assertTrue(result["canPrepare"])
        self.assertEqual(before, sorted(p.relative_to(self.root) for p in self.root.rglob("*")))
        self.assertEqual(self.run.call_count, 1)  # Probe only, no npm/patch/import or Gateway.

    async def test_refreshed_tree_receipt_patch_and_abi_changes_are_visible(self):
        receipt(self.root)
        self.assertTrue((await self.check())["ready"])
        patch_script = self.root / "scripts/patch-baileys-ephemeral.mjs"
        patch_script.write_text("// updated patch\n", encoding="utf-8")
        self.assertFalse((await self.check())["ready"])
        receipt(self.root)
        self.assertTrue((await self.check())["ready"])
        self.run.return_value = node_output(abi="999")
        self.assertFalse((await self.check())["ready"])
        self.run.return_value = node_output()
        (self.root / "node_modules/ws/package.json").unlink()
        self.assertFalse((await self.check())["ready"])

    async def test_ready_tree_needs_no_npm_and_can_be_shared(self):
        receipt(self.root)
        dependencies.register_gateway_child(self.root, SimpleNamespace(returncode=None))
        with patch("gateway_runtime._npm_command", side_effect=RuntimeError("npm absent")):
            result = await self.check()
        self.assertEqual(result["status"], "dependencies_current")
        self.assertTrue(result["ready"])
        self.assertFalse(result["npm"]["available"])

    async def test_stale_tree_with_live_gateway_reports_blocked(self):
        dependencies.register_gateway_child(self.root, SimpleNamespace(returncode=None))
        result = await self.check()
        self.assertEqual(result["status"], "dependencies_blocked")
        self.assertFalse(result["ready"])
        self.assertFalse(result["canPrepare"])

    async def test_absent_npm_prevents_preparation_but_does_not_install(self):
        with patch("gateway_runtime._npm_command", side_effect=RuntimeError("npm-cli.js absent")):
            result = await self.check()
        self.assertEqual(result["status"], "npm_unavailable")
        self.assertFalse(result["canPrepare"])

    async def test_preflight_and_startup_share_exact_node_floor(self):
        for version, status in (("20.8.9", "node_unsupported"), ("20.9.0", "dependencies_pending"),
                                ("22.23.2", "dependencies_pending"), ("24.20.0", "dependencies_pending")):
            with self.subTest(version=version):
                self.run.return_value = node_output(version)
                result = await self.check()
                self.assertEqual(result["status"], status)
                self.assertEqual(result["node"]["version"], version)
                self.assertEqual(result["minimumNodeVersion"], "20.9.0")
                if status == "node_unsupported":
                    with self.assertRaises(RuntimeError):
                        await stability.probe_node_runtime("configured-node")
                else:
                    await stability.probe_node_runtime("configured-node")

    async def test_missing_or_broken_node_is_not_ready(self):
        with patch("gateway_runtime.shutil.which", return_value=None):
            self.assertEqual((await self.check())["status"], "node_missing")
        self.run.return_value = b"invalid runtime"
        self.assertEqual((await self.check())["status"], "node_unavailable")

    async def test_external_gateway_does_not_probe_local_tools_or_dependencies(self):
        with patch("gateway_runtime.shutil.which", side_effect=AssertionError("local tools not required")):
            result = await runtime.inspect_gateway_requirements(self.root, "missing", managed=False)
        self.assertEqual(result["status"], "external")
        self.assertTrue(result["ready"])
        self.run.assert_not_called()


class StagedDependencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.staged = self.root / "candidate"
        self.staged.mkdir()
        write_project(self.staged, installed=False)
        (self.staged / "gateway").mkdir()
        (self.staged / "gateway/whatsapp-gateway.mjs").write_text("export {};\n", encoding="utf-8")
        self.live = self.root / "live"
        self.live.mkdir()
        write_project(self.live)
        dependencies.register_gateway_child(self.live, SimpleNamespace(returncode=None))

    async def command(self, *args, **kwargs):
        if "ci" in args:
            write_project(Path(kwargs["cwd"]))
        return b""

    async def test_staging_preserves_live_tree_and_receipt_survives_swap(self):
        live_before = {p.relative_to(self.live): p.read_bytes() for p in self.live.rglob("*") if p.is_file()}
        with (
            patch("gateway_stability.probe_node_runtime", AsyncMock(return_value=NODE)),
            patch("gateway_stability._npm_command", return_value=["custom-node", "npm-cli.js"]),
            patch("gateway_stability._run_node_command", side_effect=self.command) as install,
            patch("gateway_runtime._run_node_command", AsyncMock()) as validate,
        ):
            await runtime.prepare_staged_plugin(self.staged, "custom-node")
        self.assertEqual(install.call_count, 3)
        self.assertTrue(all(call.args[0] == "custom-node" for call in install.call_args_list))
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(live_before, {p.relative_to(self.live): p.read_bytes() for p in self.live.rglob("*") if p.is_file()})
        installed = self.staged.rename(self.root / "installed")
        self.assertTrue(dependencies.dependencies_current(installed, NODE))
        with patch("gateway_stability._run_node_command", side_effect=AssertionError("unexpected reinstall")):
            await process(installed)._ensure_node_dependencies()

    async def test_failed_repair_cannot_leave_old_success_receipt(self):
        write_project(self.staged)
        receipt(self.staged)
        (self.staged / "node_modules/ws/package.json").unlink()

        async def failed_repair(*args, **kwargs):
            write_project(self.staged)
            raise RuntimeError("partial npm failure")

        with (
            patch("gateway_stability.probe_node_runtime", AsyncMock(return_value=NODE)),
            patch("gateway_stability._npm_command", return_value=["node", "npm-cli.js"]),
            patch("gateway_stability._run_node_command", side_effect=failed_repair),
        ):
            with self.assertRaisesRegex(RuntimeError, "partial npm failure"):
                await runtime.prepare_staged_plugin(self.staged, "node")
        self.assertFalse(dependencies.dependencies_current(self.staged, NODE))
        self.assertFalse((self.staged / "node_modules" / dependencies.RECEIPT_NAME).exists())

    async def test_staging_syntax_failure_is_not_silently_accepted(self):
        with (
            patch("gateway_runtime.prepare_node_dependencies", AsyncMock()),
            patch("gateway_runtime._run_node_command", AsyncMock(side_effect=RuntimeError("syntax failed"))),
        ):
            with self.assertRaisesRegex(RuntimeError, "syntax failed"):
                await runtime.prepare_staged_plugin(self.staged, "node")

    async def test_missing_entry_fails_before_installing(self):
        (self.staged / "gateway/whatsapp-gateway.mjs").unlink()
        with patch("gateway_runtime.prepare_node_dependencies", AsyncMock()) as install:
            with self.assertRaisesRegex(RuntimeError, "missing"):
                await runtime.prepare_staged_plugin(self.staged, "node")
        install.assert_not_called()

    async def test_staging_cancellation_propagates(self):
        with patch("gateway_runtime.prepare_node_dependencies", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.prepare_staged_plugin(self.staged, "node")


if __name__ == "__main__":
    unittest.main()
