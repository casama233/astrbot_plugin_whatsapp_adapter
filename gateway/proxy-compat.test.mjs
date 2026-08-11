import assert from "node:assert/strict";
import test from "node:test";

import { HttpsProxyAgent } from "https-proxy-agent";

import {
  buildWhatsAppProxyConfig,
  isNoProxyMatch,
  sanitizedProxyMetadata,
  selectProxyEnvironment,
} from "./proxy-compat.mjs";


class RecordingAgent {
  static urls = [];

  constructor(url) {
    this.url = url;
    RecordingAgent.urls.push(url);
  }
}


test("no proxy environment has no side effects", () => {
  RecordingAgent.urls = [];
  const result = buildWhatsAppProxyConfig({
    environment: {},
    AgentClass: RecordingAgent,
  });

  assert.deepEqual(result.socketOptions, {});
  assert.deepEqual(result.metadata, {
    configured: false,
    active: false,
    reason: "not_configured",
  });
  assert.equal(RecordingAgent.urls.length, 0);
});


test("proxy environment follows secure uppercase/lowercase precedence", () => {
  const environment = {
    HTTPS_PROXY: " http://secure-upper.example:8443 ",
    https_proxy: "http://secure-lower.example:8081",
    HTTP_PROXY: "http://plain-upper.example:8082",
    http_proxy: "http://plain-lower.example:8083",
  };
  assert.deepEqual(selectProxyEnvironment(environment), {
    source: "HTTPS_PROXY",
    value: "http://secure-upper.example:8443",
  });

  delete environment.HTTPS_PROXY;
  assert.equal(selectProxyEnvironment(environment).source, "https_proxy");
  delete environment.https_proxy;
  assert.equal(selectProxyEnvironment(environment).source, "HTTP_PROXY");
  delete environment.HTTP_PROXY;
  assert.equal(selectProxyEnvironment(environment).source, "http_proxy");
});


test("lowercase proxy and no_proxy variables are honored independently", () => {
  RecordingAgent.urls = [];
  const result = buildWhatsAppProxyConfig({
    environment: {
      https_proxy: "http://proxy.example:3128",
      no_proxy: "mmg.whatsapp.net",
    },
    AgentClass: RecordingAgent,
  });

  assert.ok(result.socketOptions.agent instanceof RecordingAgent);
  assert.equal("fetchAgent" in result.socketOptions, false);
  assert.equal(result.metadata.proxy.source, "https_proxy");
  assert.equal(result.metadata.noProxySource, "no_proxy");
  assert.equal(result.metadata.targets.websocket.proxied, true);
  assert.equal(result.metadata.targets.media.bypassed, true);
  assert.equal(RecordingAgent.urls.length, 1);
});


test("NO_PROXY supports wildcard, exact, suffix and port-qualified rules", () => {
  const web = "wss://web.whatsapp.com/ws/chat";
  assert.equal(isNoProxyMatch(web, "*"), true);
  assert.equal(isNoProxyMatch(web, "web.whatsapp.com"), true);
  assert.equal(isNoProxyMatch(web, ".whatsapp.com"), true);
  assert.equal(isNoProxyMatch(web, "*.whatsapp.com"), true);
  assert.equal(isNoProxyMatch(web, "whatsapp.com"), true);
  assert.equal(isNoProxyMatch(web, "web.whatsapp.com:443"), true);
  assert.equal(isNoProxyMatch(web, "web.whatsapp.com:80"), false);
  assert.equal(isNoProxyMatch(web, "notwhatsapp.com"), false);
  assert.equal(isNoProxyMatch(web, "evilweb.whatsapp.com.example"), false);
  assert.equal(isNoProxyMatch("https://mmg.whatsapp.net/media", ".whatsapp.net"), true);
});


test("NO_PROXY can bypass web and media traffic separately or together", () => {
  const webBypass = buildWhatsAppProxyConfig({
    environment: {
      HTTPS_PROXY: "http://proxy.example:3128",
      NO_PROXY: "web.whatsapp.com",
    },
    AgentClass: RecordingAgent,
  });
  assert.equal("agent" in webBypass.socketOptions, false);
  assert.ok(webBypass.socketOptions.fetchAgent);

  RecordingAgent.urls = [];
  const allBypass = buildWhatsAppProxyConfig({
    environment: {
      HTTPS_PROXY: "http://proxy.example:3128",
      NO_PROXY: ".whatsapp.com,.whatsapp.net",
    },
    AgentClass: RecordingAgent,
  });
  assert.deepEqual(allBypass.socketOptions, {});
  assert.equal(allBypass.metadata.active, false);
  assert.equal(allBypass.metadata.reason, "bypassed");
  assert.equal(RecordingAgent.urls.length, 0);
});


test("invalid and unsupported proxy URLs fail closed without leaking input", () => {
  for (const proxyValue of ["not a URL", "socks5://secret@proxy.example:1080"]) {
    const result = buildWhatsAppProxyConfig({
      environment: { HTTPS_PROXY: proxyValue },
      AgentClass: RecordingAgent,
    });
    assert.deepEqual(result.socketOptions, {});
    assert.deepEqual(result.metadata, {
      configured: true,
      active: false,
      reason: "invalid_proxy_url",
      source: "HTTPS_PROXY",
    });
    assert.doesNotMatch(JSON.stringify(result.metadata), /secret|proxy\.example|not a URL/);
  }
});


test("proxy metadata omits credentials, path, query and fragment", () => {
  const metadata = sanitizedProxyMetadata(
    "https://alice:p%40ss@proxy.example:8443/private?token=top-secret#fragment",
    "HTTPS_PROXY",
  );
  assert.deepEqual(metadata, {
    source: "HTTPS_PROXY",
    protocol: "https",
    host: "proxy.example",
    port: "8443",
    authenticated: true,
  });
  const serialized = JSON.stringify(metadata);
  assert.doesNotMatch(serialized, /alice|p%40ss|private|top-secret|fragment/);
});


test("builds one compatible HttpsProxyAgent for Baileys socket options", () => {
  const result = buildWhatsAppProxyConfig({
    environment: { HTTPS_PROXY: "http://127.0.0.1:3128" },
  });

  assert.ok(result.socketOptions.agent instanceof HttpsProxyAgent);
  assert.strictEqual(result.socketOptions.agent, result.socketOptions.fetchAgent);
  assert.equal(typeof result.socketOptions.agent.addRequest, "function");
  assert.equal(result.metadata.active, true);
  result.socketOptions.agent.destroy();
});


test("agent construction failures return only sanitized failure metadata", () => {
  class FailingAgent {
    constructor() {
      throw new Error("credentials from agent internals");
    }
  }
  const result = buildWhatsAppProxyConfig({
    environment: {
      HTTPS_PROXY: "http://alice:secret@proxy.example:3128/path?token=value#hash",
    },
    AgentClass: FailingAgent,
  });

  assert.deepEqual(result.socketOptions, {});
  assert.equal(result.metadata.reason, "agent_creation_failed");
  assert.equal(result.metadata.proxy.authenticated, true);
  assert.doesNotMatch(JSON.stringify(result.metadata), /alice|secret|path|token|value|hash/);
});
