import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import { patchGatewayGroupNames } from "./group-name-compat.mjs";
import { patchGatewayMemberTags } from "./member-tag-compat.mjs";
import { patchGatewayPrivateMediaBursts } from "./private-media-burst-compat.mjs";
import { patchGatewaySecurity } from "./security-hardening.mjs";
import { patchGatewayShutdown } from "./shutdown-hardening.mjs";
import { patchGatewayStability } from "./stability-hardening.mjs";

const execFileAsync = promisify(execFile);
const gatewayDir = path.dirname(fileURLToPath(import.meta.url));

async function patchedSource() {
  const source = await readFile(path.join(gatewayDir, "whatsapp-gateway-impl.mjs"), "utf8");
  const group = patchGatewayGroupNames(source);
  const member = patchGatewayMemberTags(group.content);
  const media = patchGatewayPrivateMediaBursts(member.content);
  const stability = patchGatewayStability(media.content);
  const shutdown = patchGatewayShutdown(stability.content);
  return patchGatewaySecurity(shutdown.content).content;
}

test("shutdown patch exposes authenticated graceful stop and final credential flush", async () => {
  const source = await patchedSource();
  assert.match(source, /url\.pathname === "\/shutdown"/);
  assert.match(source, /activeCredsSaveQueue/);
  assert.match(source, /activeSaveCreds = saveCreds/);
  assert.match(source, /final credential flush failed/);
  assert.match(source, /process\.once\(signalName/);
  assert.match(source, /if \(shuttingDown\) return Promise\.resolve/);
});

test("shutdown patch is idempotent and portable across CRLF input", async () => {
  const source = await readFile(path.join(gatewayDir, "whatsapp-gateway-impl.mjs"), "utf8");
  const group = patchGatewayGroupNames(source.replace(/\r?\n/g, "\r\n"));
  const member = patchGatewayMemberTags(group.content);
  const media = patchGatewayPrivateMediaBursts(member.content);
  const stability = patchGatewayStability(media.content);
  const first = patchGatewayShutdown(stability.content);
  assert.equal(first.changed, true);
  const second = patchGatewayShutdown(first.content);
  assert.equal(second.changed, false);
  assert.equal(second.content, first.content);
});

test("complete hardened Gateway remains syntactically valid", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "wa-gateway-shutdown-"));
  const target = path.join(dir, "generated.mjs");
  try {
    await writeFile(target, await patchedSource(), "utf8");
    await execFileAsync(process.execPath, ["--check", target]);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("Gateway wrapper applies shutdown hardening before security", async () => {
  const wrapper = await readFile(path.join(gatewayDir, "whatsapp-gateway.mjs"), "utf8");
  const stabilityIndex = wrapper.indexOf("patchGatewayStability(privateMediaPatched.content)");
  const shutdownIndex = wrapper.indexOf("patchGatewayShutdown(stabilityPatched.content)");
  const securityIndex = wrapper.indexOf("patchGatewaySecurity(shutdownPatched.content)");
  assert.ok(stabilityIndex >= 0);
  assert.ok(shutdownIndex > stabilityIndex);
  assert.ok(securityIndex > shutdownIndex);
});
