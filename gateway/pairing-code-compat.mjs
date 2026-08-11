const PAIRING_LOGIN_STATES = new Set([
  "starting",
  "qr_pending",
  "pair_code_pending",
]);

/**
 * Validate and normalize a phone number for Baileys requestPairingCode().
 *
 * Accept only an optional leading plus followed by 7 to 15 ASCII digits.  The
 * first digit must be non-zero, matching the useful subset of E.164 accepted
 * by WhatsApp.  Formatting characters are deliberately rejected rather than
 * silently rewritten so a typo cannot target a different account.
 */
export function normalizePairingPhone(value) {
  if (typeof value !== "string" || !/^\+?[1-9][0-9]{6,14}$/.test(value)) {
    throw new TypeError(
      "phone must contain 7 to 15 digits with a country code and no formatting",
    );
  }
  return value.startsWith("+") ? value.slice(1) : value;
}

/**
 * Decide whether the current socket is in the narrow state where requesting a
 * pairing code is safe.  Returned errors intentionally contain no identifiers.
 */
export function pairingCodeAvailability({
  socket = null,
  ready = false,
  registered = false,
  connectionStatus = "",
} = {}) {
  if (ready || registered) {
    return {
      ok: false,
      status: 409,
      error: "WhatsApp is already registered or connected.",
    };
  }
  if (!socket) {
    return {
      ok: false,
      status: 503,
      error: "WhatsApp login socket is not available yet.",
    };
  }
  if (!PAIRING_LOGIN_STATES.has(String(connectionStatus || ""))) {
    return {
      ok: false,
      status: 503,
      error: connectionStatus === "qr_expired"
        ? "WhatsApp login expired; restart the session before requesting a pairing code."
        : "WhatsApp is not currently waiting for login.",
    };
  }
  if (typeof socket.requestPairingCode !== "function") {
    return {
      ok: false,
      status: 501,
      error: "This Gateway runtime does not support phone pairing codes.",
    };
  }
  return { ok: true, status: 200 };
}

/**
 * Bound concurrent and repeated pairing-code requests without retaining the
 * submitted phone number or the generated code.  Call finish() in a finally
 * block after every successful begin().
 */
export function createPairingCodePolicy({ cooldownMs = 30_000, now = Date.now } = {}) {
  if (!Number.isFinite(cooldownMs) || cooldownMs < 0) {
    throw new TypeError("cooldownMs must be a non-negative finite number");
  }
  if (typeof now !== "function") throw new TypeError("now must be a function");

  let inFlight = false;
  let nextAllowedAt = 0;

  return Object.freeze({
    begin() {
      const startedAt = Number(now());
      if (!Number.isFinite(startedAt)) throw new TypeError("now() must return a finite number");
      if (inFlight) {
        return {
          ok: false,
          status: 429,
          error: "A pairing code request is already in progress.",
        };
      }

      const retryAfterMs = Math.max(0, Math.ceil(nextAllowedAt - startedAt));
      if (retryAfterMs > 0) {
        return {
          ok: false,
          status: 429,
          error: "Please wait before requesting another pairing code.",
          retryAfterMs,
        };
      }

      inFlight = true;
      let finished = false;
      return {
        ok: true,
        status: 200,
        finish() {
          if (finished) return;
          finished = true;
          inFlight = false;
          const finishedAt = Number(now());
          nextAllowedAt = (Number.isFinite(finishedAt) ? finishedAt : startedAt) + cooldownMs;
        },
      };
    },
  });
}
