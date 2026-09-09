"""Run explicitly with an installed AstrBot; never contacts a WhatsApp account.

python tests/integration/verify_astrbot.py --framework /path/to/AstrBot
The real framework, configuration stores, adapter and event types are used.
The application Context is an inert harness; Gateway connect/events/health
are replaced with network-free doubles. Native framework tasks still execute.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch


parser = argparse.ArgumentParser()
parser.add_argument('--framework', type=Path, required=True)
parser.add_argument('--expected-version')
args = parser.parse_args()
source = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(args.framework.resolve()))
temporary = tempfile.TemporaryDirectory(prefix='whatsapp-framework-')
root = Path(temporary.name)
os.environ['ASTRBOT_ROOT'] = str(root)
os.chdir(root)
(root / 'data/config').mkdir(parents=True)

from astrbot.core.config import AstrBotConfig
from astrbot.core.config.default import VERSION
from astrbot.core.platform.manager import PlatformManager
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import AstrBotMessage

if args.expected_version and VERSION != args.expected_version:
    raise RuntimeError(f"wrong framework version: {VERSION} != {args.expected_version}")

package = ModuleType('whatsapp_integration')
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
main = importlib.import_module(f'{package.__name__}.main')
adapter_module = importlib.import_module(f'{package.__name__}.whatsapp_adapter')
event_module = importlib.import_module(f'{package.__name__}.whatsapp_event')


async def verify():
    web = importlib.import_module(f'{package.__name__}.whatsapp_web')
    if web.request.__class__.__name__ == '_LegacyRequest':
        from quart import Quart
        app = Quart(__name__)
        @app.post('/json')
        async def json_route():
            return web.json_response(await web.request.json(default={'fallback': True}), status_code=202)
        client = app.test_client()
        response = await client.post('/json', json={'value': '中文'})
        assert response.status_code == 202 and await response.get_json() == {'value': '中文'}
        response = await client.post('/json', data='invalid', headers={'Content-Type': 'application/json'})
        assert await response.get_json() == {'fallback': True}
    else:
        from astrbot.api.web import bind_request_context, PluginRequest
        from starlette.requests import Request
        async def receive():
            return {'type': 'http.request', 'body': b'{"value":true}', 'more_body': False}
        raw = Request({'type': 'http', 'method': 'POST', 'path': '/json', 'headers': [],
                       'query_string': b'', 'scheme': 'http', 'server': ('test', 80)}, receive)
        with bind_request_context(PluginRequest(raw)):
            response = web.json_response(await web.request.json(default={}), status_code=202)
        assert response.status_code == 202 and json.loads(response.body) == {'value': True}
    schema = json.loads((source / '_conf_schema.json').read_text(encoding='utf-8'))
    plugin_store = AstrBotConfig(str(root / 'data/config/plugin.json'), schema=schema)
    plugin_store.save_config({'auto_start_gateway': False})
    root_store = AstrBotConfig(str(root / 'data/root.json'), default_config={
        'platform': [{'type': 'whatsapp', 'id': 'integration', 'enable': True,
                      '_legacy_gateway_gateway_port': 18888, '_legacy_typing_indicator': False,
                      'dm_policy': 'allowlist', 'allow_from': ['111']}],
        'platform_settings': {'unique_session': True},
    })
    manager = PlatformManager(root_store, asyncio.Queue())
    routes = {}
    context = SimpleNamespace(platform_manager=manager, get_config=lambda: root_store,
        register_web_api=lambda path, handler, *_: routes.update({path: handler}),
        get_platform_inst=lambda ident: next((a for a in manager.platform_insts if a.meta().id == ident), None))
    plugin = main.WhatsAppAdapterPlugin(context, plugin_store)
    await plugin.initialize()
    assert plugin_store['gateway_port'] == 18888
    assert root_store['platform'][0]['_whatsapp_migration']['version'] == 1
    # Use the real task/map owner, not a manually appended inert instance.
    await manager.load_platform(manager.platforms_config[0])
    await asyncio.sleep(0)
    adapter = context.get_platform_inst('integration')
    assert adapter is not None
    try:
        assert adapter.config['typing_indicator'] is False
        assert adapter.client.base_url == 'http://127.0.0.1:18888'
        assert adapter._config_sources['typing_indicator'] == 'retained_legacy'
        message = await adapter.convert_message({
            'chatJid': '120363000000000001@g.us', 'senderJid': '111@s.whatsapp.net',
            'senderPn': '111@s.whatsapp.net', 'senderName': 'Test', 'selfJid': '999@s.whatsapp.net',
            'messageId': 'integration-message', 'text': 'hello',
        })
        assert isinstance(message, AstrBotMessage)
        assert message.sender.user_id == '111'
        assert message.group_id == '120363000000000001'
        assert message.session_id == '111_120363000000000001'
        event = event_module.WhatsAppMessageEvent(message.message_str, message, adapter.meta(),
            message.session_id, adapter.client, '120363000000000001@g.us')
        assert isinstance(event, AstrMessageEvent)
        assert event.unified_msg_origin == 'integration:GroupMessage:111_120363000000000001'
        previous_auth = adapter._auth_dir()
        plugin_store.save_config({'gateway_port': 18999, 'default_typing_indicator': True})
        await plugin.reload_config(plugin_store)
        assert adapter.client.base_url == 'http://127.0.0.1:18999'
        assert adapter._auth_dir() == previous_auth
        assert adapter.config['typing_indicator'] is False
        # Loading the real store again verifies disk persistence and schema handling.
        restored = AstrBotConfig(plugin_store.config_path, schema=schema)
        assert restored['gateway_port'] == 18999
        assert json.loads(Path(root_store.config_path).read_text(encoding='utf-8-sig'))['platform'][0]['_whatsapp_migration']['version'] == 1
        assert any(path.endswith('/diagnostics') for path in routes)
        assert plugin._management_scope()['targetInstanceId'] == 'integration'
        before_task = manager._platform_tasks[adapter.client_self_id].run
        assert not before_task.done()
        await plugin._quiesce_update_runtime([adapter])
        assert before_task.done()
        assert adapter not in adapter_module.get_active_whatsapp_adapters()
        await plugin._resume_quiesced_runtime(['integration'])
        restored_adapter = context.get_platform_inst('integration')
        assert restored_adapter is not adapter
        assert restored_adapter in adapter_module.get_active_whatsapp_adapters()
        assert not manager._platform_tasks[restored_adapter.client_self_id].run.done()
        assert restored_adapter._auth_dir() == previous_auth
        await plugin._verify_adapter_health('integration', attempts=1)
        print(json.dumps({'astrbot': VERSION, 'framework': str(args.framework),
            'checks': ['real imports', 'Web JSON request/response', 'plugin initialize', 'versioned migration persistence',
                       'adapter reload', 'stable auth directory', 'group UMO', 'diagnostic scope',
                       'native managed task stop', 'updater recovery creates new native task', 'recovery HTTP health gate'],
            'whatsappNetworkUsed': False}))
    finally:
        await manager.terminate()
        await plugin.terminate()


async def connected_without_network(self):
    self._reconnect_event.clear()
    await asyncio.sleep(0)


async def idle_events(self):
    # Keep the actual run loop/task alive without opening any network connection.
    await asyncio.Event().wait()
    if False:
        yield {}


try:
    with (
        patch.object(adapter_module.WhatsAppPlatformAdapter, '_connect_gateway', connected_without_network),
        patch.object(main.WhatsAppGatewayClient, 'events', idle_events),
        patch.object(main.WhatsAppGatewayClient, 'health', AsyncMock(return_value={'ok': True})),
    ):
        asyncio.run(verify())
finally:
    temporary.cleanup()
