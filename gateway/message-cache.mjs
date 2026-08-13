export function chatMessageKey(chatJid, messageId) {
  const chat = String(chatJid || "");
  const id = String(messageId || "");
  return chat && id ? `${chat}:${id}` : null;
}

export function chatMessageKeys(chatJid, messageId, identityRegistry = null) {
  const chat = String(chatJid || "");
  const id = String(messageId || "");
  if (!chat || !id) return [];
  const aliases = typeof identityRegistry?.aliases === "function"
    ? identityRegistry.aliases(chat)
    : [chat];
  return [...new Set(aliases.map((alias) => chatMessageKey(alias, id)).filter(Boolean))];
}

export function cacheChatMessage(cache, item, maxSize = 500, identityRegistry = null) {
  const keys = chatMessageKeys(item?.key?.remoteJid, item?.key?.id, identityRegistry);
  const key = keys[0];
  if (!key || !item?.message) return false;
  for (const aliasKey of keys) cache.delete(aliasKey);
  cache.set(key, item);
  const limit = Math.max(1, Number(maxSize) || 1);
  while (cache.size > limit) {
    cache.delete(cache.keys().next().value);
  }
  return true;
}

/**
 * Replace a cached message after WhatsApp accepts an edit.
 *
 * Edit acknowledgements have their own stanza ID, but replies must continue to
 * target the original message key. Preserve any cached metadata while storing
 * the final edited content under that original key.
 */
export function cacheEditedMessage(
  cache,
  originalKey,
  editedMessage,
  maxSize = 500,
  identityRegistry = null,
) {
  const chatJid = String(originalKey?.remoteJid || "");
  const messageId = String(originalKey?.id || "");
  const cacheKeys = chatMessageKeys(chatJid, messageId, identityRegistry);
  if (!cacheKeys.length || !editedMessage) return false;

  const current = findChatMessage(cache, chatJid, messageId, identityRegistry);
  const item = {
    ...(current || {}),
    key: {
      ...(current?.key || {}),
      ...originalKey,
      remoteJid: chatJid,
      id: messageId,
    },
    message: editedMessage,
  };

  // Refresh the insertion order so a newly edited message is not immediately
  // evicted merely because its original version was cached a long time ago.
  for (const cacheKey of cacheKeys) cache.delete(cacheKey);
  return cacheChatMessage(cache, item, maxSize, identityRegistry);
}

export function findChatMessage(cache, chatJid, messageId, identityRegistry = null) {
  for (const key of chatMessageKeys(chatJid, messageId, identityRegistry)) {
    const item = cache.get(key);
    if (item) return item;
  }
  return undefined;
}
