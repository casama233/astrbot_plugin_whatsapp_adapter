import assert from "node:assert/strict";
import test from "node:test";

import {
  contextInfoFromMessagePayload,
  normalizeIncomingItem,
  normalizeMessageInput,
  normalizeMessagePayload,
} from "./message-normalization.mjs";

const WRAPPED_MESSAGES = [
  [
    "ephemeral",
    { ephemeralMessage: { message: { conversation: "ephemeral text" } } },
    { conversation: "ephemeral text" },
  ],
  [
    "view once",
    { viewOnceMessage: { message: { imageMessage: { caption: "view once image" } } } },
    { imageMessage: { caption: "view once image" } },
  ],
  [
    "document with caption",
    {
      documentWithCaptionMessage: {
        message: { documentMessage: { caption: "document caption", fileName: "report.pdf" } },
      },
    },
    { documentMessage: { caption: "document caption", fileName: "report.pdf" } },
  ],
  [
    "edited",
    { editedMessage: { message: { extendedTextMessage: { text: "edited text" } } } },
    { extendedTextMessage: { text: "edited text" } },
  ],
];

for (const [label, wrapped, expected] of WRAPPED_MESSAGES) {
  test(`normalizes ${label} message content without mutation`, () => {
    const snapshot = structuredClone(wrapped);

    assert.deepEqual(normalizeMessagePayload(wrapped), expected);
    assert.deepEqual(wrapped, snapshot);
  });
}

test("keeps an ordinary message unchanged", () => {
  const message = { conversation: "ordinary text" };

  assert.strictEqual(normalizeMessagePayload(message), message);
  assert.deepEqual(message, { conversation: "ordinary text" });
});

test("normalizes an incoming item while preserving key and metadata", () => {
  const item = {
    key: { remoteJid: "120363000000000000@g.us", id: "message-id", fromMe: false },
    pushName: "Alice",
    messageTimestamp: 1234567890,
    message: {
      ephemeralMessage: {
        message: { extendedTextMessage: { text: "hello" } },
      },
    },
  };
  const snapshot = structuredClone(item);

  const normalized = normalizeIncomingItem(item);

  assert.notStrictEqual(normalized, item);
  assert.strictEqual(normalized.key, item.key);
  assert.equal(normalized.pushName, "Alice");
  assert.equal(normalized.messageTimestamp, 1234567890);
  assert.deepEqual(normalized.message, { extendedTextMessage: { text: "hello" } });
  assert.deepEqual(item, snapshot);
});

test("reads wrapped document metadata from a complete WAMessage item", () => {
  const item = {
    key: { id: "document-id" },
    message: {
      ephemeralMessage: {
        message: {
          documentWithCaptionMessage: {
            message: {
              documentMessage: {
                fileName: "report.pdf",
                mimetype: "application/pdf",
                fileLength: 12_345,
              },
            },
          },
        },
      },
    },
  };

  assert.deepEqual(normalizeMessageInput(item), {
    documentMessage: {
      fileName: "report.pdf",
      mimetype: "application/pdf",
      fileLength: 12_345,
    },
  });
  assert.equal(normalizeMessageInput(item).documentMessage.fileName, "report.pdf");
  assert.equal(normalizeMessageInput(item).documentMessage.mimetype, "application/pdf");
  assert.equal(Number(normalizeMessageInput(item).documentMessage.fileLength), 12_345);
});

test("handles empty messages and empty items", () => {
  assert.equal(normalizeMessagePayload(null), null);
  assert.equal(normalizeMessagePayload(undefined), undefined);
  assert.deepEqual(normalizeMessagePayload({}), {});
  assert.equal(normalizeIncomingItem(null), null);
  assert.equal(normalizeIncomingItem(undefined), undefined);

  const item = { key: { id: "empty" }, message: null, marker: true };
  assert.deepEqual(normalizeIncomingItem(item), item);
  assert.deepEqual(normalizeIncomingItem({ key: { id: "missing" } }), {
    key: { id: "missing" },
  });
});

test("finds quote and mention-all context on non-media components", () => {
  const contextInfo = {
    stanzaId: "quoted-id",
    participant: "85254420939@s.whatsapp.net",
    nonJidMentions: 1,
  };

  for (const message of [
    { locationMessage: { degreesLatitude: 22.3, contextInfo } },
    { contactMessage: { displayName: "Alice", contextInfo } },
    { buttonsResponseMessage: { selectedButtonId: "yes", contextInfo } },
    { listResponseMessage: { title: "Choice", contextInfo } },
    { pollCreationMessageV3: { name: "Poll", contextInfo } },
  ]) {
    assert.strictEqual(contextInfoFromMessagePayload(message), contextInfo);
  }
});
