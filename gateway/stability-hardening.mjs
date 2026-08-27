const PATCH_MARKER = "astrbotGatewayStabilityHardening";

function replaceRequired(source, needle, replacement, label) {
  if (!source.includes(needle)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(needle, replacement);
}

const IMPORT_ANCHOR = 'import { buildWhatsAppProxyConfig } from "./proxy-compat.mjs";';
const CONFIG_ANCHOR = 'const tempDir = process.env.WA_TEMP_DIR || path.join(dataDir, "..", "..", "temp");';

const HEALTH_ROUTE = `    if (req.method === "GET" && url.pathname === "/health") {
      sendJson(res, 200, { ok: true, ready, selfJid, configured });
      return;
    }`;

const EPHEMERAL_UPDATE_BLOCK = `    for (const chat of chats || []) {
      if (chat?.id) {
        if (chat.ephemeralExpiration !== undefined) {
          chatEphemeral.set(chat.id, chat.ephemeralExpiration);
        } else if (chat.ephemeralExpiration === 0 || chat.ephemeralExpiration === null) {
          chatEphemeral.delete(chat.id);
        }
      }
    }`;

const SSE_WRITE_BLOCK = `function sendSse(client, data) {
  try {
    client.write(\`data: \${JSON.stringify(data)}\\n\\n\`);
  } catch {
    sseClients.delete(client);
  }
}`;

const SSE_HEARTBEAT_BLOCK = `      const heartbeat = setInterval(() => {
        try {
          res.write(": keepalive\\n\\n");
        } catch {
          clearInterval(heartbeat);
          sseClients.delete(res);
        }
      }, 240000);`;

const MEDIA_DOWNLOAD_BLOCK = `    const stream = await downloadMediaMessage(
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
          reject(new Error(\`\${kind} exceeds inbound limit \${Math.floor(maxBytes / 1024 / 1024)}MB\`));
        }
      });
      stream.pipe(writeStream).on("finish", () => {
        writeStream.close();
        resolve();
      });
    });`;

export function patchGatewayStability(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  // Git may check the implementation out with CRLF on Windows. Normalize only
  // the generated runtime content so exact source anchors remain portable.
  let content = source.replace(/\r\n/g, "\n");
  content = replaceRequired(
    content,
    IMPORT_ANCHOR,
    `${IMPORT_ANCHOR}\nimport { envDurationMs, pipeWithWatchdog, withDeadline, writeBoundedSse } from "./stability-runtime.mjs";`,
    "Gateway stability runtime import anchor",
  );
  content = replaceRequired(
    content,
    CONFIG_ANCHOR,
    `${CONFIG_ANCHOR}\nconst inboundMediaIdleTimeoutMs = envDurationMs("WA_INBOUND_MEDIA_IDLE_TIMEOUT_MS", 30000, 5000, 300000);\nconst inboundMediaTotalTimeoutMs = envDurationMs("WA_INBOUND_MEDIA_TOTAL_TIMEOUT_MS", 900000, 30000, 3600000);\nconst maxSseBufferedBytes = Math.max(65536, Math.min(Number.parseInt(process.env.WA_MAX_SSE_BUFFER_BYTES || "1048576", 10) || 1048576, 8388608));`,
    "Gateway stability configuration anchor",
  );
  content = replaceRequired(
    content,
    HEALTH_ROUTE,
    `    if (req.method === "GET" && url.pathname === "/health") {
      const gatewayHealthy = !["error", "stopping"].includes(connectionStatus);
      sendJson(res, gatewayHealthy ? 200 : 503, {
        ok: gatewayHealthy,
        ready,
        selfJid,
        configured,
        status: connectionStatus,
        lastError,
      });
      return;
    }`,
    "Gateway health route",
  );
  content = replaceRequired(
    content,
    EPHEMERAL_UPDATE_BLOCK,
    `    for (const chat of chats || []) {
      if (chat?.id) {
        if (chat.ephemeralExpiration === 0 || chat.ephemeralExpiration === null) {
          chatEphemeral.delete(chat.id);
        } else if (chat.ephemeralExpiration !== undefined) {
          chatEphemeral.set(chat.id, chat.ephemeralExpiration);
        }
      }
    }`,
    "ephemeral chat update block",
  );
  content = replaceRequired(
    content,
    SSE_WRITE_BLOCK,
    `function sendSse(client, data) {
  const payload = \`data: \${JSON.stringify(data)}\\n\\n\`;
  if (!writeBoundedSse(client, payload, maxSseBufferedBytes)) {
    sseClients.delete(client);
  }
}`,
    "bounded SSE writer",
  );
  content = replaceRequired(
    content,
    SSE_HEARTBEAT_BLOCK,
    `      const heartbeat = setInterval(() => {
        if (!writeBoundedSse(res, ": keepalive\\n\\n", maxSseBufferedBytes)) {
          clearInterval(heartbeat);
          sseClients.delete(res);
        }
      }, 240000);`,
    "bounded SSE heartbeat",
  );
  content = replaceRequired(
    content,
    MEDIA_DOWNLOAD_BLOCK,
    `    const stream = await withDeadline(
      downloadMediaMessage(
        message,
        "stream",
        {},
        inboundMediaDownloadContext(mediaSocket, log),
      ),
      inboundMediaTotalTimeoutMs,
      "inbound media stream acquisition",
    );
    if (!stream || typeof stream.pipe !== "function") {
      throw new Error("downloadMediaMessage did not return a readable stream");
    }
    const writeStream = createWriteStream(filePath);
    const writtenBytes = await pipeWithWatchdog(stream, writeStream, {
      maxBytes,
      idleTimeoutMs: inboundMediaIdleTimeoutMs,
      totalTimeoutMs: inboundMediaTotalTimeoutMs,
      overflowMessage: \`\${kind} exceeds inbound limit \${Math.floor(maxBytes / 1024 / 1024)}MB\`,
    });`,
    "inbound media watchdog block",
  );

  const markerAnchor = 'const host = process.env.WA_GATEWAY_HOST || "127.0.0.1";';
  content = replaceRequired(
    content,
    markerAnchor,
    `${markerAnchor}\nconst ${PATCH_MARKER} = true;`,
    "Gateway stability marker anchor",
  );

  return { content, changed: true };
}
