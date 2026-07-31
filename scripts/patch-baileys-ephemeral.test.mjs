import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  astrbotRememberEphemeralChats,
  astrbotRememberEphemeralMessages,
  patchBaileysEphemeralMetadata,
  patchBaileysMessagesSend,
  patchBaileysMessagesUtility,
  patchInstalledBaileys,
} from "./patch-baileys-ephemeral.mjs";

const UTILITY_RC14_SNIPPET = `
export const generateWAMessageFromContent = (jid, message, options) => {
  const innerMessage = message
  const key = Object.keys(innerMessage)[0]
  if (
    // if we want to send a disappearing message
    !!options?.ephemeralExpiration &&
    key !== "protocolMessage" &&
    key !== "ephemeralMessage"
  ) {
    innerMessage[key].contextInfo = {
      ...(innerMessage[key].contextInfo || {}),
      expiration: options.ephemeralExpiration || WA_DEFAULT_EPHEMERAL
      //ephemeralSettingTimestamp: options.ephemeralOptions.eph_setting_ts?.toString()
    }
  }
  return message
}
`;

const LEGACY_UTILITY_SNIPPET = UTILITY_RC14_SNIPPET.replace(
  "expiration: options.ephemeralExpiration || WA_DEFAULT_EPHEMERAL\n      //ephemeralSettingTimestamp: options.ephemeralOptions.eph_setting_ts?.toString()",
  "expiration: options.ephemeralExpiration || WA_DEFAULT_EPHEMERAL,\n      ephemeralSettingTimestamp: options.ephemeralSettingTimestamp || unixTimestampSeconds()",
);

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

test("utility patch uses the real chat setting timestamp and guards incomplete metadata", () => {
  const result = patchBaileysMessagesUtility(UTILITY_RC14_SNIPPET);

  assert.equal(result.changed, true);
  assert.match(result.content, /Symbol\.for\("astrbot\.whatsapp\.ephemeral-settings"\)/);
  assert.match(result.content, /!!astrbotEphemeralSettingTimestamp &&/);
  assert.match(
    result.content,
    /ephemeralSettingTimestamp: astrbotEphemeralSettingTimestamp/,
  );
  assert.doesNotMatch(result.content, /unixTimestampSeconds\(\)/);
  assert.doesNotMatch(result.content, /\/\/ephemeralSettingTimestamp/);
});

test("utility patch migrates the earlier send-time fallback", () => {
  const result = patchBaileysMessagesUtility(LEGACY_UTILITY_SNIPPET);
  assert.equal(result.changed, true);
  assert.match(
    result.content,
    /ephemeralSettingTimestamp: astrbotEphemeralSettingTimestamp/,
  );
  assert.doesNotMatch(
    result.content,
    /options\.ephemeralSettingTimestamp\s*\|\|\s*unixTimestampSeconds/,
  );
});

test("socket patch records chat and inbound-message metadata", () => {
  const result = patchBaileysMessagesSend(SOCKET_RC14_SNIPPET);

  assert.equal(result.changed, true);
  assert.match(result.content, /const astrbotEphemeralSettingsKey = Symbol\.for/);
  assert.match(result.content, /ev\.on\("chats\.upsert"/);
  assert.match(result.content, /ev\.on\("chats\.update"/);
  assert.match(result.content, /ev\.on\("messages\.upsert"/);
  assert.match(result.content, /astrbotRememberEphemeralMessages/);
});

test("both source patches are idempotent", () => {
  const utility = patchBaileysMessagesUtility(UTILITY_RC14_SNIPPET);
  const utilityAgain = patchBaileysMessagesUtility(utility.content);
  assert.equal(utilityAgain.changed, false);
  assert.equal(utilityAgain.content, utility.content);

  const socket = patchBaileysMessagesSend(SOCKET_RC14_SNIPPET);
  const socketAgain = patchBaileysMessagesSend(socket.content);
  assert.equal(socketAgain.changed, false);
  assert.equal(socketAgain.content, socket.content);
});

test("chat cache preserves complete metadata and rejects stale or incomplete updates", () => {
  const cache = new Map();
  astrbotRememberEphemeralChats(cache, [
    {
      id: "chat@s.whatsapp.net",
      ephemeralExpiration: 86400,
      ephemeralSettingTimestamp: 1700000000,
    },
  ]);
  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 86400,
    timestamp: "1700000000",
  });

  astrbotRememberEphemeralChats(cache, [
    { id: "chat@s.whatsapp.net", ephemeralExpiration: 86400 },
  ]);
  assert.equal(cache.get("chat@s.whatsapp.net").timestamp, "1700000000");

  astrbotRememberEphemeralChats(cache, [
    { id: "chat@s.whatsapp.net", ephemeralExpiration: 604800 },
  ]);
  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 604800,
    timestamp: undefined,
  });

  astrbotRememberEphemeralChats(cache, [
    {
      id: "chat@s.whatsapp.net",
      ephemeralSettingTimestamp: "1700001000",
    },
  ]);
  assert.deepEqual(cache.get("chat@s.whatsapp.net"), {
    expiration: 604800,
    timestamp: "1700001000",
  });
  astrbotRememberEphemeralChats(cache, [
    { id: "chat@s.whatsapp.net", ephemeralExpiration: 0 },
  ]);
  assert.equal(cache.has("chat@s.whatsapp.net"), false);
});

test("inbound message context can repopulate the cache", () => {
  const cache = new Map();
  const normalize = (message) => message.ephemeralMessage?.message || message;
  astrbotRememberEphemeralMessages(
    cache,
    {
      messages: [
        {
          key: { remoteJid: "group@g.us" },
          message: {
            ephemeralMessage: {
              message: {
                extendedTextMessage: {
                  text: "hello",
                  contextInfo: {
                    expiration: 86400,
                    ephemeralSettingTimestamp: "1700002000",
                  },
                },
              },
            },
          },
        },
      ],
    },
    normalize,
  );

  assert.deepEqual(cache.get("group@g.us"), {
    expiration: 86400,
    timestamp: "1700002000",
  });
});

test("patches both installed Baileys runtime files", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "baileys-ephemeral-"));
  const utilityPath = path.join(
    directory,
    "node_modules",
    "@whiskeysockets",
    "baileys",
    "lib",
    "Utils",
    "messages.js",
  );
  const socketPath = path.join(
    directory,
    "node_modules",
    "@whiskeysockets",
    "baileys",
    "lib",
    "Socket",
    "messages-send.js",
  );

  try {
    await mkdir(path.dirname(utilityPath), { recursive: true });
    await mkdir(path.dirname(socketPath), { recursive: true });
    await writeFile(utilityPath, UTILITY_RC14_SNIPPET, "utf8");
    await writeFile(socketPath, SOCKET_RC14_SNIPPET, "utf8");

    const first = await patchInstalledBaileys({ cwd: directory });
    const second = await patchInstalledBaileys({ cwd: directory });
    const utility = await readFile(utilityPath, "utf8");
    const socket = await readFile(socketPath, "utf8");

    assert.deepEqual(first, { checked: 2, changed: 2 });
    assert.deepEqual(second, { checked: 2, changed: 0 });
    assert.match(utility, /astrbotEphemeralSettingTimestamp/);
    assert.match(socket, /astrbotEphemeralSettingsKey/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("fails loudly when either pinned Baileys layout changes", () => {
  assert.throws(
    () => patchBaileysEphemeralMetadata("export const unrelated = true;"),
    /timestamp marker was not found/,
  );
  assert.throws(
    () => patchBaileysMessagesSend("export const unrelated = true;"),
    /initialization anchor was not found/,
  );
});
