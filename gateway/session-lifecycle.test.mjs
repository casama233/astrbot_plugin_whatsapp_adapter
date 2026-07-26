import assert from "node:assert/strict";
import test from "node:test";

import {
  disconnectKind,
  reconnectDelayMs,
  sessionDirectory,
} from "./session-lifecycle.mjs";

test("classifies invalid auth separately from transient disconnects", () => {
  assert.equal(disconnectKind(401), "auth_invalid");
  assert.equal(disconnectKind(411), "auth_invalid");
  assert.equal(disconnectKind(500), "auth_invalid");
  assert.equal(disconnectKind(515), "restart");
  assert.equal(disconnectKind(408), "transient");
  assert.equal(disconnectKind(undefined), "transient");
});

test("transient reconnects back off to a five minute ceiling", () => {
  assert.equal(reconnectDelayMs(1), 3_000);
  assert.equal(reconnectDelayMs(2), 6_000);
  assert.equal(reconnectDelayMs(3), 12_000);
  assert.equal(reconnectDelayMs(20), 300_000);
});

test("new auth epochs use isolated directories and reject traversal", () => {
  assert.equal(
    sessionDirectory("/data/whatsapp-auth", "session-2"),
    "/data/whatsapp-auth/.sessions/session-2",
  );
  assert.throws(() => sessionDirectory("/data/whatsapp-auth", "../escape"));
});
