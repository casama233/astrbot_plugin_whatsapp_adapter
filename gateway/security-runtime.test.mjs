import assert from "node:assert/strict";
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  isAuthorizedGatewayRequest,
  isPublicIpAddress,
  prepareSafeMediaSource,
} from "./security-runtime.mjs";

test("Bearer authorization requires the exact token", () => {
  assert.equal(
    isAuthorizedGatewayRequest(
      { headers: { authorization: "Bearer secret" } },
      "secret",
    ),
    true,
  );
  assert.equal(
    isAuthorizedGatewayRequest(
      { headers: { authorization: "Bearer wrong" } },
      "secret",
    ),
    false,
  );
  assert.equal(isAuthorizedGatewayRequest({ headers: {} }, "secret"), false);
  assert.equal(
    isAuthorizedGatewayRequest(
      { headers: { authorization: "Bearer secret" } },
      "",
    ),
    false,
  );
});

test("private, transition, mapped, and special IP ranges are rejected", () => {
  for (const address of [
    "127.0.0.1",
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.1.2",
    "192.88.99.1",
    "::1",
    "fc00::1",
    "fe80::1",
    "2001:db8::1",
    "::ffff:127.0.0.1",
    "::ffff:7f00:1",
    "0:0:0:0:0:ffff:7f00:1",
    "64:ff9b::7f00:1",
    "2001::1",
    "2002:7f00:1::",
    "3fff::1",
  ]) {
    assert.equal(isPublicIpAddress(address), false, address);
  }
  assert.equal(isPublicIpAddress("1.1.1.1"), true);
  assert.equal(isPublicIpAddress("2606:4700:4700::1111"), true);
  assert.equal(isPublicIpAddress("2001:4860:4860::8888"), true);
});

test("local media is limited to trusted roots and file URLs are rejected", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "wa-sec-"));
  const tempDir = path.join(root, "temp");
  const outsideDir = path.join(root, "outside");
  await mkdir(tempDir, { recursive: true });
  await mkdir(outsideDir, { recursive: true });
  const allowed = path.join(tempDir, "allowed.png");
  const outside = path.join(outsideDir, "secret.txt");
  await writeFile(allowed, "ok");
  await writeFile(outside, "secret");

  try {
    const prepared = await prepareSafeMediaSource(allowed, { tempDir });
    assert.equal(prepared.pathOrUrl, await realpath(allowed));
    await prepared.cleanup();
    await assert.rejects(
      () => prepareSafeMediaSource(outside, { tempDir }),
      /outside the allowed media roots/,
    );
    await assert.rejects(
      () => prepareSafeMediaSource(`file://${outside}`, { tempDir }),
      /file:\/\/ media sources are not allowed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
