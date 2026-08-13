import {
  identityUser,
  isLidJid,
  isPnJid,
  normalizeIdentityJid,
} from "./identity-compat.mjs";

const GROUP_LOCAL_PATTERN = /^\d+(?:-\d+)?$/;

export function normalizeAllowlistIdentity(value) {
  const text = String(value || "").trim();
  if (!text || text === "*") return text;
  if (isPnJid(text) || isLidJid(text)) {
    const normalized = normalizeIdentityJid(text);
    const localPart = normalized.slice(0, normalized.lastIndexOf("@"));
    return /^\d+$/.test(localPart) ? normalized : text;
  }
  if (/^\+?\d+$/.test(text)) {
    return `${text.replace(/^\+/, "")}@s.whatsapp.net`;
  }
  return text;
}

export function allowedByIdentityList(value, allowList, identityRegistry) {
  const candidate = normalizeAllowlistIdentity(value);
  return (Array.isArray(allowList) ? allowList : []).some((item) => {
    const allowed = normalizeAllowlistIdentity(item);
    if (allowed === "*") return true;
    return typeof identityRegistry?.same === "function"
      ? identityRegistry.same(candidate, allowed)
      : candidate === allowed;
  });
}

export function normalizeGroupAllowlistValue(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "*") return raw;
  if (raw.endsWith("@g.us")) {
    const groupId = raw.slice(0, -"@g.us".length);
    return GROUP_LOCAL_PATTERN.test(groupId) ? `${groupId}@g.us` : raw;
  }
  return GROUP_LOCAL_PATTERN.test(raw) ? `${raw}@g.us` : raw;
}

/** Resolve one allowlisted PN to its matching LID without changing domains. */
export async function resolveAllowlistPnToLid(
  value,
  socket,
  onError = null,
) {
  let pnJid = normalizeIdentityJid(value);
  if (!socket || !isPnJid(pnJid) || !/^\d+$/.test(identityUser(pnJid))) {
    return null;
  }

  // Baileys onWhatsApp is a phone lookup and always operates in the standard
  // PN namespace. Calling it for a hosted PN can silently downgrade @hosted to
  // @s.whatsapp.net, so hosted identities go straight to the LID repository.
  if (pnJid.endsWith("@s.whatsapp.net") && socket.onWhatsApp) {
    try {
      const result = await socket.onWhatsApp(identityUser(pnJid));
      const found = (result || []).find((item) => {
        const candidate = normalizeIdentityJid(item?.jid);
        return item?.exists && isPnJid(candidate) && /^\d+$/.test(identityUser(candidate));
      });
      if (found?.jid) pnJid = normalizeIdentityJid(found.jid);
    } catch (error) {
      onError?.(error, "onWhatsApp", pnJid);
    }
  }

  try {
    const lid = await socket.signalRepository?.lidMapping?.getLIDForPN?.(pnJid);
    const lidJid = normalizeIdentityJid(lid);
    if (isLidJid(lidJid) && /^\d+$/.test(identityUser(lidJid))) {
      return { lidJid, pnJid };
    }
  } catch (error) {
    onError?.(error, "lidMapping.getLIDForPN", pnJid);
  }
  return null;
}
