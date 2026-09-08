import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";



async function currentGatewaySource() {
  return readFile(new URL("./whatsapp-gateway-impl.mjs", import.meta.url), "utf8");
}


async function gatewaySource() {
  const source = await currentGatewaySource();
  return { content: source };
}


test("private media burst runtime preserves captioned direct-chat albums", async () => {
  const result = await gatewaySource();

  assert.match(result.content, /hasCaption && chatJid\.endsWith\("@g\.us"\)/);
  assert.match(result.content, /caption: textFromMessage\(item\.message\) \|\| ""/);
  assert.match(result.content, /\.\.\.albumMediaMetadata\(albumItem, albumItems\.length\)/);
  assert.match(result.content, /albumMentionedJids\(albumItems\)/);
  assert.match(result.content, /albumMentionedNames\(albumItems\)/);
  assert.match(result.content, /albumMentionAll\(albumItems\)/);
});


test("private media burst runtime keeps source ordering and replay protection", async () => {
  const result = await gatewaySource();

  assert.match(result.content, /async function flushAlbumBuffer\(/);
  assert.match(result.content, /Math\.abs\(timestampMs - buffer\.lastTimestampMs\) > debounceMs/);
  assert.match(result.content, /runtimeScopeKeys\(runtimeIdentities, \[expectedGeneration, chatJid, senderJid\]\)/);
  assert.match(result.content, /keys\.find\(\(key\) => albumBuffers\.has\(key\)\) \|\| keys\[0\]/);
  assert.match(result.content, /albumBuffers\.has\(bufferKey\) && !albumCandidate/);
  assert.match(result.content, /await flushAlbumBuffer\(bufferKey, expectedGeneration, eventSocket\);/);
  assert.match(result.content, /await scheduleAlbumItem\(item, expectedGeneration, eventSocket\);/);
});


test("patched gateway remains syntactically valid", async () => {
  const result = await gatewaySource();
  const directory = await mkdtemp(path.join(os.tmpdir(), "wa-gateway-check-"));
  const target = path.join(directory, "gateway-check.mjs");
  try {
    await writeFile(target, result.content, "utf8");
    execFileSync(process.execPath, ["--check", target], { stdio: "pipe" });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
