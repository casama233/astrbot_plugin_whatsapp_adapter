const PATCH_MARKER = "astrbotMemberTagCompatibility";

function replaceRequired(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(pattern, replacement);
}

const MEMBER_TAG_CACHE_HELPERS = `const groupMemberTagCache = new Map();
const maxGroupMemberTagCacheSize = 50000;

function memberTagIdentityKeys(...values) {
  const keys = new Set();
  for (const value of values) {
    const raw = String(value || "").trim();
    if (!raw || raw.endsWith("@g.us")) continue;
    const normalized = normalizeIdentityJid(raw);
    if (!normalized) continue;
    keys.add(normalized);

    const canonical = runtimeIdentities.canonical(normalized);
    if (canonical) keys.add(normalizeIdentityJid(canonical));

    if (isLidJid(normalized)) {
      const pnJid = resolveLidToPn(normalized);
      if (pnJid) keys.add(normalizeIdentityJid(pnJid));
    }
  }
  keys.delete("");
  return [...keys];
}

function memberTagCacheKey(groupJid, participantJid) {
  return String(groupJid || "").trim() + "\\u0000" + String(participantJid || "").trim();
}

function rememberGroupMemberTag(groupJid, label, timestamp = 0, ...identities) {
  const group = String(groupJid || "").trim();
  if (!group.endsWith("@g.us")) return "";

  const normalizedLabel = String(label || "").trim();
  const parsedTimestamp = Number(timestamp || 0);
  const updatedAt = Number.isFinite(parsedTimestamp) ? parsedTimestamp : 0;
  for (const identity of memberTagIdentityKeys(...identities)) {
    const key = memberTagCacheKey(group, identity);
    const previous = groupMemberTagCache.get(key);
    if (
      previous
      && previous.updatedAt
      && updatedAt
      && updatedAt < previous.updatedAt
    ) {
      continue;
    }
    groupMemberTagCache.delete(key);
    groupMemberTagCache.set(key, {
      label: normalizedLabel,
      updatedAt: updatedAt || previous?.updatedAt || 0,
    });
  }

  while (groupMemberTagCache.size > maxGroupMemberTagCacheSize) {
    groupMemberTagCache.delete(groupMemberTagCache.keys().next().value);
  }
  return normalizedLabel;
}

function forgetGroupMemberTag(groupJid, ...identities) {
  const group = String(groupJid || "").trim();
  if (!group) return;
  for (const identity of memberTagIdentityKeys(...identities)) {
    groupMemberTagCache.delete(memberTagCacheKey(group, identity));
  }
}

function groupMemberTagFor(groupJid, ...identities) {
  const group = String(groupJid || "").trim();
  if (!group) return "";
  for (const identity of memberTagIdentityKeys(...identities)) {
    const record = groupMemberTagCache.get(memberTagCacheKey(group, identity));
    if (record) return String(record.label || "");
  }
  return "";
}

function memberTagSnapshotFromMessagePayload(payload) {
  const memberLabel = contextInfoFromMessagePayload(payload)?.memberLabel;
  if (
    !memberLabel
    || typeof memberLabel !== "object"
    || !Object.prototype.hasOwnProperty.call(memberLabel, "label")
  ) {
    return null;
  }
  const parsedTimestamp = Number(memberLabel.labelTimestamp || 0);
  return {
    label: String(memberLabel.label || "").trim(),
    timestamp: Number.isFinite(parsedTimestamp) ? parsedTimestamp : 0,
  };
}

const groupMetadataCache = new Map();`;

const INBOUND_MEMBER_TAG_ENRICHMENT = `  const senderMemberTagSnapshot = isGroup
    ? memberTagSnapshotFromMessagePayload(primary.message)
    : null;
  if (senderMemberTagSnapshot) {
    rememberGroupMemberTag(
      chatJid,
      senderMemberTagSnapshot.label,
      senderMemberTagSnapshot.timestamp || Number(primary.messageTimestamp || 0),
      senderJid,
      senderPn,
      primary.key.participantAlt,
    );
  }
  const senderMemberTag = isGroup
    ? groupMemberTagFor(chatJid, senderJid, senderPn, primary.key.participantAlt)
    : "";
  broadcast({`;

export function patchGatewayMemberTags(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  let content = source.replace(/\r\n?/g, "\n");
  content = replaceRequired(
    content,
    /const astrbotGroupNameCompatibility = true;/,
    `const astrbotGroupNameCompatibility = true;\nconst ${PATCH_MARKER} = true;`,
    "group compatibility marker",
  );
  content = replaceRequired(
    content,
    /const groupMetadataCache = new Map\(\);/,
    MEMBER_TAG_CACHE_HELPERS,
    "group metadata cache anchor",
  );
  content = replaceRequired(
    content,
    /  (socket(?:ForGeneration)?)\.ev\.on\("contacts\.upsert", \(contacts\) => \{/,
    `  $1.ev.on("group.member-tag.update", (update) => {\n    if (generation !== socketGeneration) return;\n    rememberGroupMemberTag(\n      update?.groupId,\n      update?.label ?? "",\n      update?.messageTimestamp || 0,\n      update?.participant,\n      update?.participantAlt,\n    );\n  });\n  $1.ev.on("group-participants.update", (update) => {\n    if (generation !== socketGeneration || update?.action !== "remove") return;\n    for (const participant of update?.participants || []) {\n      const identity = groupParticipantIdentity(participant);\n      forgetGroupMemberTag(\n        update?.id,\n        participant?.id,\n        participant?.jid,\n        participant?.phoneNumber,\n        identity?.jid,\n        identity?.pnJid,\n        identity?.lidJid,\n      );\n    }\n  });\n  $1.ev.on("contacts.upsert", (contacts) => {`,
    "contact listener anchor",
  );
  content = replaceRequired(
    content,
    /  broadcast\(\{\n    type: "message",/,
    INBOUND_MEMBER_TAG_ENRICHMENT,
    "inbound message broadcast anchor",
  );
  content = replaceRequired(
    content,
    /(    groupAdminIdentities,\n    senderRole,\n)/,
    `$1    senderMemberTag,\n`,
    "inbound group fields anchor",
  );
  content = replaceRequired(
    content,
    /(          name: mentionDisplayName\(pnJid\) \|\| mentionDisplayName\(identity\?\.lidJid \|\| jid\) \|\| normalizeJid\(pnJid \|\| jid\),\n)(          role,\n)/,
    `$1          memberTag: groupMemberTagFor(groupJid, jid, pnJid, identity?.lidJid),\n$2`,
    "group info participant anchor",
  );
  content = replaceRequired(
    content,
    /if \(typeof groupMetadataCache !== "undefined"\) groupMetadataCache\.clear\(\);/,
    `if (typeof groupMetadataCache !== "undefined") groupMetadataCache.clear();\n  groupMemberTagCache.clear();`,
    "runtime cache reset anchor",
  );

  return { content, changed: true };
}
