const PATCH_MARKER = "astrbotGatewaySecurityHardening";

function replaceRequired(source, needle, replacement, label) {
  if (!source.includes(needle)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(needle, replacement);
}

const IMPORT_ANCHOR = 'import { buildWhatsAppProxyConfig } from "./proxy-compat.mjs";';
const CONFIG_ANCHOR = 'const logLevel = process.env.WA_LOG_LEVEL || "info";';
const SERVER_ANCHOR = '    const url = new URL(req.url, `http://${req.headers.host}`);';
const SSE_ANCHOR = `    if (req.method === "GET" && url.pathname === "/events") {\n      res.writeHead(200, {`;
const BROADCAST_BLOCK = `function broadcast(data) {\n  for (const client of sseClients) {\n    sendSse(client, data);\n  }\n}`;
const FAIL_OPEN_BLOCK = `  if (!configured) {\n    log.warn({ chatJid, senderJid, messageId: primary.key.id }, "Gateway not yet configured; passing message through without allowlist check");\n  }\n  let allowedResult = configured ? allowedMessageResult(chatJid, senderJid, primary) : { allowed: true, reason: "not_yet_configured", senderPhone: "" };`;
const MEDIA_ROUTE = `    if (req.method === "POST" && url.pathname === "/send/media") {\n      const body = await readJson(req);\n      if (!body.pathOrUrl) {\n        sendJson(res, 400, { ok: false, error: "pathOrUrl is required" });\n        return;\n      }\n      if (!/^https?:\\/\\//i.test(body.pathOrUrl)) await stat(normalizeLocalMediaPath(body.pathOrUrl));\n      const options = quotedKey(body) || {};\n      const ephemeral = getEphemeralExpiration(body.to);\n      if (ephemeral) options.ephemeralExpiration = ephemeral;\n      const { mentions, mentionAll } = resolveExplicitMentions(body.mentions, body.to);\n      const renderedCaption = await renderOutboundMentionNames(\n        body.caption,\n        mentions,\n        body.to,\n      );\n      const payload = resolveMediaPayload(\n        body.type,\n        body.pathOrUrl,\n        renderedCaption,\n        body.fileName,\n      );\n      if (mentions.length) payload.mentions = mentions;\n      if (mentionAll) payload.mentionAll = true;\n      const result = await socket.sendMessage(body.to, payload, options);\n      cacheChatMessage(messageCache, result, maxMessageCacheSize);\n      sendJson(res, 200, { ok: true, id: result?.key?.id });\n      return;\n    }`;

export function patchGatewaySecurity(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  let content = source;
  content = replaceRequired(
    content,
    IMPORT_ANCHOR,
    `${IMPORT_ANCHOR}\nimport { isAuthorizedGatewayRequest, prepareSafeMediaSource } from "./security-runtime.mjs";`,
    "security runtime import anchor",
  );
  content = replaceRequired(
    content,
    CONFIG_ANCHOR,
    `${CONFIG_ANCHOR}\nconst ${PATCH_MARKER} = true;\nconst gatewayAuthToken = String(process.env.WA_GATEWAY_TOKEN || "").trim();\nconst maxSseClients = Math.max(1, Math.min(Number.parseInt(process.env.WA_MAX_SSE_CLIENTS || "8", 10) || 8, 64));`,
    "Gateway configuration anchor",
  );
  content = replaceRequired(
    content,
    SERVER_ANCHOR,
    `${SERVER_ANCHOR}\n    if (!isAuthorizedGatewayRequest(req, gatewayAuthToken)) {\n      res.setHeader("www-authenticate", 'Bearer realm="astrbot-whatsapp-gateway"');\n      sendJson(res, 401, { ok: false, error: "unauthorized" });\n      return;\n    }`,
    "HTTP authorization anchor",
  );
  content = replaceRequired(
    content,
    SSE_ANCHOR,
    `    if (req.method === "GET" && url.pathname === "/events") {\n      if (sseClients.size >= maxSseClients) {\n        sendJson(res, 503, { ok: false, error: "too many event stream clients" });\n        return;\n      }\n      res.writeHead(200, {`,
    "SSE route anchor",
  );
  content = replaceRequired(
    content,
    BROADCAST_BLOCK,
    `function broadcast(data) {\n  const safeData = data?.type === "rejected"\n    ? {\n        type: "rejected",\n        reason: String(data.reason || "policy"),\n        messageId: data.messageId || null,\n        timestamp: Number(data.timestamp || Date.now() / 1000),\n      }\n    : data;\n  for (const client of sseClients) {\n    sendSse(client, safeData);\n  }\n}`,
    "SSE broadcast helper",
  );
  content = replaceRequired(
    content,
    FAIL_OPEN_BLOCK,
    `  if (!configured) {\n    log.warn({ messageId: primary.key.id }, "Gateway not yet configured; dropping inbound message until policy is loaded");\n    return;\n  }\n  let allowedResult = allowedMessageResult(chatJid, senderJid, primary);`,
    "pre-configuration fail-open block",
  );
  content = replaceRequired(
    content,
    MEDIA_ROUTE,
    `    if (req.method === "POST" && url.pathname === "/send/media") {\n      const body = await readJson(req);\n      if (!body.pathOrUrl) {\n        sendJson(res, 400, { ok: false, error: "pathOrUrl is required" });\n        return;\n      }\n      let preparedMedia;\n      try {\n        preparedMedia = await prepareSafeMediaSource(body.pathOrUrl, { tempDir });\n      } catch (error) {\n        sendJson(res, 403, { ok: false, error: String(error?.message || error) });\n        return;\n      }\n      try {\n        const options = quotedKey(body) || {};\n        const ephemeral = getEphemeralExpiration(body.to);\n        if (ephemeral) options.ephemeralExpiration = ephemeral;\n        const { mentions, mentionAll } = resolveExplicitMentions(body.mentions, body.to);\n        const renderedCaption = await renderOutboundMentionNames(\n          body.caption,\n          mentions,\n          body.to,\n        );\n        const payload = resolveMediaPayload(\n          body.type,\n          preparedMedia.pathOrUrl,\n          renderedCaption,\n          body.fileName,\n        );\n        if (mentions.length) payload.mentions = mentions;\n        if (mentionAll) payload.mentionAll = true;\n        const result = await socket.sendMessage(body.to, payload, options);\n        cacheChatMessage(messageCache, result, maxMessageCacheSize);\n        sendJson(res, 200, { ok: true, id: result?.key?.id });\n        return;\n      } finally {\n        await preparedMedia.cleanup();\n      }\n    }`,
    "outbound media route",
  );

  return { content, changed: true };
}
