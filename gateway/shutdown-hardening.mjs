const PATCH_MARKER = "astrbotGatewayShutdownHardening";

function replaceRequired(source, needle, replacement, label) {
  if (!source.includes(needle)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(needle, replacement);
}

const IMPORT_ANCHOR = 'import { buildWhatsAppProxyConfig } from "./proxy-compat.mjs";';
const RUNTIME_QUEUE_ANCHOR = "let runtimeIdentityPersistQueue = Promise.resolve();";
const STATUS_ROUTE_ANCHOR = `    if (req.method === "GET" && url.pathname === "/status") {`;
const SOCKET_START_BLOCK = `function requestSocketStart(opts = {}) {
  return enqueueSocketTransition("start", () => startSocket(opts));
}`;
const START_SOCKET_BLOCK = `async function startSocket(opts = {}) {
  const generation = ++socketGeneration;`;
const AUTH_STATE_BLOCK = `  const { state, saveCreds } = await useMultiFileAuthState(currentAuthDir);
  const socketForGeneration = makeWASocket({`;
const CREDS_QUEUE_BLOCK = `  let credsSaveQueue = Promise.resolve();
  socketForGeneration.ev.on("creds.update", () => {
    if (generation !== socketGeneration) return;
    credsSaveQueue = credsSaveQueue
      .then(() => saveCreds())
      .catch((error) => {
        if (generation !== socketGeneration || socketForGeneration !== socket) return;
        lastError = \`保存登录凭证失败: \${String(error?.message || error)}\`;
        log.error({ error, generation, sessionId: currentSessionId }, "failed to persist auth credentials");
      });
  });`;
const SERVER_ANCHOR = 'server.on("error", (error) => {';
const STARTUP_FAILURE_BLOCK = `initializeAuthSession().then(() => requestSocketStart()).catch((error) => {
  connectionStatus = "error";
  lastError = \`Gateway 启动失败: \${String(error?.message || error)}\`;
  log.error({ error }, "WhatsApp Gateway startup failed");
  process.exitCode = 1;
});`;

export function patchGatewayShutdown(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  let content = source.replace(/\r\n/g, "\n");
  content = replaceRequired(
    content,
    IMPORT_ANCHOR,
    `${IMPORT_ANCHOR}\nimport { settleWithin } from "./stability-runtime.mjs";`,
    "shutdown runtime import anchor",
  );
  content = replaceRequired(
    content,
    RUNTIME_QUEUE_ANCHOR,
    `${RUNTIME_QUEUE_ANCHOR}\nlet activeCredsSaveQueue = Promise.resolve();\nlet activeSaveCreds = null;\nlet shuttingDown = false;`,
    "shutdown persistence queue anchor",
  );
  content = replaceRequired(
    content,
    SOCKET_START_BLOCK,
    `function requestSocketStart(opts = {}) {
  if (shuttingDown) return Promise.resolve({ ok: false, status: "stopping" });
  return enqueueSocketTransition("start", () => startSocket(opts));
}`,
    "socket start shutdown guard",
  );
  content = replaceRequired(
    content,
    START_SOCKET_BLOCK,
    `async function startSocket(opts = {}) {
  if (shuttingDown) return { ok: false, status: "stopping" };
  // A reconnect must never load the auth directory while the previous socket
  // generation is still persisting credentials into it. Treat credential
  // durability as a barrier between generations rather than best-effort I/O.
  const previousCredsSettled = await settleWithin([activeCredsSaveQueue], 5000);
  if (!previousCredsSettled) {
    throw new Error("previous credential persistence queue did not settle before socket restart");
  }
  activeSaveCreds = null;
  activeCredsSaveQueue = Promise.resolve();
  if (shuttingDown) return { ok: false, status: "stopping" };
  const generation = ++socketGeneration;`,
    "cross-generation credential persistence barrier",
  );
  content = replaceRequired(
    content,
    AUTH_STATE_BLOCK,
    `  const { state, saveCreds } = await useMultiFileAuthState(currentAuthDir);
  // Shutdown can begin while auth state is being read from disk. Do not create
  // a fresh WebSocket after the graceful-stop sequence has already started.
  if (shuttingDown) return { ok: false, status: "stopping" };
  const socketForGeneration = makeWASocket({`,
    "post-auth shutdown guard",
  );
  content = replaceRequired(
    content,
    CREDS_QUEUE_BLOCK,
    `  let credsSaveQueue = Promise.resolve();
  activeCredsSaveQueue = credsSaveQueue;
  activeSaveCreds = saveCreds;
  socketForGeneration.ev.on("creds.update", () => {
    if (generation !== socketGeneration) return;
    activeCredsSaveQueue = credsSaveQueue = credsSaveQueue
      .then(() => saveCreds())
      .catch((error) => {
        if (generation !== socketGeneration || socketForGeneration !== socket) return;
        lastError = \`保存登录凭证失败: \${String(error?.message || error)}\`;
        log.error({ error, generation, sessionId: currentSessionId }, "failed to persist auth credentials");
      });
  });`,
    "credential persistence queue",
  );
  content = replaceRequired(
    content,
    STATUS_ROUTE_ANCHOR,
    `    if (req.method === "POST" && url.pathname === "/shutdown") {
      sendJson(res, 202, { ok: true, status: "stopping" });
      setImmediate(() => void gracefulShutdown("api_shutdown", 0));
      return;
    }
${STATUS_ROUTE_ANCHOR}`,
    "graceful shutdown route",
  );
  content = replaceRequired(
    content,
    SERVER_ANCHOR,
    `async function gracefulShutdown(reason = "shutdown", exitCode = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  connectionStatus = "stopping";
  ready = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  stopPresenceTimer();
  clearAlbumBuffers();
  broadcast({ type: "status", status: connectionStatus, ready: false, reason });

  await settleWithin([socketTransition], 1500);
  if (activeSaveCreds) {
    activeCredsSaveQueue = activeCredsSaveQueue
      .catch(() => {})
      .then(() => activeSaveCreds())
      .catch((error) => log.warn({ error }, "final credential flush failed"));
  }
  const persisted = await settleWithin([
    activeCredsSaveQueue,
    runtimeIdentityPersistQueue,
  ], 3000);
  if (!persisted) log.warn({ reason }, "Gateway persistence flush timed out during shutdown");

  ++socketGeneration;
  const retiringSocket = socket;
  if (retiringSocket?.ev?.removeAllListeners) {
    try { retiringSocket.ev.removeAllListeners(); } catch {}
  }
  if (retiringSocket?.end) {
    try { retiringSocket.end(undefined); } catch {}
  }
  socket = null;
  for (const client of sseClients) {
    try { client.end(); } catch {}
  }
  sseClients.clear();

  await settleWithin([
    new Promise((resolve) => {
      try { server.close(() => resolve()); } catch { resolve(); }
    }),
  ], 2000);
  process.exitCode = exitCode;
}

for (const signalName of ["SIGTERM", "SIGINT"]) {
  process.once(signalName, () => void gracefulShutdown(signalName, 0));
}

${SERVER_ANCHOR}`,
    "graceful shutdown lifecycle anchor",
  );
  content = replaceRequired(
    content,
    STARTUP_FAILURE_BLOCK,
    `initializeAuthSession().then(() => requestSocketStart()).catch((error) => {
  connectionStatus = "error";
  lastError = \`Gateway 启动失败: \${String(error?.message || error)}\`;
  log.error({ error }, "WhatsApp Gateway startup failed");
  void gracefulShutdown("startup_failure", 1);
});`,
    "startup failure shutdown",
  );

  const markerAnchor = 'const host = process.env.WA_GATEWAY_HOST || "127.0.0.1";';
  content = replaceRequired(
    content,
    markerAnchor,
    `${markerAnchor}\nconst ${PATCH_MARKER} = true;`,
    "shutdown marker anchor",
  );

  return { content, changed: true };
}
