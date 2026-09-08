// The real protocol library/auth serializer runs; only the remote socket is fake.
export * from '../../node_modules/@whiskeysockets/baileys/lib/index.js';
import { EventEmitter } from 'node:events';
import { appendFile } from 'node:fs/promises';
export default function makeSocket({ auth }) {
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
  setImmediate(() => { ev.emit('connection.update', { connection: 'open' }); ev.emit('creds.update', {}); });
  return socket;
}
