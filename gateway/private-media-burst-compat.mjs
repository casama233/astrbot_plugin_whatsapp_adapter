const PATCH_MARKER = "astrbotPrivateMediaBurstCompatibility";

function replaceRequired(source, pattern, replacement, label) {
  if (!pattern.test(source)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(pattern, replacement);
}

const ALBUM_SCHEDULER = `function albumBufferKey(item, expectedGeneration = socketGeneration) {
  const chatJid = item.key.remoteJid;
  const senderJid = item.key.participant || item.key.participantAlt || (item.key.fromMe ? selfJid : null) || chatJid;
  return \`${"${expectedGeneration}:${chatJid}:${senderJid}"}\`;
}

function inboundMessageTimestampMs(item) {
  const raw = item?.messageTimestamp;
  const value = Number(raw && typeof raw === "object" && typeof raw.toString === "function" ? raw.toString() : raw || 0);
  if (!Number.isFinite(value) || value <= 0) return 0;
  return value > 1_000_000_000_000 ? value : value * 1000;
}

async function flushAlbumBuffer(
  bufferKey,
  expectedGeneration = socketGeneration,
  eventSocket = socket,
) {
  const pending = albumBuffers.get(bufferKey);
  albumBuffers.delete(bufferKey);
  if (!pending?.items?.length) return false;
  if (pending.timer) clearTimeout(pending.timer);
  if (expectedGeneration !== socketGeneration || eventSocket !== socket) return false;
  const items = pending.items;
  await handleIncomingMessage(
    items[0],
    { albumItems: items },
    expectedGeneration,
    eventSocket,
  );
  return true;
}

async function scheduleAlbumItem(item, expectedGeneration = socketGeneration, eventSocket = socket) {
  const bufferKey = albumBufferKey(item, expectedGeneration);
  const debounceMs = Number(runtimeConfig.mediaAlbumDebounceMs || 0);
  const timestampMs = inboundMessageTimestampMs(item);
  let buffer = albumBuffers.get(bufferKey);

  // Debounce is intended to coalesce pictures the user actually sent as one
  // short burst, not old messages that merely arrived together after reconnect.
  if (
    buffer
    && timestampMs
    && buffer.lastTimestampMs
    && Math.abs(timestampMs - buffer.lastTimestampMs) > debounceMs
  ) {
    await flushAlbumBuffer(bufferKey, expectedGeneration, eventSocket);
    if (expectedGeneration !== socketGeneration || eventSocket !== socket) return;
    buffer = null;
  }

  if (!buffer) {
    buffer = { items: [], timer: null, lastTimestampMs: 0 };
    albumBuffers.set(bufferKey, buffer);
  }
  buffer.items.push(item);
  if (timestampMs) buffer.lastTimestampMs = timestampMs;
  if (buffer.timer) clearTimeout(buffer.timer);
  buffer.timer = setTimeout(() => {
    flushAlbumBuffer(bufferKey, expectedGeneration, eventSocket).catch((error) =>
      log.warn({ error, count: buffer.items.length }, "album message handling failed"),
    );
  }, debounceMs);
}`;

const ROUTE_ALBUM_BLOCK = `  const debounceMs = Number(runtimeConfig.mediaAlbumDebounceMs || 0);
  const albumCandidate = debounceMs > 0 && isAlbumCandidate(item);
  const bufferKey = albumBufferKey(item, expectedGeneration);

  // An image burst is deliberately delayed for a short debounce window.  If
  // the same sender follows it with text, a captioned image, a reply, or any
  // other semantic message, flush the pending pictures first so the delayed
  // album cannot overtake the newer message in AstrBot's event queue.
  if (debounceMs > 0 && albumBuffers.has(bufferKey) && !albumCandidate) {
    await flushAlbumBuffer(bufferKey, expectedGeneration, eventSocket);
    if (expectedGeneration !== socketGeneration || eventSocket !== socket) return;
  }

  if (albumCandidate) {
    await scheduleAlbumItem(item, expectedGeneration, eventSocket);
    return;
  }
  await handleIncomingMessage(item, {}, expectedGeneration, eventSocket);`;

export function patchGatewayPrivateMediaBursts(source) {
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
    /function scheduleAlbumItem\([\s\S]*?\n}\n\nasync function routeIncomingMessage/,
    `${ALBUM_SCHEDULER}\n\nasync function routeIncomingMessage`,
    "album scheduler",
  );
  content = replaceRequired(
    content,
    /  const debounceMs = Number\(runtimeConfig\.mediaAlbumDebounceMs \|\| 0\);\n  if \(debounceMs > 0 && isAlbumCandidate\(item\)\) \{\n    scheduleAlbumItem\(item, expectedGeneration, eventSocket\);\n    return;\n  \}\n  await handleIncomingMessage\(item, \{\}, expectedGeneration, eventSocket\);/,
    ROUTE_ALBUM_BLOCK,
    "incoming album routing block",
  );

  return { content, changed: true };
}
