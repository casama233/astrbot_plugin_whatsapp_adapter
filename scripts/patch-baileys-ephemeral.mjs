import { access, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ACTIVE_TIMESTAMP_PATTERN =
  /expiration:\s*options\.ephemeralExpiration\s*\|\|\s*WA_DEFAULT_EPHEMERAL\s*,\s*ephemeralSettingTimestamp:\s*options\.ephemeralSettingTimestamp\s*\|\|\s*unixTimestampSeconds\(\)/m;

const COMMENTED_TIMESTAMP_PATTERN =
  /(expiration:\s*options\.ephemeralExpiration\s*\|\|\s*WA_DEFAULT_EPHEMERAL)(\s*\r?\n\s*)\/\/\s*ephemeralSettingTimestamp:[^\r\n]*/m;

export function patchBaileysEphemeralMetadata(source) {
  if (ACTIVE_TIMESTAMP_PATTERN.test(source)) {
    return { content: source, changed: false };
  }

  if (!COMMENTED_TIMESTAMP_PATTERN.test(source)) {
    throw new Error(
      "Unsupported Baileys messages implementation: ephemeral timestamp marker was not found.",
    );
  }

  return {
    content: source.replace(
      COMMENTED_TIMESTAMP_PATTERN,
      "$1,$2ephemeralSettingTimestamp: options.ephemeralSettingTimestamp || unixTimestampSeconds()",
    ),
    changed: true,
  };
}

async function fileExists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

export async function patchInstalledBaileys({ cwd = process.cwd(), targetPath } = {}) {
  const candidates = targetPath
    ? [path.resolve(targetPath)]
    : [
        path.join(
          cwd,
          "node_modules",
          "@whiskeysockets",
          "baileys",
          "lib",
          "Utils",
          "messages.js",
        ),
        path.join(
          cwd,
          "node_modules",
          "@whiskeysockets",
          "baileys",
          "src",
          "Utils",
          "messages.ts",
        ),
      ];

  const existing = [];
  for (const candidate of candidates) {
    if (await fileExists(candidate)) existing.push(candidate);
  }

  if (!existing.length) {
    throw new Error(
      `Baileys messages implementation was not found. Checked: ${candidates.join(", ")}`,
    );
  }

  let changed = 0;
  for (const candidate of existing) {
    const source = await readFile(candidate, "utf8");
    const result = patchBaileysEphemeralMetadata(source);
    if (!result.changed) continue;
    await writeFile(candidate, result.content, "utf8");
    changed += 1;
  }

  return { checked: existing.length, changed };
}

async function main() {
  const result = await patchInstalledBaileys({
    targetPath: process.env.BAILEYS_MESSAGES_PATH,
  });
  const action = result.changed ? "patched" : "already patched";
  console.log(
    `[whatsapp-adapter] Baileys ephemeral metadata ${action} (${result.checked} file(s) checked).`,
  );
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  main().catch((error) => {
    console.error(`[whatsapp-adapter] ${error.message}`);
    process.exitCode = 1;
  });
}
