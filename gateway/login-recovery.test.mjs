import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("..", import.meta.url));
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function startGateway(t, mode) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "wa-login-test-"));
  const listener = createServer();
  await new Promise((resolve) => listener.listen(0, "127.0.0.1", resolve));
  const port = listener.address().port;
  await new Promise((resolve) => listener.close(resolve));
  const child = spawn(process.execPath, [
    "--import", pathToFileURL(path.join(root, "tests/gateway-fixtures/register.mjs")).href,
    path.join(root, "gateway/whatsapp-gateway.mjs"),
  ], {
    cwd: root,
    env: { ...process.env, WA_GATEWAY_HOST: "127.0.0.1", WA_GATEWAY_PORT: String(port),
      WA_GATEWAY_TOKEN: "synthetic-login-test-only", WA_DATA_DIR: dir,
      WA_AUTH_DIR: path.join(dir, "auth"), WA_TEST_LOGIN_FLOW: mode,
      WA_TEST_SOCKET_LOG: path.join(dir, "sockets.txt"), WA_LOG_LEVEL: "silent" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (data) => { output += data; });
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
  const requests = await Promise.all([gateway.request("/restart", "POST"), gateway.request("/restart", "POST")]);
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
