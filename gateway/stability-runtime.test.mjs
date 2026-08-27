import assert from "node:assert/strict";
import { PassThrough, Writable } from "node:stream";
import test from "node:test";

import {
  pipeWithWatchdog,
  withDeadline,
  writeBoundedSse,
} from "./stability-runtime.mjs";

test("withDeadline rejects stalled operations", async () => {
  await assert.rejects(
    withDeadline(new Promise(() => {}), 15, "stalled operation"),
    /stalled operation timed out/,
  );
});

test("pipeWithWatchdog rejects an idle inbound media stream", async () => {
  const source = new PassThrough();
  const sink = new Writable({ write(_chunk, _encoding, callback) { callback(); } });
  await assert.rejects(
    pipeWithWatchdog(source, sink, {
      maxBytes: 1024,
      idleTimeoutMs: 15,
      totalTimeoutMs: 100,
    }),
    /idle timed out/,
  );
});

test("pipeWithWatchdog enforces the streamed byte ceiling", async () => {
  const source = new PassThrough();
  const sink = new Writable({ write(_chunk, _encoding, callback) { callback(); } });
  const result = pipeWithWatchdog(source, sink, {
    maxBytes: 4,
    idleTimeoutMs: 100,
    totalTimeoutMs: 200,
    overflowMessage: "too large",
  });
  source.end(Buffer.from("12345"));
  await assert.rejects(result, /too large/);
});

test("writeBoundedSse drops a client before its buffer can grow without bound", () => {
  let destroyed = false;
  let writes = 0;
  const client = {
    writableLength: 100,
    write() { writes += 1; },
    destroy() { destroyed = true; },
  };
  assert.equal(writeBoundedSse(client, "12345", 104), false);
  assert.equal(destroyed, true);
  assert.equal(writes, 0);
});
