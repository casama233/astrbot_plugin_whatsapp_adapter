import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  patchBaileysEphemeralMetadata,
  patchInstalledBaileys,
} from "./patch-baileys-ephemeral.mjs";

const RC14_SNIPPET = `
innerMessage[key].contextInfo = {
  ...((innerMessage[key]).contextInfo || {}),
  expiration: options.ephemeralExpiration || WA_DEFAULT_EPHEMERAL
  //ephemeralSettingTimestamp: options.ephemeralOptions.eph_setting_ts?.toString()
}
`;

test("adds the missing ephemeral setting timestamp to Baileys rc14", () => {
  const result = patchBaileysEphemeralMetadata(RC14_SNIPPET);

  assert.equal(result.changed, true);
  assert.match(
    result.content,
    /expiration: options\.ephemeralExpiration \|\| WA_DEFAULT_EPHEMERAL,/,
  );
  assert.match(
    result.content,
    /ephemeralSettingTimestamp: options\.ephemeralSettingTimestamp \|\| unixTimestampSeconds\(\)/,
  );
  assert.doesNotMatch(result.content, /\/\/ephemeralSettingTimestamp/);
});

test("is idempotent after the patch is present", () => {
  const first = patchBaileysEphemeralMetadata(RC14_SNIPPET);
  const second = patchBaileysEphemeralMetadata(first.content);

  assert.equal(second.changed, false);
  assert.equal(second.content, first.content);
});

test("patches an installed Baileys file through an explicit target", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "baileys-ephemeral-"));
  const target = path.join(directory, "messages.js");

  try {
    await writeFile(target, RC14_SNIPPET, "utf8");

    const first = await patchInstalledBaileys({ targetPath: target });
    const second = await patchInstalledBaileys({ targetPath: target });
    const patched = await readFile(target, "utf8");

    assert.deepEqual(first, { checked: 1, changed: 1 });
    assert.deepEqual(second, { checked: 1, changed: 0 });
    assert.match(patched, /ephemeralSettingTimestamp/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("fails loudly when the pinned Baileys layout changes", () => {
  assert.throws(
    () => patchBaileysEphemeralMetadata("export const unrelated = true;"),
    /timestamp marker was not found/,
  );
});
