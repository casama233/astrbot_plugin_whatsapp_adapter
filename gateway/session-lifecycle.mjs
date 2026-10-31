import path from "node:path";

export const INVALID_AUTH_STATUS_CODES = new Set([401, 411, 500]);

// Web QR pairing supplies `me` without setting the mobile-registration flag.
export function hasLinkedCredentials(creds) {
  return Boolean(creds?.me?.id || creds?.registered);
}

export function disconnectKind(statusCode) {
  if (INVALID_AUTH_STATUS_CODES.has(Number(statusCode))) return "auth_invalid";
  if (Number(statusCode) === 515) return "restart";
  return "transient";
}

export function reconnectDelayMs(attempt) {
  const safeAttempt = Math.max(1, Number(attempt) || 1);
  return Math.min(300_000, 3_000 * (2 ** (safeAttempt - 1)));
}

export function sessionDirectory(authRoot, sessionId) {
  if (!/^[a-zA-Z0-9._-]+$/.test(String(sessionId || ""))) {
    throw new Error("invalid auth session id");
  }
  return path.join(authRoot, ".sessions", sessionId);
}
