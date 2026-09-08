import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { prepareSafeMediaSource } from "./security-runtime.mjs";

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), "wa-media-policy-"));
  const dataDir = path.join(root, "data");
  const pluginDir = path.join(dataDir, "plugin_data", "astrbot_plugin_whatsapp_adapter");
  const tempDir = path.join(dataDir, "temp");
  const authDir = path.join(pluginDir, "whatsapp-auth");
  await mkdir(tempDir, { recursive: true });
  await mkdir(authDir, { recursive: true });
  const keys = ["WA_DATA_DIR", "WA_AUTH_DIR", "WA_MEDIA_ALLOWED_ROOTS"];
  const previous = new Map(keys.map((key) => [key, process.env[key]]));
  process.env.WA_DATA_DIR = pluginDir;
  process.env.WA_AUTH_DIR = authDir;
  process.env.WA_MEDIA_ALLOWED_ROOTS = dataDir;
  t.after(async () => {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(root, { recursive: true, force: true });
  });
  const file = async (relative) => {
    const target = path.join(root, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, "synthetic fixture; no real credentials");
    return target;
  };
  return { root, dataDir, pluginDir, tempDir, authDir, file };
}

// Top-level tests run sequentially: each fixture restores its environment.
test("AstrBot plugin media and ordinary documents remain supported", async (t) => {
  const f = await fixture(t);
  for (const relative of [
    "data/temp/image.png",
    "data/temp_images/render.png",
    "data/plugin_data/drawing/output/image.png",
    "data/plugin_data/astrbot_plugin_whatsapp_adapter/media/inbound.jpg",
    "data/plugin_data/reports/report.json",
  ]) {
    const source = await f.file(relative);
    const prepared = await prepareSafeMediaSource(source, { tempDir: f.tempDir });
    assert.equal(prepared.pathOrUrl, await realpath(source));
    await prepared.cleanup();
    assert.ok(await readFile(source)); // Never delete a caller-owned file.
  }
});

test("default and secondary auth directories override broad allowed roots", async (t) => {
  const f = await fixture(t);
  for (const directory of ["whatsapp-auth", "whatsapp-auth-second"]) {
    for (const name of ["creds.json", "session-1/pre-key-1.json", ".active-session.json"]) {
      const source = await f.file(`data/plugin_data/astrbot_plugin_whatsapp_adapter/${directory}/${name}`);
      await assert.rejects(
        prepareSafeMediaSource(source, { tempDir: f.tempDir }),
        /protected WhatsApp credential directory/,
      );
    }
  }
});

test("custom auth remains protected even when explicitly allowed", async (t) => {
  const f = await fixture(t);
  const source = await f.file("custom-login/creds.json");
  process.env.WA_AUTH_DIR = path.dirname(source);
  process.env.WA_MEDIA_ALLOWED_ROOTS = path.dirname(source);
  await assert.rejects(prepareSafeMediaSource(source, { tempDir: f.tempDir }), /protected WhatsApp/);
});

test("Gateway default auth path is protected when WA_AUTH_DIR is unset", async (t) => {
  const f = await fixture(t);
  delete process.env.WA_AUTH_DIR;
  const source = await f.file("data/plugin_data/astrbot_plugin_whatsapp_adapter/whatsapp-auth/creds.json");
  await assert.rejects(prepareSafeMediaSource(source, { tempDir: f.tempDir }), /protected WhatsApp/);
});

test("an explicit empty allowlist supports temp only", async (t) => {
  const f = await fixture(t);
  process.env.WA_MEDIA_ALLOWED_ROOTS = "";
  const temporary = await f.file("data/temp/image.png");
  const pluginOutput = await f.file("data/plugin_data/drawing/image.png");
  await prepareSafeMediaSource(temporary, { tempDir: f.tempDir });
  await assert.rejects(prepareSafeMediaSource(pluginOutput, { tempDir: f.tempDir }), /outside the allowed/);
});

test("explicit external output roots work but prefix siblings do not", async (t) => {
  const f = await fixture(t);
  process.env.WA_MEDIA_ALLOWED_ROOTS = path.join(f.root, "exports");
  const allowed = await f.file("exports/report.json");
  const outside = await f.file("exports-private/report.json");
  await prepareSafeMediaSource(allowed, { tempDir: f.tempDir });
  await assert.rejects(prepareSafeMediaSource(outside, { tempDir: f.tempDir }), /outside the allowed/);
});

test("symlink aliases do not make credential directories sendable", async (t) => {
  const f = await fixture(t);
  await f.file("data/plugin_data/astrbot_plugin_whatsapp_adapter/whatsapp-auth/creds.json");
  const alias = path.join(f.tempDir, "alias");
  await symlink(f.authDir, alias, process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(
    prepareSafeMediaSource(path.join(alias, "creds.json"), { tempDir: f.tempDir }),
    /protected WhatsApp/,
  );
});

test("a configured auth symlink protects its actual target", async (t) => {
  const f = await fixture(t);
  const source = await f.file("data/actual-login/creds.json");
  const alias = path.join(f.root, "login-alias");
  await symlink(path.dirname(source), alias, process.platform === "win32" ? "junction" : "dir");
  process.env.WA_AUTH_DIR = alias;
  await assert.rejects(prepareSafeMediaSource(source, { tempDir: f.tempDir }), /protected WhatsApp/);
});

test("file URLs and private remote URLs remain rejected", async (t) => {
  const f = await fixture(t);
  await assert.rejects(prepareSafeMediaSource("file:///secret", { tempDir: f.tempDir }), /file:\/\//);
  await assert.rejects(prepareSafeMediaSource("http://127.0.0.1/secret", { tempDir: f.tempDir }), /non-public/);
});
