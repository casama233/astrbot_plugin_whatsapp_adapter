import { access, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const GLOBAL_SETTINGS_SYMBOL = "astrbot.whatsapp.ephemeral-settings";
const UTILITY_PATCH_MARKER = "astrbotEphemeralSettingTimestamp";
const SOCKET_PATCH_MARKER = "astrbotEphemeralSettingsKey";

const COMMENTED_TIMESTAMP_PATTERN =
  /(expiration:\s*options\.ephemeralExpiration\s*\|\|\s*WA_DEFAULT_EPHEMERAL)(\s*\r?\n\s*)\/\/\s*ephemeralSettingTimestamp:[^\r\n]*/m;
const LEGACY_TIMESTAMP_PATTERN =
  /(expiration:\s*options\.ephemeralExpiration\s*\|\|\s*WA_DEFAULT_EPHEMERAL\s*,\s*ephemeralSettingTimestamp:\s*)options\.ephemeralSettingTimestamp\s*\|\|\s*unixTimestampSeconds\(\)/m;
const CORRECT_TIMESTAMP_PATTERN =
  /expiration:\s*options\.ephemeralExpiration\s*\|\|\s*WA_DEFAULT_EPHEMERAL\s*,\s*ephemeralSettingTimestamp:\s*astrbotEphemeralSettingTimestamp/m;

export function astrbotNormalizeEphemeralTimestamp(value) {
  if (value === undefined || value === null) return undefined;
  const text = typeof value === "bigint" ? value.toString() : String(value?.toString?.() ?? value);
  return /^\d+$/.test(text) && text !== "0" ? text : undefined;
}

export function astrbotRememberEphemeralChats(cache, chats) {
  for (const chat of chats || []) {
    const jid = String(chat?.id || "");
    if (!jid) continue;

    const previous = cache.get(jid);
    const hasExpiration = Object.prototype.hasOwnProperty.call(chat, "ephemeralExpiration");
    const suppliedTimestamp = astrbotNormalizeEphemeralTimestamp(chat?.ephemeralSettingTimestamp);

    if (!hasExpiration) {
      if (suppliedTimestamp && previous?.expiration) {
        cache.set(jid, { ...previous, timestamp: suppliedTimestamp });
      }
      continue;
    }

    const expiration = Number(chat?.ephemeralExpiration || 0);
    if (!Number.isFinite(expiration) || expiration <= 0) {
      cache.delete(jid);
      continue;
    }

    const timestamp =
      suppliedTimestamp ||
      (previous?.expiration === expiration ? previous?.timestamp : undefined);
    cache.set(jid, { expiration, timestamp });
  }
}

export function astrbotRememberEphemeralMessages(cache, payload, normalizeMessageContent) {
  for (const item of payload?.messages || []) {
    const jid = String(item?.key?.remoteJid || "");
    if (!jid || !item?.message) continue;

    const content = normalizeMessageContent(item.message);
    if (!content) continue;
    const key = Object.keys(content).find(
      (name) => (name === "conversation" || name.includes("Message")) && name !== "senderKeyDistributionMessage",
    );
    const node = key ? content[key] : undefined;
    const contextInfo = node && typeof node === "object" ? node.contextInfo : undefined;
    const expiration = Number(contextInfo?.expiration || 0);
    const timestamp = astrbotNormalizeEphemeralTimestamp(
      contextInfo?.ephemeralSettingTimestamp,
    );
    if (expiration > 0 && timestamp) {
      cache.set(jid, { expiration, timestamp });
    }
  }
}

function indentSource(source, indent) {
  return source
    .split("\n")
    .map((line) => (line ? `${indent}${line}` : ""))
    .join("\n");
}

function insertBeforeIndex(source, index, block) {
  return `${source.slice(0, index)}${block}${source.slice(index)}`;
}

export function patchBaileysMessagesUtility(source) {
  let content = source;
  let changed = false;

  if (LEGACY_TIMESTAMP_PATTERN.test(content)) {
    content = content.replace(
      LEGACY_TIMESTAMP_PATTERN,
      "$1astrbotEphemeralSettingTimestamp",
    );
    changed = true;
  } else if (COMMENTED_TIMESTAMP_PATTERN.test(content)) {
    content = content.replace(
      COMMENTED_TIMESTAMP_PATTERN,
      "$1,$2ephemeralSettingTimestamp: astrbotEphemeralSettingTimestamp",
    );
    changed = true;
  } else if (!CORRECT_TIMESTAMP_PATTERN.test(content)) {
    throw new Error(
      "Unsupported Baileys messages implementation: ephemeral timestamp marker was not found.",
    );
  }

  if (!content.includes(`const ${UTILITY_PATCH_MARKER} =`)) {
    const conditionToken = "!!options?.ephemeralExpiration";
    const conditionIndex = content.indexOf(conditionToken);
    if (conditionIndex < 0) {
      throw new Error(
        "Unsupported Baileys messages implementation: ephemeral condition was not found.",
      );
    }
    const ifIndex = content.lastIndexOf("if (", conditionIndex);
    if (ifIndex < 0) {
      throw new Error(
        "Unsupported Baileys messages implementation: ephemeral if-block was not found.",
      );
    }
    const lineStart = content.lastIndexOf("\n", ifIndex) + 1;
    const indent = content.slice(lineStart, ifIndex).match(/^\s*/)?.[0] || "";
    const lookupBlock = [
      `const astrbotEphemeralSettings = globalThis[Symbol.for(${JSON.stringify(GLOBAL_SETTINGS_SYMBOL)})]`,
      "const astrbotEphemeralSetting = astrbotEphemeralSettings?.get(String(jid || \"\"))",
      "const astrbotEphemeralSettingTimestamp =",
      "  options?.ephemeralOptions?.eph_setting_ts?.toString() ||",
      "  (Number(astrbotEphemeralSetting?.expiration) === Number(options?.ephemeralExpiration)",
      "    ? astrbotEphemeralSetting?.timestamp",
      "    : undefined)",
      "",
    ].join("\n");
    content = insertBeforeIndex(content, lineStart, indentSource(lookupBlock, indent));
    changed = true;
  }

  const timestampGuardPattern =
    /!!options\?\.ephemeralExpiration\s*&&\s*!!astrbotEphemeralSettingTimestamp\s*&&/m;
  if (!timestampGuardPattern.test(content)) {
    const conditionToken = "!!options?.ephemeralExpiration &&";
    const conditionIndex = content.indexOf(conditionToken);
    if (conditionIndex < 0) {
      throw new Error(
        "Unsupported Baileys messages implementation: expiration guard was not found.",
      );
    }
    const lineStart = content.lastIndexOf("\n", conditionIndex) + 1;
    const indent = content.slice(lineStart, conditionIndex).match(/^\s*/)?.[0] || "";
    content = content.replace(
      conditionToken,
      `${conditionToken}\n${indent}!!astrbotEphemeralSettingTimestamp &&`,
    );
    changed = true;
  }

  if (!timestampGuardPattern.test(content)) {
    throw new Error(
      "Unsupported Baileys messages implementation: timestamp guard was not installed.",
    );
  }

  return { content, changed };
}

export function patchBaileysMessagesSend(source) {
  if (source.includes(`const ${SOCKET_PATCH_MARKER} =`)) {
    return { content: source, changed: false };
  }

  const anchorPattern = /const\s+getLIDForPN\s*=/m;
  const anchor = anchorPattern.exec(source);
  if (!anchor) {
    throw new Error(
      "Unsupported Baileys messages-send implementation: socket initialization anchor was not found.",
    );
  }

  const lineStart = source.lastIndexOf("\n", anchor.index) + 1;
  const indent = source.slice(lineStart, anchor.index).match(/^\s*/)?.[0] || "";
  const helperBlock = [
    `const astrbotEphemeralSettingsKey = Symbol.for(${JSON.stringify(GLOBAL_SETTINGS_SYMBOL)})`,
    "const astrbotEphemeralSettings = globalThis[astrbotEphemeralSettingsKey] || new Map()",
    "globalThis[astrbotEphemeralSettingsKey] = astrbotEphemeralSettings",
    "astrbotEphemeralSettings.clear()",
    astrbotNormalizeEphemeralTimestamp.toString(),
    astrbotRememberEphemeralChats.toString(),
    astrbotRememberEphemeralMessages.toString(),
    "ev.on(\"chats.upsert\", (chats) => astrbotRememberEphemeralChats(astrbotEphemeralSettings, chats))",
    "ev.on(\"chats.update\", (chats) => astrbotRememberEphemeralChats(astrbotEphemeralSettings, chats))",
    "ev.on(\"messages.upsert\", (payload) =>",
    "  astrbotRememberEphemeralMessages(astrbotEphemeralSettings, payload, normalizeMessageContent),",
    ")",
    "",
  ].join("\n");

  return {
    content: insertBeforeIndex(source, lineStart, indentSource(helperBlock, indent)),
    changed: true,
  };
}

// Backward-compatible export used by the first revision of this PR.
export const patchBaileysEphemeralMetadata = patchBaileysMessagesUtility;

async function fileExists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

export async function patchInstalledBaileys({
  cwd = process.cwd(),
  messagesPath,
  messagesSendPath,
} = {}) {
  const targets = [
    {
      label: "messages",
      path: path.resolve(
        messagesPath ||
          path.join(
            cwd,
            "node_modules",
            "@whiskeysockets",
            "baileys",
            "lib",
            "Utils",
            "messages.js",
          ),
      ),
      patch: patchBaileysMessagesUtility,
    },
    {
      label: "messages-send",
      path: path.resolve(
        messagesSendPath ||
          path.join(
            cwd,
            "node_modules",
            "@whiskeysockets",
            "baileys",
            "lib",
            "Socket",
            "messages-send.js",
          ),
      ),
      patch: patchBaileysMessagesSend,
    },
  ];

  for (const target of targets) {
    if (!(await fileExists(target.path))) {
      throw new Error(
        `Baileys ${target.label} implementation was not found: ${target.path}`,
      );
    }
  }

  let changed = 0;
  for (const target of targets) {
    const source = await readFile(target.path, "utf8");
    const result = target.patch(source);
    if (!result.changed) continue;
    await writeFile(target.path, result.content, "utf8");
    changed += 1;
  }

  return { checked: targets.length, changed };
}

async function main() {
  const result = await patchInstalledBaileys({
    messagesPath: process.env.BAILEYS_MESSAGES_PATH,
    messagesSendPath: process.env.BAILEYS_MESSAGES_SEND_PATH,
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
