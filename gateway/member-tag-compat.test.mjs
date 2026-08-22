import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { patchGatewayGroupNames } from "./group-name-compat.mjs";
import { patchGatewayMemberTags } from "./member-tag-compat.mjs";
import { patchGatewayPrivateMediaBursts } from "./private-media-burst-compat.mjs";
import { patchGatewaySecurity } from "./security-hardening.mjs";

function patchedGateway() {
  const source = readFileSync(
    new URL("./whatsapp-gateway-impl.mjs", import.meta.url),
    "utf8",
  );
  const groupPatched = patchGatewayGroupNames(source);
  return patchGatewayMemberTags(groupPatched.content);
}

test("bridges group member tags into inbound events and group info", () => {
  const result = patchedGateway();
  assert.equal(result.changed, true);
  assert.match(result.content, /group\.member-tag\.update/);
  assert.match(result.content, /memberTagSnapshotFromMessagePayload\(primary\.message\)/);
  assert.match(result.content, /senderMemberTag,/);
  assert.match(
    result.content,
    /broadcast\(\{\n    type: "message",\n    messageId:/,
  );
  assert.match(
    result.content,
    /memberTag: groupMemberTagFor\(groupJid, jid, pnJid, identity\?\.lidJid\)/,
  );
});

test("uses message metadata as a removal-safe member tag source", () => {
  const result = patchedGateway();
  assert.match(result.content, /Object\.prototype\.hasOwnProperty\.call\(memberLabel, "label"\)/);
  assert.match(result.content, /label: String\(memberLabel\.label \|\| ""\)\.trim\(\)/);
  assert.match(
    result.content,
    /senderMemberTagSnapshot\.timestamp \|\| Number\(primary\.messageTimestamp \|\| 0\)/,
  );
});

test("keeps member tags group-scoped and separate from permissions", () => {
  const result = patchedGateway();
  assert.match(result.content, /memberTagCacheKey\(groupJid, participantJid\)/);
  assert.match(result.content, /senderRole,/);
  assert.match(result.content, /senderMemberTag,/);
  assert.doesNotMatch(result.content, /senderRole\s*=\s*senderMemberTag/);
});

test("drops cached tags when a participant leaves or runtime resets", () => {
  const result = patchedGateway();
  assert.match(result.content, /update\?\.action !== "remove"/);
  assert.match(result.content, /forgetGroupMemberTag/);
  assert.match(result.content, /groupMemberTagCache\.clear\(\)/);
});

test("is idempotent after the member-tag compatibility marker is present", () => {
  const first = patchedGateway();
  const second = patchGatewayMemberTags(first.content);
  assert.equal(second.changed, false);
  assert.equal(second.content, first.content);
});

test("complete runtime patch chain preserves the inbound message discriminator", () => {
  const memberPatched = patchedGateway();
  const privateMediaPatched = patchGatewayPrivateMediaBursts(memberPatched.content);
  const secured = patchGatewaySecurity(privateMediaPatched.content);

  assert.match(
    secured.content,
    /broadcast\(\{\n    type: "message",\n    messageId:/,
  );
});
