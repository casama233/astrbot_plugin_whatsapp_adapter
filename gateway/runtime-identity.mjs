import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

import {
  isLidJid,
  isPnJid,
  normalizeIdentityJid,
} from "./identity-compat.mjs";

export const RUNTIME_IDENTITY_MAPPING_FILE = "astrbot-lid-mappings-v1.json";
// Gateway is the sole writer of this shared mapping file. Python deliberately
// treats it as read-only and owns its separate identity-projections file.
const PN_DOMAINS = ["@s.whatsapp.net", "@hosted"];
const LID_DOMAINS = ["@lid", "@hosted.lid"];

function normalizedRuntimeValue(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  return (isPnJid(raw) || isLidJid(raw)) ? normalizeIdentityJid(raw) : raw;
}

function identityKind(value) {
  const localPart = identityLocalPart(value);
  if (!/^\d+$/.test(localPart)) return null;
  if (isPnJid(value)) return "pn";
  if (isLidJid(value)) return "lid";
  return null;
}

function identityLocalPart(value) {
  const normalized = normalizeIdentityJid(value);
  const separator = normalized.lastIndexOf("@");
  return separator > 0 ? normalized.slice(0, separator) : "";
}

function identityNamespaceKey(value) {
  const kind = identityKind(value);
  const localPart = identityLocalPart(value);
  return kind && localPart ? `${kind}:${localPart}` : null;
}

function identityNamespaceAliases(value) {
  const normalized = normalizeIdentityJid(value);
  const kind = identityKind(normalized);
  const localPart = identityLocalPart(normalized);
  if (!kind || !localPart) return normalized ? [normalized] : [];
  const domains = kind === "pn" ? PN_DOMAINS : LID_DOMAINS;
  return domains.map((domain) => `${localPart}${domain}`);
}

function canonicalIdentity(value) {
  const normalized = normalizeIdentityJid(value);
  const kind = identityKind(normalized);
  const localPart = identityLocalPart(normalized);
  if (!kind || !localPart) return normalized;
  return `${localPart}${kind === "pn" ? PN_DOMAINS[0] : LID_DOMAINS[0]}`;
}

/**
 * Keep PN and LID identities separate unless WhatsApp has supplied an explicit
 * mapping. In particular, matching digits on opposite domains are not proof
 * that two identities belong to the same account.
 */
export class RuntimeIdentityRegistry {
  constructor() {
    this.lidToPn = new Map();
    this.pnToLids = new Map();
  }

  clear() {
    this.lidToPn.clear();
    this.pnToLids.clear();
  }

  rememberMapping(lidValue, pnValue) {
    const lidJid = normalizeIdentityJid(lidValue);
    const pnJid = normalizeIdentityJid(pnValue);
    if (!isLidJid(lidJid) || !isPnJid(pnJid)) return false;

    const lidKey = identityNamespaceKey(lidJid);
    const pnKey = identityNamespaceKey(pnJid);
    if (!lidKey || !pnKey) return false;
    const previous = this.lidToPn.get(lidKey);
    if (previous?.lidJid === lidJid && previous?.pnJid === pnJid) return false;

    if (previous) {
      const previousAliases = this.pnToLids.get(previous.pnKey);
      previousAliases?.delete(lidKey);
      if (previousAliases?.size === 0) this.pnToLids.delete(previous.pnKey);
    }

    this.lidToPn.set(lidKey, { lidJid, pnJid, pnKey });
    let aliases = this.pnToLids.get(pnKey);
    if (!aliases) {
      aliases = new Set();
      this.pnToLids.set(pnKey, aliases);
    }
    aliases.add(lidKey);
    return true;
  }

  canonical(value) {
    const normalized = normalizedRuntimeValue(value);
    if (!normalized) return "";
    const namespaceKey = identityNamespaceKey(normalized);
    const mapping = namespaceKey?.startsWith("lid:")
      ? this.lidToPn.get(namespaceKey)
      : null;
    return canonicalIdentity(mapping?.pnJid || normalized);
  }

  aliases(value) {
    const normalized = normalizedRuntimeValue(value);
    if (!normalized) return [];
    const canonical = this.canonical(normalized);
    const result = [canonical];
    for (const alias of identityNamespaceAliases(canonical)) {
      if (!result.includes(alias)) result.push(alias);
    }
    const pnKey = identityNamespaceKey(canonical);
    for (const lidKey of this.pnToLids.get(pnKey) || []) {
      const mapping = this.lidToPn.get(lidKey);
      for (const alias of identityNamespaceAliases(mapping?.lidJid)) {
        if (!result.includes(alias)) result.push(alias);
      }
    }
    if (identityKind(normalized) === "lid" && !this.lidToPn.has(identityNamespaceKey(normalized))) {
      for (const alias of identityNamespaceAliases(normalized)) {
        if (!result.includes(alias)) result.push(alias);
      }
    } else if (!result.includes(normalized)) {
      result.push(normalized);
    }
    return result;
  }

  same(left, right) {
    const leftValue = normalizedRuntimeValue(left);
    const rightValue = normalizedRuntimeValue(right);
    if (!leftValue || !rightValue) return false;
    if (leftValue === rightValue) return true;
    return this.canonical(leftValue) === this.canonical(rightValue);
  }

  mappings() {
    return [...this.lidToPn.values()]
      .map(({ lidJid, pnJid }) => ({ lidJid, pnJid }))
      .sort((left, right) => (
        left.lidJid.localeCompare(right.lidJid)
        || left.pnJid.localeCompare(right.pnJid)
      ));
  }
}

/** Return canonical-first keys plus every explicitly mapped alias combination. */
export function runtimeScopeKeys(registry, values) {
  const identityRegistry = registry instanceof RuntimeIdentityRegistry ? registry : null;
  let combinations = [[]];
  for (const value of Array.isArray(values) ? values : []) {
    const aliases = identityRegistry?.aliases(value) || [String(value || "")];
    const uniqueAliases = [...new Set(aliases.length ? aliases : [""])];
    combinations = combinations.flatMap((prefix) => (
      uniqueAliases.map((alias) => [...prefix, alias])
    ));
  }
  return [...new Set(combinations.map((parts) => JSON.stringify(parts)))];
}

/** Keys safe for mention/name lookup without exposing an unresolved LID as a phone. */
export function runtimeIdentityLookupKeys(registry, value) {
  const identityRegistry = registry instanceof RuntimeIdentityRegistry ? registry : null;
  const normalized = normalizedRuntimeValue(value);
  if (!normalized) return [];
  const result = identityRegistry?.aliases(normalized) || [normalized];
  const canonical = identityRegistry?.canonical(normalized) || normalized;
  if (identityKind(canonical) === "pn") {
    const digits = identityLocalPart(canonical);
    if (digits) result.push(digits, `+${digits}`);
  }
  return [...new Set(result)];
}

/**
 * Remember a scoped runtime event under its canonical identity key while
 * checking every known alias. Returns true when any alias was seen already.
 */
export function rememberRuntimeScope(
  cache,
  registry,
  values,
  maxSize = 2000,
  storedValue = Date.now(),
) {
  const keys = runtimeScopeKeys(registry, values);
  if (!keys.length) return false;
  if (keys.some((key) => cache.has(key))) return true;
  cache.set(keys[0], storedValue);
  const limit = Math.max(1, Number(maxSize) || 1);
  while (cache.size > limit) cache.delete(cache.keys().next().value);
  return false;
}

function mappingDocument(mappings) {
  const registry = new RuntimeIdentityRegistry();
  for (const mapping of Array.isArray(mappings) ? mappings : []) {
    registry.rememberMapping(mapping?.lidJid, mapping?.pnJid);
  }
  return {
    version: 1,
    lidToPn: Object.fromEntries(
      registry.mappings().map(({ lidJid, pnJid }) => [lidJid, pnJid]),
    ),
  };
}

export async function readRuntimeIdentityMappings(directory) {
  const target = path.join(directory, RUNTIME_IDENTITY_MAPPING_FILE);
  let content;
  try {
    content = await readFile(target, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  const document = JSON.parse(content);
  if (
    document?.version !== 1
    || !document?.lidToPn
    || typeof document.lidToPn !== "object"
    || Array.isArray(document.lidToPn)
  ) {
    throw new Error(`unsupported runtime identity mapping format in ${target}`);
  }
  const normalized = mappingDocument(
    Object.entries(document.lidToPn).map(([lidJid, pnJid]) => ({ lidJid, pnJid })),
  );
  return Object.entries(normalized.lidToPn).map(([lidJid, pnJid]) => ({ lidJid, pnJid }));
}

export async function persistRuntimeIdentityMappings(directory, mappings) {
  await mkdir(directory, { recursive: true });
  const target = path.join(directory, RUNTIME_IDENTITY_MAPPING_FILE);
  const temporary = `${target}.tmp-${process.pid}-${randomUUID()}`;
  try {
    const merged = new RuntimeIdentityRegistry();
    for (const mapping of await readRuntimeIdentityMappings(directory)) {
      merged.rememberMapping(mapping.lidJid, mapping.pnJid);
    }
    for (const mapping of Array.isArray(mappings) ? mappings : []) {
      merged.rememberMapping(mapping?.lidJid, mapping?.pnJid);
    }
    const content = `${JSON.stringify(mappingDocument(merged.mappings()), null, 2)}\n`;
    await writeFile(temporary, content, "utf8");
    await rename(temporary, target);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    throw error;
  }
}
