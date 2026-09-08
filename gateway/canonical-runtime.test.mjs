import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('canonical Gateway authenticates, preserves media policy, sends through the protocol boundary and flushes on shutdown', { timeout: 20000 }, async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'wa-canonical-'));
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  const token = 'synthetic-fixture-token';
  const child = spawn(process.execPath, ['--import', new URL('../tests/gateway-fixtures/register.mjs', import.meta.url).href, path.join(root, 'gateway/whatsapp-gateway.mjs')], {
    cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, WA_GATEWAY_HOST: '127.0.0.1', WA_GATEWAY_PORT: String(port), WA_GATEWAY_TOKEN: token,
      WA_DATA_DIR: directory, WA_AUTH_DIR: path.join(directory, 'whatsapp-auth'), WA_TEMP_DIR: directory,
      WA_MEDIA_ALLOWED_ROOTS: directory, WA_LOG_LEVEL: 'error', WA_TEST_SEND_LOG: path.join(directory, 'sent.jsonl') },
  });
  const exited = once(child, 'exit');
  let logs = '';
  child.stdout.on('data', (chunk) => { logs += chunk; });
  child.stderr.on('data', (chunk) => { logs += chunk; });
  const base = `http://127.0.0.1:${port}`;
  const request = (url, options = {}) => fetch(base + url, {
    ...options, signal: AbortSignal.timeout(3000), headers: { Authorization: `Bearer ${token}`, ...options.headers },
  });
  const post = (url, body) => request(url, { method: 'POST', body: JSON.stringify(body) });
  try {
    let health;
    for (let i = 0; i < 100; i++) {
      assert.equal(child.exitCode, null, logs);
      try { health = await (await request('/health')).json(); } catch {}
      if (health?.ready) break;
      await delay(50);
    }
    assert.equal(health?.ready, true, logs);
    assert.equal(health.configured, false);
    assert.equal((await fetch(base + '/health')).status, 401);
    assert.equal((await request('/shutdown', { method: 'POST', headers: { Authorization: 'Bearer wrong' } })).status, 401);
    assert.equal((await post('/config', { dmPolicy: 'open', groupPolicy: 'open', markOnline: false })).status, 200);
    assert.equal((await (await request('/health')).json()).configured, true);
    const group = await (await post('/group/info', { groupJid: '123456789-12345@g.us' })).json();
    assert.equal(group.subject, 'Fixture Group');
    assert.equal((await post('/send/text', { to: '15550001@s.whatsapp.net', text: 'fixture' })).status, 200);
    const media = path.join(directory, 'report.txt');
    await writeFile(media, 'synthetic report');
    assert.equal((await post('/send/media', { to: '15550001@s.whatsapp.net', type: 'document', pathOrUrl: media })).status, 200);
    const denied = await post('/send/media', { to: '15550001@s.whatsapp.net', type: 'document', pathOrUrl: path.join(directory, 'whatsapp-auth/.active-session.json') });
    assert.ok(denied.status >= 400);
    const sends = (await readFile(path.join(directory, 'sent.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(sends.length, 2);
    assert.equal(sends[0].payload.text, 'fixture');
    assert.equal((await request('/shutdown', { method: 'POST' })).status, 202);
    assert.deepEqual(await exited, [0, null], logs);
    // The actual Baileys serializer must have durably written the final auth state.
    const authRoot = path.join(directory, 'whatsapp-auth');
    const entries = await readdir(authRoot, { recursive: true });
    assert.ok(entries.some((name) => name.endsWith('creds.json')), 'credentials not flushed');
    const gatewayFiles = await readdir(path.join(root, 'gateway'));
    assert.equal(gatewayFiles.some((name) => name.includes('.generated.mjs')), false);
  } finally {
    if (child.exitCode === null) child.kill('SIGKILL');
    await exited;
    await rm(directory, { recursive: true, force: true });
  }
});
