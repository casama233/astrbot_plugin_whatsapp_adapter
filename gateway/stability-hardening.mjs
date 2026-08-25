const PATCH_MARKER = "astrbotGatewayStabilityHardening";

function replaceRequired(source, needle, replacement, label) {
  if (!source.includes(needle)) {
    throw new Error(`Unsupported WhatsApp Gateway layout: ${label} was not found.`);
  }
  return source.replace(needle, replacement);
}

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

export function patchGatewayStability(source) {
  if (source.includes(`const ${PATCH_MARKER} = true;`)) {
    return { content: source, changed: false };
  }

  let content = source;
  content = replaceRequired(
    content,
    HEALTH_ROUTE,
    `    if (req.method === "GET" && url.pathname === "/health") {
      const gatewayHealthy = connectionStatus !== "error";
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

  const markerAnchor = 'const host = process.env.WA_GATEWAY_HOST || "127.0.0.1";';
  content = replaceRequired(
    content,
    markerAnchor,
    `${markerAnchor}\nconst ${PATCH_MARKER} = true;`,
    "Gateway stability marker anchor",
  );

  return { content, changed: true };
}
