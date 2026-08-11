export function chatMessageKey(chatJid, messageId) {
  const chat = String(chatJid || "");
  const id = String(messageId || "");
  return chat && id ? `${chat}:${id}` : null;
}

export function cacheChatMessage(cache, item, maxSize = 500) {
  const key = chatMessageKey(item?.key?.remoteJid, item?.key?.id);
  if (!key || !item?.message) return false;
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
export function cacheEditedMessage(cache, originalKey, editedMessage, maxSize = 500) {
  const chatJid = String(originalKey?.remoteJid || "");
  const messageId = String(originalKey?.id || "");
  const cacheKey = chatMessageKey(chatJid, messageId);
  if (!cacheKey || !editedMessage) return false;

  const current = cache.get(cacheKey);
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
  cache.delete(cacheKey);
  return cacheChatMessage(cache, item, maxSize);
}

export function findChatMessage(cache, chatJid, messageId) {
  const key = chatMessageKey(chatJid, messageId);
  return key ? cache.get(key) : undefined;
}
