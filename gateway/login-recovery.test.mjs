import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("..", import.meta.url));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function startGateway(t, mode) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "wa-login-test-"));
  let port = null;
  const child = spawn(process.execPath, [
    "--import", pathToFileURL(path.join(root, "tests/gateway-fixtures/register.mjs")).href,
    path.join(root, "gateway/whatsapp-gateway.mjs"),
  ], {
    cwd: root,
    env: { ...process.env, WA_GATEWAY_HOST: "127.0.0.1", WA_GATEWAY_PORT: "0",
      WA_GATEWAY_TOKEN: "synthetic-login-test-only", WA_DATA_DIR: dir,
      WA_AUTH_DIR: path.join(dir, "auth"), WA_TEST_LOGIN_FLOW: mode,
      WA_TEST_SOCKET_LOG: path.join(dir, "sockets.txt"), WA_LOG_LEVEL: "info" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  let pendingLog = "";
  child.stdout.on("data", (data) => {
    output += data;
    pendingLog += data;
    const lines = pendingLog.split("\n");
    pendingLog = lines.pop();
    for (const line of lines) {
      try { const item = JSON.parse(line); if (item.msg === "WhatsApp Gateway listening") port = item.port; } catch {}
    }
  });
  child.stderr.on("data", (data) => { output += data; });
  const exited = new Promise((resolve) => child.once("exit", resolve));
  t.after(async () => {
    child.kill();
    let timer;
    await Promise.race([exited, new Promise((resolve) => { timer = setTimeout(() => { child.kill("SIGKILL"); resolve(); }, 3000); })]);
    clearTimeout(timer);
    await rm(dir, { recursive: true, force: true });
  });
  async function request(route, method = "GET", body = undefined) {
    if (port === null) throw new Error("Gateway has not bound its port yet");
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {
      method, headers: { Authorization: "Bearer synthetic-login-test-only", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  }
  async function until(predicate) {
    for (let i = 0; i < 160; i += 1) {
      if (child.exitCode !== null) throw new Error(`Gateway exited: ${output}`);
      try { const value = await request("/status"); if (predicate(value.body)) return value.body; } catch (error) {
        if (i === 159) throw new Error(`Gateway did not become ready: ${error}; ${output}`);
      }
      await pause(50);
    }
    throw new Error(`Gateway state did not match: ${output}`);
  }
  return { request, until, dir };
}

test("expired QR refresh keeps auth and duplicate refreshes reuse the new socket", async (t) => {
  const gateway = await startGateway(t, "qr-expiry");
  const before = await gateway.until((s) => s.status === "qr_expired");
  const requests = await Promise.all([gateway.request("/restart", "POST", { expectedGeneration: before.connectionGeneration }), gateway.request("/restart", "POST", { expectedGeneration: before.connectionGeneration })]);
  assert.ok(requests.every((r) => r.status === 200));
  const after = await gateway.until((s) => s.status === "qr_pending" && s.hasQr);
  assert.equal(after.sessionId, before.sessionId);
  assert.equal(after.authDir, before.authDir);
  assert.equal(after.resetSequence, 0);
  assert.equal((await readFile(path.join(gateway.dir, "sockets.txt"), "utf8")).trim().split("\n").length, 2);
});

test("a stale page cannot reset a linked Web account whose registered flag is false", async (t) => {
  const gateway = await startGateway(t, "linked-web");
  const before = await gateway.until((s) => s.ready);
  const result = await gateway.request("/session/reset", "POST");
  assert.equal(result.status, 409);
  const after = (await gateway.request("/status")).body;
  assert.equal(after.ready, true);
  assert.equal(after.sessionId, before.sessionId);
  assert.equal(after.resetSequence, 0);
  assert.equal((await readFile(path.join(gateway.dir, "sockets.txt"), "utf8")).trim(), "1");
});

test("a linked Web account cannot request a second pairing code while reconnecting", async (t) => {
  const gateway = await startGateway(t, "linked-connecting");
  await gateway.until((s) => s.status === "starting");
  const result = await gateway.request("/pair-code", "POST", { phone: "+15555550100" });
  assert.equal(result.status, 409);
  assert.match(result.body.error, /registered or connected/);
});


test("duplicate auth resets create one fresh session and do not invalidate its QR", async (t) => {
  const gateway = await startGateway(t, "qr-expiry");
  await gateway.until((s) => s.status === "qr_expired");
  const replies = await Promise.all([gateway.request("/session/reset", "POST"), gateway.request("/session/reset", "POST")]);
  assert.ok(replies.every((r) => r.status === 200));
  const after = await gateway.until((s) => s.status === "qr_pending");
  assert.equal(after.resetSequence, 1);
  assert.equal(replies[0].body.sessionId, replies[1].body.sessionId);
  assert.equal((await readFile(path.join(gateway.dir, "sockets.txt"), "utf8")).trim().split("\n").length, 2);
});

test("a linked account is protected from reset while its transport reconnects", async (t) => {
  const gateway = await startGateway(t, "linked-connecting");
  const before = await gateway.until((s) => s.status === "starting");
  const result = await gateway.request("/session/reset", "POST");
  assert.equal(result.status, 409);
  assert.equal((await gateway.request("/status")).body.sessionId, before.sessionId);
});

test("an explicit fresh retry replaces a pending socket, and retires its QR immediately", async (t) => {
  const gateway = await startGateway(t, "qr-expiry");
  const before = await gateway.until((s) => s.status === "qr_expired");
  await gateway.request("/restart", "POST", { expectedGeneration: before.connectionGeneration });
  const pending = await gateway.until((s) => s.status === "qr_pending" && s.hasQr);
  await gateway.request("/restart", "POST", { expectedGeneration: pending.connectionGeneration });
  const after = await gateway.until((s) => s.connectionGeneration > pending.connectionGeneration && s.hasQr);
  assert.equal(after.sessionId, before.sessionId);
  assert.equal((await gateway.request("/qr")).body.qr, "fixture-qr-3");
});

test("failed auth persistence blocks reload, then retries the same credentials after storage recovers", async (t) => {
  const gateway = await startGateway(t, "qr-expiry-save-error");
  const before = await gateway.until((s) => s.status === "qr_expired");
  assert.equal((await gateway.request("/restart", "POST")).status, 500);
  const blocked = (await gateway.request("/status")).body;
  assert.equal(blocked.lastErrorCode, "auth_storage_unavailable");
  await rm(path.join(gateway.dir, "auth/creds.json"), { recursive: true });
  assert.equal((await gateway.request("/restart", "POST")).status, 200);
  const after = await gateway.until((s) => s.status === "qr_pending" && s.hasQr);
  assert.equal(after.sessionId, before.sessionId);
  assert.equal(after.lastErrorCode, null);
  assert.ok(JSON.parse(await readFile(path.join(gateway.dir, "auth/creds.json"), "utf8")).noiseKey);
});
