const PATCH_MARKER = "astrbotGroupNameCompatibility";

function replaceRequired(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(pattern, replacement);
}

const GROUP_METADATA_HELPERS = `function cacheGroupMetadata(metadata, complete = Array.isArray(metadata?.participants)) {
  const jid = String(metadata?.id || "");
  if (!jid) return metadata || null;
  const previous = groupMetadataCache.get(jid) || {};
  const incoming = { ...(metadata || {}) };
  if (!complete) delete incoming.participants;
  const merged = { ...(previous.metadata || {}), ...incoming, id: jid };
  groupMetadataCache.set(jid, {
    metadata: merged,
    cachedAt: complete ? Date.now() : (previous.cachedAt || 0),
    complete: Boolean(complete || previous.complete),
  });
  while (groupMetadataCache.size > maxGroupMetadataCacheSize) {
    groupMetadataCache.delete(groupMetadataCache.keys().next().value);
  }
  return merged;
}

async function rememberGroupParticipants(
  chatJid,
  expectedGeneration = socketGeneration,
  metadataSocket = socket,
) {
  if (!metadataSocket?.groupMetadata || !String(chatJid || "").endsWith("@g.us")) return null;
  try {
    const fetched = await metadataSocket.groupMetadata(chatJid);
    if (expectedGeneration !== socketGeneration || metadataSocket !== socket) return null;
    const metadata = cacheGroupMetadata(fetched, true);
    rememberGroupOwnerIdentity(metadata);
    for (const participant of metadata?.participants || []) {
      rememberGroupParticipantIdentity(participant, chatJid);
    }
    return metadata;
  } catch (error) {
    log.debug({ error, chatJid }, "failed to refresh group mention directory");
    const cached = groupMetadataCache.get(String(chatJid || ""));
    return cached?.complete ? cached.metadata : null;
  }
}

async function groupMetadataForMessage(
  chatJid,
  expectedGeneration = socketGeneration,
  metadataSocket = socket,
) {
  const cached = groupMetadataCache.get(String(chatJid || ""));
  if (cached?.complete && Date.now() - cached.cachedAt < groupMetadataCacheTtlMs) {
    return cached.metadata;
  }

  let timer;
  try {
    return await Promise.race([
      rememberGroupParticipants(chatJid, expectedGeneration, metadataSocket),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(cached?.complete ? cached.metadata : null), 2500);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function mentionTokensFromText`;

const GROUP_BROADCAST_ENRICHMENT = `  const groupMetadata = isGroup ? await groupMetadataPromise : null;
  if (isStaleSocketEvent()) return;
  const groupName = String(groupMetadata?.subject || "").trim();
  const groupParticipants = Array.isArray(groupMetadata?.participants) ? groupMetadata.participants : [];
  const ownerIdentity = rememberGroupOwnerIdentity(groupMetadata);
  const groupOwnerJid = normalizeIdentityJid(
    ownerIdentity?.jid
    || ownerIdentity?.lidJid
    || ownerIdentity?.pnJid
    || "",
  );
  const groupOwnerPnJid = normalizeIdentityJid(
    ownerIdentity?.pnJid
    || resolveLidToPn(ownerIdentity?.lidJid || ownerIdentity?.jid)
    || "",
  );
  const groupOwner = normalizeJid(groupOwnerPnJid || groupOwnerJid);
  const groupAdminIdentities = groupParticipants
    .filter((participant) => participant?.admin === "admin" || participant?.admin === "superadmin")
    .map((participant) => {
      const identity = rememberGroupParticipantIdentity(participant, chatJid);
      return {
        jid: normalizeIdentityJid(identity?.jid || identity?.lidJid || identity?.pnJid || ""),
        pnJid: normalizeIdentityJid(
          identity?.pnJid || resolveLidToPn(identity?.lidJid || identity?.jid) || "",
        ),
        lidJid: normalizeIdentityJid(identity?.lidJid || ""),
      };
    })
    .filter((identity) => identity.jid || identity.pnJid || identity.lidJid);
  const groupAdminJids = groupAdminIdentities.map((identity) => identity.jid).filter(Boolean);
  const groupAdminPnJids = groupAdminIdentities.map((identity) => identity.pnJid).filter(Boolean);
  const groupAdmins = groupAdminIdentities
    .map((identity) => normalizeJid(identity.pnJid || identity.jid || identity.lidJid))
    .filter(Boolean);
  const senderGroupParticipant = groupParticipants.find((participant) =>
    sameGroupParticipant(participant, senderJid),
  );
  const senderUserId = normalizeJid(senderPn || resolveLidToPn(senderJid) || senderJid);
  const senderRole = senderGroupParticipant?.admin === "superadmin" || (groupOwner && senderUserId === groupOwner)
    ? "owner"
    : senderGroupParticipant?.admin === "admin"
      ? "admin"
      : "member";
  broadcast({
    type: "message",`;

export function patchGatewayGroupNames(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  // Git commonly checks JavaScript out as CRLF on Windows. All structural
  // patterns below describe JavaScript layout, not a particular newline
  // encoding, so canonicalize before matching and emit deterministic LF output.
  let content = source.replace(/\r\n?/g, "\n");
  content = replaceRequired(
    content,
    /const host = process\.env\.WA_GATEWAY_HOST/,
    `const ${PATCH_MARKER} = true;\n\nconst host = process.env.WA_GATEWAY_HOST`,
    "Gateway configuration anchor",
  );
  content = replaceRequired(
    content,
    /const knownContacts = new Map\(\);/,
    `const knownContacts = new Map();\n\nconst groupMetadataCache = new Map();\nconst groupMetadataCacheTtlMs = 5 * 60 * 1000;\nconst maxGroupMetadataCacheSize = 1000;`,
    "contact cache anchor",
  );
  content = replaceRequired(
    content,
    /async function rememberGroupParticipants\([\s\S]*?\) \{[\s\S]*?\n\}\n\nfunction mentionTokensFromText/,
    GROUP_METADATA_HELPERS,
    "group metadata helper",
  );
  content = replaceRequired(
    content,
    /(  const isGroup = chatJid\.endsWith\("@g\.us"\);\n)(  const senderJid =)/,
    `$1  const groupMetadataPromise = groupMetadataForMessage(\n    chatJid,\n    expectedGeneration,\n    eventSocket,\n  );\n$2`,
    "incoming group anchor",
  );
  content = replaceRequired(
    content,
    /\n  rememberGroupParticipants\(chatJid(?:, expectedGeneration, eventSocket)?\)\.catch\(\(\) => \{\}\);/,
    "",
    "legacy participant refresh call",
  );
  content = replaceRequired(
    content,
    /  (socket(?:ForGeneration)?)\.ev\.on\("contacts\.upsert", \(contacts\) => \{/,
    `  $1.ev.on("groups.update", (groups) => {\n    if (generation !== socketGeneration) return;\n    for (const update of groups || []) cacheGroupMetadata(update, false);\n  });\n  $1.ev.on("group-participants.update", (update) => {\n    if (generation !== socketGeneration) return;\n    const jid = String(update?.id || "");\n    if (jid) {\n      groupMetadataCache.delete(jid);\n      mentionDirectoriesByChat.delete(jid);\n    }\n  });\n  $1.ev.on("contacts.upsert", (contacts) => {`,
    "group update listener anchor",
  );
  content = replaceRequired(
    content,
    /  broadcast\(\{\n    type: "message",/,
    GROUP_BROADCAST_ENRICHMENT,
    "message broadcast anchor",
  );
  content = replaceRequired(
    content,
    /(    albumMessageIds: albumItems\.length > 1 \? albumItems\.map\(\(albumItem\) => albumItem\.key\.id\) : undefined,\n    chatJid,\n)(    senderJid,)/,
    `$1    groupName,\n    group_name: groupName,\n    groupSubject: groupName,\n    groupOwner,\n    groupOwnerJid,\n    groupOwnerPnJid,\n    groupAdmins,\n    groupAdminJids,\n    groupAdminPnJids,\n    groupAdminIdentities,\n    senderRole,\n$2`,
    "message group fields anchor",
  );

  return { content, changed: true };
}
