"""Run explicitly with an installed AstrBot; never contacts a WhatsApp account.

python tests/integration/verify_astrbot.py --framework /path/to/AstrBot
The real framework, configuration stores, adapter and event types are used.
Only the surrounding application Context is replaced with an inert harness.
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


parser = argparse.ArgumentParser()
parser.add_argument('--framework', type=Path, required=True)
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
    adapter = adapter_module.WhatsAppPlatformAdapter(manager.platforms_config[0], manager.settings, manager.event_queue)
    manager.platform_insts.append(adapter)
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
        print(json.dumps({'astrbot': VERSION, 'framework': str(args.framework),
            'checks': ['real imports', 'Web JSON request/response', 'plugin initialize', 'versioned migration persistence',
                       'adapter reload', 'stable auth directory', 'group UMO', 'diagnostic scope'],
            'whatsappNetworkUsed': False}))
    finally:
        await adapter.terminate()
        await plugin.terminate()


try:
    asyncio.run(verify())
finally:
    temporary.cleanup()
