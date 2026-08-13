import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
  const fromMe = Boolean(primary.key.fromMe);
  const isGroup = chatJid.endsWith("@g.us");
  const senderJid = primary.key.participant || primary.key.participantAlt || (fromMe ? selfJid : null) || chatJid;
  rememberMentionIdentity(senderJid, primary.pushName);
  rememberGroupParticipants(chatJid).catch(() => {});
  if (primary.message?.protocolMessage) return;
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
  assert.match(
    result.content,
    /groupMetadataForMessage\(\s*chatJid,\s*expectedGeneration,\s*eventSocket/,
  );
  assert.match(result.content, /groupName,/);
  assert.match(result.content, /group_name: groupName/);
  assert.match(result.content, /groupSubject: groupName/);
  assert.match(result.content, /groupOwner,/);
  assert.match(result.content, /groupOwnerJid,/);
  assert.match(result.content, /groupOwnerPnJid,/);
  assert.match(result.content, /groupAdmins,/);
  assert.match(result.content, /groupAdminJids,/);
  assert.match(result.content, /groupAdminPnJids,/);
  assert.match(result.content, /groupAdminIdentities,/);
  assert.match(result.content, /senderRole,/);
  assert.match(result.content, /participant\?\.admin === "superadmin"/);
  assert.match(result.content, /sameGroupParticipant\(participant, senderJid\)/);
  assert.match(result.content, /ownerIdentity\?\.pnJid/);
  assert.match(result.content, /senderPn \|\| resolveLidToPn/);
  assert.match(result.content, /socket\.ev\.on\("groups\.update"/);
  assert.match(result.content, /socket\.ev\.on\("group-participants\.update"/);
  assert.match(result.content, /groupMetadataCache\.delete\(jid\)/);
  assert.match(result.content, /cached\?\.complete/);
  assert.match(result.content, /if \(generation !== socketGeneration\) return/);
});

test("enriches the mention directory with participant display names", () => {
  const result = patchGatewayGroupNames(FIXTURE);
  assert.match(result.content, /rememberGroupParticipantIdentity\(participant, chatJid\)/);
  assert.match(result.content, /rememberGroupOwnerIdentity\(metadata\)/);
});

test("partial group updates never create a fresh permission snapshot", () => {
  const result = patchGatewayGroupNames(FIXTURE);
  assert.match(result.content, /if \(!complete\) delete incoming\.participants/);
  assert.match(result.content, /cachedAt: complete \? Date\.now\(\)/);
  assert.match(result.content, /cacheGroupMetadata\(update, false\)/);
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

test("patches the real Gateway implementation entry layout", () => {
  const source = readFileSync(
    new URL("./whatsapp-gateway-impl.mjs", import.meta.url),
    "utf8",
  );

  const result = patchGatewayGroupNames(source);
  assert.equal(result.changed, true);
  assert.match(result.content, /const groupMetadataPromise = groupMetadataForMessage/);
  assert.match(result.content, /participantAlt/);
});

test("group info keeps owner separate from string-normalized admins", () => {
  const source = readFileSync(
    new URL("./whatsapp-gateway-impl.mjs", import.meta.url),
    "utf8",
  );

  assert.match(source, /userId: normalizeJid\(pnJid \|\| jid\)/);
  assert.match(source, /lidJid: identity\?\.lidJid/);
  assert.match(source, /const owner = normalizeJid\(/);
  assert.match(source, /ownerJid,/);
  assert.match(source, /ownerPnJid,/);
  assert.match(source, /adminIdentities,/);
  assert.match(source, /adminJids,/);
  assert.match(source, /adminPnJids,/);
  assert.match(
    source,
    /participant\.role === "admin" && participant\.userId !== owner/,
  );
  assert.doesNotMatch(
    source,
    /participant\.role === "owner" \|\| participant\.role === "admin"/,
  );
});
