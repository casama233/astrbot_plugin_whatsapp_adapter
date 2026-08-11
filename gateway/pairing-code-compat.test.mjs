import assert from "node:assert/strict";
import test from "node:test";

import {
  createPairingCodePolicy,
  normalizePairingPhone,
  pairingCodeAvailability,
} from "./pairing-code-compat.mjs";


test("accepts only strict E.164-like pairing phone numbers", () => {
  assert.equal(normalizePairingPhone("8613800138000"), "8613800138000");
  assert.equal(normalizePairingPhone("+85212345678"), "85212345678");

  for (const value of [
    "123456",
    "1234567890123456",
    "01234567",
    " 85212345678",
    "852 1234 5678",
    "852-1234-5678",
    "+",
    85212345678,
    null,
  ]) {
    assert.throws(() => normalizePairingPhone(value), /7 to 15 digits/);
  }
});


test("reports registered, login-state, and unsupported conditions precisely", () => {
  const supportedSocket = { requestPairingCode() {} };

  assert.equal(pairingCodeAvailability({ socket: supportedSocket, ready: true }).status, 409);
  assert.equal(pairingCodeAvailability({ socket: supportedSocket, registered: true }).status, 409);
  assert.equal(pairingCodeAvailability({ connectionStatus: "qr_pending" }).status, 503);
  assert.match(
    pairingCodeAvailability({
      socket: supportedSocket,
      connectionStatus: "qr_expired",
    }).error,
    /restart/,
  );
  assert.equal(
    pairingCodeAvailability({ socket: {}, connectionStatus: "qr_pending" }).status,
    501,
  );
  assert.deepEqual(
    pairingCodeAvailability({
      socket: supportedSocket,
      connectionStatus: "starting",
    }),
    { ok: true, status: 200 },
  );
  assert.equal(
    pairingCodeAvailability({
      socket: supportedSocket,
      connectionStatus: "qr_pending",
    }).ok,
    true,
  );
});


test("serializes pairing availability errors without account secrets", () => {
  const sensitivePhone = "8613800138000";
  const sensitiveCode = "AB12-CD34";
  const decisions = [
    pairingCodeAvailability({ ready: true }),
    pairingCodeAvailability({ connectionStatus: "qr_pending" }),
    pairingCodeAvailability({ socket: {}, connectionStatus: "qr_pending" }),
  ];
  const serialized = JSON.stringify(decisions);
  assert.doesNotMatch(serialized, new RegExp(sensitivePhone));
  assert.doesNotMatch(serialized, new RegExp(sensitiveCode));
});


test("blocks concurrent and cooldown pairing requests without retaining secrets", () => {
  let clock = 1_000;
  const policy = createPairingCodePolicy({ cooldownMs: 5_000, now: () => clock });

  const lease = policy.begin();
  assert.equal(lease.ok, true);
  assert.deepEqual(policy.begin(), {
    ok: false,
    status: 429,
    error: "A pairing code request is already in progress.",
  });

  lease.finish();
  lease.finish();
  assert.deepEqual(policy.begin(), {
    ok: false,
    status: 429,
    error: "Please wait before requesting another pairing code.",
    retryAfterMs: 5_000,
  });

  clock += 5_000;
  const nextLease = policy.begin();
  assert.equal(nextLease.ok, true);
  assert.doesNotMatch(JSON.stringify(policy), /phone|code|8613800138000|AB12-CD34/i);
  nextLease.finish();
});
