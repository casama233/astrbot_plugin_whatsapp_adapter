const PN_DOMAINS = ["@s.whatsapp.net", "@hosted"];
const LID_DOMAINS = ["@lid", "@hosted.lid"];
const IDENTITY_DOMAINS = [...PN_DOMAINS, ...LID_DOMAINS];
const DEVICE_USER_PATTERN = /^(\d+)(?:_\d+)?(?::\d+)?$/;

export function normalizeIdentityJid(value) {
  const raw = String(value || "").trim();
  const separator = raw.lastIndexOf("@");
  if (separator <= 0) return raw;
  const domain = `@${raw.slice(separator + 1).toLowerCase()}`;
  if (!IDENTITY_DOMAINS.includes(domain)) return raw;
  const match = raw.slice(0, separator).match(DEVICE_USER_PATTERN);
  return match ? `${match[1]}${domain}` : raw;
}

function jidForKind(value, domains, fallbackDomain, allowBare = false) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const normalized = normalizeIdentityJid(raw);
  if (domains.some((domain) => normalized.endsWith(domain))) {
    return /^\d+$/.test(identityUser(normalized)) ? normalized : null;
  }
  if (raw.includes("@") || !allowBare) return null;
  if (!/^\+?\d+$/.test(raw)) return null;
  const digits = raw.replace(/^\+/, "");
  return digits ? `${digits}${fallbackDomain}` : null;
}

export function isPnJid(value) {
  const raw = normalizeIdentityJid(value);
  return PN_DOMAINS.some((domain) => raw.endsWith(domain));
}

export function isLidJid(value) {
  const raw = normalizeIdentityJid(value);
  return LID_DOMAINS.some((domain) => raw.endsWith(domain));
}

export function identityUser(value) {
  const localPart = String(value || "").trim().split("@", 1)[0];
  const match = localPart.match(DEVICE_USER_PATTERN);
  return match ? match[1] : localPart;
}

export function phoneFromIdentity(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;
  if (raw.includes("@")) {
    if (!isPnJid(raw)) return null;
    const digits = identityUser(normalizeIdentityJid(raw));
    return /^\d+$/.test(digits) ? `+${digits}` : null;
  }
  if (!/^\+?\d+$/.test(raw)) return null;
  return `+${raw.replace(/^\+/, "")}`;
}

export function identityPair(values) {
  const candidates = Array.isArray(values) ? values : [values];
  return {
    pnJid: candidates.map((value) => jidForKind(value, PN_DOMAINS, "@s.whatsapp.net")).find(Boolean) || null,
    lidJid: candidates.map((value) => jidForKind(value, LID_DOMAINS, "@lid")).find(Boolean) || null,
  };
}

/** Resolve the actual sender identity fields used by Baileys 7 and older builds. */
export function senderIdentityFromKey(
  key,
  { isGroup = false, fromMe = false, senderJid = null, chatJid = null, selfJid = null, selfLid = null } = {},
) {
  let values;
  if (fromMe) {
    values = [selfJid, selfLid, senderJid];
  } else if (isGroup) {
    values = [
      senderJid,
      key?.participant,
      key?.participantAlt,
      key?.senderPn,
      key?.participantPn,
    ];
  } else {
    values = [
      senderJid,
      chatJid,
      key?.remoteJid,
      key?.remoteJidAlt,
      key?.senderPn,
      key?.participantPn,
    ];
  }
  const pair = identityPair(values);
  const legacyPn = [key?.senderPn, key?.participantPn]
    .map((value) => jidForKind(value, PN_DOMAINS, "@s.whatsapp.net", true))
    .find(Boolean);
  return { ...pair, pnJid: pair.pnJid || legacyPn || null };
}

/** Resolve the dual PN/LID representation used by GroupParticipant in rc14. */
export function groupParticipantIdentity(participant) {
  const values = [
    participant?.id,
    participant?.jid,
    participant?.phoneNumber,
    participant?.lid,
  ];
  const pair = identityPair(values);
  const pnJid = pair.pnJid || jidForKind(participant?.phoneNumber, PN_DOMAINS, "@s.whatsapp.net", true);
  const lidJid = pair.lidJid || jidForKind(participant?.lid, LID_DOMAINS, "@lid", true);
  return {
    jid: normalizeIdentityJid(
      participant?.id || participant?.jid || participant?.lid || participant?.phoneNumber || "",
    ),
    pnJid: pnJid || null,
    lidJid: lidJid || null,
  };
}

/** GroupMetadata.ownerPn is the stable phone-number identity in Baileys 7. */
export function groupOwnerIdentity(metadata) {
  const pair = identityPair([metadata?.ownerPn, metadata?.owner]);
  return {
    jid: normalizeIdentityJid(metadata?.owner || metadata?.ownerPn || ""),
    pnJid: pair.pnJid || jidForKind(metadata?.ownerPn, PN_DOMAINS, "@s.whatsapp.net", true),
    lidJid: pair.lidJid || jidForKind(metadata?.owner, LID_DOMAINS, "@lid", true),
  };
}

export function participantIdentityValues(participant) {
  const identity = groupParticipantIdentity(participant);
  return [...new Set([
    identity.jid,
    identity.pnJid,
    identity.lidJid,
    participant?.id,
    participant?.jid,
    participant?.phoneNumber,
    participant?.lid,
  ].map(normalizeIdentityJid).filter(Boolean))];
}

/**
 * Resolve transport mentions without ever forwarding arbitrary strings to
 * Baileys as JIDs.  Display names are accepted only when the caller's scoped
 * directory can prove which participant they identify; bare phone numbers are
 * converted to PN JIDs, and explicit PN/LID values lose device suffixes.
 */
export function resolveExplicitIdentityMentions(
  values,
  {
    chatJid = null,
    scopedDirectory = null,
    globalDirectory = null,
  } = {},
) {
  const mentions = [];
  let mentionAll = false;

  for (const rawValue of Array.isArray(values) ? values : []) {
    const value = String(rawValue || "").trim();
    if (!value) continue;
    const token = value.replace(/^@+/, "");
    if (token.toLowerCase() === "all") {
      mentionAll = true;
      continue;
    }

    const key = token.toLowerCase();
    const isBarePhone = /^\+?\d+$/.test(token);
    let jid = scopedDirectory?.get(key) || null;
    if (!jid && (!chatJid || isBarePhone)) jid = globalDirectory?.get(key) || null;

    if (!jid && (isPnJid(token) || isLidJid(token))) {
      const normalized = normalizeIdentityJid(token);
      jid = /^\d+$/.test(identityUser(normalized)) ? normalized : null;
    } else if (!jid && isBarePhone) {
      const digits = token.replace(/\D/g, "");
      jid = digits ? `${digits}@s.whatsapp.net` : null;
    }

    if (!isPnJid(jid) && !isLidJid(jid)) continue;
    const normalized = normalizeIdentityJid(jid);
    if (!/^\d+$/.test(identityUser(normalized))) continue;
    if (normalized && !mentions.includes(normalized)) mentions.push(normalized);
  }

  return { mentions, mentionAll };
}
