from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_whatsapp_adapter_compat import _adapter_module
from whatsapp_config_migration import migrate_plugin_configuration
from whatsapp_config_policy import extract_legacy_behavior_overrides, extract_legacy_command_prefix, runtime_config_sources


class ConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'config-migration.json'
        self.sanitize = _adapter_module().sanitize_whatsapp_platform_config
        self.plugin = {'gateway_port': 18789, 'default_typing_indicator': True}
        self.platforms = [{'type': 'whatsapp', 'id': 'whatsapp', 'enable': True,
                           '_legacy_gateway_gateway_port': 18888,
                           '_legacy_typing_indicator': False, '_legacy_command_prefix': '!'}]
        self.save_plugin, self.save_platforms = Mock(), Mock()

    def run_migration(self, plugin=None):
        return migrate_plugin_configuration(plugin or self.plugin, self.platforms, self.path,
            save_plugin=self.save_plugin, save_platforms=self.save_platforms, sanitize_platform=self.sanitize)

    def test_persists_once_and_respects_later_plugin_edit(self):
        effective, state = self.run_migration()
        self.assertEqual(effective['gateway_port'], 18888)
        self.assertEqual(state['version'], 1)
        self.assertTrue(self.path.is_file())
        self.assertFalse(any(key.startswith('_legacy_') for key in self.platforms[0]))
        self.assertEqual(extract_legacy_behavior_overrides(self.platforms[0]), {'typing_indicator': False})
        self.assertEqual(extract_legacy_command_prefix(self.platforms[0]), '!')
        second, _ = self.run_migration({'gateway_port': 18789, 'default_typing_indicator': False})
        self.assertEqual(second['gateway_port'], 18789, 'old port must not be adopted again')
        self.save_plugin.assert_called_once()
        self.save_platforms.assert_called_once()

    def test_failed_persistence_does_not_mark_complete_or_mutate_original(self):
        before = copy.deepcopy(self.platforms)
        self.save_platforms.side_effect = OSError('read-only config')
        with self.assertRaises(OSError): self.run_migration()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.platforms, before)
        self.save_platforms.side_effect = None
        self.assertEqual(self.run_migration()[0]['gateway_port'], 18888)

    def test_migrated_record_ignores_reintroduced_legacy_keys_and_can_release_override(self):
        self.run_migration()
        config = self.platforms[0]
        config['_legacy_typing_indicator'] = True
        self.assertEqual(extract_legacy_behavior_overrides(config), {'typing_indicator': False})
        config['_whatsapp_migration']['behavior'] = {}
        self.assertEqual(extract_legacy_behavior_overrides(config), {})
        config['_whatsapp_migration']['command_prefix'] = ''
        self.assertEqual(extract_legacy_command_prefix(config), '')

    def test_existing_plugin_choice_wins_and_new_accounts_get_versioned_once(self):
        self.assertEqual(self.run_migration({'gateway_port': 19999})[0]['gateway_port'], 19999)
        self.platforms.append({'type': 'whatsapp', 'id': 'new', 'enable': True, 'typing_indicator': False})
        self.run_migration()
        self.assertEqual(self.platforms[1]['_whatsapp_migration']['behavior'], {'typing_indicator': False})
        self.assertEqual(self.save_platforms.call_count, 2)
        self.run_migration()
        self.assertEqual(self.save_platforms.call_count, 2)

    def test_sources_follow_the_same_runtime_precedence(self):
        self.assertEqual(runtime_config_sources({'fixed': 1, 'shared': 1, 'old': 1, 'account': 1},
            {'shared': 2, 'old': 2, 'account': 2}, {'old': 3, 'account': 3}, {'account': 4}),
            {'fixed': 'internal_default', 'shared': 'plugin_default', 'old': 'retained_legacy', 'account': 'platform_instance'})
