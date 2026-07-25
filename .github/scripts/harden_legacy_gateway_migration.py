from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

policy_path = ROOT / "whatsapp_config_policy.py"
policy = policy_path.read_text("utf-8")
old = '''        for key, historical_default in LEGACY_GATEWAY_DEFAULTS.items():
            if key not in config:
                continue
            value = config[key]
            if key == "log_level":
'''
new = '''        for key, historical_default in LEGACY_GATEWAY_DEFAULTS.items():
            hidden_key = f"_legacy_gateway_{key}"
            if hidden_key in config:
                value = config[hidden_key]
            elif key in config:
                value = config[key]
            else:
                continue
            if key == "log_level":
'''
if old not in policy:
    raise RuntimeError("legacy Gateway adoption block not found")
policy_path.write_text(policy.replace(old, new, 1), "utf-8")

adapter_path = ROOT / "whatsapp_adapter.py"
adapter = adapter_path.read_text("utf-8")
adapter = adapter.replace(
    "    LOG_LEVELS,\n    MEDIA_CAPTION_MODES,\n",
    "    LEGACY_GATEWAY_DEFAULTS,\n    LOG_LEVELS,\n    MEDIA_CAPTION_MODES,\n",
    1,
)
needle = '''    # Preserve only explicit old per-instance behaviour choices. Historical
    # template defaults are ignored so plugin-wide default_* settings can work.
    for key, value in extract_legacy_behavior_overrides(config).items():
'''
replacement = '''    # Preserve explicit legacy Gateway choices long enough for the plugin page
    # to adopt them, even if an adapter is constructed before plugin.initialize.
    for key, default in LEGACY_GATEWAY_DEFAULTS.items():
        hidden_key = f"_legacy_gateway_{key}"
        if hidden_key in config:
            sanitized[hidden_key] = config[hidden_key]
        elif key in config and config[key] != default:
            sanitized[hidden_key] = config[key]

    # Preserve only explicit old per-instance behaviour choices. Historical
    # template defaults are ignored so plugin-wide default_* settings can work.
    for key, value in extract_legacy_behavior_overrides(config).items():
'''
if needle not in adapter:
    raise RuntimeError("adapter migration insertion point not found")
adapter_path.write_text(adapter.replace(needle, replacement, 1), "utf-8")

main_path = ROOT / "main.py"
main = main_path.read_text("utf-8")
old_loop = '''        from .whatsapp_adapter import get_active_whatsapp_adapters

        for adapter in get_active_whatsapp_adapters():
            try:
                await adapter.reload(adapter._platform_config)
            except Exception as exc:
                logger.warning(
                    "Failed to propagate plugin config reload to WhatsApp adapter %s: %s",
                    getattr(adapter.meta(), "id", None),
                    exc,
                )

    async def initialize(self) -> None:
'''
new_loop = '''        await self._reload_active_adapters()

    async def _reload_active_adapters(self) -> None:
        from .whatsapp_adapter import get_active_whatsapp_adapters

        for adapter in get_active_whatsapp_adapters():
            try:
                await adapter.reload(adapter._platform_config)
            except Exception as exc:
                logger.warning(
                    "Failed to propagate plugin config reload to WhatsApp adapter %s: %s",
                    getattr(adapter.meta(), "id", None),
                    exc,
                )

    async def initialize(self) -> None:
'''
if old_loop not in main:
    raise RuntimeError("main active-adapter reload block not found")
main = main.replace(old_loop, new_loop, 1)
main = main.replace(
    '''        self.page_client.update_base_url(self._base_url)
        await self._restore_platform_adapters()
''',
    '''        self.page_client.update_base_url(self._base_url)
        await self._restore_platform_adapters()
        await self._reload_active_adapters()
''',
    1,
)
main_path.write_text(main, "utf-8")

test_path = ROOT / "tests/test_whatsapp_config_policy.py"
test = test_path.read_text("utf-8")
test = test.replace(
    '''        self.assertEqual(set(migrated), {"gateway_port", "auth_dir"})

        explicit_plugin = dict(plugin, gateway_port=19999)
''',
    '''        self.assertEqual(set(migrated), {"gateway_port", "auth_dir"})

        hidden_effective, hidden_migrated = adopt_legacy_gateway_defaults(plugin, [{
            "type": "whatsapp",
            "_legacy_gateway_gateway_port": 17777,
        }])
        self.assertEqual(hidden_effective["gateway_port"], 17777)
        self.assertEqual(hidden_migrated["gateway_port"], 17777)

        explicit_plugin = dict(plugin, gateway_port=19999)
''',
    1,
)
test = test.replace(
    '''        self.assertIn("extract_legacy_behavior_overrides(platform_config)", adapter)
        self.assertIn("_message_matches_known_command", adapter)
        self.assertIn("adopt_legacy_gateway_defaults", main)
''',
    '''        self.assertIn("extract_legacy_behavior_overrides(platform_config)", adapter)
        self.assertIn("_legacy_gateway_", adapter)
        self.assertIn("_message_matches_known_command", adapter)
        self.assertIn("adopt_legacy_gateway_defaults", main)
        self.assertIn("await self._reload_active_adapters()", main)
''',
    1,
)
test_path.write_text(test, "utf-8")

precommit_path = ROOT / ".pre-commit-config.yaml"
config = precommit_path.read_text("utf-8")
hook = '''      - id: harden-legacy-gateway-migration
        name: Harden legacy Gateway migration
        entry: python .github/scripts/harden_legacy_gateway_migration.py
        language: system
        pass_filenames: false
'''
precommit_path.write_text(config.replace(hook, ""), "utf-8")
Path(__file__).unlink(missing_ok=True)
try:
    (ROOT / ".github/scripts").rmdir()
except OSError:
    pass
