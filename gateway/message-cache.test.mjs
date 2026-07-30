import assert from "node:assert/strict";
import test from "node:test";

import {
  cacheChatMessage,
  chatMessageKey,
  findChatMessage,
} from "./message-cache.mjs";

function message(chatJid, id, text) {
  return {
    key: { remoteJid: chatJid, id },
    message: { conversation: text },
  };
}

test("quoted-message lookup is isolated by chat", () => {
  const cache = new Map();
  cacheChatMessage(cache, message("a@g.us", "same-id", "chat-a"));
  cacheChatMessage(cache, message("b@g.us", "same-id", "chat-b"));

  assert.equal(
    findChatMessage(cache, "a@g.us", "same-id").message.conversation,
    "chat-a",
  );
  assert.equal(
    findChatMessage(cache, "b@g.us", "same-id").message.conversation,
    "chat-b",
  );
  assert.equal(findChatMessage(cache, "c@g.us", "same-id"), undefined);
  assert.equal(cache.has("same-id"), false);
});

test("quoted-message cache rejects incomplete entries and evicts oldest chat key", () => {
  const cache = new Map();
  assert.equal(cacheChatMessage(cache, { key: { id: "missing-chat" } }, 1), false);
  cacheChatMessage(cache, message("a@g.us", "1", "first"), 1);
  cacheChatMessage(cache, message("a@g.us", "2", "second"), 1);

  assert.equal(cache.has(chatMessageKey("a@g.us", "1")), false);
  assert.equal(findChatMessage(cache, "a@g.us", "2").message.conversation, "second");
});
