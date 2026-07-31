import assert from "node:assert/strict";
import test from "node:test";

import { patchGatewayGroupNames } from "./group-name-compat.mjs";

const FIXTURE = `
const host = process.env.WA_GATEWAY_HOST || "127.0.0.1";
const knownContacts = new Map();
async function rememberGroupParticipants(chatJid) {
  if (!socket?.groupMetadata || !String(chatJid || "").endsWith("@g.us")) return;
  try {
    const metadata = await socket.groupMetadata(chatJid);
    for (const participant of metadata?.participants || []) {
      const jid = participant?.id || participant?.jid;
      rememberMentionIdentity(jid);
      if (jid && String(jid).endsWith("@lid")) {
        const resolved = resolveLidToPn(jid);
        if (resolved) rememberLidPnMapping(jid, resolved);
      }
    }
  } catch (error) {
    log.debug({ error, chatJid }, "failed to refresh group mention directory");
  }
}

function mentionTokensFromText(text) { return []; }
async function handleIncomingMessage(item, options = {}) {
  const albumItems = options.albumItems?.length ? options.albumItems : [item];
  const primary = albumItems[0];
  const chatJid = primary.key.remoteJid;
  const senderJid = primary.key.participant || chatJid;
  rememberMentionIdentity(senderJid, primary.pushName);
  rememberGroupParticipants(chatJid).catch(() => {});
  if (primary.message?.protocolMessage) return;
  const isGroup = chatJid.endsWith("@g.us");
  if (!configured) {}
  broadcast({
    type: "message",
    messageId: primary.key.id,
    albumMessageIds: albumItems.length > 1 ? albumItems.map((albumItem) => albumItem.key.id) : undefined,
    chatJid,
    senderJid,
  });
}
function wire(socket) {
  socket.ev.on("contacts.upsert", (contacts) => {
    for (const contact of contacts || []) rememberContact(contact);
  });
}
`;

test("adds group metadata to inbound Gateway events", () => {
  const result = patchGatewayGroupNames(FIXTURE);
  assert.equal(result.changed, true);
  assert.match(result.content, /groupMetadataForMessage\(chatJid\)/);
  assert.match(result.content, /groupName,/);
  assert.match(result.content, /group_name: groupName/);
  assert.match(result.content, /groupSubject: groupName/);
  assert.match(result.content, /socket\.ev\.on\("groups\.update"/);
});

test("is idempotent after the compatibility marker is present", () => {
  const first = patchGatewayGroupNames(FIXTURE);
  const second = patchGatewayGroupNames(first.content);
  assert.equal(second.changed, false);
  assert.equal(second.content, first.content);
});

test("fails loudly if the Gateway layout changes", () => {
  assert.throws(
    () => patchGatewayGroupNames("export const unrelated = true;"),
    /Gateway configuration anchor was not found/,
  );
});
