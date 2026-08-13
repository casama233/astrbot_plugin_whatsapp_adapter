import assert from "node:assert/strict";
import test from "node:test";

import {
  groupOwnerIdentity,
  groupParticipantIdentity,
  normalizeIdentityJid,
  phoneFromIdentity,
  participantIdentityValues,
  resolveExplicitIdentityMentions,
  senderIdentityFromKey,
} from "./identity-compat.mjs";

test("resolves Baileys 7 group sender participantAlt", () => {
  const identity = senderIdentityFromKey(
    {
      participant: "125013663469807:4@lid",
      participantAlt: "85254420939:23@s.whatsapp.net",
    },
    {
      isGroup: true,
      senderJid: "125013663469807:4@lid",
      chatJid: "120363000000000001@g.us",
    },
  );

  assert.deepEqual(identity, {
    pnJid: "85254420939@s.whatsapp.net",
    lidJid: "125013663469807@lid",
  });
});

test("resolves Baileys 7 direct sender remoteJidAlt", () => {
  const identity = senderIdentityFromKey(
    {
      remoteJid: "125013663469807@lid",
      remoteJidAlt: "85254420939@s.whatsapp.net",
    },
    {
      senderJid: "125013663469807@lid",
      chatJid: "125013663469807@lid",
    },
  );

  assert.equal(identity.pnJid, "85254420939@s.whatsapp.net");
  assert.equal(identity.lidJid, "125013663469807@lid");
});

test("keeps legacy bare participantPn compatible", () => {
  const identity = senderIdentityFromKey(
    { participant: "125013663469807@lid", participantPn: "85254420939" },
    { isGroup: true, senderJid: "125013663469807@lid" },
  );

  assert.equal(identity.pnJid, "85254420939@s.whatsapp.net");
  assert.equal(identity.lidJid, "125013663469807@lid");
});

test("resolves GroupParticipant phoneNumber and lid fields", () => {
  const participant = {
    id: "125013663469807:4@lid",
    lid: "125013663469807:4@lid",
    phoneNumber: "85254420939:23@s.whatsapp.net",
  };
  const identity = groupParticipantIdentity(participant);

  assert.deepEqual(identity, {
    jid: "125013663469807@lid",
    pnJid: "85254420939@s.whatsapp.net",
    lidJid: "125013663469807@lid",
  });
  assert.deepEqual(
    new Set(participantIdentityValues(participant)),
    new Set(["125013663469807@lid", "85254420939@s.whatsapp.net"]),
  );
});

test("prefers GroupMetadata ownerPn as stable owner identity", () => {
  assert.deepEqual(
    groupOwnerIdentity({
      owner: "125013663469807:4@hosted.lid",
      ownerPn: "85254420939:23@hosted",
    }),
    {
      jid: "125013663469807@hosted.lid",
      pnJid: "85254420939@hosted",
      lidJid: "125013663469807@hosted.lid",
    },
  );
});

test("strips a device suffix before normalizing a phone number", () => {
  assert.equal(phoneFromIdentity("85254420939:20@s.whatsapp.net"), "+85254420939");
  assert.equal(phoneFromIdentity("85254420939:99@hosted"), "+85254420939");
  assert.equal(phoneFromIdentity("85254420939@lid"), null);
  assert.equal(phoneFromIdentity("abc123@s.whatsapp.net"), null);
  assert.equal(phoneFromIdentity("abc123"), null);
});

test("rejects malformed device suffixes and alphanumeric legacy identity fields", () => {
  assert.equal(phoneFromIdentity("123:device@s.whatsapp.net"), null);
  assert.equal(phoneFromIdentity("123:7:8@hosted"), null);
  assert.equal(phoneFromIdentity("123_agent:7@s.whatsapp.net"), null);
  assert.equal(phoneFromIdentity("123_128:7:8@hosted"), null);

  const sender = senderIdentityFromKey(
    { participant: "1@lid", participantPn: "abc123" },
    { isGroup: true, senderJid: "1@lid" },
  );
  assert.deepEqual(sender, { pnJid: null, lidJid: "1@lid" });

  const participant = groupParticipantIdentity({
    id: "1@lid",
    phoneNumber: "abc123",
    lid: "def456",
  });
  assert.deepEqual(participant, {
    jid: "1@lid",
    pnJid: null,
    lidJid: "1@lid",
  });
});

test("normalizes own PN and LID device identities before public status", () => {
  assert.equal(
    normalizeIdentityJid("85254420939:23@s.whatsapp.net"),
    "85254420939@s.whatsapp.net",
  );
  assert.equal(
    normalizeIdentityJid("125013663469807:4@lid"),
    "125013663469807@lid",
  );
  assert.equal(
    normalizeIdentityJid("85254420939_128:23@hosted"),
    "85254420939@hosted",
  );
  assert.equal(
    normalizeIdentityJid("125013663469807_129:4@hosted.lid"),
    "125013663469807@hosted.lid",
  );
});

test("explicit mentions accept only resolved or valid WhatsApp identities", () => {
  const scopedDirectory = new Map([
    ["alice", "125013663469807:4@lid"],
    ["85254420939@s.whatsapp.net", "125013663469807:4@lid"],
  ]);
  const globalDirectory = new Map([
    ["bob", "85260000000@s.whatsapp.net"],
  ]);

  assert.deepEqual(
    resolveExplicitIdentityMentions(
      [
        "@all",
        "Alice",
        "+85251112222",
        "85254420939:23@s.whatsapp.net",
        "unknown",
        "foo@bar",
        "abc123@s.whatsapp.net",
      ],
      {
        chatJid: "120363000000000001@g.us",
        scopedDirectory,
        globalDirectory,
      },
    ),
    {
      mentions: [
        "125013663469807@lid",
        "85251112222@s.whatsapp.net",
        "85254420939@s.whatsapp.net",
      ],
      mentionAll: true,
    },
  );
});

test("explicit group mentions never use a nickname learned in another chat", () => {
  const result = resolveExplicitIdentityMentions(
    ["Bob"],
    {
      chatJid: "120363000000000001@g.us",
      scopedDirectory: new Map(),
      globalDirectory: new Map([["bob", "85260000000@s.whatsapp.net"]]),
    },
  );

  assert.deepEqual(result, { mentions: [], mentionAll: false });
});
