"""Execute production updater methods with synthetic disk and transport fixtures.

The framework-neutral harness avoids network imports in the fast unit suite.
The integration job separately uses the real AstrBot PlatformManager.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugin_updater import (
    PluginUpdateError, acquire_update_transaction, atomic_swap_plugin,
    release_update_transaction, restore_plugin_backup,
)

from update_lifecycle import await_update_operation

ROOT = Path(__file__).resolve().parents[1]


def updater_class(live, active, swap=atomic_swap_plugin):
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WhatsAppAdapterPlugin')
    names = {'_perform_update', '_commit_update', '_quiesce_update_runtime',
             '_resume_quiesced_runtime', '_verify_adapter_health', '_verify_update_health',
             '_reload_after_update', '_rollback_update'}
    methods = [n for n in original.body if isinstance(n, ast.AsyncFunctionDef) and n.name in names]
    unit = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
        ast.ClassDef(name='Updater', bases=[], keywords=[], body=methods, decorator_list=[]),
    ], type_ignores=[]))
    package = types.ModuleType('updater_fixture')
    package.__path__ = []
    adapter = types.ModuleType('updater_fixture.whatsapp_adapter')
    adapter.get_active_whatsapp_adapters = lambda: list(active)
    modules = {'updater_fixture': package, 'updater_fixture.whatsapp_adapter': adapter}
    def extract(_archive, destination, **_):
        destination.mkdir()
        (destination / 'payload').write_text('new', encoding='utf-8')
        return {'astrbot_version': '>=4,<5'}
    scope = {'__package__': 'updater_fixture', 'asyncio': asyncio, 'shutil': shutil, 'time': time,
             'logger': logging.getLogger('update-fixture'), 'PLUGIN_DIR': live,
             'PLUGIN_VERSION': '0.2.46', 'PLUGIN_NAME': 'fixture', 'PluginUpdateError': PluginUpdateError,
             'is_newer_version': lambda *_: True, 'download_release_archive': AsyncMock(return_value='digest'),
             'extract_validated_release': extract, 'validate_python_requirements_unchanged': lambda *_: None,
             'atomic_swap_plugin': swap, 'await_update_operation': await_update_operation,
             'release_update_transaction': release_update_transaction, 'restore_plugin_backup': restore_plugin_backup}
    exec(compile(unit, str(ROOT / 'main.py'), 'exec'), scope)
    return scope['Updater'], modules


class UpdateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.live = self.root / 'live'
        self.live.mkdir()
        (self.live / 'payload').write_text('old', encoding='utf-8')
        self.active = []
        self.tasks = []
        self.restored = {}
        self.configs = []
        self.calls = []
        self.states = []
        self.release = SimpleNamespace(version='0.2.47', download_url='unused', asset_digest='unused',
                                       as_dict=lambda: {'version': '0.2.47'})
        self.tx = 'test-transaction'
        self.lock = self.root / 'transaction.lock'
        self.registered = SimpleNamespace(version='0.2.46')
        self.pm = SimpleNamespace(platforms_config=self.configs, _platform_tasks={})
        self.pm.reload = AsyncMock(side_effect=self.restart)
        self.pm._stop_platform_task = AsyncMock(side_effect=self.stop_native)
        self.manager = SimpleNamespace(reload=AsyncMock(), context=SimpleNamespace(
            get_registered_star=lambda _: self.registered),
            _validate_astrbot_version_specifier=lambda _: (True, ''))
        self.make_updater()

    def make_updater(self, swap=atomic_swap_plugin):
        cls, modules = updater_class(self.live, self.active, swap)
        context = patch.dict(sys.modules, modules)
        context.start()
        self.addCleanup(context.stop)
        self.plugin = cls()
        p = self.plugin
        p.context = SimpleNamespace(_star_manager=self.manager, platform_manager=self.pm,
            get_platform_inst=lambda pid: self.restored.get(pid))
        p._update_lock = asyncio.Lock()
        p._update_work_root = lambda: self.root / 'work'
        p._update_backup_root = lambda: self.root / 'backup'
        p._update_lock_path = lambda: self.lock
        p._prepare_staged_plugin = AsyncMock()
        p._write_update_state = lambda state: self.states.append(state)
        p._safe_update_error = str
        p.page_client = SimpleNamespace(close=AsyncMock(), health=AsyncMock(return_value={}))
        p.page_gateway_process = None
        p._ensure_page_gateway = AsyncMock()
        p._reload_after_update = AsyncMock(side_effect=self.reload_success)

    async def reload_success(self, *_args, **_kwargs):
        self.assertTrue(self.lock.exists())
        self.assertEqual((self.live / 'payload').read_text(), 'new')
        self.states.append({'phase': 'completed'})

    async def new_task(self):
        task = asyncio.create_task(asyncio.Event().wait())
        self.tasks.append(task)
        await asyncio.sleep(0)
        return task

    async def account(self, pid, *, fail_stop=False, managed=True):
        self.configs.append({'type': 'whatsapp', 'id': pid, 'enable': True})
        inst = SimpleNamespace(meta=lambda: SimpleNamespace(id=pid), client_self_id=pid,
            config={'auto_start_gateway': managed}, client=SimpleNamespace(health=AsyncMock(return_value={})))
        task = await self.new_task()
        self.pm._platform_tasks[pid] = SimpleNamespace(run=task, wrapper=None)
        async def terminate():
            self.active[:] = [a for a in self.active if a is not inst]
            if fail_stop:
                raise RuntimeError('partial stop failure')
        inst.terminate = AsyncMock(side_effect=terminate)
        self.active.append(inst)
        self.restored[pid] = inst
        return inst, task

    async def stop_native(self, pid):
        record = self.pm._platform_tasks.pop(pid, None)
        if record and record.run:
            record.run.cancel()
            await asyncio.gather(record.run, return_exceptions=True)

    async def restart(self, config):
        pid = config['id']
        self.calls.append(pid)
        await self.stop_native(pid)
        task = await self.new_task()
        inst = SimpleNamespace(client_self_id=pid,
            client=SimpleNamespace(health=AsyncMock(return_value={})))
        self.restored[pid] = inst
        self.pm._platform_tasks[pid] = SimpleNamespace(run=task)

    async def perform(self):
        acquire_update_transaction(self.lock, self.tx)
        await self.plugin._perform_update(self.release, self.tx)

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.temp.cleanup()

    async def test_swap_failure_restarts_saved_accounts_with_native_tasks(self):
        self.make_updater(swap=lambda *_: (_ for _ in ()).throw(OSError('swap failed')))
        _, old_task = await self.account('primary')
        await self.perform()
        self.assertTrue(old_task.done())
        self.assertEqual(self.active, [])  # Mock manager does not repopulate it.
        self.assertEqual(self.calls, ['primary'])
        self.assertFalse(self.pm._platform_tasks['primary'].run.done())
        self.restored['primary'].client.health.assert_awaited_once()
        self.assertTrue(self.states[-1]['runtimeRestored'])
        self.assertEqual((self.live / 'payload').read_text(), 'old')
        self.assertFalse(self.lock.exists())

    async def test_partial_quiescence_recovers_every_saved_account(self):
        await self.account('first', fail_stop=True)
        await self.account('second')
        await self.perform()
        self.assertEqual(self.calls, ['first', 'second'])
        self.assertTrue(self.states[-1]['runtimeRestored'])
        self.assertIn('partial stop failure', self.states[-1]['error'])
        self.plugin._reload_after_update.assert_not_called()

    async def test_one_restart_failure_does_not_skip_other_accounts(self):
        await self.account('broken', fail_stop=True)
        await self.account('healthy')
        async def restart(config):
            if config['id'] == 'broken':
                raise RuntimeError('restart refused')
            await self.restart(config)
        self.pm.reload.side_effect = restart
        await self.perform()
        self.assertEqual(self.calls, ['healthy'])
        self.assertFalse(self.states[-1]['runtimeRestored'])
        self.assertIn('restart refused', self.states[-1]['recoveryError'])

    async def test_disabled_or_removed_accounts_are_not_resurrected(self):
        self.configs.extend([{'id': 'disabled', 'type': 'whatsapp', 'enable': False}])
        await self.plugin._resume_quiesced_runtime(['removed', 'disabled'])
        self.pm.reload.assert_not_called()

    async def test_page_only_runtime_is_recovered_after_failure(self):
        self.make_updater(swap=lambda *_: (_ for _ in ()).throw(OSError('swap failed')))
        self.plugin.page_gateway_process = SimpleNamespace(process=SimpleNamespace(returncode=None), stop=AsyncMock())
        await self.perform()
        self.plugin._ensure_page_gateway.assert_awaited_once()
        self.plugin.page_client.health.assert_awaited_once()
        self.assertTrue(self.states[-1]['runtimeRestored'])

    async def test_health_requires_live_native_task_and_external_transport(self):
        inst, task = await self.account('external', managed=False)
        await self.plugin._verify_adapter_health('external', attempts=1)
        inst.client.health.assert_awaited_once()
        inst.client.health.side_effect = OSError('external unavailable')
        with self.assertRaisesRegex(PluginUpdateError, 'external unavailable'):
            await self.plugin._verify_adapter_health('external', attempts=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        with self.assertRaisesRegex(PluginUpdateError, 'run task is not active'):
            await self.plugin._verify_adapter_health('external', attempts=1)

    async def test_cancellation_waits_for_real_swap_and_health_before_unlock(self):
        entered, proceed, finished = threading.Event(), threading.Event(), threading.Event()
        def swap(*args):
            entered.set()
            if not proceed.wait(10):
                raise RuntimeError('fixture barrier timeout')
            try:
                return atomic_swap_plugin(*args)
            finally:
                finished.set()
        self.make_updater(swap)
        task = asyncio.create_task(self.perform())
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            self.assertTrue(entered.is_set())
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()  # A repeated shutdown request cannot break the barrier.
            await asyncio.sleep(.01)
            self.assertTrue(self.lock.exists())
            self.assertFalse(task.done())
            self.assertFalse(finished.is_set())
            self.assertEqual(self.states[-1]['phase'], 'installing')
            proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(finished.is_set())
            self.assertFalse(self.lock.exists())
            self.assertEqual(self.states[-1]['phase'], 'completed')
            self.plugin._reload_after_update.assert_awaited_once()
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_during_failed_swap_still_completes_recovery(self):
        entered, proceed = threading.Event(), threading.Event()
        def swap(*_):
            entered.set()
            if not proceed.wait(10):
                raise RuntimeError('fixture barrier timeout')
            raise OSError('failed swap after cancellation')
        self.make_updater(swap)
        await self.account('recover')
        task = asyncio.create_task(self.perform())
        try:
            for _ in range(1000):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            self.assertTrue(entered.is_set())
            task.cancel()
            await asyncio.sleep(.01)
            self.assertTrue(self.lock.exists())
            proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.calls, ['recover'])
            self.assertTrue(self.states[-1]['runtimeRestored'])
            self.assertIn('failed swap after cancellation', self.states[-1]['error'])
            self.assertFalse(self.lock.exists())
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_during_health_does_not_unlock_early(self):
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def health(*_, **__):
            entered.set()
            await proceed.wait()
            self.assertTrue(self.lock.exists())
            self.states.append({'phase': 'completed'})
        self.plugin._reload_after_update.side_effect = health
        task = asyncio.create_task(self.perform())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(.01)
            self.assertTrue(self.lock.exists())
            self.assertFalse(task.done())
            proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.states[-1]['phase'], 'completed')
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_before_quiescence_does_not_swap_or_stop_accounts(self):
        entered = asyncio.Event()
        async def prepare(_):
            entered.set()
            await asyncio.Event().wait()
        self.plugin._prepare_staged_plugin.side_effect = prepare
        inst, task_before = await self.account('unchanged')
        task = asyncio.create_task(self.perform())
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        inst.terminate.assert_not_called()
        self.assertFalse(task_before.done())
        self.assertEqual((self.live / 'payload').read_text(), 'old')
        self.assertEqual(self.states[-1]['phase'], 'failed')
        self.assertFalse(self.lock.exists())

    async def test_rollback_failure_is_not_reported_as_success(self):
        # Execute the production reload/rollback methods with real file swaps.
        self.plugin._reload_after_update = types.MethodType(type(self.plugin)._reload_after_update, self.plugin)
        self.manager.reload.side_effect = [(False, 'new version broken'), (True, '')]
        self.plugin._verify_update_health = AsyncMock(side_effect=PluginUpdateError('old runtime unhealthy'))
        await self.perform()
        self.assertEqual((self.live / 'payload').read_text(), 'old')
        self.assertFalse(self.states[-1]['rolledBack'])
        self.assertIn('old runtime unhealthy', self.states[-1]['rollbackError'])
        self.assertTrue((self.root / 'work' / self.tx).exists())
        self.assertFalse(self.lock.exists())

    async def test_failed_health_stops_new_runtime_before_restoring_files(self):
        self.plugin._reload_after_update = types.MethodType(type(self.plugin)._reload_after_update, self.plugin)
        new_tasks = []
        async def reload(_):
            if not new_tasks:
                self.assertEqual((self.live / 'payload').read_text(), 'new')
                inst, task = await self.account('new-version')
                new_tasks.append(task)
                return True, ''
            self.assertTrue(new_tasks[0].done())
            self.assertEqual((self.live / 'payload').read_text(), 'old')
            return True, ''
        self.manager.reload.side_effect = reload
        self.plugin._verify_update_health = AsyncMock(side_effect=[PluginUpdateError('new unhealthy'), None])
        await self.perform()
        self.assertTrue(self.states[-1]['rolledBack'])
        self.assertEqual(self.states[-1]['phase'], 'failed')
        self.assertTrue(new_tasks[0].done())
        self.assertFalse(self.lock.exists())

    async def test_cancel_during_quiescence_defers_until_recovery_finishes(self):
        entered, proceed = asyncio.Event(), asyncio.Event()
        await self.account('account')
        async def stop(adapters):
            await type(self.plugin)._quiesce_update_runtime(self.plugin, adapters)
            entered.set()
            await proceed.wait()
            raise OSError('late stop failure')
        self.plugin._quiesce_update_runtime = stop
        task = asyncio.create_task(self.perform())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(.01)
            self.assertTrue(self.lock.exists())
            self.assertFalse(task.done())
            proceed.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.calls, ['account'])
            self.assertTrue(self.states[-1]['runtimeRestored'])
            self.assertFalse(self.lock.exists())
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)


class CancellationBarrierTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_worker_is_observed_after_cancellation(self):
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def work():
            entered.set()
            await proceed.wait()
            raise OSError('worker failure')
        task = asyncio.create_task(await_update_operation(work()))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        proceed.set()
        with self.assertRaises(asyncio.CancelledError) as caught:
            await task
        self.assertIsInstance(caught.exception.__cause__, OSError)

    async def test_inner_cancellation_propagates_instead_of_looping(self):
        async def work():
            raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(await_update_operation(work()), 1)
