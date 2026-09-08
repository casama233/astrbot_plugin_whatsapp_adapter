import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";


const SOURCE = readFileSync(new URL("./whatsapp-gateway-impl.mjs", import.meta.url), "utf8");

test("adds group metadata to inbound Gateway events", () => {
  const result = { content: SOURCE };

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
  assert.match(result.content, /socketForGeneration\.ev\.on\("groups\.update"/);
  assert.match(result.content, /socketForGeneration\.ev\.on\("group-participants\.update"/);
  assert.match(result.content, /groupMetadataCache\.delete\(jid\)/);
  assert.match(result.content, /cached\?\.complete/);
  assert.match(result.content, /if \(generation !== socketGeneration\) return/);
});

test("enriches the mention directory with participant display names", () => {
  const result = { content: SOURCE };
  assert.match(result.content, /rememberGroupParticipantIdentity\(participant, chatJid\)/);
  assert.match(result.content, /rememberGroupOwnerIdentity\(metadata\)/);
});

test("partial group updates never create a fresh permission snapshot", () => {
  const result = { content: SOURCE };
  assert.match(result.content, /if \(!complete\) delete incoming\.participants/);
  assert.match(result.content, /cachedAt: complete \? Date\.now\(\)/);
  assert.match(result.content, /cacheGroupMetadata\(update, false\)/);
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
