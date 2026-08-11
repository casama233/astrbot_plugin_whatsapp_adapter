import { HttpsProxyAgent } from "https-proxy-agent";


const PROXY_ENV_KEYS = [
  "HTTPS_PROXY",
  "https_proxy",
  "HTTP_PROXY",
  "http_proxy",
];
const NO_PROXY_ENV_KEYS = ["NO_PROXY", "no_proxy"];

export const WHATSAPP_PROXY_TARGETS = Object.freeze({
  websocket: "wss://web.whatsapp.com/ws/chat",
  media: "https://mmg.whatsapp.net/",
});


function firstNonEmptyEnvironmentValue(environment, keys) {
  for (const key of keys) {
    const value = String(environment?.[key] ?? "").trim();
    if (value) return { source: key, value };
  }
  return null;
}


/** Select a proxy using the documented uppercase/lowercase precedence. */
export function selectProxyEnvironment(environment = process.env) {
  return firstNonEmptyEnvironmentValue(environment, PROXY_ENV_KEYS);
}


/** Select the NO_PROXY list without exposing its contents in log metadata. */
export function selectNoProxyEnvironment(environment = process.env) {
  return firstNonEmptyEnvironmentValue(environment, NO_PROXY_ENV_KEYS);
}


function effectivePort(url) {
  if (url.port) return url.port;
  if (url.protocol === "https:" || url.protocol === "wss:") return "443";
  if (url.protocol === "http:" || url.protocol === "ws:") return "80";
  return "";
}


function normalizedHostname(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "");
}


function splitNoProxyRule(rawRule) {
  let rule = String(rawRule || "").trim().toLowerCase();
  if (!rule) return null;
  if (rule === "*") return { wildcard: true, host: "", port: "" };

  let port = "";
  if (rule.startsWith("[")) {
    const closingBracket = rule.indexOf("]");
    if (closingBracket < 0) return null;
    const remainder = rule.slice(closingBracket + 1);
    if (remainder) {
      if (!/^:\d+$/.test(remainder)) return null;
      port = remainder.slice(1);
    }
    rule = rule.slice(1, closingBracket);
  } else {
    const colonCount = (rule.match(/:/g) || []).length;
    if (colonCount === 1) {
      const separator = rule.lastIndexOf(":");
      const possiblePort = rule.slice(separator + 1);
      if (/^\d+$/.test(possiblePort)) {
        port = possiblePort;
        rule = rule.slice(0, separator);
      }
    }
  }

  const suffix = rule.startsWith("*.") || rule.startsWith(".");
  const host = normalizedHostname(rule.replace(/^\*?\./, ""));
  if (!host || host.includes("*") || host.includes("/") || host.includes("@")) {
    return null;
  }
  return { wildcard: false, host, port, suffix };
}


/**
 * Match a target URL against a conventional comma-separated NO_PROXY value.
 * Bare domains and leading-dot domains cover the domain itself and its
 * subdomains; an optional port limits the match to that effective URL port.
 */
export function isNoProxyMatch(target, noProxyValue) {
  let url;
  try {
    url = target instanceof URL ? target : new URL(String(target));
  } catch {
    return false;
  }
  const hostname = normalizedHostname(url.hostname);
  const port = effectivePort(url);
  if (!hostname) return false;

  for (const rawRule of String(noProxyValue || "").split(",")) {
    const rule = splitNoProxyRule(rawRule);
    if (!rule) continue;
    if (rule.wildcard) return true;
    if (rule.port && rule.port !== port) continue;
    if (hostname === rule.host || hostname.endsWith(`.${rule.host}`)) return true;
  }
  return false;
}


function parseProxyUrl(value) {
  const parsed = new URL(value);
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new TypeError("unsupported proxy URL");
  }
  return parsed;
}


/** Return log-safe proxy fields only; never return userinfo, path or tokens. */
export function sanitizedProxyMetadata(proxyUrl, source = null) {
  const parsed = proxyUrl instanceof URL ? proxyUrl : parseProxyUrl(proxyUrl);
  return {
    source,
    protocol: parsed.protocol.slice(0, -1),
    host: normalizedHostname(parsed.hostname),
    port: parsed.port || null,
    authenticated: Boolean(parsed.username || parsed.password),
  };
}


function targetMetadata(target, bypassed) {
  const url = target instanceof URL ? target : new URL(String(target));
  return {
    host: normalizedHostname(url.hostname),
    port: effectivePort(url),
    bypassed,
    proxied: !bypassed,
  };
}


/**
 * Construct the Baileys socket options for WhatsApp Web and media traffic.
 * Callers must log only `metadata`, never the selected environment value or
 * the returned agent, because the latter intentionally retains credentials.
 */
export function buildWhatsAppProxyConfig({
  environment = process.env,
  AgentClass = HttpsProxyAgent,
  targets = WHATSAPP_PROXY_TARGETS,
} = {}) {
  const selected = selectProxyEnvironment(environment);
  if (!selected) {
    return {
      socketOptions: {},
      metadata: { configured: false, active: false, reason: "not_configured" },
    };
  }

  let proxyUrl;
  try {
    proxyUrl = parseProxyUrl(selected.value);
  } catch {
    return {
      socketOptions: {},
      metadata: {
        configured: true,
        active: false,
        reason: "invalid_proxy_url",
        source: selected.source,
      },
    };
  }

  const noProxy = selectNoProxyEnvironment(environment);
  const websocketBypassed = isNoProxyMatch(targets.websocket, noProxy?.value);
  const mediaBypassed = isNoProxyMatch(targets.media, noProxy?.value);
  const metadata = {
    configured: true,
    active: false,
    proxy: sanitizedProxyMetadata(proxyUrl, selected.source),
    noProxySource: noProxy?.source || null,
    targets: {
      websocket: targetMetadata(targets.websocket, websocketBypassed),
      media: targetMetadata(targets.media, mediaBypassed),
    },
  };

  if (websocketBypassed && mediaBypassed) {
    return {
      socketOptions: {},
      metadata: { ...metadata, reason: "bypassed" },
    };
  }

  let agent;
  try {
    agent = new AgentClass(proxyUrl);
  } catch {
    return {
      socketOptions: {},
      metadata: { ...metadata, reason: "agent_creation_failed" },
    };
  }

  const socketOptions = {};
  if (!websocketBypassed) socketOptions.agent = agent;
  if (!mediaBypassed) socketOptions.fetchAgent = agent;
  return {
    socketOptions,
    metadata: { ...metadata, active: true, reason: "enabled" },
  };
}
