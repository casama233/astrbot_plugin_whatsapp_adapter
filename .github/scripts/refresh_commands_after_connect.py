from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
adapter_path = ROOT / "whatsapp_adapter.py"
source = adapter_path.read_text("utf-8")
needle = '''        log("WhatsApp 适配器已连接: %s", self._base_url)
        self._mark_running()
        await self._restart_health_monitor()
'''
replacement = '''        log("WhatsApp 适配器已连接: %s", self._base_url)
        self._mark_running()
        await self._restart_health_monitor()
        # Other plugins may finish registering after this adapter is created.
        # Refresh here so legacy-prefix compatibility and command pre-ack see
        # the complete active CommandFilter registry after every reconnect.
        self._refresh_registered_commands()
'''
if needle not in source:
    raise RuntimeError("Gateway connect completion block not found")
source = source.replace(needle, replacement, 1)
adapter_path.write_text(source, "utf-8")

test_path = ROOT / "tests/test_whatsapp_config_policy.py"
test = test_path.read_text("utf-8")
needle = '''        self.assertIn("_message_matches_known_command", adapter)
        self.assertIn("adopt_legacy_gateway_defaults", main)
'''
replacement = '''        self.assertIn("_message_matches_known_command", adapter)
        self.assertIn(
            "await self._restart_health_monitor()\\n"
            "        # Other plugins may finish registering after this adapter is created.\\n"
            "        # Refresh here so legacy-prefix compatibility and command pre-ack see\\n"
            "        # the complete active CommandFilter registry after every reconnect.\\n"
            "        self._refresh_registered_commands()",
            adapter,
        )
        self.assertIn("adopt_legacy_gateway_defaults", main)
'''
if needle not in test:
    raise RuntimeError("command refresh test insertion point not found")
test_path.write_text(test.replace(needle, replacement, 1), "utf-8")

precommit_path = ROOT / ".pre-commit-config.yaml"
config = precommit_path.read_text("utf-8")
hook = '''      - id: refresh-commands-after-connect
        name: Refresh commands after Gateway connect
        entry: python .github/scripts/refresh_commands_after_connect.py
        language: system
        pass_filenames: false
'''
precommit_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
