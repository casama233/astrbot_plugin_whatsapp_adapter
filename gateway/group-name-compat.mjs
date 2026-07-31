const PATCH_MARKER = "astrbotGroupNameCompatibility";

function replaceRequired(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(pattern, replacement);
}

export function patchGatewayGroupNames(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  let content = source;
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
    /async function rememberGroupParticipants\(chatJid\) \{[\s\S]*?\n\}\n\nfunction mentionTokensFromText/,
    `function cacheGroupMetadata(metadata) {\n  const jid = String(metadata?.id || \"\");\n  if (!jid) return metadata || null;\n  const previous = groupMetadataCache.get(jid)?.metadata || {};\n  const merged = { ...previous, ...metadata, id: jid };\n  groupMetadataCache.set(jid, { metadata: merged, cachedAt: Date.now() });\n  while (groupMetadataCache.size > maxGroupMetadataCacheSize) {\n    groupMetadataCache.delete(groupMetadataCache.keys().next().value);\n  }\n  return merged;\n}\n\nasync function rememberGroupParticipants(chatJid) {\n  if (!socket?.groupMetadata || !String(chatJid || \"\").endsWith(\"@g.us\")) return null;\n  try {\n    const metadata = cacheGroupMetadata(await socket.groupMetadata(chatJid));\n    for (const participant of metadata?.participants || []) {\n      const jid = participant?.id || participant?.jid;\n      rememberMentionIdentity(jid);\n      if (jid && String(jid).endsWith(\"@lid\")) {\n        const resolved = resolveLidToPn(jid);\n        if (resolved) rememberLidPnMapping(jid, resolved);\n      }\n    }\n    return metadata;\n  } catch (error) {\n    log.debug({ error, chatJid }, \"failed to refresh group mention directory\");\n    return groupMetadataCache.get(String(chatJid || \"\"))?.metadata || null;\n  }\n}\n\nasync function groupMetadataForMessage(chatJid) {\n  const cached = groupMetadataCache.get(String(chatJid || \"\"));\n  if (cached && Date.now() - cached.cachedAt < groupMetadataCacheTtlMs) {\n    return cached.metadata;\n  }\n\n  let timer;\n  try {\n    return await Promise.race([\n      rememberGroupParticipants(chatJid),\n      new Promise((resolve) => {\n        timer = setTimeout(() => resolve(cached?.metadata || null), 2500);\n      }),\n    ]);\n  } finally {\n    if (timer) clearTimeout(timer);\n  }\n}\n\nfunction mentionTokensFromText`,
    "group metadata helper",
  );

  content = replaceRequired(
    content,
    /const senderJid = primary\.key\.participant \|\| chatJid;\n  rememberMentionIdentity\(senderJid, primary\.pushName\);/,
    `const senderJid = primary.key.participant || chatJid;\n  const isGroup = chatJid.endsWith(\"@g.us\");\n  const groupMetadataPromise = isGroup\n    ? groupMetadataForMessage(chatJid)\n    : Promise.resolve(null);\n  rememberMentionIdentity(senderJid, primary.pushName);`,
    "incoming sender anchor",
  );

  content = replaceRequired(
    content,
    /\n  rememberGroupParticipants\(chatJid\)\.catch\(\(\) => \{\}\);/,
    "",
    "legacy participant refresh call",
  );

  content = replaceRequired(
    content,
    /\n  const isGroup = chatJid\.endsWith\("@g\.us"\);\n  if \(!configured\)/,
    `\n  if (!configured)`,
    "duplicate group declaration",
  );

  content = replaceRequired(
    content,
    /  socket\.ev\.on\("contacts\.upsert", \(contacts\) => \{/,
    `  socket.ev.on(\"groups.update\", (groups) => {\n    for (const update of groups || []) cacheGroupMetadata(update);\n  });\n  socket.ev.on(\"contacts.upsert\", (contacts) => {`,
    "group update listener anchor",
  );

  content = replaceRequired(
    content,
    /  broadcast\(\{\n    type: "message",/,
    `  const groupMetadata = isGroup ? await groupMetadataPromise : null;\n  const groupName = String(groupMetadata?.subject || \"\").trim();\n  broadcast({\n    type: \"message\",`,
    "message broadcast anchor",
  );

  content = replaceRequired(
    content,
    /(    albumMessageIds: albumItems\.length > 1 \? albumItems\.map\(\(albumItem\) => albumItem\.key\.id\) : undefined,\n    chatJid,\n)(    senderJid,)/,
    `$1    groupName,\n    group_name: groupName,\n    groupSubject: groupName,\n$2`,
    "message group fields anchor",
  );

  return { content, changed: true };
}
