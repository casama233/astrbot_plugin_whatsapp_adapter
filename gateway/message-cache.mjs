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

export function findChatMessage(cache, chatJid, messageId) {
  const key = chatMessageKey(chatJid, messageId);
  return key ? cache.get(key) : undefined;
}
