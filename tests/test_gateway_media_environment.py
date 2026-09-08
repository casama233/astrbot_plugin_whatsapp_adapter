from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_security_module():
    spec = importlib.util.spec_from_file_location(
        "gateway_media_environment_security", ROOT / "gateway_security.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GatewayMediaEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def _start_with_environment(self, explicit):
        import whatsapp_client as module

        class Process(module.GatewayProcess):
            def __init__(self, root):
                super().__init__("node", root / "plugins/adapter/gateway/main.mjs",
                                 "127.0.0.1", 18789, root / "data/plugin_data/adapter/whatsapp-auth",
                                 "info", root / "data/plugin_data/adapter")
                self._ensure_node_runtime = AsyncMock()
                self._ensure_node_dependencies = AsyncMock()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Process(root)
            env = {} if explicit is None else {"WA_MEDIA_ALLOWED_ROOTS": explicit}
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(
                    module.asyncio,
                    "create_subprocess_exec",
                    AsyncMock(return_value=SimpleNamespace(returncode=None)),
                ) as spawn,
            ):
                await process.start()
                child_env = spawn.call_args.kwargs["env"]
                self.assertEqual(dict(os.environ), env)
                self.assertEqual(child_env["WA_AUTH_DIR"], str(process.auth_dir))
                self.assertEqual(child_env["WA_DATA_DIR"], str(process.data_dir))
                expected = str((root / "data").resolve()) if explicit is None else explicit
                self.assertEqual(child_env["WA_MEDIA_ALLOWED_ROOTS"], expected)
                process.process.returncode = 0
                await process.stop()

    async def test_default_keeps_astrbot_plugin_output_roots(self):
        await self._start_with_environment(None)

    async def test_explicit_roots_are_preserved(self):
        await self._start_with_environment(os.pathsep.join(["custom-output", "other-output"]))

    async def test_explicit_empty_roots_are_not_replaced(self):
        await self._start_with_environment("")


if __name__ == "__main__":
    unittest.main()
