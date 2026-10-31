// The real protocol library/auth serializer runs; only the remote socket is fake.
export * from '../../node_modules/@whiskeysockets/baileys/lib/index.js';
import { EventEmitter } from 'node:events';
import { appendFile } from 'node:fs/promises';
import { appendFileSync } from 'node:fs';
let socketCount = 0;
export default function makeSocket({ auth }) {
  const instance = ++socketCount;
  if (process.env.WA_TEST_SOCKET_LOG) appendFileSync(process.env.WA_TEST_SOCKET_LOG, `${instance}\n`);
  if (process.env.WA_TEST_LOGIN_FLOW === 'linked-connecting') auth.creds.me = { id: '15550000:1@s.whatsapp.net' };
  const ev = new EventEmitter();
  const socket = {
    ev, authState: auth, user: { id: '15550000@s.whatsapp.net' },
    end() {}, sendPresenceUpdate: async () => {}, readMessages: async () => {},
    groupMetadata: async (id) => ({ id, subject: 'Fixture Group', owner: '15550001@s.whatsapp.net',
      participants: [{ id: '15550001@s.whatsapp.net', admin: 'superadmin' }] }),
    sendMessage: async (to, payload) => {
      await appendFile(process.env.WA_TEST_SEND_LOG, JSON.stringify({ to, payload }) + '\n');
      return { key: { id: 'fixture-send', remoteJid: to }, message: payload };
    },
  };
  setImmediate(() => {
    if (process.env.WA_TEST_LOGIN_FLOW === 'qr-expiry') {
      ev.emit('connection.update', { qr: `fixture-qr-${instance}` });
      if (instance === 1) setTimeout(() => ev.emit('connection.update', {
        connection: 'close', lastDisconnect: { error: { message: 'QR refs attempts ended', output: { statusCode: 408 } } },
      }), 40);
    } else if (process.env.WA_TEST_LOGIN_FLOW === 'linked-connecting') {
      ev.emit('connection.update', { connection: 'connecting' });
    } else {
      if (process.env.WA_TEST_LOGIN_FLOW === 'linked-web') auth.creds.me = { id: '15550000:1@s.whatsapp.net' };
      ev.emit('connection.update', { connection: 'open' });
      ev.emit('creds.update', {});
    }
  });
  return socket;
}
