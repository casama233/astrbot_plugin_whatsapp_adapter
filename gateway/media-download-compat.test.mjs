import assert from "node:assert/strict";
import test from "node:test";

import {
  inboundMediaDownloadContext,
  removePartialInboundMedia,
} from "./media-download-compat.mjs";


test("media retry callback stays bound to the originating socket", async () => {
  const mediaSocket = {
    account: "original",
    async updateMediaMessage(message) {
      return { account: this.account, message };
    },
  };
  const logger = {};
  const context = inboundMediaDownloadContext(mediaSocket, logger);

  assert.strictEqual(context.logger, logger);
  assert.deepEqual(await context.reuploadRequest({ id: "expired" }), {
    account: "original",
    message: { id: "expired" },
  });
  assert.throws(
    () => inboundMediaDownloadContext({}, logger),
    /re-upload callback is unavailable/,
  );
});


test("partial media cleanup ignores missing files and logs real cleanup failures", async () => {
  const calls = [];
  const logs = [];
  const logger = { debug: (...args) => logs.push(args) };

  await removePartialInboundMedia("/tmp/partial-one", logger, async (filePath) => {
    calls.push(filePath);
  });
  await removePartialInboundMedia("/tmp/already-gone", logger, async () => {
    const error = new Error("gone");
    error.code = "ENOENT";
    throw error;
  });
  await removePartialInboundMedia("/tmp/cleanup-failed", logger, async () => {
    const error = new Error("denied");
    error.code = "EACCES";
    throw error;
  });

  assert.deepEqual(calls, ["/tmp/partial-one"]);
  assert.equal(logs.length, 1);
  assert.equal(logs[0][0].filePath, "/tmp/cleanup-failed");
});
