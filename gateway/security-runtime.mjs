import { randomUUID, timingSafeEqual } from "node:crypto";
import { lookup } from "node:dns/promises";
import { mkdir, realpath, stat, unlink, writeFile } from "node:fs/promises";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import path from "node:path";

const DEFAULT_REMOTE_MAX_BYTES = 32 * 1024 * 1024;
const DEFAULT_REMOTE_TIMEOUT_MS = 15_000;
const MAX_REDIRECTS = 4;

function safeEqualText(left, right) {
  const a = Buffer.from(String(left || ""), "utf8");
  const b = Buffer.from(String(right || ""), "utf8");
  if (!a.length || a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

export function isAuthorizedGatewayRequest(req, expectedToken) {
  const token = String(expectedToken || "").trim();
  if (!token) return false;
  const header = String(req?.headers?.authorization || "");
  const match = /^Bearer\s+(.+)$/i.exec(header);
  return Boolean(match && safeEqualText(match[1].trim(), token));
}

function isPublicIpv4(address) {
  const parts = String(address || "").split(".").map((value) => Number(value));
  if (parts.length !== 4 || parts.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) return false;
  const [a, b, c] = parts;
  if (a === 0 || a === 10 || a === 127) return false;
  if (a === 100 && b >= 64 && b <= 127) return false;
  if (a === 169 && b === 254) return false;
  if (a === 172 && b >= 16 && b <= 31) return false;
  if (a === 192 && b === 0 && c === 0) return false;
  if (a === 192 && b === 0 && c === 2) return false;
  if (a === 192 && b === 168) return false;
  if (a === 198 && (b === 18 || b === 19)) return false;
  if (a === 198 && b === 51 && c === 100) return false;
  if (a === 203 && b === 0 && c === 113) return false;
  if (a >= 224) return false;
  return true;
}

function isPublicIpv6(address) {
  const value = String(address || "").split("%")[0].toLowerCase();
  if (!value || value === "::" || value === "::1") return false;
  if (value.startsWith("::ffff:")) {
    const mapped = value.slice("::ffff:".length);
    if (net.isIP(mapped) === 4) return isPublicIpv4(mapped);
  }
  const first = Number.parseInt(value.split(":")[0] || "0", 16);
  if (Number.isFinite(first)) {
    if ((first & 0xfe00) === 0xfc00) return false;
    if ((first & 0xffc0) === 0xfe80) return false;
    if ((first & 0xff00) === 0xff00) return false;
  }
  if (value === "2001:db8::" || value.startsWith("2001:db8:")) return false;
  return true;
}

export function isPublicIpAddress(address) {
  const family = net.isIP(String(address || "").split("%")[0]);
  if (family === 4) return isPublicIpv4(address);
  if (family === 6) return isPublicIpv6(address);
  return false;
}

async function resolvePinnedPublicAddress(hostname) {
  const host = String(hostname || "").replace(/^\[|\]$/g, "");
  if (!host) throw new Error("remote media URL has no hostname");
  const literalFamily = net.isIP(host);
  if (literalFamily) {
    if (!isPublicIpAddress(host)) throw new Error("remote media URL resolves to a non-public address");
    return { address: host, family: literalFamily };
  }
  const resolved = await lookup(host, { all: true, verbatim: true });
  if (!resolved.length || resolved.some((item) => !isPublicIpAddress(item.address))) {
    throw new Error("remote media URL resolves to a non-public address");
  }
  return resolved[0];
}

function pinnedRequest(url, resolved, maxBytes, timeoutMs) {
  return new Promise((resolve, reject) => {
    const transport = url.protocol === "https:" ? https : http;
    const request = transport.request(
      {
        protocol: url.protocol,
        hostname: url.hostname.replace(/^\[|\]$/g, ""),
        port: url.port || undefined,
        method: "GET",
        path: `${url.pathname}${url.search}`,
        headers: {
          accept: "*/*",
          host: url.host,
          "user-agent": "astrbot-whatsapp-gateway/secure-media-fetch",
        },
        lookup: (_hostname, _options, callback) => callback(null, resolved.address, resolved.family),
      },
      (response) => {
        const status = Number(response.statusCode || 0);
        const location = response.headers.location;
        if ([301, 302, 303, 307, 308].includes(status) && location) {
          response.resume();
          resolve({ status, location, body: null });
          return;
        }
        if (status < 200 || status >= 300) {
          response.resume();
          reject(new Error(`remote media request failed with HTTP ${status}`));
          return;
        }
        const contentLength = Number(response.headers["content-length"] || 0);
        if (Number.isFinite(contentLength) && contentLength > maxBytes) {
          response.resume();
          reject(new Error("remote media exceeds the outbound size limit"));
          return;
        }
        const chunks = [];
        let total = 0;
        response.on("data", (chunk) => {
          total += chunk.length;
          if (total > maxBytes) {
            request.destroy(new Error("remote media exceeds the outbound size limit"));
            return;
          }
          chunks.push(chunk);
        });
        response.on("end", () => resolve({ status, location: null, body: Buffer.concat(chunks) }));
      },
    );
    request.setTimeout(timeoutMs, () => request.destroy(new Error("remote media request timed out")));
    request.on("error", reject);
    request.end();
  });
}

function outboundMaxBytes() {
  const mb = Number(process.env.WA_OUTBOUND_MEDIA_MAX_MB || 32);
  if (!Number.isFinite(mb) || mb <= 0) return DEFAULT_REMOTE_MAX_BYTES;
  return Math.min(Math.max(Math.floor(mb * 1024 * 1024), 1024), 128 * 1024 * 1024);
}

function safeExtension(url) {
  const ext = path.extname(url.pathname || "");
  return /^\.[A-Za-z0-9]{1,10}$/.test(ext) ? ext : ".bin";
}

async function downloadRemoteMedia(sourceUrl, tempDir) {
  await mkdir(tempDir, { recursive: true });
  let current = new URL(sourceUrl);
  if (!/^https?:$/.test(current.protocol)) throw new Error("remote media URL must use HTTP or HTTPS");
  if (current.username || current.password) throw new Error("remote media URL must not contain credentials");

  const maxBytes = outboundMaxBytes();
  for (let redirect = 0; redirect <= MAX_REDIRECTS; redirect += 1) {
    const resolved = await resolvePinnedPublicAddress(current.hostname);
    const response = await pinnedRequest(current, resolved, maxBytes, DEFAULT_REMOTE_TIMEOUT_MS);
    if (response.location) {
      if (redirect === MAX_REDIRECTS) throw new Error("remote media exceeded the redirect limit");
      current = new URL(response.location, current);
      if (!/^https?:$/.test(current.protocol)) throw new Error("remote media redirect must use HTTP or HTTPS");
      if (current.username || current.password) throw new Error("remote media redirect must not contain credentials");
      continue;
    }
    const target = path.join(tempDir, `wa-outbound-${Date.now()}-${randomUUID()}${safeExtension(current)}`);
    await writeFile(target, response.body, { flag: "wx", mode: 0o600 });
    return {
      pathOrUrl: target,
      cleanup: async () => {
        try {
          await unlink(target);
        } catch (error) {
          if (error?.code !== "ENOENT") throw error;
        }
      },
    };
  }
  throw new Error("remote media could not be downloaded safely");
}

function pathInside(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

async function allowedLocalRoots(tempDir) {
  const roots = [tempDir];
  const extra = String(process.env.WA_MEDIA_ALLOWED_ROOTS || "")
    .split(path.delimiter)
    .map((value) => value.trim())
    .filter(Boolean);
  roots.push(...extra);
  const resolved = [];
  for (const root of roots) {
    try {
      resolved.push(await realpath(path.resolve(root)));
    } catch {
      // Ignore missing optional roots. tempDir is created by the caller.
    }
  }
  return resolved;
}

export async function prepareSafeMediaSource(pathOrUrl, { tempDir }) {
  const value = String(pathOrUrl || "").trim();
  if (!value) throw new Error("pathOrUrl is required");
  if (/^file:\/\//i.test(value)) throw new Error("file:// media sources are not allowed");
  if (/^https?:\/\//i.test(value)) return downloadRemoteMedia(value, tempDir);
  const windowsDrivePath = /^[A-Za-z]:[\\/]/.test(value);
  if (!windowsDrivePath && /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) {
    throw new Error("unsupported media URL scheme");
  }

  await mkdir(tempDir, { recursive: true });
  const candidate = await realpath(path.resolve(value));
  const info = await stat(candidate);
  if (!info.isFile()) throw new Error("local media source must be a regular file");
  const roots = await allowedLocalRoots(tempDir);
  if (!roots.some((root) => pathInside(candidate, root))) {
    throw new Error("local media source is outside the allowed media roots");
  }
  return { pathOrUrl: candidate, cleanup: async () => {} };
}
