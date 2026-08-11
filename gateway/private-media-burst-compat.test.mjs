import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { patchGatewayGroupNames } from "./group-name-compat.mjs";
import { patchGatewayPrivateMediaBursts } from "./private-media-burst-compat.mjs";


async function currentGatewaySource() {
  return readFile(new URL("./whatsapp-gateway-impl.mjs", import.meta.url), "utf8");
}


test("private media burst patch applies after the existing group-name patch", async () => {
  const source = await currentGatewaySource();
  const grouped = patchGatewayGroupNames(source);
  const result = patchGatewayPrivateMediaBursts(grouped.content);

  assert.equal(result.changed, true);
  assert.match(result.content, /const astrbotPrivateMediaBurstCompatibility = true;/);
  assert.match(result.content, /async function flushAlbumBuffer\(/);
  assert.match(result.content, /Math\.abs\(timestampMs - buffer\.lastTimestampMs\) > debounceMs/);
  assert.match(result.content, /albumBuffers\.has\(bufferKey\) && !albumCandidate/);
  assert.match(result.content, /await flushAlbumBuffer\(bufferKey, expectedGeneration, eventSocket\);/);
  assert.match(result.content, /await scheduleAlbumItem\(item, expectedGeneration, eventSocket\);/);
});


test("private media burst patch is idempotent", async () => {
  const source = await currentGatewaySource();
  const grouped = patchGatewayGroupNames(source);
  const first = patchGatewayPrivateMediaBursts(grouped.content);
  const second = patchGatewayPrivateMediaBursts(first.content);

  assert.equal(first.changed, true);
  assert.equal(second.changed, false);
  assert.equal(second.content, first.content);
});
