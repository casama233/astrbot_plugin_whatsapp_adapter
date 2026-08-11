import assert from "node:assert/strict";
import test from "node:test";

import {
  astrbotRememberEphemeralChats,
  astrbotRememberEphemeralMessages,
  patchBaileysMessagesSend,
} from "./patch-baileys-ephemeral.mjs";

const normalize = (message) => message.ephemeralMessage?.message || message;

const SOCKET_RC14_SNIPPET = `
export const makeMessagesSocket = (config) => {
  const sock = makeNewsletterSocket(config)
  const {
    ev,
    signalRepository,
  } = sock

  const getLIDForPN = signalRepository.lidMapping.getLIDForPN.bind(signalRepository.lidMapping)

  return { ...sock }
}
`;

function ephemeralPayload({ type = "notify", expiration = 86400, timestamp = "1700000000" } = {}) {
  return {
    type,
    messages: [
      {
        key: { remoteJid: "chat@s.whatsapp.net" },
        message: {
          extendedTextMessage: {
            text: "hello",
            contextInfo: {
              expiration,
              ephemeralSettingTimestamp: timestamp,
            },
          },
        },
      },
    ],
  };
}

test("delayed chat metadata cannot roll a newer disappearing setting backwards", () => {
  const cache = new Map();
  astrbotRememberEphemeralChats(cache, [
    {
      id: "chat@s.whatsapp.net",
      ephemeralExpiration: 86400,
      ephemeralSettingTimestamp: "1700002000",
    },
  ]);

  astrbotRememberEphemeralChats(cache, [
    {
      id: "chat@s.whatsapp.net",
      ephemeralExpiration: 604800,
      ephemeralSettingTimestamp: "1700001000",
    },
  ]);

  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 86400,
    timestamp: "1700002000",
  });
});

test("historical messages cannot overwrite the current disappearing setting", () => {
  const cache = new Map([
    ["chat@s.whatsapp.net", { expiration: 86400, timestamp: "1700002000" }],
  ]);

  astrbotRememberEphemeralMessages(
    cache,
    ephemeralPayload({ type: "append", expiration: 86400, timestamp: "1700001000" }),
    normalize,
  );

  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 86400,
    timestamp: "1700002000",
  });
});

test("live messages only advance disappearing metadata and never regress it", () => {
  const cache = new Map([
    ["chat@s.whatsapp.net", { expiration: 86400, timestamp: "1700002000" }],
  ]);

  astrbotRememberEphemeralMessages(
    cache,
    ephemeralPayload({ expiration: 86400, timestamp: "1700001000" }),
    normalize,
  );
  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 86400,
    timestamp: "1700002000",
  });

  astrbotRememberEphemeralMessages(
    cache,
    ephemeralPayload({ expiration: 604800, timestamp: "1700003000" }),
    normalize,
  );
  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 604800,
    timestamp: "1700003000",
  });
});

test("socket patch seeds metadata from messaging history and filters message history", () => {
  const result = patchBaileysMessagesSend(SOCKET_RC14_SNIPPET);
  assert.equal(result.changed, true);
  assert.match(result.content, /ev\.on\("messaging-history\.set"/);
  assert.match(result.content, /payload\?\.type && payload\.type !== "notify"/);
});

test("an already-installed older socket patch is upgraded in place", () => {
  const current = patchBaileysMessagesSend(SOCKET_RC14_SNIPPET).content;
  const historyListener = /^\s*ev\.on\("messaging-history\.set"[^\n]*\n/m;
  const legacyMessageGuard = "  if (payload?.type && payload.type !== \"notify\") return;\n\n";
  const legacy = current
    .replace(historyListener, "")
    .replace(legacyMessageGuard, "");

  const migrated = patchBaileysMessagesSend(legacy);
  assert.equal(migrated.changed, true);
  assert.match(migrated.content, /ev\.on\("messaging-history\.set"/);
  assert.match(migrated.content, /payload\?\.type && payload\.type !== "notify"/);

  const again = patchBaileysMessagesSend(migrated.content);
  assert.equal(again.changed, false);
  assert.equal(again.content, migrated.content);
});
