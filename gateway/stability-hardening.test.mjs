import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { patchGatewayStability } from "./stability-hardening.mjs";

const gatewayDir = path.dirname(fileURLToPath(import.meta.url));

async function implementationSource() {
  return readFile(path.join(gatewayDir, "whatsapp-gateway-impl.mjs"), "utf8");
}

test("stability patch makes terminal Gateway errors fail health checks", async () => {
  const source = await implementationSource();
  for (const candidate of [source, source.replace(/\r?\n/g, "\r\n")]) {
    const patched = patchGatewayStability(candidate);
    assert.equal(patched.changed, true);
    assert.match(patched.content, /const gatewayHealthy = connectionStatus !== "error";/);
    assert.match(patched.content, /sendJson\(res, gatewayHealthy \? 200 : 503,/);
    assert.match(patched.content, /status: connectionStatus,/);
    assert.match(patched.content, /lastError,/);

    const second = patchGatewayStability(patched.content);
    assert.equal(second.changed, false);
    assert.equal(second.content, patched.content);
  }
});

test("stability patch deletes disabled ephemeral settings instead of retaining stale entries", async () => {
  const patched = patchGatewayStability(await implementationSource());
  const deleteIndex = patched.content.indexOf(
    'if (chat.ephemeralExpiration === 0 || chat.ephemeralExpiration === null)',
  );
  const setIndex = patched.content.indexOf(
    'else if (chat.ephemeralExpiration !== undefined)',
    deleteIndex,
  );
  assert.ok(deleteIndex >= 0);
  assert.ok(setIndex > deleteIndex);
});

test("stability patch bounds inbound media acquisition and stream stalls", async () => {
  const patched = patchGatewayStability(await implementationSource());
  assert.match(patched.content, /inboundMediaIdleTimeoutMs/);
  assert.match(patched.content, /inboundMediaTotalTimeoutMs/);
  assert.match(patched.content, /await withDeadline\(/);
  assert.match(patched.content, /await pipeWithWatchdog\(/);
});

test("stability patch bounds each SSE client buffer", async () => {
  const patched = patchGatewayStability(await implementationSource());
  assert.match(patched.content, /maxSseBufferedBytes/);
  assert.match(patched.content, /writeBoundedSse\(client, payload, maxSseBufferedBytes\)/);
  assert.match(patched.content, /writeBoundedSse\(res, ": keepalive\\n\\n", maxSseBufferedBytes\)/);
});

test("Gateway wrapper writes generated runtime atomically", async () => {
  const wrapper = await readFile(path.join(gatewayDir, "whatsapp-gateway.mjs"), "utf8");
  assert.match(wrapper, /\.tmp-\$\{process\.pid\}-\$\{randomUUID\(\)\}/);
  assert.match(wrapper, /await rename\(temporaryPath, generatedPath\);/);
  assert.match(wrapper, /patchGatewayStability\(privateMediaPatched\.content\)/);
});
