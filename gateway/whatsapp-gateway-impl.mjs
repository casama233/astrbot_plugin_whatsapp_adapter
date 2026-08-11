import { createServer } from "node:http";
import { mkdir, readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { createWriteStream, statSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";


import makeWASocket, {
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  generateWAMessageFromContent,
  proto,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import pino from "pino";
import QRCode from "qrcode";
import qrcode from "qrcode-terminal";
import {
  disconnectKind,
  reconnectDelayMs,
  sessionDirectory,
} from "./session-lifecycle.mjs";
import {
  cacheChatMessage,
  cacheEditedMessage,
  findChatMessage,
} from "./message-cache.mjs";
import {
  inboundMediaDownloadContext,
  removePartialInboundMedia,
} from "./media-download-compat.mjs";
import {
  contextInfoFromMessagePayload,
  normalizeIncomingItem,
  normalizeMessageInput,
  normalizeMessagePayload,
} from "./message-normalization.mjs";
import {
  groupOwnerIdentity,
  groupParticipantIdentity,
  identityUser,
  isLidJid,
  isPnJid,
  normalizeIdentityJid,
  participantIdentityValues,
  phoneFromIdentity,
  resolveExplicitIdentityMentions,
  senderIdentityFromKey,
} from "./identity-compat.mjs";
import {
  buildContactContent,
  buildEventContent,
  buildPollContent,
  eventDetailsFromMessage,
} from "./native-tools.mjs";
import {
  createPairingCodePolicy,
  normalizePairingPhone,
  pairingCodeAvailability,
} from "./pairing-code-compat.mjs";

const host = process.env.WA_GATEWAY_HOST || "127.0.0.1";
const port = Number.parseInt(process.env.WA_GATEWAY_PORT || "18789", 10);
const dataDir = process.env.WA_DATA_DIR || path.join(process.cwd(), "data", "plugin_data", "astrbot_plugin_whatsapp_adapter");
const authDir = process.env.WA_AUTH_DIR || path.join(dataDir, "whatsapp-auth");
const activeSessionFile = path.join(authDir, ".active-session.json");
const logLevel = process.env.WA_LOG_LEVEL || "info";
const tempDir = process.env.WA_TEMP_DIR || path.join(dataDir, "..", "..", "temp");

const log = pino({ level: logLevel });
const sseClients = new Set();
const seenIncomingMessages = new Map();
const maxSeenIncomingMessages = 2000;
const albumBuffers = new Map();
const pairingCodePolicy = createPairingCodePolicy({ cooldownMs: 30_000 });

let socket = null;
let currentAuthDir = authDir;
let currentSessionId = "legacy";
let socketTransition = Promise.resolve();
let resetSequence = 0;
let consecutiveFreshAuthFailures = 0;
let transientReconnectAttempt = 0;
let ready = false;
let configured = false;
let selfJid = null;
let selfLid = null;
let lastError = null;
const messageCache = new Map();
const maxMessageCacheSize = 500;
const mentionDirectory = new Map();
const mentionDirectoriesByChat = new Map();
const mentionDisplayNames = new Map();
const maxKnownContacts = 10000;

// 統一聯絡人儲存：JID → contact 物件（含 id/lid/jid 欄位）
const knownContacts = new Map();

// 聊天室 ephemeral 快取：JID → expiration 秒數（如 86400 = 24hr）
const chatEphemeral = new Map();

function updateContact(contact) {
  const identity = groupParticipantIdentity(contact);
  const id = String(contact?.id || contact?.jid || identity.lidJid || identity.pnJid || "");
  if (!id) return;
  const previous = knownContacts.get(id) || {};
  const merged = {
    ...previous,
    ...contact,
    id,
    jid: contact?.jid || identity.pnJid || previous.jid,
    lid: contact?.lid || identity.lidJid || previous.lid,
    phoneNumber: contact?.phoneNumber || identity.pnJid || previous.phoneNumber,
  };
  for (const alias of [merged.id, merged.jid, merged.lid, merged.phoneNumber].filter(Boolean)) {
    knownContacts.set(String(alias), merged);
  }
  while (knownContacts.size > maxKnownContacts) {
    knownContacts.delete(knownContacts.keys().next().value);
  }
}

function pnJidFromValue(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "*" || raw.endsWith("@g.us")) return null;
  if (isPnJid(raw)) return raw;
  if (isLidJid(raw)) return resolveLidToPn(raw);
  const digits = raw.replace(/\D/g, "");
  return digits ? `${digits}@s.whatsapp.net` : null;
}

async function persistLidMapping(lidJid, pnJid) {
  const lidNum = normalizeJid(lidJid).replace(/\D/g, "");
  const pnNum = normalizeJid(pnJid).replace(/\D/g, "");
  if (!lidNum || !pnNum) return;
  try {
    await mkdir(currentAuthDir, { recursive: true });
    await writeFile(path.join(currentAuthDir, `lid-mapping-${lidNum}_reverse.json`), JSON.stringify(pnNum), "utf-8");
  } catch (error) {
    log.debug({ error, lidJid, pnJid }, "failed to persist lid→pn mapping");
  }
}

function rememberLidPnMapping(lidJid, pnJid, persist = true) {
  if (!lidJid || !pnJid || !isLidJid(lidJid) || !isPnJid(pnJid)) return false;
  updateContact({ id: lidJid, jid: pnJid, lid: lidJid });
  if (persist) persistLidMapping(lidJid, pnJid).catch(() => {});
  return true;
}

/**
 * 從任何 JID 解析出 E.164 電話號碼字串（如 "+85266631531"）
 *  - PN JID (@s.whatsapp.net) → 直接提取數字
 *  - LID JID (@lid)        → 查聯絡人 → 取得 PN → 提取數字
 *  - 純數字字串            → 視為電話號碼
 *  - 無效                  → null
 */
function resolvePhone(jid) {
  if (!jid) return null;
  const raw = String(jid).trim();
  if (isPnJid(raw)) {
    return phoneFromIdentity(raw);
  }
  if (isLidJid(raw)) {
    const contact = knownContacts.get(raw);
    if (contact) {
      const pnJid = [contact.phoneNumber, contact.jid, contact.id]
        .find((j) => isPnJid(j));
      if (pnJid) {
        const digits = pnJid.split("@")[0].replace(/\D/g, "");
        if (digits) return `+${digits}`;
      }
    }
    return null;
  }
  const digits = raw.replace(/\D/g, "");
  return digits ? `+${digits}` : null;
}

function normalizeLidJid(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const normalized = normalizeIdentityJid(text);
  return isLidJid(normalized) ? normalized : `${identityUser(normalized)}@lid`;
}

/**
 * 嘗試將 LID JID 解析為 PN JID（如 "208070378541290@lid" → "85266631531:20@s.whatsapp.net"）
 * 透過 knownContacts 查找 LID→PN 映射
 */
function resolveLidToPn(lidJid) {
  if (!lidJid || !isLidJid(lidJid)) return null;
  const contact = knownContacts.get(String(lidJid).trim());
  if (contact) {
    const pnJid = [contact.phoneNumber, contact.jid, contact.id]
      .find((j) => isPnJid(j));
    if (pnJid) return pnJid;
  }
  return null;
}

async function resolveLidToPnDeep(
  lidJid,
  mappingSocket = socket,
  expectedGeneration = socketGeneration,
) {
  const existing = resolveLidToPn(lidJid);
  if (existing) return existing;
  if (
    expectedGeneration !== socketGeneration
    || mappingSocket !== socket
    || !mappingSocket?.signalRepository?.lidMapping
    || !isLidJid(lidJid)
  ) return null;
  try {
    const pn = await mappingSocket.signalRepository.lidMapping.getPNForLID(lidJid);
    if (expectedGeneration !== socketGeneration || mappingSocket !== socket) return null;
    if (isPnJid(pn)) {
      rememberLidPnMapping(lidJid, pn);
      return pn;
    }
  } catch (error) {
    log.debug({ error, lidJid }, "lidMapping.getPNForLID failed");
  }
  return null;
}

/**
 * 從 auth 目錄載入磁碟上已有的 lid-mapping-*_reverse.json
 * 在 Gateway 重啟時恢復 Lid→PN 映射，補償 Baileys 不重播既有映射事件。
 */
async function loadLidMappingsFromDisk() {
  try {
    const files = await readdir(currentAuthDir);
    let loaded = 0;
    for (const name of files) {
      const match = name.match(/^lid-mapping-(\d+)_reverse\.json$/);
      if (!match) continue;
      try {
        const content = await readFile(path.join(currentAuthDir, name), "utf-8");
        const phone = JSON.parse(content);
        if (phone && typeof phone === "string") {
          const lid = `${match[1]}@lid`;
          const pnJid = `${phone}@s.whatsapp.net`;
          rememberLidPnMapping(lid, pnJid, false);
          loaded++;
        }
      } catch {
        // ignore malformed files
      }
    }
    if (loaded) log.info({ loaded }, "loaded lid→pn mappings from disk on startup");
  } catch (error) {
    log.debug({ error }, "failed to load lid mappings from disk");
  }
}

/**
 * 等待 Lid→PN 映射事件到達，超時後返回 null。
 * Baileys 在收到 Lid 訊息後會透過 contacts.upsert / lid-mapping.update / chats.phoneNumberShare
 * 提供 Lid→PN 映射，但可能比 messages.upsert 稍晚到達。
 */
async function waitForLidPnMapping(lidJid, timeoutMs) {
  const mappingSocket = socket;
  const expectedGeneration = socketGeneration;
  const isStale = () => (
    expectedGeneration !== socketGeneration || mappingSocket !== socket
  );
  const resolveDeep = () => resolveLidToPnDeep(
    lidJid,
    mappingSocket,
    expectedGeneration,
  );
  const existing = await resolveDeep();
  if (existing) return existing;
  if (isStale()) return null;
  return (async () => {
    if (mappingSocket?.presenceSubscribe) {
      try { await mappingSocket.presenceSubscribe(lidJid); } catch {}
    }
    if (isStale()) return null;
    const afterSubscribe = await resolveDeep();
    if (afterSubscribe) return afterSubscribe;
    return new Promise((resolve) => {
      const EVENTS = ["contacts.upsert", "lid-mapping.update", "chats.phoneNumberShare"];
      const removeListeners = () => {
        for (const evt of EVENTS) mappingSocket?.ev?.off(evt, handler);
      };
      const timer = setTimeout(() => {
        removeListeners();
        resolve(isStale() ? null : resolveDeep());
      }, timeoutMs);
      const handler = (...args) => {
        if (isStale()) {
          clearTimeout(timer);
          removeListeners();
          resolve(null);
          return;
        }
        const data = args[0];
        let pn = null;
        if (data && data.id === lidJid) {
          pn = [data.phoneNumber, data.jid, data.id].find((j) => isPnJid(j));
        } else if (data && data.lid === lidJid) {
          pn = data.pn || data.pnJid || data.jid;
        } else if (data && data.lidJid === lidJid) {
          pn = data.pnJid || data.pn || data.jid;
        }
        if (!pn && Array.isArray(data)) {
          const matched = data.find((c) => c.id === lidJid || c.lid === lidJid || c.jid === lidJid);
          if (matched) pn = [matched.phoneNumber, matched.jid, matched.id].find((j) => isPnJid(j));
        }
        if (pn) {
          rememberLidPnMapping(lidJid, pn);
          clearTimeout(timer);
          removeListeners();
          resolve(pn);
        }
      };
      for (const evt of EVENTS) mappingSocket.ev.on(evt, handler);
      resolveDeep().then((recheck) => {
        if (!recheck) return;
        clearTimeout(timer);
        removeListeners();
        resolve(recheck);
      });
    });
  })();
}

/**
 * 從訊息中提取所有可能的比對候選（電話號碼 + 原始 JID）
 * 同时接受 Baileys 7 的 participantAlt/remoteJidAlt 与旧版 PN 字段。
 */
function messageCandidates(item, chatJid, senderJid) {
  const phones = [];
  const seenPhones = new Set();
  const jids = [];
  const seenJids = new Set();

  const add = (value) => {
    const jid = String(value || "").trim();
    if (!jid || jid.endsWith("@g.us") || jid.endsWith("@status") || jid.endsWith("@broadcast") || seenJids.has(jid)) return;
    seenJids.add(jid);
    jids.push(jid);
    const phone = resolvePhone(jid);
    if (phone && !seenPhones.has(phone)) {
      seenPhones.add(phone);
      phones.push(phone);
    }
  };

  if (!String(chatJid || "").endsWith("@g.us")) add(chatJid);
  add(senderJid);
  for (const candidate of [
    item?.key?.participant,
    item?.key?.participantAlt,
    item?.key?.remoteJidAlt,
    item?.key?.senderPn,
    item?.key?.participantPn,
  ]) {
    add(candidate);
  }

  return { phones, jids };
}

let latestQr = null;
let latestQrDataUrl = null;
let connectionStatus = "starting";
let reconnectTimer = null;
let presenceTimer = null;
let lastPresenceAt = null;
let socketGeneration = 0;
let runtimeConfig = {
  dmPolicy: "allowlist",
  allowFrom: [],
  groupPolicy: "disabled",
  groupAllowFrom: [],
  groups: [],
  sendReadReceipts: true,
  markOnline: false,
  mediaMaxMb: 50,
  mediaMessageMaxMb: 100,
  documentMaxMb: 2048,
  audioMaxMb: 16,
  mediaAlbumDebounceMs: 2500,
  // 預設關閉：Baileys 7.0.0-rc13 省略了 ephemeralSettingTimestamp 欄位，
  // 導致收件端把發送端當作「舊版 WhatsApp」並顯示「此訊息不會自動刪除」警告。
  // 開啟後會把聊天室的消失訊息設定套用到外寄訊息（可能觸發上述警告）。
  applyEphemeral: false,
};

function configuredAllowlistPnJids() {
  const values = [
    ...(runtimeConfig.allowFrom || []),
    ...(runtimeConfig.groupAllowFrom || []),
  ];
  const result = [];
  const seen = new Set();
  for (const value of values) {
    const pnJid = pnJidFromValue(value);
    if (pnJid && !seen.has(pnJid)) {
      seen.add(pnJid);
      result.push(pnJid);
    }
  }
  return result;
}

async function resolvePnToLid(pnJid) {
  if (!socket || !pnJid?.endsWith?.("@s.whatsapp.net")) return null;
  let normalizedPn = pnJid;
  try {
    const result = await socket.onWhatsApp(normalizeJid(pnJid));
    const found = (result || []).find((item) => item?.exists && item?.jid?.endsWith?.("@s.whatsapp.net"));
    if (found?.jid) normalizedPn = found.jid;
  } catch (error) {
    log.debug({ error, pnJid }, "onWhatsApp lookup failed while resolving allowlist LID");
  }
  try {
    const lid = await socket.signalRepository?.lidMapping?.getLIDForPN?.(normalizedPn);
    if (lid?.endsWith?.("@lid")) {
      rememberLidPnMapping(lid, normalizedPn);
      return lid;
    }
  } catch (error) {
    log.debug({ error, pnJid: normalizedPn }, "lidMapping.getLIDForPN failed");
  }
  return null;
}

async function refreshAllowlistLidMappings(reason = "manual") {
  if (!socket || !ready) return 0;
  const pnJids = configuredAllowlistPnJids();
  let resolved = 0;
  for (const pnJid of pnJids) {
    const lid = await resolvePnToLid(pnJid);
    if (lid) resolved++;
  }
  if (resolved) log.info({ reason, resolved, count: pnJids.length }, "refreshed allowlist LID mappings");
  return resolved;
}

function stopPresenceTimer() {
  if (presenceTimer) clearInterval(presenceTimer);
  presenceTimer = null;
}

async function sendAvailablePresence() {
  if (!runtimeConfig.markOnline || !socket?.sendPresenceUpdate || !ready) return;
  await socket.sendPresenceUpdate("available");
  lastPresenceAt = new Date().toISOString();
}

async function sendUnavailablePresence() {
  if (!socket?.sendPresenceUpdate || !ready) return;
  await socket.sendPresenceUpdate("unavailable");
  lastPresenceAt = null;
}

async function sendReplyPresence(state, to) {
  // `available` and `unavailable` are account-wide WhatsApp presence states;
  // `composing` and `paused` are scoped to a chat.  When persistent online is
  // disabled, briefly become available for a reply so the contact sees the
  // real response activity, then explicitly return offline afterwards.
  if (state === "composing" && !runtimeConfig.markOnline) {
    await socket.sendPresenceUpdate("available");
    lastPresenceAt = new Date().toISOString();
  }

  if (state === "paused") {
    try {
      await socket.sendPresenceUpdate("paused", to);
    } finally {
      if (!runtimeConfig.markOnline) await sendUnavailablePresence();
    }
    return;
  }

  await socket.sendPresenceUpdate(state, to);
}

function startPresenceTimer() {
  stopPresenceTimer();
  if (!runtimeConfig.markOnline) return;
  sendAvailablePresence().catch((error) => log.debug({ error }, "presence update failed"));
  presenceTimer = setInterval(() => {
    sendAvailablePresence().catch((error) => log.debug({ error }, "presence update failed"));
  }, 25000);
}

function sendSse(client, data) {
  try {
    client.write(`data: ${JSON.stringify(data)}\n\n`);
  } catch {
    sseClients.delete(client);
  }
}

function broadcast(data) {
  for (const client of sseClients) {
    sendSse(client, data);
  }
}

function normalizeJid(jid) {
  return identityUser(jid);
}

function mentionKey(value) {
  return String(value || "").trim().replace(/^@+/, "").toLowerCase();
}

function rememberMentionIdentity(jid, ...names) {
  if (!jid) return;
  const fullJid = String(jid);
  const keys = [fullJid, normalizeJid(fullJid), jidToPhone(fullJid), ...names].filter(Boolean);
  for (const key of keys) {
    const normalized = mentionKey(key);
    if (normalized) mentionDirectory.set(normalized, fullJid);
  }
  const displayName = names
    .map((name) => String(name || "").trim())
    .find((name) => name && mentionKey(name) !== mentionKey(fullJid) && name !== normalizeJid(fullJid));
  if (displayName) mentionDisplayNames.set(fullJid, displayName);
  while (mentionDirectory.size > 5000) {
    mentionDirectory.delete(mentionDirectory.keys().next().value);
  }
  while (mentionDisplayNames.size > 5000) {
    mentionDisplayNames.delete(mentionDisplayNames.keys().next().value);
  }
}

function rememberScopedMentionIdentity(chatJid, jid, ...names) {
  rememberMentionIdentity(jid, ...names);
  const chat = String(chatJid || "");
  if (!chat || !jid) return;
  let directory = mentionDirectoriesByChat.get(chat);
  if (!directory) {
    directory = new Map();
    mentionDirectoriesByChat.set(chat, directory);
  }
  const fullJid = String(jid);
  for (const key of [fullJid, normalizeJid(fullJid), jidToPhone(fullJid), ...names].filter(Boolean)) {
    const normalized = mentionKey(key);
    if (normalized) directory.set(normalized, fullJid);
  }
  while (mentionDirectoriesByChat.size > 1000) {
    mentionDirectoriesByChat.delete(mentionDirectoriesByChat.keys().next().value);
  }
}

function rememberScopedMentionAliases(chatJid, targetJid, aliases, names = []) {
  const chat = String(chatJid || "");
  const target = String(targetJid || "");
  if (!chat || !target) return;
  rememberScopedMentionIdentity(chat, target, ...names);
  const directory = mentionDirectoriesByChat.get(chat);
  for (const alias of aliases.filter(Boolean)) {
    const value = String(alias);
    for (const key of [value, normalizeJid(value), jidToPhone(value)]) {
      const normalized = mentionKey(key);
      if (normalized) directory.set(normalized, target);
    }
  }
  for (const name of names.filter(Boolean)) {
    const normalized = mentionKey(name);
    if (normalized) directory.set(normalized, target);
  }
}

function rememberResolvedIdentity(identity, ...names) {
  if (!identity) return identity;
  if (identity.lidJid && identity.pnJid) {
    rememberLidPnMapping(identity.lidJid, identity.pnJid);
  }
  for (const jid of [identity.jid, identity.pnJid, identity.lidJid].filter(Boolean)) {
    rememberMentionIdentity(jid, ...names);
  }
  return identity;
}

function rememberGroupParticipantIdentity(participant, chatJid = null) {
  const identity = groupParticipantIdentity(participant);
  const names = [
    participant?.name,
    participant?.notify,
    participant?.username,
    participant?.verifiedName,
    participant?.pushName,
    participant?.displayName,
  ];
  if (identity.lidJid && identity.pnJid) {
    rememberLidPnMapping(identity.lidJid, identity.pnJid);
  }
  const aliases = [identity.pnJid, identity.lidJid, identity.jid].filter(Boolean);
  for (const jid of aliases) {
    rememberMentionIdentity(jid, ...names);
  }
  if (chatJid) rememberScopedMentionAliases(chatJid, identity.jid, aliases, names);
  return identity;
}

function rememberGroupOwnerIdentity(metadata) {
  return rememberResolvedIdentity(groupOwnerIdentity(metadata));
}

function sameGroupParticipant(participant, jid) {
  return participantIdentityValues(participant).some((value) => sameWhatsappUser(value, jid));
}

function contactDisplayName(contact) {
  return [
    contact?.name,
    contact?.notify,
    contact?.username,
    contact?.verifiedName,
    contact?.pushName,
    contact?.displayName,
  ]
    .map((name) => String(name || "").trim())
    .find(Boolean) || "";
}

function mentionDisplayName(jid) {
  const value = String(jid || "");
  if (!value) return "";
  if ((selfJid && sameWhatsappUser(value, selfJid)) || (selfLid && sameWhatsappUser(value, selfLid))) {
    return String(socket?.user?.name || "").trim();
  }
  const contact = knownContacts.get(value);
  const aliases = [value, contact?.id, contact?.jid, contact?.lid, contact?.phoneNumber].filter(Boolean);
  for (const alias of aliases) {
    const remembered = mentionDisplayNames.get(String(alias));
    if (remembered) return remembered;
  }
  return contactDisplayName(contact);
}

function rememberContact(contact) {
  const identity = rememberResolvedIdentity(
    groupParticipantIdentity(contact),
    contact?.name,
    contact?.notify,
    contact?.username,
    contact?.verifiedName,
    contact?.pushName,
    contact?.displayName,
  );
  updateContact({ ...contact, id: contact?.id || identity?.jid });
}

async function rememberGroupParticipants(
  chatJid,
  expectedGeneration = socketGeneration,
  metadataSocket = socket,
) {
  if (
    expectedGeneration !== socketGeneration
    || metadataSocket !== socket
    || !metadataSocket?.groupMetadata
    || !String(chatJid || "").endsWith("@g.us")
  ) return null;
  try {
    const metadata = await metadataSocket.groupMetadata(chatJid);
    if (expectedGeneration !== socketGeneration || metadataSocket !== socket) return null;
    for (const participant of metadata?.participants || []) {
      const jid = participant?.id || participant?.jid;
      rememberMentionIdentity(
        jid,
        participant?.name,
        participant?.notify,
        participant?.verifiedName,
        participant?.pushName,
        participant?.displayName,
      );
      if (jid && isLidJid(jid)) {
        const resolved = resolveLidToPn(jid);
        if (resolved) rememberLidPnMapping(jid, resolved);
      }
    }
    return metadata;
  } catch (error) {
    log.debug({ error, chatJid }, "failed to refresh group mention directory");
    return null;
  }
}

function mentionTokensFromText(text) {
  const tokens = [];
  const regex = /@([^\s@,，。:：;；)）(（]+)/g;
  let match;
  while ((match = regex.exec(String(text || "")))) tokens.push(match[1]);
  return tokens;
}

function resolveMentionTokens(tokens, chatJid = null) {
  const mentions = [];
  const scoped = mentionDirectoriesByChat.get(String(chatJid || ""));
  for (const token of tokens || []) {
    const key = mentionKey(token);
    if (!key) continue;
    const raw = String(token || "").trim();
    const isDirectIdentity = raw.includes("@") || /^\+?\d+$/.test(raw);
    let jid = scoped?.get(key) || (!chatJid || isDirectIdentity ? mentionDirectory.get(key) : null);
    if (!jid && isDirectIdentity) {
      const digits = raw.replace(/\D/g, "");
      if (digits) jid = scoped?.get(mentionKey(digits)) || mentionDirectory.get(mentionKey(digits)) || `${digits}@s.whatsapp.net`;
    }
    if (jid && !mentions.includes(jid)) mentions.push(jid);
  }
  return mentions;
}

function resolveExplicitMentions(values, chatJid = null) {
  const scoped = mentionDirectoriesByChat.get(String(chatJid || ""));
  return resolveExplicitIdentityMentions(values, {
    chatJid,
    scopedDirectory: scoped,
    globalDirectory: mentionDirectory,
  });
}

function sameWhatsappUser(left, right) {
  return normalizeJid(left) === normalizeJid(right);
}

function rememberIncomingMessage(key) {
  if (!key) return false;
  if (seenIncomingMessages.has(key)) return true;
  seenIncomingMessages.set(key, Date.now());
  while (seenIncomingMessages.size > maxSeenIncomingMessages) {
    seenIncomingMessages.delete(seenIncomingMessages.keys().next().value);
  }
  return false;
}

function mentionedJidsForAstrBot(message) {
  return mentionedJidsFromMessage(message).map((jid) => {
    if (selfJid && sameWhatsappUser(jid, selfJid)) return selfJid;
    if (selfJid && selfLid && sameWhatsappUser(jid, selfLid)) return selfJid;
    if (isLidJid(jid)) return resolveLidToPn(jid) || jid;
    return jid;
  });
}

function mentionedNamesForAstrBot(message) {
  const names = {};
  for (const jid of mentionedJidsForAstrBot(message)) {
    const name = mentionDisplayName(jid);
    if (name) names[jid] = name;
  }
  return names;
}

function normalizePhone(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text === "*") return "*";
  return phoneFromIdentity(text) || text;
}

function jidToPhone(jid) {
  return phoneFromIdentity(jid) || normalizeJid(jid);
}

function allowedByList(value, allowList) {
  const normalized = normalizePhone(value);
  const raw = String(value || "").trim();
  const normalizedJid = normalizeJid(raw);
  const normalizedList = (Array.isArray(allowList) ? allowList : []).map((item) => ({
    raw: String(item || "").trim(),
    phone: normalizePhone(item),
    jid: normalizeJid(item),
  }));
  return normalizedList.some((item) => (
    item.raw === "*" || item.phone === "*" ||
    item.phone === normalized || item.raw === raw || item.jid === normalizedJid
  ));
}

function allowedMessageResult(chatJid, senderJid, item) {
  const isGroup = chatJid.endsWith("@g.us");
  const { phones, jids } = messageCandidates(item, chatJid, senderJid);
  const senderPhone = phones.find((v) => v?.startsWith?.("+")) || "";
  const allowedByCandidates = (allowList) =>
    phones.some((phone) => allowedByList(phone, allowList)) ||
    jids.some((jid) => allowedByList(jid, allowList));
  if (!isGroup) {
    if (runtimeConfig.dmPolicy === "disabled") return { allowed: false, reason: "dm_disabled", senderPhone };
    if (runtimeConfig.dmPolicy === "open") return { allowed: true, reason: "dm_open", senderPhone };
    return {
      allowed: allowedByCandidates(runtimeConfig.allowFrom),
      reason: "dm_allowlist",
      senderPhone,
    };
  }

  if (runtimeConfig.groupPolicy === "disabled") return { allowed: false, reason: "group_disabled", senderPhone };
  const groups = runtimeConfig.groups || [];
  if (groups.length > 0 && !groups.includes("*") && !groups.includes(chatJid)) {
    return { allowed: false, reason: "group_not_allowed", senderPhone };
  }
  if (runtimeConfig.groupPolicy === "open") return { allowed: true, reason: "group_open", senderPhone };
  const groupAllowFrom = (runtimeConfig.groupAllowFrom || []).length
    ? runtimeConfig.groupAllowFrom
    : runtimeConfig.allowFrom;
  return {
    allowed: allowedByCandidates(groupAllowFrom),
    reason: "group_allowlist",
    senderPhone,
  };
}

async function refreshAndRetryAllowedMessage(chatJid, senderJid, item) {
  await refreshAllowlistLidMappings("inbound_unresolved_lid");
  const cachedPn = resolveLidToPn(senderJid) || resolveLidToPn(chatJid);
  if (cachedPn && isLidJid(senderJid)) {
    rememberLidPnMapping(senderJid, cachedPn);
  }
  return allowedMessageResult(chatJid, senderJid, item);
}

function textFromMessage(message) {
  message = normalizeMessagePayload(message);
  if (!message) return "";
  if (message.conversation) return message.conversation;
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
  if (message.imageMessage?.caption) return message.imageMessage.caption;
  if (message.videoMessage?.caption) return message.videoMessage.caption;
  if (message.documentMessage?.caption) return message.documentMessage.caption;
  if (message.buttonsResponseMessage?.selectedDisplayText) return message.buttonsResponseMessage.selectedDisplayText;
  if (message.listResponseMessage?.title) return message.listResponseMessage.title;
  if (message.locationMessage) {
    const lat = message.locationMessage.degreesLatitude || 0;
    const lon = message.locationMessage.degreesLongitude || 0;
    const name = message.locationMessage.name || "";
    const addr = message.locationMessage.address || "";
    const parts = [name, addr, `${lat},${lon}`].filter(Boolean);
    return parts.join(" — ") || `${lat},${lon}`;
  }
  if (message.liveLocationMessage) {
    const lat = message.liveLocationMessage.degreesLatitude || 0;
    const lon = message.liveLocationMessage.degreesLongitude || 0;
    return `📍 ${lat},${lon}`;
  }
  if (message.contactMessage) {
    const displayName = message.contactMessage.displayName || "";
    const vcard = message.contactMessage.vcard || "";
    const phones = [...vcard.matchAll(/TEL[^:]*:([^\r\n]+)/gi)].map((m) => m[1].trim()).filter(Boolean);
    const label = displayName || phones[0] || "Contact";
    return phones.length > 0 ? `${label}: ${phones.join(", ")}` : label;
  }
  if (message.contactsArrayMessage) {
    const contacts = message.contactsArrayMessage.contacts || [];
    const names = contacts.map((c) => c.displayName || "Contact").filter(Boolean);
    return names.join(", ") || "Contacts";
  }
  if (message.reactionMessage) return "";
  const pollMsg = pollFromMessage(message);
  if (pollMsg) {
    const options = (pollMsg.options || []).map((item) => item.optionName).filter(Boolean);
    const name = pollMsg.name || "Poll";
    return options.length ? `${name}: ${options.join(", ")}` : name;
  }
  if (message.productMessage) {
    const title = message.productMessage.product?.title || "";
    const price = message.productMessage.product?.currencyCode && message.productMessage.product?.priceAmount1000
      ? `${message.productMessage.product.currencyCode} ${Number(message.productMessage.product.priceAmount1000) / 1000}`
      : "";
    return [title, price].filter(Boolean).join(" — ") || "Product";
  }
  const event = eventDetailsFromMessage(message);
  if (event) {
    const label = event.name || "Event";
    const summary = event.description ? `${label}: ${event.description}` : label;
    return event.isCanceled ? `Cancelled event: ${summary}` : summary;
  }
  return "";
}

function extrasFromMessage(message) {
  message = normalizeMessagePayload(message);
  if (!message) return null;
  const extras = {};
  const locationMessage = message.locationMessage || message.liveLocationMessage;
  if (locationMessage) {
    extras.location = {
      latitude: locationMessage.degreesLatitude || 0,
      longitude: locationMessage.degreesLongitude || 0,
      name: locationMessage.name || "",
      address: locationMessage.address || "",
      live: Boolean(message.liveLocationMessage),
    };
  }
  if (message.contactMessage) {
    extras.contact = {
      displayName: message.contactMessage.displayName || "",
      vcard: message.contactMessage.vcard || "",
    };
  }
  if (message.reactionMessage) {
    extras.reaction = {
      key: message.reactionMessage.key,
      text: message.reactionMessage.text || "",
    };
  }
  if (message.buttonsResponseMessage) {
    extras.buttonResponse = {
      selectedButtonId: message.buttonsResponseMessage.selectedButtonId || "",
      selectedDisplayText: message.buttonsResponseMessage.selectedDisplayText || "",
    };
  }
  if (message.listResponseMessage) {
    extras.listResponse = {
      title: message.listResponseMessage.title || "",
      description: message.listResponseMessage.description || "",
      singleSelectReply: message.listResponseMessage.singleSelectReply?.selectedRowId || "",
    };
  }
  const pollMsg = pollFromMessage(message);
  if (pollMsg) {
    extras.poll = {
      name: pollMsg.name || "",
      selectableCount: pollMsg.selectableOptionsCount || 0,
      options: (pollMsg.options || []).map((item) => item.optionName || "").filter(Boolean),
    };
  }
  const event = eventDetailsFromMessage(message);
  if (event) extras.event = event;
  if (Object.keys(extras).length === 0) return null;
  return extras;
}

function pollFromMessage(message) {
  if (!message) return null;
  return message.pollCreationMessage || message.pollCreationMessageV2 || message.pollCreationMessageV3 || null;
}

function buildQuotedContext(body) {
  if (!body?.quotedMessageId) return null;
  const cached = findChatMessage(messageCache, body.to, body.quotedMessageId);
  return {
    stanzaId: body.quotedMessageId,
    participant: cached?.key?.participant || body.quotedParticipant || undefined,
  };
}

function buildButtonsContent(body) {
  const buttons = (body.buttons || []).slice(0, 3).map((btn, index) => ({
    buttonId: String(btn.id || `btn_${index}`),
    buttonText: { displayText: String(btn.text || btn.label || `Option ${index + 1}`).slice(0, 20) },
    type: proto.Message.ButtonsMessage.Button.Type.RESPONSE,
  }));
  const buttonsMessage = {
    contentText: String(body.text || body.body || "").slice(0, 1024),
    footerText: String(body.footer || "").slice(0, 60),
    headerType: proto.Message.ButtonsMessage.HeaderType.EMPTY,
    buttons,
  };
  const contextInfo = buildQuotedContext(body);
  if (contextInfo) buttonsMessage.contextInfo = contextInfo;
  return { buttonsMessage };
}

function buildListContent(body) {
  const sections = (body.sections || []).slice(0, 10).map((section) => ({
    title: String(section.title || "").slice(0, 24),
    rows: (section.rows || []).slice(0, 10).map((row, index) => ({
      title: String(row.title || row.text || `Item ${index + 1}`).slice(0, 24),
      description: String(row.description || "").slice(0, 72),
      rowId: String(row.id || row.rowId || `row_${index}`).slice(0, 200),
    })),
  }));
  const listMessage = {
    title: String(body.title || "").slice(0, 60),
    description: String(body.description || body.text || "").slice(0, 72),
    buttonText: String(body.buttonText || body.button_text || "選項").slice(0, 20),
    footerText: String(body.footer || "").slice(0, 60),
    listType: proto.Message.ListMessage.ListType.SINGLE_SELECT,
    sections,
  };
  const contextInfo = buildQuotedContext(body);
  if (contextInfo) listMessage.contextInfo = contextInfo;
  return { listMessage };
}

async function relayProtoContent(jid, contentObj, body) {
  const options = quotedKey(body) || {};
  const ephemeral = getEphemeralExpiration(jid);
  if (ephemeral) options.ephemeralExpiration = ephemeral;
  const message = proto.Message.fromObject(contentObj);
  const waMsg = generateWAMessageFromContent(jid, message, options);
  await socket.relayMessage(jid, waMsg.message, { messageId: waMsg.key.id });
  cacheChatMessage(messageCache, waMsg, maxMessageCacheSize);
  return { ok: true, id: waMsg.key.id, key: waMsg.key };
}

function contextInfoFromMessage(message) {
  return contextInfoFromMessagePayload(message);
}

function quotedInfoFromContext(contextInfo) {
  if (!contextInfo) return null;
  const stanzaId = contextInfo.stanzaId || null;
  const participant = contextInfo.participant || null;
  const quotedMessage = normalizeMessagePayload(contextInfo.quotedMessage) || null;
  if (!stanzaId && !quotedMessage) return null;
  return { stanzaId, participant, quotedMessage };
}

function mentionedJidsFromMessage(message) {
  const mentioned = contextInfoFromMessage(message)?.mentionedJid || [];
  return Array.isArray(mentioned) ? mentioned.filter(Boolean) : [];
}

function mentionAllFromMessage(message) {
  return Number(contextInfoFromMessage(message)?.nonJidMentions || 0) === 1;
}

function mediaPayloadFromInput(message) {
  return normalizeMessageInput(message);
}

function mediaKind(message) {
  message = mediaPayloadFromInput(message);
  if (!message) return null;
  if (message.imageMessage) return "image";
  if (message.videoMessage) return "video";
  if (message.audioMessage) return "audio";
  if (message.documentMessage) return "document";
  if (message.stickerMessage) return "sticker";
  return null;
}

function mediaFileName(message, kind) {
  message = mediaPayloadFromInput(message);
  return message?.documentMessage?.fileName || message?.imageMessage?.fileName || message?.videoMessage?.fileName || `${kind || "media"}`;
}

function mediaMimeType(message, kind) {
  message = mediaPayloadFromInput(message);
  return (
    message?.imageMessage?.mimetype ||
    message?.videoMessage?.mimetype ||
    message?.audioMessage?.mimetype ||
    message?.documentMessage?.mimetype ||
    message?.stickerMessage?.mimetype ||
    mimeTypeForExt(`.${extensionForKind(kind)}`)
  );
}

function quotedKey(body) {
  if (!body?.quotedMessageId) return undefined;
  const quoted = findChatMessage(messageCache, body.to, body.quotedMessageId);
  if (!quoted?.message) return undefined;
  return { quoted };
}

async function saveInboundMedia(message, kind, id, mediaSocket = socket) {
  const maxBytes = inboundMaxBytes(kind);
  const expectedBytes = inboundMediaSize(message, kind);
  if (expectedBytes && expectedBytes > maxBytes) {
    throw new Error(`${kind} exceeds inbound limit ${Math.floor(maxBytes / 1024 / 1024)}MB`);
  }
  const originalName = mediaFileName(message, kind);
  const extension = extensionForInboundMedia(originalName, message, kind);
  const fileName = `whatsapp_${kind}_${Date.now()}_${randomUUID().slice(0, 8)}.${extension}`;
  const filePath = path.join(tempDir, fileName);
  await mkdir(tempDir, { recursive: true });
  try {
    // Baileys retries expired (404/410) media URLs only when supplied with the
    // socket re-upload callback. Capture and bind the originating socket so a
    // reconnect cannot redirect an old message's retry through a new account.
    // 串流寫入磁碟，避免大型媒體佔用記憶體
    const stream = await downloadMediaMessage(
      message,
      "stream",
      {},
      inboundMediaDownloadContext(mediaSocket, log),
    );
    if (!stream || typeof stream.pipe !== "function") {
      throw new Error("downloadMediaMessage did not return a readable stream");
    }
    const writeStream = createWriteStream(filePath);
    let writtenBytes = 0;
    await new Promise((resolve, reject) => {
      writeStream.on("error", (err) => {
        stream.destroy();
        reject(err);
      });
      stream.on("error", (err) => {
        writeStream.destroy();
        reject(err);
      });
      stream.on("data", (chunk) => {
        writtenBytes += chunk.length;
        if (writtenBytes > maxBytes) {
          writeStream.destroy();
          stream.destroy();
          reject(new Error(`${kind} exceeds inbound limit ${Math.floor(maxBytes / 1024 / 1024)}MB`));
        }
      });
      stream.pipe(writeStream).on("finish", () => {
        writeStream.close();
        resolve();
      });
    });
    return { path: filePath, size: writtenBytes, fileName: originalName, mimetype: mediaMimeType(message, kind) };
  } catch (error) {
    await removePartialInboundMedia(filePath, log);
    throw error;
  }
}

function extensionForInboundMedia(fileName, message, kind) {
  const ext = path.extname(String(fileName || "")).replace(/^\./, "").toLowerCase();
  if (ext) return safeExtension(ext);
  const mimeExt = extensionForMime(mediaMimeType(message, kind));
  return mimeExt || extensionForKind(kind);
}

function safeExtension(ext) {
  const normalized = String(ext || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
  return normalized || "bin";
}

function extensionForMime(mimetype) {
  const types = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/3gpp": "3gp",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/wav": "wav",
    "audio/flac": "flac",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "application/json": "json",
    "application/zip": "zip",
  };
  return types[String(mimetype || "").split(";", 1)[0].toLowerCase()] || "";
}

function inboundMediaSize(message, kind) {
  message = mediaPayloadFromInput(message);
  const value = (
    message?.imageMessage?.fileLength ||
    message?.videoMessage?.fileLength ||
    message?.audioMessage?.fileLength ||
    message?.documentMessage?.fileLength ||
    message?.stickerMessage?.fileLength ||
    0
  );
  return Number(value || 0);
}

function inboundMaxBytes(kind) {
  if (kind === "audio") return Number(runtimeConfig.audioMaxMb || 16) * 1024 * 1024;
  if (kind === "document") return Number(runtimeConfig.documentMaxMb || 2048) * 1024 * 1024;
  return Number(runtimeConfig.mediaMessageMaxMb || runtimeConfig.mediaMaxMb || 100) * 1024 * 1024;
}

function extensionForKind(kind) {
  if (kind === "image") return "jpg";
  if (kind === "video") return "mp4";
  if (kind === "audio") return "ogg";
  if (kind === "sticker") return "webp";
  return "bin";
}

function isAlbumCandidate(item) {
  if (!item?.message) return false;
  if (mediaKind(item.message) !== "image") return false;
  if (textFromMessage(item.message)) return false;
  if (extrasFromMessage(item.message)) return false;
  const contextInfo = contextInfoFromMessage(item.message);
  if (contextInfo?.stanzaId || contextInfo?.quotedMessage) return false;
  return true;
}

function getEphemeralExpiration(jid) {
  if (!runtimeConfig.applyEphemeral) return undefined;
  return chatEphemeral.get(String(jid || "")) || undefined;
}

function clearAlbumBuffers() {
  for (const [, buffer] of albumBuffers) {
    if (buffer.timer) clearTimeout(buffer.timer);
  }
  albumBuffers.clear();
}

function clearAccountRuntimeCaches() {
  messageCache.clear();
  seenIncomingMessages.clear();
  knownContacts.clear();
  mentionDirectory.clear();
  mentionDirectoriesByChat.clear();
  mentionDisplayNames.clear();
  chatEphemeral.clear();
  if (typeof groupMetadataCache !== "undefined") groupMetadataCache.clear();
}

function scheduleAlbumItem(item, expectedGeneration = socketGeneration, eventSocket = socket) {
  const chatJid = item.key.remoteJid;
  const senderJid = item.key.participant || item.key.participantAlt || (item.key.fromMe ? selfJid : null) || chatJid;
  const bufferKey = `${expectedGeneration}:${chatJid}:${senderJid}`;
  const debounceMs = Number(runtimeConfig.mediaAlbumDebounceMs || 0);
  let buffer = albumBuffers.get(bufferKey);
  if (!buffer) {
    buffer = { items: [], timer: null };
    albumBuffers.set(bufferKey, buffer);
  }
  buffer.items.push(item);
  if (buffer.timer) clearTimeout(buffer.timer);
  buffer.timer = setTimeout(() => {
    const pending = albumBuffers.get(bufferKey);
    albumBuffers.delete(bufferKey);
    if (!pending?.items?.length) return;
    if (expectedGeneration !== socketGeneration || eventSocket !== socket) return;
    const items = pending.items;
    handleIncomingMessage(
      items[0],
      { albumItems: items },
      expectedGeneration,
      eventSocket,
    ).catch((error) =>
      log.warn({ error, count: items.length }, "album message handling failed"),
    );
  }, debounceMs);
}

async function routeIncomingMessage(
  item,
  expectedGeneration = socketGeneration,
  eventSocket = socket,
) {
  if (expectedGeneration !== socketGeneration || eventSocket !== socket) return;
  item = normalizeIncomingItem(item);
  const chatJid = item.key.remoteJid;
  if (!chatJid || chatJid.endsWith("@status") || chatJid.endsWith("@broadcast")) return;
  const senderJid = item.key.participant || item.key.participantAlt || (item.key.fromMe ? selfJid : null) || chatJid;
  const dedupKey = `${chatJid}:${senderJid}:${item.key.id || ""}`;
  if (rememberIncomingMessage(dedupKey)) {
    log.debug({ chatJid, senderJid, messageId: item.key.id }, "ignored duplicate inbound WhatsApp message");
    return;
  }
  const debounceMs = Number(runtimeConfig.mediaAlbumDebounceMs || 0);
  if (debounceMs > 0 && isAlbumCandidate(item)) {
    scheduleAlbumItem(item, expectedGeneration, eventSocket);
    return;
  }
  await handleIncomingMessage(item, {}, expectedGeneration, eventSocket);
}

async function handleIncomingMessage(
  item,
  options = {},
  expectedGeneration = socketGeneration,
  eventSocket = socket,
) {
  const isStaleSocketEvent = () => (
    expectedGeneration !== socketGeneration || eventSocket !== socket
  );
  if (isStaleSocketEvent()) return;
  const albumItems = (options.albumItems?.length ? options.albumItems : [item])
    .map(normalizeIncomingItem);
  const primary = albumItems[0];
  const chatJid = primary.key.remoteJid;
  if (!chatJid || chatJid.endsWith("@status") || chatJid.endsWith("@broadcast")) return;
  const fromMe = Boolean(primary.key.fromMe);
  const isGroup = chatJid.endsWith("@g.us");
  const senderJid = primary.key.participant || primary.key.participantAlt || (fromMe ? selfJid : null) || chatJid;
  const senderIdentity = senderIdentityFromKey(primary.key, {
    isGroup,
    fromMe,
    senderJid,
    chatJid,
    selfJid,
    selfLid,
  });
  rememberResolvedIdentity({ jid: senderJid, ...senderIdentity }, primary.pushName);
  const senderPn = senderIdentity.pnJid;
  const directSessionIdentity = isGroup
    ? null
    : senderIdentityFromKey(primary.key, {
      isGroup: false,
      fromMe: false,
      senderJid: chatJid,
      chatJid,
    });
  if (directSessionIdentity) {
    rememberResolvedIdentity({ jid: chatJid, ...directSessionIdentity }, primary.pushName);
  }
  const canonicalSessionJid = isGroup
    ? chatJid
    : (directSessionIdentity?.lidJid || directSessionIdentity?.pnJid || chatJid);
  rememberGroupParticipants(chatJid, expectedGeneration, eventSocket).catch(() => {});
  if (primary.message?.protocolMessage) {
    log.debug({ chatJid, messageId: primary.key.id, protocolType: primary.message.protocolMessage.type }, "ignored protocol message");
    return;
  }
  if (!configured) {
    log.warn({ chatJid, senderJid, messageId: primary.key.id }, "Gateway not yet configured; passing message through without allowlist check");
  }
  let allowedResult = configured ? allowedMessageResult(chatJid, senderJid, primary) : { allowed: true, reason: "not_yet_configured", senderPhone: "" };
  if (configured && !allowedResult.allowed && !allowedResult.senderPhone && isLidJid(senderJid)) {
    const retry = await refreshAndRetryAllowedMessage(chatJid, senderJid, primary);
    if (isStaleSocketEvent()) return;
    if (retry.allowed || retry.senderPhone) allowedResult = retry;
  }
  if (!isGroup) {
    log.info(
      {
        chatJid,
        senderJid,
        senderPhone: allowedResult.senderPhone,
        dmPolicy: runtimeConfig.dmPolicy,
        allowFrom: runtimeConfig.allowFrom,
        fromMe,
        messageId: primary.key.id,
        allowed: allowedResult.allowed,
      },
      "DM allowlist check",
    );
  }
  if (fromMe) {
    const adapterSent = Boolean(findChatMessage(messageCache, chatJid, primary.key.id));
    if (runtimeConfig.ignoreSelfMessages || adapterSent) {
      log.debug(
        { chatJid, senderJid, messageId: primary.key.id, adapterSent },
        "ignored inbound message from self",
      );
      return;
    }
  }
  if (!allowedResult.allowed) {
    log.info(
      {
        chatJid,
        senderJid,
        senderPhone: allowedResult.senderPhone,
        reason: allowedResult.reason,
        dmPolicy: runtimeConfig.dmPolicy,
        groupPolicy: runtimeConfig.groupPolicy,
        allowFrom: runtimeConfig.allowFrom,
        allowFromCount: (runtimeConfig.allowFrom || []).length,
      },
      "rejected inbound WhatsApp message",
    );
    if (!isGroup && allowedResult.reason === "dm_allowlist") {
      if (!allowedResult.senderPhone && isLidJid(senderJid)) {
        const resolved = await waitForLidPnMapping(senderJid, 3000);
        if (isStaleSocketEvent()) return;
        if (resolved) {
          rememberLidPnMapping(senderJid, resolved);
          const retry = allowedMessageResult(chatJid, senderJid, primary);
          if (retry.allowed) {
            allowedResult = retry;
          } else {
            broadcast({
              type: "rejected",
              chatJid, senderJid,
              senderPn: primary.key.senderPn || null,
              senderName: primary.pushName || senderJid,
              senderPhone: retry.senderPhone,
              reason: retry.reason, fromMe,
              messageId: primary.key.id,
              text: textFromMessage(primary.message) || "",
              timestamp: Number(primary.messageTimestamp || Date.now() / 1000),
            });
            return;
          }
        } else {
          log.warn({ senderJid, chatJid, messageId: primary.key.id }, "lid→PN mapping unresolved within timeout, rejecting allowlist message");
          broadcast({
            type: "rejected",
            chatJid, senderJid,
            senderPn: primary.key.senderPn || null,
            senderName: primary.pushName || senderJid,
            senderPhone: "",
            reason: "lid_unresolved", fromMe,
            messageId: primary.key.id,
            text: textFromMessage(primary.message) || "",
            timestamp: Number(primary.messageTimestamp || Date.now() / 1000),
          });
          return;
        }
      } else {
        broadcast({
          type: "rejected",
          chatJid, senderJid,
          senderPn,
          senderName: primary.pushName || senderJid,
          senderPhone: allowedResult.senderPhone,
          reason: allowedResult.reason, fromMe,
          messageId: primary.key.id,
          text: textFromMessage(primary.message) || "",
          timestamp: Number(primary.messageTimestamp || Date.now() / 1000),
        });
        return;
      }
    } else {
      return;
    }
  }
  const contextInfo = contextInfoFromMessage(primary.message);
  const quotedInfo = quotedInfoFromContext(contextInfo);
  log.info(
    {
      chatJid,
      senderJid,
      senderPhone: allowedResult.senderPhone,
      reason: allowedResult.reason,
      messageId: primary.key.id,
      albumCount: albumItems.length,
      hasText: Boolean(textFromMessage(primary.message)),
      mentionCount: mentionedJidsFromMessage(primary.message).length,
      mentions: mentionedJidsForAstrBot(primary.message),
      mentionNames: mentionedNamesForAstrBot(primary.message),
      mediaKind: mediaKind(primary.message),
      quotedStanzaId: quotedInfo?.stanzaId || null,
      quotedParticipant: quotedInfo?.participant || null,
    },
    "accepted inbound WhatsApp message",
  );
  for (const albumItem of albumItems) {
    cacheChatMessage(messageCache, albumItem, maxMessageCacheSize);
  }

  const media = [];
  for (const albumItem of albumItems) {
    const kind = mediaKind(albumItem.message);
    if (!kind) continue;
    try {
      media.push({
        type: kind,
        ...(await saveInboundMedia(albumItem, kind, albumItem.key.id, eventSocket)),
      });
      if (isStaleSocketEvent()) return;
    } catch (error) {
      log.warn(
        {
          error: String(error?.message || error),
          stack: error?.stack,
          messageId: albumItem.key.id,
        },
        "failed to save inbound media",
      );
      media.push({ type: kind, error: String(error?.message || error) });
    }
  }
  const kind = media[0]?.type || mediaKind(primary.message);

  let quoted = null;
  if (quotedInfo) {
    const cachedQuoted = findChatMessage(messageCache, chatJid, quotedInfo.stanzaId);
    const cachedFromMe = Boolean(cachedQuoted?.key?.fromMe);
    const quotedParticipant = cachedFromMe
      ? (selfJid || selfLid || quotedInfo.participant || "")
      : (quotedInfo.participant || cachedQuoted?.key?.participant || cachedQuoted?.key?.remoteJid || "");
    const quotedIdentity = senderIdentityFromKey(cachedQuoted?.key, {
      isGroup,
      fromMe: cachedFromMe,
      senderJid: quotedParticipant,
      chatJid,
      selfJid,
      selfLid,
    });
    rememberResolvedIdentity({ jid: quotedParticipant, ...quotedIdentity }, cachedQuoted?.pushName);
    const quotedParticipantPn = quotedIdentity.pnJid || resolveLidToPn(quotedParticipant) || "";
    const quotedMessage = normalizeMessagePayload(quotedInfo.quotedMessage || cachedQuoted?.message) || null;
    const quotedKind = quotedMessage ? mediaKind(quotedMessage) : null;
    const quotedText = quotedMessage ? textFromMessage(quotedMessage) : "";
    const quotedMedia = [];
    if (quotedMessage && quotedKind) {
      try {
        const quotedItem = {
          key: { id: quotedInfo.stanzaId, remoteJid: chatJid, fromMe: cachedFromMe || sameWhatsappUser(quotedParticipant, selfJid || "") },
          message: quotedMessage,
        };
        quotedMedia.push({
          type: quotedKind,
          ...(await saveInboundMedia(
            quotedItem,
            quotedKind,
            `quoted-${quotedInfo.stanzaId || "unknown"}`,
            eventSocket,
          )),
        });
        if (isStaleSocketEvent()) return;
      } catch (error) {
        log.warn(
          {
            error: String(error?.message || error),
            stack: error?.stack,
            quotedStanzaId: quotedInfo.stanzaId,
          },
          "failed to save quoted inbound media",
        );
        quotedMedia.push({ type: quotedKind, error: String(error?.message || error) });
      }
    }
    quoted = {
      stanzaId: quotedInfo.stanzaId,
      participant: quotedParticipant,
      participantPn: quotedParticipantPn,
      participantName: mentionDisplayName(quotedParticipant) || cachedQuoted?.pushName || quotedParticipant,
      timestamp: Number(cachedQuoted?.messageTimestamp || 0),
      text: quotedText || (quotedKind ? `<media:${quotedKind}>` : ""),
      mentionedJids: quotedMessage ? mentionedJidsForAstrBot(quotedMessage) : [],
      mentionedNames: quotedMessage ? mentionedNamesForAstrBot(quotedMessage) : {},
      mentionAll: quotedMessage ? mentionAllFromMessage(quotedMessage) : false,
      media: quotedMedia,
    };
  }

  if (isStaleSocketEvent()) return;
  if (runtimeConfig.sendReadReceipts && eventSocket) {
    const isSelf = selfJid && senderJid && sameWhatsappUser(senderJid, selfJid);
    if (!runtimeConfig.ignoreSelfMessages || !isSelf) {
      eventSocket.readMessages(albumItems.map((albumItem) => albumItem.key)).catch((error) =>
        log.debug({ error }, "read receipt failed"),
      );
    }
  }

  const text =
    textFromMessage(primary.message) ||
    (media.length > 1 ? `<media:${kind || "image"}> x${media.length}` : kind ? `<media:${kind}>` : "");
  const extras = extrasFromMessage(primary.message);
  if (!text && !media.length && !extras) {
    log.debug({ chatJid, messageId: primary.key.id }, "ignored empty system/protocol message (no content)");
    return;
  }
  broadcast({
    type: "message",
    messageId: primary.key.id,
    albumMessageIds: albumItems.length > 1 ? albumItems.map((albumItem) => albumItem.key.id) : undefined,
    chatJid,
    senderJid,
    senderPn,
    senderLid: senderIdentity.lidJid,
    canonicalSessionJid,
    senderPhone: allowedResult.senderPhone || resolvePhone(senderJid) || "",
    senderName: primary.pushName || senderJid,
    fromMe,
    selfJid,
    selfLid,
    text,
    mentionedJids: mentionedJidsForAstrBot(primary.message),
    mentionedNames: mentionedNamesForAstrBot(primary.message),
    mentionAll: mentionAllFromMessage(primary.message),
    media,
    quoted,
    extras,
    albumCount: albumItems.length,
    timestamp: Number(primary.messageTimestamp || Date.now() / 1000),
    raw: { key: primary.key },
  });
}

function enqueueSocketTransition(label, action) {
  const run = socketTransition.then(action, action);
  socketTransition = run.catch((error) => {
    connectionStatus = "error";
    lastError = String(error?.message || error);
    broadcast({ type: "status", status: connectionStatus, ready: false, lastError });
    log.error({ error, label }, "socket transition failed");
  });
  return run;
}

async function initializeAuthSession() {
  await mkdir(authDir, { recursive: true });
  try {
    const pointer = JSON.parse(await readFile(activeSessionFile, "utf-8"));
    if (pointer?.sessionId) {
      currentSessionId = String(pointer.sessionId);
      currentAuthDir = sessionDirectory(authDir, currentSessionId);
      await mkdir(currentAuthDir, { recursive: true });
      return;
    }
  } catch {
    // Existing installations keep using the legacy root until the first reset.
  }
  currentSessionId = "legacy";
  currentAuthDir = authDir;
}

async function activateFreshAuthSession(reason) {
  const sessionId = `${Date.now()}-${randomUUID()}`;
  const nextDir = sessionDirectory(authDir, sessionId);
  await mkdir(nextDir, { recursive: true });
  const tempPointer = `${activeSessionFile}.${process.pid}.${randomUUID()}.tmp`;
  await writeFile(tempPointer, JSON.stringify({
    sessionId,
    createdAt: new Date().toISOString(),
    reason,
  }), "utf-8");
  await rename(tempPointer, activeSessionFile);
  currentSessionId = sessionId;
  currentAuthDir = nextDir;
  return sessionId;
}

function invalidateCurrentSocket() {
  const oldSocket = socket;
  ++socketGeneration;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (oldSocket?.ev?.removeAllListeners) {
    try {
      oldSocket.ev.removeAllListeners();
    } catch (error) {
      log.debug({ error }, "failed to remove old socket listeners");
    }
  }
  if (oldSocket?.end) {
    try {
      oldSocket.end(undefined);
    } catch (error) {
      log.debug({ error }, "failed to close old socket");
    }
  }
  socket = null;
  ready = false;
  stopPresenceTimer();
  clearAlbumBuffers();
}

async function resetSocketSession(reason = "manual_reset") {
  if (String(reason).startsWith("manual_")) {
    consecutiveFreshAuthFailures = 0;
    transientReconnectAttempt = 0;
  }
  connectionStatus = "resetting";
  broadcast({ type: "status", status: connectionStatus, ready: false, reason });
  invalidateCurrentSocket();
  clearAccountRuntimeCaches();
  selfJid = null;
  selfLid = null;
  latestQr = null;
  latestQrDataUrl = null;
  lastPresenceAt = null;
  lastError = null;
  const sessionId = await activateFreshAuthSession(reason);
  resetSequence += 1;
  log.info({ reason, sessionId, resetSequence }, "activated fresh isolated auth session");
  await startSocket({ fresh: true });
  return { ok: true, status: connectionStatus, sessionId, resetSequence };
}

function requestSocketStart(opts = {}) {
  return enqueueSocketTransition("start", () => startSocket(opts));
}

function requestSessionReset(reason) {
  return enqueueSocketTransition("reset", () => resetSocketSession(reason));
}

function requestLogoutAndReset(reason) {
  return enqueueSocketTransition("logout_reset", async () => {
    // Make every event from the retiring socket stale before requesting remote logout.
    ++socketGeneration;
    if (ready && socket?.logout) {
      await Promise.race([
        socket.logout().catch((error) => log.debug({ error }, "socket logout failed")),
        new Promise((resolve) => setTimeout(resolve, 3000)),
      ]);
    }
    return resetSocketSession(reason);
  });
}

async function startSocket(opts = {}) {
  const generation = ++socketGeneration;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  const previousSocket = socket;
  if (previousSocket?.ev?.removeAllListeners) {
    try {
      previousSocket.ev.removeAllListeners();
    } catch (error) {
      log.debug({ error }, "failed to remove previous socket listeners");
    }
  }
  if (previousSocket?.end) {
    try {
      previousSocket.end(undefined);
    } catch (error) {
      log.debug({ error }, "failed to close previous socket");
    }
  }
  socket = null;
  ready = false;
  stopPresenceTimer();
  connectionStatus = "starting";
  await mkdir(currentAuthDir, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(currentAuthDir);
  const socketForGeneration = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger: log,
    markOnlineOnConnect: Boolean(runtimeConfig.markOnline),
    browser: Browsers.macOS("Chrome"),
    syncFullHistory: false,
  });
  socket = socketForGeneration;

  let credsSaveQueue = Promise.resolve();
  socketForGeneration.ev.on("creds.update", () => {
    if (generation !== socketGeneration) return;
    credsSaveQueue = credsSaveQueue
      .then(() => saveCreds())
      .catch((error) => {
        if (generation !== socketGeneration || socketForGeneration !== socket) return;
        lastError = `保存登录凭证失败: ${String(error?.message || error)}`;
        log.error({ error, generation, sessionId: currentSessionId }, "failed to persist auth credentials");
      });
  });
  socketForGeneration.ev.on("messaging-history.set", (history) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    for (const mapping of history?.lidPnMappings || []) {
      if (mapping?.lid && mapping?.pn) rememberLidPnMapping(mapping.lid, mapping.pn);
    }
    for (const contact of history?.contacts || []) rememberContact(contact);
    if (runtimeConfig.applyEphemeral) {
      for (const chat of history?.chats || []) {
        if (chat?.id && chat?.ephemeralExpiration) {
          chatEphemeral.set(chat.id, chat.ephemeralExpiration);
        }
      }
    }
  });
  socketForGeneration.ev.on("contacts.upsert", (contacts) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    for (const contact of contacts || []) {
      rememberContact(contact);
    }
  });
  socketForGeneration.ev.on("contacts.update", (contacts) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    for (const contact of contacts || []) rememberContact(contact);
  });
  socketForGeneration.ev.on("chats.upsert", (chats) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    if (!runtimeConfig.applyEphemeral) return;
    for (const chat of chats || []) {
      if (chat?.id && chat?.ephemeralExpiration) {
        chatEphemeral.set(chat.id, chat.ephemeralExpiration);
      }
    }
  });
  socketForGeneration.ev.on("chats.update", (chats) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    if (!runtimeConfig.applyEphemeral) {
      chatEphemeral.clear();
      return;
    }
    for (const chat of chats || []) {
      if (chat?.id) {
        if (chat.ephemeralExpiration !== undefined) {
          chatEphemeral.set(chat.id, chat.ephemeralExpiration);
        } else if (chat.ephemeralExpiration === 0 || chat.ephemeralExpiration === null) {
          chatEphemeral.delete(chat.id);
        }
      }
    }
  });
  // lid-mapping.update — Baileys 提供 LID→PN 映射，統一存入 contact store
  socketForGeneration.ev.on("lid-mapping.update", (mapping) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    if (mapping?.lid && mapping?.pn) rememberLidPnMapping(mapping.lid, mapping.pn);
    if (mapping?.lidJid && mapping?.pnJid) rememberLidPnMapping(mapping.lidJid, mapping.pnJid);
  });
  // chats.phoneNumberShare — Baileys 在收到 LID 格式訊息時提供 LID→PN 映射
  socketForGeneration.ev.on("chats.phoneNumberShare", ({ lid, jid }) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    if (lid && jid) {
      log.debug({ lid, pn: jid }, "phoneNumberShare: LID→PN mapping received");
      rememberLidPnMapping(lid, jid);
    }
  });
  socketForGeneration.ev.on("connection.update", (update) => {
    if (generation !== socketGeneration || socketForGeneration !== socket) return;
    const { connection, lastDisconnect, qr, isNewLogin } = update;
    if (qr) {
      connectionStatus = "qr_pending";
      latestQr = qr;
      QRCode.toDataURL(qr, { margin: 1, width: 320 })
        .then((dataUrl) => {
          if (generation !== socketGeneration) return;
          latestQrDataUrl = dataUrl;
          broadcast({ type: "qr", qr, qrDataUrl: dataUrl });
        })
        .catch((error) => log.warn({ error }, "failed to render qr data url"));
      qrcode.generate(qr, { small: true });
    }
    if (isNewLogin) {
      latestQr = null;
      latestQrDataUrl = null;
      connectionStatus = "pairing";
      broadcast({ type: "status", status: connectionStatus, ready: false });
    }
    if (connection === "open") {
      consecutiveFreshAuthFailures = 0;
      transientReconnectAttempt = 0;
      ready = true;
      latestQr = null;
      latestQrDataUrl = null;
      lastError = null;
      connectionStatus = "connected";
      selfJid = socketForGeneration.user?.id
        ? normalizeIdentityJid(socketForGeneration.user.id)
        : null;
      selfLid = normalizeLidJid(socketForGeneration.authState?.creds?.me?.lid);
      if (selfLid && selfJid) rememberLidPnMapping(selfLid, selfJid);
      // Presence is a global account state. Keep an account explicitly offline
      // when the periodic-online option is disabled; chat typing is handled
      // separately with composing/paused updates.
      if (runtimeConfig.markOnline) {
        sendAvailablePresence().catch(() => {});
        startPresenceTimer();
      } else {
        stopPresenceTimer();
        sendUnavailablePresence().catch(() => {});
      }
      broadcast({ type: "status", status: "connected", selfJid, selfLid });
      refreshAllowlistLidMappings("connection_open").catch((error) =>
        log.debug({ error }, "allowlist LID mapping refresh failed after connect"),
      );
    }
    if (connection === "close") {
      ready = false;
      stopPresenceTimer();
      clearAlbumBuffers();
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const errorMsg = lastDisconnect?.error?.message || lastDisconnect?.error?.output?.payload?.message || `statusCode=${statusCode}`;
      lastError = `连接关闭: ${errorMsg}`;
      const kind = disconnectKind(statusCode);
      const qrExpired = !state.creds.registered && /QR refs attempts ended/i.test(errorMsg);
      if (qrExpired) {
        connectionStatus = "qr_expired";
        latestQr = null;
        latestQrDataUrl = null;
        broadcast({ type: "status", status: connectionStatus, statusCode, lastError });
        return;
      }
      connectionStatus = kind === "auth_invalid" ? "session_invalid" : "disconnected";
      if (kind === "auth_invalid") {
        selfJid = null;
        selfLid = null;
        latestQr = null;
        latestQrDataUrl = null;
      }
      broadcast({ type: "status", status: connectionStatus, statusCode, lastError });
      if (kind === "restart") {
        connectionStatus = "pairing_restart";
        broadcast({ type: "status", status: connectionStatus, ready: false, statusCode, lastError });
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          if (generation !== socketGeneration) return;
          credsSaveQueue
            .then(() => requestSocketStart({ pairingRestart: true }))
            .catch((error) => log.error({ error }, "pairing restart failed"));
        }, 250);
      } else if (kind === "transient") {
        transientReconnectAttempt += 1;
        const baseDelay = reconnectDelayMs(transientReconnectAttempt);
        const jitteredDelay = Math.round(baseDelay * (0.8 + Math.random() * 0.4));
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          if (generation === socketGeneration) {
            requestSocketStart().catch((error) => log.error({ error }, "reconnect failed"));
          }
        }, jitteredDelay);
        log.warn(
          { statusCode, attempt: transientReconnectAttempt, retryInMs: jitteredDelay },
          "transient disconnect; reconnect scheduled with backoff",
        );
      } else {
        if (opts.fresh) consecutiveFreshAuthFailures += 1;
        if (!opts.fresh || consecutiveFreshAuthFailures <= 2) {
          requestSessionReset(`disconnect_${statusCode || "invalid_auth"}`).catch((error) =>
            log.error({ error }, "automatic auth session reset failed"),
          );
        } else {
          connectionStatus = "error";
          lastError = `全新登录 session 仍连接失败: ${errorMsg}`;
          broadcast({ type: "status", status: connectionStatus, ready: false, statusCode, lastError });
        }
      }
    }
  });
  socketForGeneration.ev.on("messages.upsert", async ({ messages, type }) => {
    if (
      generation !== socketGeneration
      || socketForGeneration !== socket
      || type !== "notify"
    ) return;
    for (const message of messages) {
      if (generation !== socketGeneration || socketForGeneration !== socket) return;
      await routeIncomingMessage(message, generation, socketForGeneration).catch((error) =>
        log.warn({ error }, "message handling failed"),
      );
    }
  });
  await loadLidMappingsFromDisk();
}

async function readJson(req) {
  const chunks = [];
  let totalBytes = 0;
  const maxBytes = 1024 * 1024; // 1MB 限制
  for await (const chunk of req) {
    totalBytes += chunk.length;
    if (totalBytes > maxBytes) throw new Error("request body too large");
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

function sendJson(res, status, value) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(value));
}

function statusPayload() {
  return {
    ok: true,
    ready,
    status: connectionStatus,
    selfJid,
    selfLid,
    authDir,
    sessionAuthDir: currentAuthDir,
    sessionId: currentSessionId,
    resetSequence,
    hasQr: Boolean(latestQr),
    lastPresenceAt,
    lastError,
    config: runtimeConfig,
  };
}

function resolveMediaPayload(type, pathOrUrl, caption, requestedFileName = "") {
  const payload = {};
  const normalizedPath = normalizeLocalMediaPath(pathOrUrl);
  const mediaInfo = mediaInfoForPath(type, pathOrUrl, normalizedPath);
  const source = /^https?:\/\//i.test(pathOrUrl)
    ? { url: pathOrUrl }
    : { url: normalizedPath };
  if (mediaInfo.type === "image") payload.image = source;
  else if (mediaInfo.type === "sticker") payload.sticker = source;
  else if (mediaInfo.type === "video") payload.video = source;
  else if (mediaInfo.type === "audio") {
    payload.audio = source;
    payload.ptt = true;
  } else {
    payload.document = source;
    payload.fileName = path.basename(String(requestedFileName || mediaInfo.fileName || "file"));
  }
  if (mediaInfo.mimetype) payload.mimetype = mediaInfo.mimetype;
  if (mediaInfo.type === "image" || mediaInfo.type === "video") payload.jpegThumbnail = null;
  if (caption && mediaInfo.type !== "audio" && mediaInfo.type !== "sticker") payload.caption = caption;
  return payload;
}

function mediaInfoForPath(requestedType, pathOrUrl, normalizedPath) {
  const fileName = fileNameForMedia(pathOrUrl, normalizedPath);
  const ext = path.extname(fileName).toLowerCase();
  const mimetype = mimeTypeForExt(ext);
  let type = requestedType;
  const sizeBytes = localMediaSize(pathOrUrl, normalizedPath);
  if (requestedType === "image" && ![".jpg", ".jpeg", ".png", ".webp"].includes(ext)) type = "document";
  if (requestedType === "sticker" && ![".webp"].includes(ext)) type = "image";
  if (requestedType === "video" && ![".mp4", ".mov", ".3gp"].includes(ext)) type = "document";
  if (requestedType === "audio" && ![".mp3", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".flac"].includes(ext)) type = "document";
  if ((type === "image" || type === "video" || type === "sticker") && sizeBytes > Number(runtimeConfig.mediaMessageMaxMb || 100) * 1024 * 1024) type = "document";
  if (type === "audio" && sizeBytes > Number(runtimeConfig.audioMaxMb || 16) * 1024 * 1024) {
    throw new Error(`audio exceeds outbound limit ${runtimeConfig.audioMaxMb || 16}MB`);
  }
  if (type === "document" && sizeBytes > Number(runtimeConfig.documentMaxMb || 2048) * 1024 * 1024) {
    throw new Error(`document exceeds outbound limit ${runtimeConfig.documentMaxMb || 2048}MB`);
  }
  if (!["image", "sticker", "video", "audio", "document"].includes(type)) type = "document";
  return { type, fileName, mimetype };
}

function localMediaSize(pathOrUrl, normalizedPath) {
  if (/^https?:\/\//i.test(pathOrUrl || "")) return 0;
  try {
    return statSync(normalizedPath).size;
  } catch {
    return 0;
  }
}

function fileNameForMedia(pathOrUrl, normalizedPath) {
  if (/^https?:\/\//i.test(pathOrUrl || "")) {
    try {
      const parsed = new URL(pathOrUrl);
      const baseName = path.basename(decodeURIComponent(parsed.pathname || ""));
      if (baseName && baseName !== "/" && baseName !== ".") return baseName;
    } catch {
      // fall through to local-style basename
    }
  }
  return path.basename(normalizedPath || "media") || "media";
}

function mimeTypeForExt(ext) {
  const types = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".3gp": "video/3gpp",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg; codecs=opus",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".json": "application/json",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
  };
  return types[ext] || "application/octet-stream";
}

function normalizeLocalMediaPath(pathOrUrl) {
  const value = String(pathOrUrl || "");
  if (!/^file:\/\//i.test(value)) return value;
  const normalized = `/${value.replace(/^file:/i, "").replace(/^\/+/, "")}`;
  try {
    return decodeURIComponent(normalized);
  } catch {
    return normalized;
  }
}

function isSingleUrlText(text) {
  return /^https?:\/\/\S+$/i.test(String(text || "").trim());
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (req.method === "GET" && url.pathname === "/health") {
      sendJson(res, 200, { ok: true, ready, selfJid, configured });
      return;
    }
    if (req.method === "GET" && url.pathname === "/status") {
      sendJson(res, 200, statusPayload());
      return;
    }
    if (req.method === "GET" && url.pathname === "/qr") {
      sendJson(res, 200, { ok: true, ready, status: connectionStatus, qr: latestQr, qrDataUrl: latestQrDataUrl });
      return;
    }
    if (req.method === "GET" && url.pathname === "/events") {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        connection: "keep-alive",
      });
      sseClients.add(res);
      const heartbeat = setInterval(() => {
        try {
          res.write(": keepalive\n\n");
        } catch {
          clearInterval(heartbeat);
          sseClients.delete(res);
        }
      }, 240000);
      sendSse(res, { type: "status", status: connectionStatus, ready, selfJid });
      if (latestQr) sendSse(res, { type: "qr", qr: latestQr, qrDataUrl: latestQrDataUrl });
      req.on("close", () => {
        clearInterval(heartbeat);
        sseClients.delete(res);
      });
      return;
    }
    if (req.method === "POST" && url.pathname === "/config") {
      const body = await readJson(req);
      // 輸入驗證
      if (body.dmPolicy !== undefined && !["allowlist", "open", "disabled"].includes(body.dmPolicy)) {
        sendJson(res, 400, { ok: false, error: `invalid dmPolicy: ${body.dmPolicy}` });
        return;
      }
      if (body.groupPolicy !== undefined && !["allowlist", "open", "disabled"].includes(body.groupPolicy)) {
        sendJson(res, 400, { ok: false, error: `invalid groupPolicy: ${body.groupPolicy}` });
        return;
      }
      for (const key of ["allowFrom", "groupAllowFrom", "groups"]) {
        if (body[key] !== undefined && !Array.isArray(body[key])) {
          sendJson(res, 400, { ok: false, error: `${key} must be an array` });
          return;
        }
      }
      for (const key of ["mediaMaxMb", "mediaMessageMaxMb", "documentMaxMb", "audioMaxMb", "mediaAlbumDebounceMs"]) {
        if (body[key] !== undefined && (typeof body[key] !== "number" || body[key] < 0 || !Number.isFinite(body[key]))) {
          sendJson(res, 400, { ok: false, error: `${key} must be a non-negative finite number` });
          return;
        }
      }
      if (body.applyEphemeral !== undefined && typeof body.applyEphemeral !== "boolean") {
        sendJson(res, 400, { ok: false, error: "applyEphemeral must be a boolean" });
        return;
      }
      if (body.markOnline !== undefined && typeof body.markOnline !== "boolean") {
        sendJson(res, 400, { ok: false, error: "markOnline must be a boolean" });
        return;
      }
      runtimeConfig = { ...runtimeConfig, ...body };
      configured = true;
      if (ready) {
        if (runtimeConfig.markOnline) {
          startPresenceTimer();
        } else {
          stopPresenceTimer();
          sendUnavailablePresence().catch((error) => log.debug({ error }, "presence update failed"));
        }
      }
      if (ready) refreshAllowlistLidMappings("config_update").catch((error) =>
        log.debug({ error }, "allowlist LID mapping refresh failed after config update"),
      );
      sendJson(res, 200, { ok: true, config: runtimeConfig });
      return;
    }
    if (req.method === "POST" && url.pathname === "/mentions/resolve") {
      const body = await readJson(req);
      if (body.chatJid) await rememberGroupParticipants(body.chatJid);
      const tokens = Array.isArray(body.tokens) ? body.tokens : mentionTokensFromText(body.text);
      sendJson(res, 200, { ok: true, mentions: resolveMentionTokens(tokens, body.chatJid) });
      return;
    }
    if (req.method === "POST" && url.pathname === "/lid/resolve") {
      const body = await readJson(req);
      const lidJid = String(body.lidJid || "").trim();
      if (!isLidJid(lidJid)) {
        sendJson(res, 400, { ok: false, error: "lidJid must be a WhatsApp LID JID" });
        return;
      }
      const existing = resolveLidToPn(lidJid);
      if (existing) {
        sendJson(res, 200, { ok: true, pnJid: existing });
        return;
      }
      const resolved = await waitForLidPnMapping(lidJid, 3000);
      sendJson(res, 200, { ok: true, pnJid: resolved });
      return;
    }
    if (req.method === "POST" && url.pathname === "/restart") {
      const result = ["session_invalid", "logged_out", "qr_expired"].includes(connectionStatus)
        ? await requestSessionReset("manual_restart_invalid_session")
        : (await requestSocketStart(), { ok: true, status: connectionStatus, sessionId: currentSessionId });
      sendJson(res, 200, result);
      return;
    }
    if (req.method === "POST" && (url.pathname === "/logout" || url.pathname === "/session/reset")) {
      const reason = url.pathname === "/logout" ? "manual_logout" : "manual_session_reset";
      const result = url.pathname === "/logout"
        ? await requestLogoutAndReset(reason)
        : await requestSessionReset(reason);
      sendJson(res, 200, result);
      return;
    }
    if (req.method === "POST" && url.pathname === "/pair-code") {
      const body = await readJson(req);
      let phone;
      try {
        phone = normalizePairingPhone(body.phone);
      } catch {
        sendJson(res, 400, {
          ok: false,
          error: "phone must contain 7 to 15 digits with a country code and no formatting",
        });
        return;
      }

      const initialAvailability = pairingCodeAvailability({
        socket,
        ready,
        registered: Boolean(socket?.authState?.creds?.registered),
        connectionStatus,
      });
      if (!initialAvailability.ok) {
        sendJson(res, initialAvailability.status, {
          ok: false,
          error: initialAvailability.error,
        });
        return;
      }
      if (socket?.ws?.isOpen !== true) {
        sendJson(res, 503, {
          ok: false,
          error: "WhatsApp login transport is not ready yet.",
        });
        return;
      }

      const lease = pairingCodePolicy.begin();
      if (!lease.ok) {
        sendJson(res, lease.status, {
          ok: false,
          error: lease.error,
          ...(lease.retryAfterMs ? { retryAfterMs: lease.retryAfterMs } : {}),
        });
        return;
      }

      try {
        const result = await enqueueSocketTransition("pair_code", async () => {
          const pairingSocket = socket;
          const availability = pairingCodeAvailability({
            socket: pairingSocket,
            ready,
            registered: Boolean(pairingSocket?.authState?.creds?.registered),
            connectionStatus,
          });
          if (!availability.ok) return availability;
          if (pairingSocket?.ws?.isOpen !== true) {
            return {
              ok: false,
              status: 503,
              error: "WhatsApp login transport is not ready yet.",
            };
          }

          try {
            const code = String(await pairingSocket.requestPairingCode(phone));
            if (pairingSocket !== socket || !/^[A-Za-z0-9]{8}$/.test(code)) {
              return {
                ok: false,
                status: 502,
                error: "WhatsApp did not return a usable pairing code.",
              };
            }
            latestQr = null;
            latestQrDataUrl = null;
            connectionStatus = "pair_code_pending";
            broadcast({ type: "status", status: connectionStatus, ready: false });
            log.info({ sessionId: currentSessionId }, "phone pairing code generated securely");
            return { ok: true, status: 200, code };
          } catch (error) {
            log.warn(
              {
                errorName: String(error?.name || "Error"),
                statusCode: error?.output?.statusCode || error?.statusCode || null,
              },
              "phone pairing code request failed",
            );
            return {
              ok: false,
              status: 502,
              error: "WhatsApp rejected the pairing code request.",
            };
          }
        });

        if (!result.ok) {
          sendJson(res, result.status, { ok: false, error: result.error });
          return;
        }
        sendJson(res, 200, { ok: true, code: result.code });
      } finally {
        lease.finish();
      }
      return;
    }
    if (!socket || !ready) {
      sendJson(res, 503, { error: "WhatsApp is not connected. Scan the QR code in Gateway logs." });
      return;
    }
    if (req.method === "POST" && url.pathname === "/group/info") {
      const body = await readJson(req);
      const groupJid = String(body.groupJid || "").trim();
      if (!groupJid.endsWith("@g.us")) {
        sendJson(res, 400, { error: "groupJid must end with @g.us" });
        return;
      }
      const metadata = await socket.groupMetadata(groupJid);
      const participants = (metadata?.participants || []).map((participant) => {
        const identity = rememberGroupParticipantIdentity(participant, groupJid);
        const jid = String(identity?.jid || "");
        const pnJid = identity?.pnJid || resolveLidToPn(identity?.lidJid || jid) || "";
        const role = participant?.admin === "superadmin"
          ? "owner"
          : participant?.admin === "admin"
            ? "admin"
            : "member";
        return {
          jid,
          pnJid,
          userId: normalizeJid(pnJid || jid),
          name: mentionDisplayName(pnJid) || mentionDisplayName(identity?.lidJid || jid) || normalizeJid(pnJid || jid),
          role,
        };
      });
      const ownerIdentity = rememberGroupOwnerIdentity(metadata);
      const owner = normalizeJid(
        ownerIdentity?.pnJid
        || resolveLidToPn(ownerIdentity?.lidJid || ownerIdentity?.jid)
        || ownerIdentity?.jid
        || "",
      )
        || participants.find((participant) => participant.role === "owner")?.userId
        || "";
      const admins = participants
        .filter((participant) => participant.role === "admin" && participant.userId !== owner)
        .map((participant) => participant.userId);
      sendJson(res, 200, {
        ok: true,
        groupId: normalizeJid(metadata?.id || groupJid),
        groupJid,
        subject: String(metadata?.subject || ""),
        owner,
        admins,
        participants,
      });
      return;
    }
    if (req.method === "POST" && url.pathname === "/presence") {
      const body = await readJson(req);
      const state = String(body.state || "available");
      if (!body.to) {
        sendJson(res, 400, { error: "missing target jid" });
        return;
      }
      await sendReplyPresence(state, body.to);
      sendJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/text") {
      const body = await readJson(req);
      const text = String(body.text || "");
      if (!text) {
        sendJson(res, 200, { ok: true, skipped: true });
        return;
      }
      const options = quotedKey(body) || {};
      const ephemeral = getEphemeralExpiration(body.to);
      if (ephemeral) options.ephemeralExpiration = ephemeral;
      const { mentions: resolvedExplicit, mentionAll } = resolveExplicitMentions(body.mentions, body.to);
      const autoMentions = body.resolveTextMentions ? resolveMentionTokens(mentionTokensFromText(text), body.to) : [];
      const mentions = [...new Set([...resolvedExplicit, ...autoMentions])];
      const payload = { text };
      if (body.linkPreview === false) payload.linkPreview = null;
      if (mentions.length) payload.mentions = mentions;
      if (mentionAll) payload.mentionAll = true;
      const result = await socket.sendMessage(body.to, payload, options);
      cacheChatMessage(messageCache, result, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id, key: result?.key });
      return;
    }
    if (req.method === "POST" && url.pathname === "/edit/text") {
      const body = await readJson(req);
      const text = String(body.text || "");
      if (!body.to || !body.messageId) {
        sendJson(res, 400, { error: "missing target jid or message id" });
        return;
      }
      const key = { remoteJid: body.to, id: body.messageId, fromMe: true };
      if (body.participant) key.participant = body.participant;
      const payload = { text, edit: key };
      const ephemeral = getEphemeralExpiration(body.to);
      const editOptions = ephemeral ? { ephemeralExpiration: ephemeral } : {};
      const { mentions: resolvedExplicit, mentionAll } = resolveExplicitMentions(body.mentions, body.to);
      if (resolvedExplicit.length) payload.mentions = resolvedExplicit;
      if (mentionAll) payload.mentionAll = true;
      const result = await socket.sendMessage(body.to, payload, editOptions);
      let editedMessage = normalizeMessagePayload(result?.message?.protocolMessage?.editedMessage);
      if (!editedMessage) {
        const cached = findChatMessage(messageCache, body.to, body.messageId);
        const previousContext = contextInfoFromMessagePayload(cached?.message);
        const contextInfo = previousContext ? { ...previousContext } : {};
        delete contextInfo.mentionedJid;
        delete contextInfo.nonJidMentions;
        if (resolvedExplicit.length) contextInfo.mentionedJid = resolvedExplicit;
        if (mentionAll) contextInfo.nonJidMentions = 1;
        const extendedTextMessage = { text };
        if (Object.keys(contextInfo).length) extendedTextMessage.contextInfo = contextInfo;
        editedMessage = { extendedTextMessage };
      }
      cacheEditedMessage(messageCache, key, editedMessage, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id, key: result?.key });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/media") {
      const body = await readJson(req);
      if (!body.pathOrUrl) {
        sendJson(res, 400, { ok: false, error: "pathOrUrl is required" });
        return;
      }
      if (!/^https?:\/\//i.test(body.pathOrUrl)) await stat(normalizeLocalMediaPath(body.pathOrUrl));
      const options = quotedKey(body) || {};
      const ephemeral = getEphemeralExpiration(body.to);
      if (ephemeral) options.ephemeralExpiration = ephemeral;
      const payload = resolveMediaPayload(
        body.type,
        body.pathOrUrl,
        body.caption,
        body.fileName,
      );
      const { mentions, mentionAll } = resolveExplicitMentions(body.mentions, body.to);
      if (mentions.length) payload.mentions = mentions;
      if (mentionAll) payload.mentionAll = true;
      const result = await socket.sendMessage(body.to, payload, options);
      cacheChatMessage(messageCache, result, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/location") {
      const body = await readJson(req);
      const latitude = Number(body.latitude);
      const longitude = Number(body.longitude);
      if (!body.to || !Number.isFinite(latitude) || !Number.isFinite(longitude)) {
        sendJson(res, 400, { ok: false, error: "target jid and finite latitude/longitude are required" });
        return;
      }
      const options = quotedKey(body) || {};
      const ephemeral = getEphemeralExpiration(body.to);
      if (ephemeral) options.ephemeralExpiration = ephemeral;
      const result = await socket.sendMessage(
        body.to,
        {
          location: {
            degreesLatitude: latitude,
            degreesLongitude: longitude,
            name: String(body.name || ""),
            address: String(body.address || ""),
          },
        },
        options,
      );
      cacheChatMessage(messageCache, result, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id, key: result?.key });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/reaction") {
      const body = await readJson(req);
      const key = { remoteJid: body.to, id: body.messageId };
      if (body.participant) key.participant = body.participant;
      const reactEphemeral = getEphemeralExpiration(body.to);
      const reactOptions = reactEphemeral ? { ephemeralExpiration: reactEphemeral } : {};
      await socket.sendMessage(body.to, { react: { text: body.emoji || "", key } }, reactOptions);
      sendJson(res, 200, { ok: true });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/buttons") {
      const body = await readJson(req);
      if (!body.to) {
        sendJson(res, 400, { error: "missing target jid" });
        return;
      }
      const buttons = body.buttons || [];
      if (!buttons.length) {
        sendJson(res, 400, { error: "buttons array is required" });
        return;
      }
      const result = await relayProtoContent(body.to, buildButtonsContent(body), body);
      sendJson(res, 200, result);
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/list") {
      const body = await readJson(req);
      if (!body.to) {
        sendJson(res, 400, { error: "missing target jid" });
        return;
      }
      const sections = body.sections || [];
      if (!sections.length) {
        sendJson(res, 400, { error: "sections array is required" });
        return;
      }
      const result = await relayProtoContent(body.to, buildListContent(body), body);
      sendJson(res, 200, result);
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/contact") {
      const body = await readJson(req);
      if (!body.to) {
        sendJson(res, 400, { error: "missing target jid" });
        return;
      }
      let content;
      try {
        content = buildContactContent(body);
      } catch (error) {
        sendJson(res, 400, { error: String(error?.message || error) });
        return;
      }
      const options = quotedKey(body) || {};
      const ephemeral = getEphemeralExpiration(body.to);
      if (ephemeral) options.ephemeralExpiration = ephemeral;
      const result = await socket.sendMessage(body.to, content, options);
      cacheChatMessage(messageCache, result, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id, key: result?.key });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/event") {
      const body = await readJson(req);
      if (!body.to) {
        sendJson(res, 400, { error: "missing target jid" });
        return;
      }
      let content;
      try {
        content = buildEventContent(body);
      } catch (error) {
        sendJson(res, 400, { error: String(error?.message || error) });
        return;
      }
      const options = quotedKey(body) || {};
      const ephemeral = getEphemeralExpiration(body.to);
      if (ephemeral) options.ephemeralExpiration = ephemeral;
      const result = await socket.sendMessage(body.to, content, options);
      cacheChatMessage(messageCache, result, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id, key: result?.key });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/poll") {
      const body = await readJson(req);
      if (!body.to) {
        sendJson(res, 400, { error: "missing target jid" });
        return;
      }
      let content;
      try {
        content = buildPollContent(body);
      } catch (error) {
        sendJson(res, 400, { error: String(error?.message || error) });
        return;
      }
      const options = quotedKey(body) || {};
      const ephemeral = getEphemeralExpiration(body.to);
      if (ephemeral) options.ephemeralExpiration = ephemeral;
      const result = await socket.sendMessage(body.to, content, options);
      cacheChatMessage(messageCache, result, maxMessageCacheSize);
      sendJson(res, 200, { ok: true, id: result?.key?.id, key: result?.key });
      return;
    }
    sendJson(res, 404, { error: "not found" });
  } catch (error) {
    log.warn({ error: String(error?.message || error), stack: error?.stack }, "request failed");
    sendJson(res, 500, { error: String(error?.message || error) });
  }
});

server.on("error", (error) => {
  if (error?.code === "EADDRINUSE") {
    log.warn({ host, port }, "WhatsApp Gateway port already in use; exiting duplicate process");
    process.exit(0);
  }
  log.error({ error }, "WhatsApp Gateway server error");
  process.exit(1);
});

server.listen(port, host, () => {
  log.info({ host, port, authDir }, "WhatsApp Gateway listening");
});

initializeAuthSession().then(() => requestSocketStart()).catch((error) => {
  connectionStatus = "error";
  lastError = `Gateway 启动失败: ${String(error?.message || error)}`;
  log.error({ error }, "WhatsApp Gateway startup failed");
  process.exitCode = 1;
});
