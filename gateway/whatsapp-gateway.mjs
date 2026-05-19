import { createServer } from "node:http";
import { mkdir, rm, stat } from "node:fs/promises";
import { createReadStream } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import pino from "pino";
import QRCode from "qrcode";
import qrcode from "qrcode-terminal";

const host = process.env.WA_GATEWAY_HOST || "127.0.0.1";
const port = Number.parseInt(process.env.WA_GATEWAY_PORT || "18789", 10);
const authDir = process.env.WA_AUTH_DIR || path.join(process.cwd(), "data", "whatsapp-auth");
const logLevel = process.env.WA_LOG_LEVEL || "info";
const dataDir = process.env.WA_DATA_DIR || path.join(process.cwd(), "data", "astrbot_plugin_whatsapp_adapter");
const mediaDir = path.join(dataDir, "media");

const log = pino({ level: logLevel });
const sseClients = new Set();

let socket = null;
let ready = false;
let selfJid = null;
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
  markOnline: true,
  mediaMaxMb: 50,
};

function stopPresenceTimer() {
  if (presenceTimer) clearInterval(presenceTimer);
  presenceTimer = null;
}

async function sendAvailablePresence() {
  if (!runtimeConfig.markOnline || !socket?.sendPresenceUpdate || !ready) return;
  await socket.sendPresenceUpdate("available");
  lastPresenceAt = new Date().toISOString();
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
  client.write(`data: ${JSON.stringify(data)}\n\n`);
}

function broadcast(data) {
  for (const client of sseClients) {
    sendSse(client, data);
  }
}

function normalizeJid(jid) {
  return String(jid || "").split(":")[0].replace("@s.whatsapp.net", "");
}

function normalizePhone(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text === "*") return "*";
  const digits = text.replace(/\D/g, "");
  return digits ? `+${digits}` : text;
}

function jidToPhone(jid) {
  const digits = normalizeJid(jid).replace(/\D/g, "");
  return digits ? `+${digits}` : normalizeJid(jid);
}

function messageJidCandidates(item, chatJid, senderJid) {
  return [
    senderJid,
    chatJid,
    item?.key?.senderPn,
    item?.key?.participantPn,
    item?.key?.remoteJidAlt,
    item?.key?.participantAlt,
  ].filter(Boolean);
}

function allowedByList(value, allowList) {
  const normalized = normalizePhone(value);
  const raw = String(value || "").trim();
  const normalizedJid = normalizeJid(raw);
  const normalizedList = (allowList || []).map((item) => ({
    raw: String(item || "").trim(),
    phone: normalizePhone(item),
    jid: normalizeJid(item),
  }));
  return normalizedList.some((item) => (
    item.raw === "*" || item.phone === "*" ||
    item.phone === normalized || item.raw === raw || item.jid === normalizedJid
  ));
}

function isAllowedMessage(chatJid, senderJid, item) {
  const result = allowedMessageResult(chatJid, senderJid, item);
  return result.allowed;
}

function allowedMessageResult(chatJid, senderJid, item) {
  const isGroup = chatJid.endsWith("@g.us");
  const candidates = messageJidCandidates(item, chatJid, senderJid);
  const senderPhone = candidates.map(jidToPhone).find((value) => value.startsWith("+")) || jidToPhone(senderJid || chatJid);
  const allowedByCandidates = (allowList) => candidates.some((value) => (
    allowedByList(jidToPhone(value), allowList) || allowedByList(value, allowList)
  ));
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

function textFromMessage(message) {
  if (!message) return "";
  if (message.conversation) return message.conversation;
  if (message.extendedTextMessage?.text) return message.extendedTextMessage.text;
  if (message.imageMessage?.caption) return message.imageMessage.caption;
  if (message.videoMessage?.caption) return message.videoMessage.caption;
  if (message.documentMessage?.caption) return message.documentMessage.caption;
  if (message.buttonsResponseMessage?.selectedDisplayText) return message.buttonsResponseMessage.selectedDisplayText;
  if (message.listResponseMessage?.title) return message.listResponseMessage.title;
  return "";
}

function contextInfoFromMessage(message) {
  if (!message) return null;
  return (
    message.extendedTextMessage?.contextInfo ||
    message.imageMessage?.contextInfo ||
    message.videoMessage?.contextInfo ||
    message.documentMessage?.contextInfo ||
    message.audioMessage?.contextInfo ||
    null
  );
}

function mentionedJidsFromMessage(message) {
  const mentioned = contextInfoFromMessage(message)?.mentionedJid || [];
  return Array.isArray(mentioned) ? mentioned.filter(Boolean) : [];
}

function mediaKind(message) {
  if (message.imageMessage) return "image";
  if (message.videoMessage) return "video";
  if (message.audioMessage) return "audio";
  if (message.documentMessage) return "document";
  if (message.stickerMessage) return "sticker";
  return null;
}

function mediaFileName(message, kind) {
  return message?.documentMessage?.fileName || message?.imageMessage?.fileName || message?.videoMessage?.fileName || `${kind || "media"}`;
}

function quotedKey(body) {
  if (!body?.quotedMessageId) return { quoted: null };
  const key = { remoteJid: body.to, id: body.quotedMessageId };
  if (body.quotedParticipant) key.participant = body.quotedParticipant;
  return { quoted: { key } };
}

async function saveInboundMedia(message, kind, id) {
  await mkdir(mediaDir, { recursive: true });
  const buffer = await downloadMediaMessage(message, "buffer", {}, { logger: log });
  const maxBytes = Number(runtimeConfig.mediaMaxMb || 50) * 1024 * 1024;
  if (buffer.length > maxBytes) {
    throw new Error(`media exceeds ${runtimeConfig.mediaMaxMb}MB`);
  }
  const fileName = `${Date.now()}-${id || "message"}.${extensionForKind(kind)}`;
  const filePath = path.join(mediaDir, fileName);
  await import("node:fs/promises").then((fs) => fs.writeFile(filePath, buffer));
  return filePath;
}

function extensionForKind(kind) {
  if (kind === "image") return "jpg";
  if (kind === "video") return "mp4";
  if (kind === "audio") return "ogg";
  if (kind === "sticker") return "webp";
  return "bin";
}

async function handleIncomingMessage(item) {
  const chatJid = item.key.remoteJid;
  if (!chatJid || chatJid.endsWith("@status") || chatJid.endsWith("@broadcast")) return;
  const fromMe = Boolean(item.key.fromMe);
  const senderJid = item.key.participant || chatJid;
  const allowed = allowedMessageResult(chatJid, senderJid, item);
  if (fromMe) {
    log.debug({ chatJid, senderJid, messageId: item.key.id }, "ignored inbound message from self");
    return;
  }
  if (!allowed.allowed) {
    log.info(
      {
        chatJid,
        senderJid,
        senderPhone: allowed.senderPhone,
        reason: allowed.reason,
        dmPolicy: runtimeConfig.dmPolicy,
        groupPolicy: runtimeConfig.groupPolicy,
        allowFromCount: (runtimeConfig.allowFrom || []).length,
      },
      "rejected inbound WhatsApp message",
    );
    return;
  }
  log.info(
    {
      chatJid,
      senderJid,
      senderPhone: allowed.senderPhone,
      reason: allowed.reason,
      messageId: item.key.id,
      hasText: Boolean(textFromMessage(item.message)),
      mentionCount: mentionedJidsFromMessage(item.message).length,
      mediaKind: mediaKind(item.message),
    },
    "accepted inbound WhatsApp message",
  );

  const kind = mediaKind(item.message);
  const media = [];
  if (kind) {
    try {
      const filePath = await saveInboundMedia(item, kind, item.key.id);
      media.push({ type: kind, path: filePath, fileName: mediaFileName(item.message, kind) });
    } catch (error) {
      log.warn({ error }, "failed to save inbound media");
      media.push({ type: kind, error: String(error?.message || error) });
    }
  }

  if (runtimeConfig.sendReadReceipts && socket) {
    socket.readMessages([item.key]).catch((error) => log.debug({ error }, "read receipt failed"));
  }

  broadcast({
    type: "message",
    messageId: item.key.id,
    chatJid,
    senderJid,
    senderName: item.pushName || senderJid,
    fromMe,
    selfJid,
    text: textFromMessage(item.message) || (kind ? `<media:${kind}>` : ""),
    mentionedJids: mentionedJidsFromMessage(item.message),
    media,
    timestamp: Number(item.messageTimestamp || Date.now() / 1000),
    raw: { key: item.key },
  });
}

async function startSocket() {
  const generation = ++socketGeneration;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (socket?.end) {
    try {
      socket.end(undefined);
    } catch (error) {
      log.debug({ error }, "failed to close previous socket");
    }
  }
  socket = null;
  ready = false;
  stopPresenceTimer();
  connectionStatus = "starting";
  await mkdir(authDir, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(authDir);
  const { version } = await fetchLatestBaileysVersion();
  socket = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger: log,
    markOnlineOnConnect: Boolean(runtimeConfig.markOnline),
  });

  socket.ev.on("creds.update", saveCreds);
  socket.ev.on("connection.update", (update) => {
    if (generation !== socketGeneration) return;
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      latestQr = qr;
      QRCode.toDataURL(qr, { margin: 1, width: 320 })
        .then((dataUrl) => {
          latestQrDataUrl = dataUrl;
          broadcast({ type: "qr", qr, qrDataUrl: dataUrl });
        })
        .catch((error) => log.warn({ error }, "failed to render qr data url"));
      qrcode.generate(qr, { small: true });
    }
    if (connection === "open") {
      ready = true;
      latestQr = null;
      latestQrDataUrl = null;
      connectionStatus = "connected";
      selfJid = socket.user?.id || null;
      startPresenceTimer();
      broadcast({ type: "status", status: "connected", selfJid });
    }
    if (connection === "close") {
      ready = false;
      stopPresenceTimer();
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      connectionStatus = statusCode === DisconnectReason.loggedOut ? "logged_out" : "disconnected";
      broadcast({ type: "status", status: "disconnected", statusCode });
      if (statusCode !== DisconnectReason.loggedOut) {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(() => {
          if (generation === socketGeneration) startSocket().catch((error) => log.error({ error }, "reconnect failed"));
        }, 3000);
      }
    }
  });
  socket.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const message of messages) {
      await handleIncomingMessage(message).catch((error) => log.warn({ error }, "message handling failed"));
    }
  });
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
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
    authDir,
    hasQr: Boolean(latestQr),
    lastPresenceAt,
    config: runtimeConfig,
  };
}

function resolveMediaPayload(type, pathOrUrl, caption) {
  const payload = {};
  const source = /^https?:\/\//i.test(pathOrUrl)
    ? { url: pathOrUrl }
    : createReadStream(pathOrUrl.replace(/^file:\/\//, ""));
  if (type === "image") payload.image = source;
  else if (type === "video") payload.video = source;
  else if (type === "audio") {
    payload.audio = source;
    payload.ptt = true;
  } else {
    payload.document = source;
    payload.fileName = path.basename(pathOrUrl);
  }
  if (caption && type !== "audio") payload.caption = caption;
  return payload;
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (req.method === "GET" && url.pathname === "/health") {
      sendJson(res, 200, { ok: true, ready, selfJid });
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
      sendSse(res, { type: "status", status: connectionStatus, ready, selfJid });
      if (latestQr) sendSse(res, { type: "qr", qr: latestQr, qrDataUrl: latestQrDataUrl });
      req.on("close", () => sseClients.delete(res));
      return;
    }
    if (req.method === "POST" && url.pathname === "/config") {
      runtimeConfig = { ...runtimeConfig, ...(await readJson(req)) };
      if (ready) startPresenceTimer();
      sendJson(res, 200, { ok: true, config: runtimeConfig });
      return;
    }
    if (req.method === "POST" && url.pathname === "/restart") {
      startSocket().catch((error) => log.error({ error }, "manual restart failed"));
      sendJson(res, 200, { ok: true, status: "restarting" });
      return;
    }
    if (req.method === "POST" && url.pathname === "/logout") {
      if (socket?.logout) {
        await socket.logout().catch((error) => log.debug({ error }, "socket logout failed"));
      }
      if (socket?.end) socket.end(undefined);
      socket = null;
      ready = false;
      selfJid = null;
      latestQr = null;
      latestQrDataUrl = null;
      stopPresenceTimer();
      connectionStatus = "logged_out";
      await rm(authDir, { recursive: true, force: true });
      broadcast({ type: "status", status: "logged_out", ready: false });
      startSocket().catch((error) => log.error({ error }, "restart after logout failed"));
      sendJson(res, 200, { ok: true, status: "logged_out" });
      return;
    }
    if (!socket || !ready) {
      sendJson(res, 503, { error: "WhatsApp is not connected. Scan the QR code in Gateway logs." });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/text") {
      const body = await readJson(req);
      const result = await socket.sendMessage(body.to, { text: body.text || "" }, quotedKey(body));
      sendJson(res, 200, { ok: true, id: result?.key?.id });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/media") {
      const body = await readJson(req);
      if (!/^https?:\/\//i.test(body.pathOrUrl || "")) await stat(String(body.pathOrUrl || ""));
      const result = await socket.sendMessage(
        body.to,
        resolveMediaPayload(body.type, body.pathOrUrl, body.caption),
        quotedKey(body),
      );
      sendJson(res, 200, { ok: true, id: result?.key?.id });
      return;
    }
    if (req.method === "POST" && url.pathname === "/send/reaction") {
      const body = await readJson(req);
      const key = { remoteJid: body.to, id: body.messageId };
      if (body.participant) key.participant = body.participant;
      await socket.sendMessage(body.to, { react: { text: body.emoji || "", key } });
      sendJson(res, 200, { ok: true });
      return;
    }
    sendJson(res, 404, { error: "not found" });
  } catch (error) {
    log.warn({ error }, "request failed");
    sendJson(res, 500, { error: String(error?.message || error) });
  }
});

server.listen(port, host, () => {
  log.info({ host, port, authDir }, "WhatsApp Gateway listening");
});

startSocket().catch((error) => {
  log.error({ error }, "WhatsApp Gateway startup failed");
  process.exitCode = 1;
});
