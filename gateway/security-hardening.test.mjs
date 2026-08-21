import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { patchGatewayGroupNames } from "./group-name-compat.mjs";
import { patchGatewayPrivateMediaBursts } from "./private-media-burst-compat.mjs";
import { patchGatewaySecurity } from "./security-hardening.mjs";

const fixture = `import { buildWhatsAppProxyConfig } from "./proxy-compat.mjs";
const logLevel = process.env.WA_LOG_LEVEL || "info";
function broadcast(data) {
  for (const client of sseClients) {
    sendSse(client, data);
  }
}
async function handleIncomingMessage(primary) {
  const chatJid = primary.key.remoteJid;
  const senderJid = primary.key.participant;
  if (!configured) {
    log.warn({ chatJid, senderJid, messageId: primary.key.id }, "Gateway not yet configured; passing message through without allowlist check");
  }
  let allowedResult = configured ? allowedMessageResult(chatJid, senderJid, primary) : { allowed: true, reason: "not_yet_configured", senderPhone: "" };
}
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, \`http://\${req.headers.host}\`);
    if (req.method === "GET" && url.pathname === "/events") {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
      });
    }
    if (req.method === "POST" && url.pathname === "/send/media") {
      const body = await readJson(req);
      if (!body.pathOrUrl) {
        sendJson(res, 400, { ok: false, error: "pathOrUrl is required" });
        return;
      }
      if (!/^https?:\\/\\//i.test(body.pathOrUrl)) await stat(normalizeLocalMediaPath(body.pathOrUrl));
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
  } catch {}
});`;

test("patch injects authentication, SSE limit, fail-closed policy, and safe media handling", () => {
  const result = patchGatewaySecurity(fixture);
  assert.equal(result.changed, true);
  assert.match(result.content, /WA_GATEWAY_TOKEN/);
  assert.match(result.content, /isAuthorizedGatewayRequest/);
  assert.match(result.content, /too many event stream clients/);
  assert.match(result.content, /dropping inbound message until policy is loaded/);
  assert.match(result.content, /prepareSafeMediaSource/);
  assert.match(result.content, /preparedMedia\.cleanup/);
  assert.doesNotMatch(result.content, /passing message through without allowlist check/);
});

test("patch redacts rejected SSE payloads", () => {
  const result = patchGatewaySecurity(fixture);
  const broadcastStart = result.content.indexOf("function broadcast(data)");
  const broadcastEnd = result.content.indexOf("async function handleIncomingMessage");
  const broadcastSource = result.content.slice(broadcastStart, broadcastEnd);
  assert.match(broadcastSource, /type: "rejected"/);
  assert.doesNotMatch(broadcastSource, /senderPhone/);
  assert.doesNotMatch(broadcastSource, /senderJid/);
  assert.doesNotMatch(broadcastSource, /text:/);
});

test("patch is idempotent", () => {
  const once = patchGatewaySecurity(fixture);
  const twice = patchGatewaySecurity(once.content);
  assert.equal(twice.changed, false);
  assert.equal(twice.content, once.content);
});

test("security patch applies after the real compatibility patch chain", async () => {
  const source = await readFile(
    new URL("./whatsapp-gateway-impl.mjs", import.meta.url),
    "utf8",
  );
  const groupPatched = patchGatewayGroupNames(source);
  const privateMediaPatched = patchGatewayPrivateMediaBursts(groupPatched.content);
  const secured = patchGatewaySecurity(privateMediaPatched.content);

  assert.equal(secured.changed, true);
  assert.match(secured.content, /const astrbotGatewaySecurityHardening = true;/);
  assert.match(secured.content, /prepareSafeMediaSource/);
  assert.match(secured.content, /isAuthorizedGatewayRequest/);
  assert.doesNotMatch(secured.content, /passing message through without allowlist check/);
});
