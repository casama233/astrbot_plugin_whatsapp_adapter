import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createTwoStepGate } from "../pages/whatsapp-login/two-step-action.js";

test("two-step gate requires a second activation inside the confirmation window", () => {
  let now = 1_000;
  const gate = createTwoStepGate({ windowMs: 10_000, now: () => now });

  assert.equal(gate.activate(), "armed");
  assert.equal(gate.isArmed(), true);
  assert.equal(gate.remainingMs(), 10_000);

  now += 2_000;
  assert.equal(gate.activate(), "confirmed");
  assert.equal(gate.isArmed(), false);
});

test("expired confirmation cannot execute an action", () => {
  let now = 5_000;
  const gate = createTwoStepGate({ windowMs: 10_000, now: () => now });

  assert.equal(gate.activate(), "armed");
  now += 10_001;
  assert.equal(gate.activate(), "armed");
  assert.equal(gate.isArmed(), true);
});

test("disarm invalidates a pending destructive action", () => {
  let now = 100;
  const gate = createTwoStepGate({ now: () => now });
  assert.equal(gate.activate(), "armed");
  gate.disarm();
  now += 1;
  assert.equal(gate.activate(), "armed");
});

test("plugin page no longer uses modal confirmation or dynamic HTML sinks", async () => {
  const app = await readFile(new URL("../pages/whatsapp-login/app.js", import.meta.url), "utf8");
  const legacy = await readFile(
    new URL("../pages/whatsapp-login/sandbox-confirm.js", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(app, /window\.confirm|\bconfirm\s*\(/);
  assert.doesNotMatch(app, /\.innerHTML\b|\.outerHTML\b|document\.write\s*\(/);
  assert.doesNotMatch(legacy, /window\.confirm\s*=|queueMicrotask/);
  assert.match(app, /createTwoStepGate/);
  assert.match(app, /cloneNode\(true\)/);
  assert.match(app, /replaceChildren/);
});

test("update confirmation submits the exact candidate identity", async () => {
  const app = await readFile(new URL("../pages/whatsapp-login/app.js", import.meta.url), "utf8");

  assert.match(app, /release\.candidateToken/);
  assert.match(app, /candidateToken,/);
  assert.match(app, /expectedVersion:\s*latest/);
  assert.match(app, /apiPost\("update\/install"/);
});

test("legacy sandbox helper is harmless for cached HTML", async () => {
  const source = await readFile(
    new URL("../pages/whatsapp-login/sandbox-confirm.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /harmless no-op/);
  assert.doesNotMatch(source, /addEventListener/);
});
