import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const source = await readFile(new URL("./whatsapp-gateway-impl.mjs", import.meta.url), "utf8");

test("runtime enforces authentication, SSE limit, fail-closed policy, and safe media handling", () => {
  const result = { content: source };

  assert.match(result.content, /WA_GATEWAY_TOKEN/);
  assert.match(result.content, /isAuthorizedGatewayRequest/);
  assert.match(result.content, /too many event stream clients/);
  assert.match(result.content, /dropping inbound message until policy is loaded/);
  assert.match(result.content, /prepareSafeMediaSource/);
  assert.match(result.content, /preparedMedia\.cleanup/);
  assert.match(result.content, /renderOutboundMentionNames/);
  assert.match(result.content, /renderedCaption/);
  assert.doesNotMatch(result.content, /passing message through without allowlist check/);
});

test("runtime redacts rejected SSE payloads", () => {
  const result = { content: source };
  const broadcastStart = result.content.indexOf("function broadcast(data)");
  const broadcastEnd = result.content.indexOf("\n}\n", broadcastStart) + 3;
  const broadcastSource = result.content.slice(broadcastStart, broadcastEnd);
  assert.match(broadcastSource, /type: "rejected"/);
  assert.doesNotMatch(broadcastSource, /senderPhone/);
  assert.doesNotMatch(broadcastSource, /senderJid/);
  assert.doesNotMatch(broadcastSource, /text:/);
});


test("canonical runtime includes security controls", async () => {
  const source = await readFile(
    new URL("./whatsapp-gateway-impl.mjs", import.meta.url),
    "utf8",
  );
  const secured = { content: source };

  assert.match(secured.content, /prepareSafeMediaSource/);
  assert.match(secured.content, /isAuthorizedGatewayRequest/);
  assert.doesNotMatch(secured.content, /passing message through without allowlist check/);
});
