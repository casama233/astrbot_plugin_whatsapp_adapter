import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";


const execFileAsync = promisify(execFile);
const gatewayDir = path.dirname(fileURLToPath(import.meta.url));

async function gatewaySource() {
  const source = await readFile(path.join(gatewayDir, "whatsapp-gateway-impl.mjs"), "utf8");
  return source;
}

async function gatewaySourceText() {
  return gatewaySource();
}

test("shutdown runtime exposes authenticated graceful stop and final credential flush", async () => {
  const source = await gatewaySourceText();
  assert.match(source, /url\.pathname === "\/shutdown"/);
  assert.match(source, /activeCredsSaveQueue/);
  assert.match(source, /activeSaveCreds = saveCreds/);
  assert.match(source, /activeCredsSaveFailure/);
  assert.match(source, /final credential flush failed/);
  assert.match(source, /process\.once\(signalName/);
  assert.match(source, /if \(shuttingDown\) return Promise\.resolve/);
});

test("socket generations require credential persistence to settle successfully before auth reload", async () => {
  const source = await gatewaySourceText();
  const barrierIndex = source.indexOf(
    "const previousCredsSettled = await settleWithin([activeCredsSaveQueue], 5000);",
  );
  const failureIndex = source.indexOf("if (activeCredsSaveFailure)", barrierIndex);
  const generationIndex = source.indexOf("const generation = ++socketGeneration;", failureIndex);
  const authLoadIndex = source.indexOf(
    "await useMultiFileAuthState(currentAuthDir)",
    generationIndex,
  );
  const socketCreateIndex = source.indexOf("const socketForGeneration = makeWASocket", authLoadIndex);
  assert.ok(barrierIndex >= 0);
  assert.ok(failureIndex > barrierIndex);
  assert.ok(generationIndex > failureIndex);
  assert.ok(authLoadIndex > generationIndex);
  assert.ok(socketCreateIndex > authLoadIndex);
  assert.match(
    source,
    /previous credential persistence queue did not settle before socket restart/,
  );
  assert.match(
    source,
    /previous credential persistence failed before socket restart/,
  );
  assert.match(source, /activeCredsSaveFailure = error;/);
  assert.match(source, /activeCredsSaveFailure = null;/);
  assert.match(
    source.slice(authLoadIndex, socketCreateIndex),
    /if \(shuttingDown\) return \{ ok: false, status: "stopping" \};/,
  );
});


test("complete hardened Gateway remains syntactically valid", async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "wa-gateway-shutdown-"));
  const target = path.join(dir, "generated.mjs");
  try {
    await writeFile(target, await gatewaySourceText(), "utf8");
    await execFileAsync(process.execPath, ["--check", target]);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
