import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  persistRuntimeIdentityMappings,
  readRuntimeIdentityMappings,
  rememberRuntimeScope,
  RUNTIME_IDENTITY_MAPPING_FILE,
  RuntimeIdentityRegistry,
  runtimeIdentityLookupKeys,
  runtimeScopeKeys,
} from "./runtime-identity.mjs";

test("same digits stay isolated across PN and LID namespaces", () => {
  const identities = new RuntimeIdentityRegistry();

  assert.equal(identities.same("123@s.whatsapp.net", "123@lid"), false);
  assert.notEqual(
    identities.canonical("123@s.whatsapp.net"),
    identities.canonical("123@lid"),
  );
});

test("invalid alphanumeric JIDs never collapse into numeric identities", () => {
  const identities = new RuntimeIdentityRegistry();

  assert.equal(identities.same("abc123@s.whatsapp.net", "123@s.whatsapp.net"), false);
  assert.equal(
    identities.rememberMapping("lid456@lid", "pn123@s.whatsapp.net"),
    false,
  );
  assert.deepEqual(identities.mappings(), []);
  assert.equal(
    identities.rememberMapping("456:device@lid", "123@s.whatsapp.net"),
    false,
  );
  assert.equal(
    identities.rememberMapping("456@lid", "123:7:8@hosted"),
    false,
  );
  assert.deepEqual(identities.mappings(), []);
});

test("hosted and standard domains share their own identity namespace", () => {
  const identities = new RuntimeIdentityRegistry();

  assert.equal(identities.same("123@s.whatsapp.net", "123@hosted"), true);
  assert.equal(identities.same("456@lid", "456@hosted.lid"), true);
  assert.equal(identities.same("123@hosted", "123@hosted.lid"), false);
});

test("mention lookup never exposes an unresolved LID as a phone alias", () => {
  const identities = new RuntimeIdentityRegistry();
  assert.deepEqual(runtimeIdentityLookupKeys(identities, "123@lid"), [
    "123@lid",
    "123@hosted.lid",
  ]);

  identities.rememberMapping("123@lid", "456@hosted");
  const mappedKeys = runtimeIdentityLookupKeys(identities, "123@hosted.lid");
  assert.ok(mappedKeys.includes("123@lid"));
  assert.ok(mappedKeys.includes("456@s.whatsapp.net"));
  assert.ok(mappedKeys.includes("456"));
  assert.ok(mappedKeys.includes("+456"));
});

test("explicit mappings bridge PN and LID aliases without merging unrelated IDs", () => {
  const identities = new RuntimeIdentityRegistry();
  identities.rememberMapping("456:9@hosted.lid", "123:4@hosted");

  assert.equal(identities.same("456@lid", "123@s.whatsapp.net"), true);
  assert.equal(identities.same("456@hosted.lid", "123@hosted"), true);
  assert.equal(identities.same("456@lid", "456@s.whatsapp.net"), false);
  assert.deepEqual(identities.mappings(), [
    { lidJid: "456@hosted.lid", pnJid: "123@hosted" },
  ]);

  const keys = runtimeScopeKeys(identities, ["chat", "456@lid", "message"]);
  assert.ok(keys.includes(JSON.stringify(["chat", "123@s.whatsapp.net", "message"])));
  assert.ok(keys.includes(JSON.stringify(["chat", "456@hosted.lid", "message"])));
});

test("dedup finds a LID event after its PN mapping becomes known", () => {
  const identities = new RuntimeIdentityRegistry();
  const seen = new Map();

  assert.equal(
    rememberRuntimeScope(seen, identities, ["group@g.us", "456@lid", "message-id"]),
    false,
  );
  identities.rememberMapping("456@hosted.lid", "123@hosted");
  assert.equal(
    rememberRuntimeScope(
      seen,
      identities,
      ["group@g.us", "123@s.whatsapp.net", "message-id"],
    ),
    true,
  );
  assert.equal(
    rememberRuntimeScope(
      seen,
      identities,
      ["other-group@g.us", "123@s.whatsapp.net", "message-id"],
    ),
    false,
  );
});

test("full-JID mapping state uses the shared versioned schema and atomic replacement", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "wa-runtime-identity-"));
  try {
    await persistRuntimeIdentityMappings(directory, [
      { lidJid: "456@hosted.lid", pnJid: "123@hosted" },
      { lidJid: "789@lid", pnJid: "987@s.whatsapp.net" },
      { lidJid: "invalid@s.whatsapp.net", pnJid: "ignored@s.whatsapp.net" },
    ]);

    const document = JSON.parse(
      await readFile(path.join(directory, RUNTIME_IDENTITY_MAPPING_FILE), "utf8"),
    );
    assert.deepEqual(document, {
      version: 1,
      lidToPn: {
        "456@hosted.lid": "123@hosted",
        "789@lid": "987@s.whatsapp.net",
      },
    });
    assert.deepEqual(await readRuntimeIdentityMappings(directory), [
      { lidJid: "456@hosted.lid", pnJid: "123@hosted" },
      { lidJid: "789@lid", pnJid: "987@s.whatsapp.net" },
    ]);

    await persistRuntimeIdentityMappings(directory, [
      { lidJid: "321@lid", pnJid: "654@s.whatsapp.net" },
    ]);
    assert.deepEqual(await readRuntimeIdentityMappings(directory), [
      { lidJid: "321@lid", pnJid: "654@s.whatsapp.net" },
      { lidJid: "456@hosted.lid", pnJid: "123@hosted" },
      { lidJid: "789@lid", pnJid: "987@s.whatsapp.net" },
    ]);
    assert.deepEqual(await readdir(directory), [RUNTIME_IDENTITY_MAPPING_FILE]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
