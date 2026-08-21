import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  hasIdentityMentionLabels,
  replaceIdentityMentionLabels,
} from "./outbound-mention-names.mjs";

const alice = {
  aliases: ["85233334444", "85233334444@s.whatsapp.net"],
  name: "委屈巴巴的煎茶",
};

test("replaces an adapter-generated numeric mention with its display name", () => {
  const text = "@85233334444\n. 这是你的今日小猪：";
  assert.equal(hasIdentityMentionLabels(text, [alice]), true);
  assert.equal(
    replaceIdentityMentionLabels(text, [alice]),
    "@委屈巴巴的煎茶\n. 这是你的今日小猪：",
  );
});

test("preserves an explicit human-readable At.name label", () => {
  const text = "@Alice hello";
  assert.equal(hasIdentityMentionLabels(text, [alice]), false);
  assert.equal(replaceIdentityMentionLabels(text, [alice]), text);
});

test("supports public LID labels and replacement metacharacters in names", () => {
  const entry = {
    aliases: ["lid-123", "123"],
    name: "R&D $1",
  };
  assert.equal(
    replaceIdentityMentionLabels("target: @lid-123!", [entry]),
    "target: @R&D $1!",
  );
});

test("does not replace partial or unknown identity tokens", () => {
  assert.equal(
    replaceIdentityMentionLabels("@852333344440 @unknown", [alice]),
    "@852333344440 @unknown",
  );
});

test("keeps the identity label when no useful display name is known", () => {
  const entry = { aliases: ["85233334444"], name: "85233334444" };
  assert.equal(
    replaceIdentityMentionLabels("@85233334444 hello", [entry]),
    "@85233334444 hello",
  );
});

test("gateway applies nickname rendering to text, edits, and media captions", async () => {
  const source = await readFile(
    new URL("./whatsapp-gateway-impl.mjs", import.meta.url),
    "utf8",
  );
  assert.match(
    source,
    /const renderedText = await renderOutboundMentionNames\(text, resolvedExplicit, body\.to\);/,
  );
  assert.match(source, /payload\.text = renderedText;/);
  assert.match(
    source,
    /const renderedCaption = await renderOutboundMentionNames\(/,
  );
  assert.match(
    source,
    /resolveMediaPayload\(\s*body\.type,\s*body\.pathOrUrl,\s*renderedCaption,/,
  );
});
