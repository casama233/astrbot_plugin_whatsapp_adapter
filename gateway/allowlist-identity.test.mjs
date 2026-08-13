import assert from "node:assert/strict";
import test from "node:test";

import {
  allowedByIdentityList,
  normalizeAllowlistIdentity,
  normalizeGroupAllowlistValue,
  resolveAllowlistPnToLid,
} from "./allowlist-identity.mjs";
import { RuntimeIdentityRegistry } from "./runtime-identity.mjs";

test("allowlist normalization accepts only strict bare phone numbers", () => {
  assert.equal(normalizeAllowlistIdentity("+123"), "123@s.whatsapp.net");
  assert.equal(normalizeAllowlistIdentity("123"), "123@s.whatsapp.net");
  assert.equal(normalizeAllowlistIdentity("abc123"), "abc123");
  assert.equal(normalizeAllowlistIdentity("123-456"), "123-456");
  assert.equal(
    normalizeAllowlistIdentity("abc123@s.whatsapp.net"),
    "abc123@s.whatsapp.net",
  );
});

test("allowlist keeps PN and LID separate until an explicit mapping exists", () => {
  const identities = new RuntimeIdentityRegistry();
  assert.equal(allowedByIdentityList("123@lid", ["123"], identities), false);
  assert.equal(allowedByIdentityList("123@hosted.lid", ["123@lid"], identities), true);

  identities.rememberMapping("123@lid", "456@s.whatsapp.net");
  assert.equal(allowedByIdentityList("123@hosted.lid", ["+456"], identities), true);
});

test("group allowlist accepts modern and legacy hyphenated group ids", () => {
  assert.equal(normalizeGroupAllowlistValue("120363001"), "120363001@g.us");
  assert.equal(normalizeGroupAllowlistValue("120363001@g.us"), "120363001@g.us");
  assert.equal(normalizeGroupAllowlistValue("123456789-123345"), "123456789-123345@g.us");
  assert.equal(
    normalizeGroupAllowlistValue("123456789-123345@g.us"),
    "123456789-123345@g.us",
  );
  assert.equal(normalizeGroupAllowlistValue("---"), "---");
  assert.equal(normalizeGroupAllowlistValue("12-34-56"), "12-34-56");
});

test("hosted allowlist prewarming preserves hosted PN and LID domains", async () => {
  const calls = [];
  const socket = {
    async onWhatsApp(value) {
      calls.push(["onWhatsApp", value]);
      return [{ exists: true, jid: `${value}@s.whatsapp.net` }];
    },
    signalRepository: {
      lidMapping: {
        async getLIDForPN(value) {
          calls.push(["getLIDForPN", value]);
          return value.endsWith("@hosted") ? "456:99@hosted.lid" : "789:4@lid";
        },
      },
    },
  };

  assert.deepEqual(
    await resolveAllowlistPnToLid("123:7@hosted", socket),
    { lidJid: "456@hosted.lid", pnJid: "123@hosted" },
  );
  assert.deepEqual(calls, [["getLIDForPN", "123@hosted"]]);

  calls.length = 0;
  assert.deepEqual(
    await resolveAllowlistPnToLid("321@s.whatsapp.net", socket),
    { lidJid: "789@lid", pnJid: "321@s.whatsapp.net" },
  );
  assert.deepEqual(calls, [
    ["onWhatsApp", "321"],
    ["getLIDForPN", "321@s.whatsapp.net"],
  ]);
});

test("allowlist LID prewarming rejects malformed PN and LID transports", async () => {
  let called = false;
  const socket = {
    signalRepository: {
      lidMapping: {
        async getLIDForPN() {
          called = true;
          return "456:device@lid";
        },
      },
    },
  };
  assert.equal(
    await resolveAllowlistPnToLid("abc123@hosted", socket),
    null,
  );
  assert.equal(called, false);
  assert.equal(
    await resolveAllowlistPnToLid("123@hosted", socket),
    null,
  );
});
