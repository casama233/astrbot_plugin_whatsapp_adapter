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
      participant: "000000000000103:4@lid",
      participantAlt: "00000000101:23@s.whatsapp.net",
    },
    {
      isGroup: true,
      senderJid: "000000000000103:4@lid",
      chatJid: "120363000000000001@g.us",
    },
  );

  assert.deepEqual(identity, {
    pnJid: "00000000101@s.whatsapp.net",
    lidJid: "000000000000103@lid",
  });
});

test("resolves Baileys 7 direct sender remoteJidAlt", () => {
  const identity = senderIdentityFromKey(
    {
      remoteJid: "000000000000103@lid",
      remoteJidAlt: "00000000101@s.whatsapp.net",
    },
    {
      senderJid: "000000000000103@lid",
      chatJid: "000000000000103@lid",
    },
  );

  assert.equal(identity.pnJid, "00000000101@s.whatsapp.net");
  assert.equal(identity.lidJid, "000000000000103@lid");
});

test("keeps legacy bare participantPn compatible", () => {
  const identity = senderIdentityFromKey(
    { participant: "000000000000103@lid", participantPn: "00000000101" },
    { isGroup: true, senderJid: "000000000000103@lid" },
  );

  assert.equal(identity.pnJid, "00000000101@s.whatsapp.net");
  assert.equal(identity.lidJid, "000000000000103@lid");
});

test("resolves GroupParticipant phoneNumber and lid fields", () => {
  const participant = {
    id: "000000000000103:4@lid",
    lid: "000000000000103:4@lid",
    phoneNumber: "00000000101:23@s.whatsapp.net",
  };
  const identity = groupParticipantIdentity(participant);

  assert.deepEqual(identity, {
    jid: "000000000000103@lid",
    pnJid: "00000000101@s.whatsapp.net",
    lidJid: "000000000000103@lid",
  });
  assert.deepEqual(
    new Set(participantIdentityValues(participant)),
    new Set(["000000000000103@lid", "00000000101@s.whatsapp.net"]),
  );
});

test("prefers GroupMetadata ownerPn as stable owner identity", () => {
  assert.deepEqual(
    groupOwnerIdentity({
      owner: "000000000000103:4@hosted.lid",
      ownerPn: "00000000101:23@hosted",
    }),
    {
      jid: "000000000000103@hosted.lid",
      pnJid: "00000000101@hosted",
      lidJid: "000000000000103@hosted.lid",
    },
  );
});

test("strips a device suffix before normalizing a phone number", () => {
  assert.equal(phoneFromIdentity("00000000101:20@s.whatsapp.net"), "+00000000101");
  assert.equal(phoneFromIdentity("00000000101:99@hosted"), "+00000000101");
  assert.equal(phoneFromIdentity("00000000101@lid"), null);
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
    normalizeIdentityJid("00000000101:23@s.whatsapp.net"),
    "00000000101@s.whatsapp.net",
  );
  assert.equal(
    normalizeIdentityJid("000000000000103:4@lid"),
    "000000000000103@lid",
  );
  assert.equal(
    normalizeIdentityJid("00000000101_128:23@hosted"),
    "00000000101@hosted",
  );
  assert.equal(
    normalizeIdentityJid("000000000000103_129:4@hosted.lid"),
    "000000000000103@hosted.lid",
  );
});

test("explicit mentions accept only resolved or valid WhatsApp identities", () => {
  const scopedDirectory = new Map([
    ["alice", "000000000000103:4@lid"],
    ["00000000101@s.whatsapp.net", "000000000000103:4@lid"],
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
        "00000000101:23@s.whatsapp.net",
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
        "000000000000103@lid",
        "85251112222@s.whatsapp.net",
        "00000000101@s.whatsapp.net",
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
