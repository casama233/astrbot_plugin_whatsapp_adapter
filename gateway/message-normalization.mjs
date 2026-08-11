import { normalizeMessageContent } from "@whiskeysockets/baileys";

/**
 * Unwrap Baileys future-proof message containers without mutating the payload.
 */
export function normalizeMessagePayload(payload) {
  if (payload === null || payload === undefined) return payload;
  return normalizeMessageContent(payload) ?? payload;
}

/**
 * Normalize either a raw MessageContent payload or a complete WAMessage item.
 * Media download receives the complete item, while metadata readers need its
 * nested content; keeping that distinction here prevents wrapped documents
 * from losing their name, MIME type, and declared size.
 */
export function normalizeMessageInput(input) {
  return normalizeMessagePayload(input?.message || input);
}

/** Read contextInfo from any standard WhatsApp message component. */
export function contextInfoFromMessagePayload(payload) {
  const message = normalizeMessagePayload(payload);
  if (!message || typeof message !== "object") return null;
  if (message.contextInfo && typeof message.contextInfo === "object") {
    return message.contextInfo;
  }
  for (const value of Object.values(message)) {
    if (value && typeof value === "object" && value.contextInfo) {
      return value.contextInfo;
    }
  }
  return null;
}

/**
 * Return a normalized incoming item while preserving its key and metadata.
 */
export function normalizeIncomingItem(item) {
  if (item === null || item === undefined || typeof item !== "object") return item;
  if (!("message" in item)) return { ...item };
  return {
    ...item,
    message: normalizeMessagePayload(item.message),
  };
}
