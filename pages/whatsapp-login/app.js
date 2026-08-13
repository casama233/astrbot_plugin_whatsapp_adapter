import { createTwoStepGate } from "./two-step-action.js";

const bridge = window.AstrBotPluginPage;
const PAGE_PREFIX = "pages.whatsapp-login";

function t(key, fallback = key) {
  return bridge.t(`${PAGE_PREFIX}.${key}`, fallback);
}

function tf(key, fallback, values = {}) {
  return t(key, fallback).replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match,
  );
}

const els = {
  metricStatusText: document.getElementById("metricStatusText"),
  metricStatusSub: document.getElementById("metricStatusSub"),
  healthDot: document.getElementById("healthDot"),
  metricGatewayText: document.getElementById("metricGatewayText"),
  metricGatewaySub: document.getElementById("metricGatewaySub"),
  gatewayDot: document.getElementById("gatewayDot"),
  metricAccountText: document.getElementById("metricAccountText"),
  metricAccountSub: document.getElementById("metricAccountSub"),
  metricUptimeText: document.getElementById("metricUptimeText"),
  metricUptimeSub: document.getElementById("metricUptimeSub"),
  statusText: document.getElementById("statusText"),
  selfJid: document.getElementById("selfJid"),
  selfLid: document.getElementById("selfLid"),
  authDir: document.getElementById("authDir"),
  gatewayUrl: document.getElementById("gatewayUrl"),
  runtimeStatus: document.getElementById("runtimeStatus"),
  configuredStatus: document.getElementById("configuredStatus"),
  policyDm: document.getElementById("policyDm"),
  policyGroup: document.getElementById("policyGroup"),
  policyAllowFrom: document.getElementById("policyAllowFrom"),
  policyGroups: document.getElementById("policyGroups"),
  updatePhase: document.getElementById("updatePhase"),
  currentVersion: document.getElementById("currentVersion"),
  latestVersion: document.getElementById("latestVersion"),
  updateMessage: document.getElementById("updateMessage"),
  releaseNotesWrap: document.getElementById("releaseNotesWrap"),
  releaseNotes: document.getElementById("releaseNotes"),
  qrWrap: document.getElementById("qrWrap"),
  qrHint: document.getElementById("qrHint"),
  qrPhase: document.getElementById("qrPhase"),
  pairCodePanel: document.getElementById("pairCodePanel"),
  pairCodeForm: document.getElementById("pairCodeForm"),
  pairPhone: document.getElementById("pairPhone"),
  pairCodeStatus: document.getElementById("pairCodeStatus"),
  pairCodeResult: document.getElementById("pairCodeResult"),
  pairCodeValue: document.getElementById("pairCodeValue"),
  eventLog: document.getElementById("eventLog"),
  refreshBtn: document.getElementById("refresh"),
  refreshQrBtn: document.getElementById("refreshQr"),
  requestPairCodeBtn: document.getElementById("requestPairCode"),
  copyPairCodeBtn: document.getElementById("copyPairCode"),
  restartBtn: document.getElementById("restart"),
  logoutBtn: document.getElementById("logout"),
  checkUpdateBtn: document.getElementById("checkUpdate"),
  installUpdateBtn: document.getElementById("installUpdate"),
  clearLogBtn: document.getElementById("clearLog"),
  refreshCountdown: document.getElementById("refreshCountdown"),
  liveDot: document.getElementById("liveDot"),
};

let countdown = 5;
let countdownTimer = null;
let loggedOutSince = 0;
let currentConnectionStatus = "unknown";
let runtimeLoaded = false;
let refreshPromise = null;
let updateInfo = null;
let updatePollTimer = null;
let updateStartedHere = false;
let updatePollStartedAt = 0;
let pairCodeRequestPending = false;
let pairCodeRequestToken = 0;
let pairCodeCooldownUntil = 0;
let pairCodeCooldownTimer = null;
let activeLocale = "en-US";
let lastStatusData = null;
let lastQrData = null;
let lastRuntimeData = null;
let installBaseNodes = [];
let logoutBaseNodes = [];

const CONFIRM_WINDOW_MS = 10_000;
const UPDATE_POLL_MAX_MS = 30 * 60 * 1000;
const PAIR_CODE_COOLDOWN_MS = 30_000;
const LOGGED_OUT_TIMEOUT_MS = 15_000;
const updateConfirmGate = createTwoStepGate({ windowMs: CONFIRM_WINDOW_MS });
const logoutConfirmGate = createTwoStepGate({ windowMs: CONFIRM_WINDOW_MS });
let updateConfirmTimer = null;
let logoutConfirmTimer = null;

function fmtTime() {
  const locale = bridge.getLocale?.() || activeLocale || "en-US";
  try {
    return new Intl.DateTimeFormat(locale, {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date());
  } catch (_) {
    return new Date().toLocaleTimeString(undefined, { hour12: false });
  }
}

function trunc(value, length = 48) {
  if (!value) return "-";
  const text = String(value);
  return text.length > length ? `${text.slice(0, length)}…` : text;
}

function formatCount(value, key, fallback) {
  const count = Array.isArray(value) ? value.length : Number(value);
  return count ? tf(key, fallback, { count }) : t("policy.none", "None");
}

function setDot(element, state) {
  element.className = `status-dot${element.classList.contains("status-dot-sm") ? " status-dot-sm" : ""}`;
  if (state === "green") element.classList.add("green");
  else if (state === "yellow") element.classList.add("yellow");
  else if (state === "red") element.classList.add("red");
  else element.classList.add("gray");
}

function setTag(element, state, label) {
  element.className = "status-tag";
  if (state === "green") element.classList.add("green");
  else if (state === "yellow") element.classList.add("yellow");
  else if (state === "red") element.classList.add("red");
  else element.classList.add("gray");
  element.textContent = label;
}

function clearChildren(element) {
  while (element.firstChild) element.firstChild.remove();
}

function applyStaticI18n() {
  activeLocale = bridge.getLocale?.() || activeLocale || "en-US";
  document.documentElement.lang = activeLocale;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const key = element.dataset.i18n;
    if (key) element.textContent = t(key, element.textContent.trim());
  });
  document.querySelectorAll("[data-i18n-title]").forEach((element) => {
    const key = element.dataset.i18nTitle;
    if (key) element.title = t(key, element.title);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    const key = element.dataset.i18nPlaceholder;
    if (key) element.placeholder = t(key, element.placeholder);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    const key = element.dataset.i18nAriaLabel;
    if (key) element.setAttribute("aria-label", t(key, element.getAttribute("aria-label") || ""));
  });
}

function captureActionButtonNodes() {
  installBaseNodes = els.installUpdateBtn
    ? [...els.installUpdateBtn.childNodes].map((node) => node.cloneNode(true))
    : [];
  logoutBaseNodes = els.logoutBtn
    ? [...els.logoutBtn.childNodes].map((node) => node.cloneNode(true))
    : [];
}

function restoreInstallButton() {
  if (!els.installUpdateBtn) return;
  if (installBaseNodes.length) {
    els.installUpdateBtn.replaceChildren(
      ...installBaseNodes.map((node) => node.cloneNode(true)),
    );
  } else {
    els.installUpdateBtn.textContent = t("update.install", "Update now");
  }
}

function restoreLogoutButton() {
  if (!els.logoutBtn) return;
  if (logoutBaseNodes.length) {
    els.logoutBtn.replaceChildren(
      ...logoutBaseNodes.map((node) => node.cloneNode(true)),
    );
  } else {
    els.logoutBtn.textContent = t("actions.logout", "Log out and scan again");
  }
}

function setInstallButtonBusy(isBusy) {
  restoreInstallButton();
  const label = els.installUpdateBtn?.querySelector("[data-action-label]");
  if (label) label.textContent = isBusy ? t("update.installing", "Updating…") : t("update.install", "Update now");
  else if (els.installUpdateBtn) els.installUpdateBtn.textContent = isBusy ? t("update.installing", "Updating…") : t("update.install", "Update now");
}

function setPairCodeStatus(message = "", state = "") {
  if (!els.pairCodeStatus) return;
  els.pairCodeStatus.textContent = message;
  els.pairCodeStatus.className = "pair-code-status";
  if (state) els.pairCodeStatus.classList.add(state);
}

function updatePairCodeControls() {
  if (!els.requestPairCodeBtn || !els.pairPhone) return;
  const remaining = Math.max(0, pairCodeCooldownUntil - Date.now());
  const coolingDown = remaining > 0;
  const unavailable = Boolean(els.pairCodePanel?.hidden);
  els.requestPairCodeBtn.disabled = unavailable || pairCodeRequestPending || coolingDown;
  els.pairPhone.disabled = unavailable || pairCodeRequestPending || coolingDown;
  els.requestPairCodeBtn.textContent = pairCodeRequestPending
    ? t("pairing.requesting", "Requesting…")
    : coolingDown
      ? tf("pairing.cooldown", "Wait {seconds}s", { seconds: Math.ceil(remaining / 1000) })
      : t("pairing.request", "Get pairing code");
}

function stopPairCodeCooldown() {
  if (pairCodeCooldownTimer) clearInterval(pairCodeCooldownTimer);
  pairCodeCooldownTimer = null;
}

function startPairCodeCooldown() {
  stopPairCodeCooldown();
  pairCodeCooldownUntil = Date.now() + PAIR_CODE_COOLDOWN_MS;
  updatePairCodeControls();
  pairCodeCooldownTimer = setInterval(() => {
    updatePairCodeControls();
    if (Date.now() >= pairCodeCooldownUntil) {
      stopPairCodeCooldown();
      pairCodeCooldownUntil = 0;
      updatePairCodeControls();
    }
  }, 1000);
}

function clearPairCode({ hide = false } = {}) {
  pairCodeRequestToken += 1;
  pairCodeRequestPending = false;
  pairCodeCooldownUntil = 0;
  stopPairCodeCooldown();
  if (els.pairPhone) els.pairPhone.value = "";
  if (els.pairCodeValue) els.pairCodeValue.textContent = "";
  if (els.pairCodeResult) els.pairCodeResult.hidden = true;
  setPairCodeStatus();
  if (hide && els.pairCodePanel) els.pairCodePanel.hidden = true;
  updatePairCodeControls();
}

function setPairCodeAvailable(available) {
  if (!els.pairCodePanel) return;
  if (!available) {
    clearPairCode({ hide: true });
    return;
  }
  els.pairCodePanel.hidden = false;
  updatePairCodeControls();
}

function normalizePairingPhone(value) {
  const raw = String(value || "").trim();
  if (!raw || !/^\+?[0-9\s().-]+$/.test(raw)) return "";
  const digits = raw.replace(/\D/g, "");
  return /^[1-9][0-9]{6,14}$/.test(digits) ? digits : "";
}

function log(level, message) {
  const empty = els.eventLog.querySelector(".log-empty");
  if (empty) empty.remove();
  const entry = document.createElement("div");
  entry.className = "log-entry";
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = fmtTime();
  const text = document.createElement("span");
  text.className = "log-msg";
  if (["info", "warn", "error"].includes(level)) text.classList.add(level);
  text.textContent = message;
  entry.append(time, text);
  els.eventLog.appendChild(entry);
  els.eventLog.scrollTop = els.eventLog.scrollHeight;
  while (els.eventLog.children.length > 100) els.eventLog.firstChild.remove();
}

function renderDashboard(data) {
  if (!data) return;
  lastStatusData = data;
  const status = data.status || (data.ready ? "connected" : "unknown");
  currentConnectionStatus = status;
  const isReady = data.ready || status === "connected";
  const isStarting = ["starting", "pairing", "pairing_restart", "resetting", "qr_pending"].includes(status) || data.hasQr;
  setPairCodeAvailable(!isReady);

  els.metricStatusText.textContent = isReady
    ? t("status.connected", "Connected")
    : isStarting ? t("status.waiting", "Waiting") : t("status.disconnected", "Disconnected");
  els.metricStatusSub.textContent = status;
  setDot(els.healthDot, isReady ? "green" : isStarting ? "yellow" : "red");

  const gatewayHealthy = data.gatewayHealthy !== undefined ? data.gatewayHealthy : data.ok;
  els.metricGatewayText.textContent = gatewayHealthy ? t("status.healthy", "Healthy") : t("status.unhealthy", "Unhealthy");
  els.metricGatewaySub.textContent = gatewayHealthy && isReady ? t("status.running", "Running") : t("status.pending", "Pending");
  setDot(els.gatewayDot, gatewayHealthy ? (isReady ? "green" : "yellow") : "red");

  els.metricAccountText.textContent = data.selfJid ? trunc(data.selfJid, 28) : "-";
  els.metricAccountSub.textContent = data.selfJid ? t("metrics.whatsapp_account", "WhatsApp account") : t("status.not_logged_in", "Not signed in");
  if (data.lastPresenceAt) {
    els.metricUptimeText.textContent = data.lastPresenceAt.replace("T", " ").split(".")[0];
    els.metricUptimeSub.textContent = t("status.last_online", "Last online");
  } else {
    els.metricUptimeText.textContent = isReady ? t("status.online", "Online") : "-";
    els.metricUptimeSub.textContent = isReady ? t("status.currently_online", "Currently online") : t("status.no_data", "No data");
  }
}

function renderSession(data) {
  if (!data) return;
  lastStatusData = data;
  const status = data.status || (data.ready ? "connected" : "unknown");
  const isReady = data.ready || status === "connected";
  const isStarting = ["starting", "pairing", "pairing_restart", "resetting", "qr_pending"].includes(status) || data.hasQr;
  setTag(
    els.statusText,
    isReady ? "green" : isStarting ? "yellow" : "red",
    isReady ? t("status.connected", "Connected") : isStarting ? t("status.waiting", "Waiting") : status,
  );
  els.selfJid.textContent = data.selfJid || "-";
  els.selfLid.textContent = data.selfLid || "-";
  els.authDir.textContent = data.authDir || "-";
  els.gatewayUrl.textContent = data.baseUrl || "-";
  els.configuredStatus.textContent = data.config ? t("session.configured_yes", "✓ Configured") : t("session.configured_no", "✗ Not synced");
  if (data.runtimeRequirements) renderRuntime(data.runtimeRequirements);
  const cfg = data.config || {};
  els.policyDm.textContent = cfg.dmPolicy || "-";
  els.policyGroup.textContent = cfg.groupPolicy || "-";
  els.policyAllowFrom.textContent = Array.isArray(cfg.allowFrom) ? formatCount(cfg.allowFrom, "policy.numbers_count", "{count} numbers") : "-";
  els.policyGroups.textContent = Array.isArray(cfg.groups) ? formatCount(cfg.groups, "policy.groups_count", "{count} groups") : "-";
}

function renderRuntime(runtime) {
  if (!runtime || !els.runtimeStatus) return;
  lastRuntimeData = runtime;
  runtimeLoaded = true;
  els.runtimeStatus.textContent = runtime.ready ? t("runtime.ready", "✓ Ready") : t("runtime.unavailable", "✗ Unavailable");
  els.runtimeStatus.style.color = runtime.ready ? "var(--green, #25D366)" : "var(--red, #ff5c6c)";
  els.runtimeStatus.title = [
    runtime.node?.version ? `Node ${runtime.node.version}` : null,
    runtime.npm?.path ? `npm ${runtime.npm.path}` : null,
    runtime.dependenciesInstalled ? t("runtime.baileys_installed", "Baileys installed") : t("runtime.baileys_pending", "Baileys not installed"),
  ].filter(Boolean).join(" · ");
  if (runtime.message) console.debug("WhatsApp runtime:", runtime.message);
}

function updatePhaseLabel(phase) {
  return t(`update.phase.${phase}`, phase);
}

function updateMessageFor(data) {
  const phase = data?.phase || "idle";
  if (data?.busy && !["completed", "failed"].includes(phase)) return t("update.message.busy", "Update transaction in progress…");
  if (phase === "available") return t("update.message.available", "A verified stable Release candidate is available.");
  if (phase === "up_to_date") return t("update.message.up_to_date", "This plugin is up to date.");
  if (phase === "checking") return t("update.message.checking", "Checking GitHub Release and artifact digest…");
  if (phase === "check_failed") return t("update.message.check_failed", "Could not verify a GitHub Release candidate.");
  if (phase === "completed") return t("update.message.completed", "Plugin update completed and passed health checks.");
  if (phase === "failed") return t("update.message.failed", "Plugin update failed or rolled back. Check AstrBot logs for details.");
  return t("update.message.idle", "Checks a release-pinned GitHub artifact and verifies its digest before installation.");
}

function renderUpdate(data) {
  if (!data) return;
  updateInfo = data;
  const phase = data.phase || "idle";
  const isError = ["failed", "check_failed"].includes(phase);
  const isSuccess = ["up_to_date", "completed"].includes(phase);
  const isBusy = Boolean(data.busy);
  els.currentVersion.textContent = data.currentVersion ? `v${data.currentVersion}` : "-";
  els.latestVersion.textContent = data.latestVersion ? `v${data.latestVersion}` : "-";
  if (!updateConfirmGate.isArmed()) els.updateMessage.textContent = updateMessageFor(data);
  els.updatePhase.textContent = updatePhaseLabel(phase);
  els.updatePhase.className = "phase-badge";
  if (isError) els.updatePhase.classList.add("error");
  else if (isSuccess) els.updatePhase.classList.add("connected");
  else if (isBusy || data.updateAvailable) els.updatePhase.classList.add("connecting");
  const notes = data.release?.notes || "";
  els.releaseNotes.textContent = notes;
  els.releaseNotesWrap.hidden = !notes;
  els.checkUpdateBtn.disabled = isBusy;
  els.installUpdateBtn.disabled = isBusy || !data.updateAvailable;
  if (!updateConfirmGate.isArmed()) setInstallButtonBusy(isBusy);
  if (data.message) console.debug("WhatsApp updater:", data.message);
}

function disarmUpdateConfirmation({ rerender = true } = {}) {
  updateConfirmGate.disarm();
  if (updateConfirmTimer) clearTimeout(updateConfirmTimer);
  updateConfirmTimer = null;
  if (rerender && updateInfo) renderUpdate(updateInfo);
}

function armUpdateConfirmation() {
  if (updateConfirmTimer) clearTimeout(updateConfirmTimer);
  const version = updateInfo?.latestVersion ? `v${String(updateInfo.latestVersion).replace(/^v/, "")}` : t("update.candidate", "this version");
  els.installUpdateBtn.replaceChildren(document.createTextNode(tf("update.confirm.armed", "Click again to confirm {version}", { version })));
  els.updateMessage.textContent = tf(
    "update.confirm.helper",
    "The {version} Release artifact is pinned. Click again within 10 seconds to install exactly this verified candidate.",
    { version },
  );
  updateConfirmTimer = setTimeout(() => {
    updateConfirmGate.disarm();
    updateConfirmTimer = null;
    if (updateInfo) renderUpdate(updateInfo);
  }, CONFIRM_WINDOW_MS);
}

function disarmLogoutConfirmation({ restore = true } = {}) {
  logoutConfirmGate.disarm();
  if (logoutConfirmTimer) clearTimeout(logoutConfirmTimer);
  logoutConfirmTimer = null;
  if (restore && els.logoutBtn && !els.logoutBtn.disabled) restoreLogoutButton();
}

function armLogoutConfirmation() {
  if (logoutConfirmTimer) clearTimeout(logoutConfirmTimer);
  els.logoutBtn.replaceChildren(document.createTextNode(t("confirm.logout_armed", "Click again to confirm logout")));
  logoutConfirmTimer = setTimeout(() => disarmLogoutConfirmation(), CONFIRM_WINDOW_MS);
}

async function checkUpdate({ quiet = false } = {}) {
  disarmUpdateConfirmation({ rerender: false });
  if (!quiet) log("info", t("log.update_check_start", "Checking GitHub Release and official artifact digest…"));
  els.checkUpdateBtn.disabled = true;
  try {
    const data = await bridge.apiPost("update/check", {});
    renderUpdate(data);
    if (!quiet) log(data.updateAvailable ? "warn" : "info", t("log.update_check_done", "Release candidate check completed"));
    return data;
  } catch (error) {
    try {
      renderUpdate(await bridge.apiGet("update/status"));
    } catch (_) {}
    console.error("WhatsApp update check failed:", error);
    log("error", tf("log.update_check_failed", "Update check failed: {error}", { error }));
    return null;
  } finally {
    if (!updateInfo?.busy) els.checkUpdateBtn.disabled = false;
  }
}

function stopUpdatePolling() {
  if (updatePollTimer) clearTimeout(updatePollTimer);
  updatePollTimer = null;
}

function pollUpdateStatus() {
  stopUpdatePolling();
  updatePollTimer = setTimeout(async () => {
    if (!updatePollStartedAt) updatePollStartedAt = Date.now();
    if (Date.now() - updatePollStartedAt > UPDATE_POLL_MAX_MS) {
      updateStartedHere = false;
      els.updateMessage.textContent = t("update.message.poll_timeout", "Frontend polling stopped after 30 minutes. The durable backend transaction may still be running; reload the page to resume reading its state.");
      log("warn", t("log.update_poll_timeout", "Update polling stopped after 30 minutes without marking the backend transaction failed"));
      return;
    }
    try {
      const data = await bridge.apiGet("update/status");
      renderUpdate(data);
      if (data.busy) {
        pollUpdateStatus();
        return;
      }
      if (data.phase === "completed") {
        log("info", t("log.update_completed", "Plugin update completed and passed health checks"));
        if (updateStartedHere) {
          updateStartedHere = false;
          setTimeout(() => window.location.reload(), 1200);
        }
        return;
      }
      if (data.phase === "failed") log("error", t("log.update_failed", "Plugin update failed or rolled back"));
    } catch (_) {
      pollUpdateStatus();
    }
  }, 1500);
}

async function initializeUpdate() {
  try {
    const state = await bridge.apiGet("update/status");
    renderUpdate(state);
    if (state.busy) {
      updatePollStartedAt = Number(state.startedAt || 0) * 1000 || Date.now();
      pollUpdateStatus();
      return;
    }
    const checkedAt = Number(state.checkedAt || 0) * 1000;
    if (checkedAt && Date.now() - checkedAt < 5 * 60 * 1000) return;
  } catch (_) {}
  await checkUpdate({ quiet: true });
}

function renderQr(data) {
  if (!data) return;
  lastQrData = data;
  if (data.ready) {
    loggedOutSince = 0;
    clearChildren(els.qrWrap);
    const placeholder = document.createElement("div");
    placeholder.className = "qr-placeholder connected";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "48");
    svg.setAttribute("height", "48");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "#25D366");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M22 11.08V12a10 10 0 1 1-5.93-9.14");
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("points", "22 4 12 14.01 9 11.01");
    svg.append(path, polyline);
    const title = document.createElement("p");
    title.style.fontWeight = "600";
    title.textContent = t("qr.connected_title", "WhatsApp connected");
    const hint = document.createElement("p");
    hint.style.fontSize = "0.85rem";
    hint.style.color = "#1b7a42";
    hint.textContent = t("qr.connected_hint", "No scan is required. Use Log out if you want to switch accounts.");
    placeholder.append(svg, title, hint);
    els.qrWrap.appendChild(placeholder);
    els.qrPhase.textContent = t("status.connected", "Connected");
    els.qrPhase.className = "phase-badge connected";
    els.qrHint.textContent = t("qr.connected_action_hint", "To switch accounts, choose Log out and scan again.");
    return;
  }

  if (data.qrDataUrl) {
    loggedOutSince = 0;
    clearChildren(els.qrWrap);
    const image = document.createElement("img");
    image.src = data.qrDataUrl;
    image.alt = t("qr.image_alt", "WhatsApp login QR code");
    els.qrWrap.appendChild(image);
    els.qrPhase.textContent = t("qr.waiting_scan", "Waiting for scan");
    els.qrPhase.className = "phase-badge connecting";
    els.qrHint.textContent = t("qr.refresh_hint", "The QR code refreshes periodically. Use the button below if scanning fails.");
    return;
  }

  const isLoggedOut = ["logged_out", "session_invalid", "qr_expired", "error"].includes(data._status);
  const now = Date.now();
  if (isLoggedOut) {
    if (loggedOutSince === 0) loggedOutSince = now;
  } else {
    loggedOutSince = 0;
  }
  const timedOut = isLoggedOut && loggedOutSince > 0 && now - loggedOutSince > LOGGED_OUT_TIMEOUT_MS;
  clearChildren(els.qrWrap);
  const placeholder = document.createElement("div");
  placeholder.className = "qr-placeholder";

  if (timedOut) {
    const icon = document.createElement("p");
    icon.style.fontSize = "2rem";
    icon.textContent = "⚠";
    const title = document.createElement("p");
    title.style.fontWeight = "600";
    title.style.color = "#c0392b";
    title.textContent = t("qr.connection_failed", "Connection failed");
    const hint = document.createElement("p");
    hint.style.fontSize = "0.82rem";
    hint.textContent = t("qr.connection_failed_hint", "Could not connect to WhatsApp. Check the network and try again.");
    if (data.lastError) console.debug("WhatsApp last error:", data.lastError);
    const retryButton = document.createElement("button");
    retryButton.className = "btn btn-sm btn-outline-danger mt-2";
    retryButton.textContent = t("qr.retry", "Retry");
    retryButton.addEventListener("click", () => {
      loggedOutSince = 0;
      clearPairCode({ hide: true });
      bridge.apiPost("session/reset", {}).then(() => {
        log("info", t("log.session_reset_created", "Fresh login session created. Waiting for a QR code…"));
        setTimeout(() => refresh().catch(() => {}), 1500);
      }).catch((error) => log("error", tf("log.session_reset_failed", "Could not create a fresh login session: {error}", { error })));
    });
    placeholder.append(icon, title, hint, retryButton);
    els.qrPhase.textContent = t("qr.connection_failed", "Connection failed");
    els.qrPhase.className = "phase-badge error";
    els.qrHint.textContent = t("qr.retry_hint", "Use Retry to create a fresh login session.");
  } else {
    const spinner = document.createElement("div");
    spinner.className = "spinner";
    const title = document.createElement("p");
    const isPairing = ["pairing", "pairing_restart"].includes(data._status);
    title.textContent = isPairing ? t("qr.pairing_title", "Phone scanned. Finishing sign-in…") : t("qr.connecting_title", "Connecting to WhatsApp Web…");
    const hint = document.createElement("p");
    hint.style.fontSize = "0.82rem";
    hint.textContent = isPairing ? t("qr.pairing_hint", "Keep the phone online and do not refresh or scan repeatedly.") : t("qr.connecting_hint", "The first startup can take a few seconds before a QR code appears.");
    placeholder.append(spinner, title, hint);
    els.qrPhase.textContent = isPairing ? t("qr.logging_in", "Signing in") : t("qr.connecting", "Connecting");
    els.qrPhase.className = "phase-badge connecting";
    els.qrHint.textContent = isPairing ? t("qr.login_success_hint", "The page will switch to Connected automatically after sign-in.") : t("qr.connecting_hint", "The first startup can take a few seconds before a QR code appears.");
  }
  els.qrWrap.appendChild(placeholder);
}

async function refreshOnce() {
  let status = {};
  let qr = {};
  if (!runtimeLoaded) {
    try {
      renderRuntime(await bridge.apiGet("runtime"));
    } catch (error) {
      if (els.runtimeStatus) {
        els.runtimeStatus.textContent = t("runtime.check_failed", "Runtime check failed");
        els.runtimeStatus.style.color = "var(--red, #ff5c6c)";
      }
      log("error", tf("log.runtime_failed", "Runtime check failed: {error}", { error }));
    }
  }
  try {
    status = await bridge.apiGet("status");
    renderDashboard(status);
    renderSession(status);
    log("info", tf("log.status_refreshed", "Status refreshed: {status}", { status: status.status || "unknown" }));
  } catch (error) {
    log("error", tf("log.status_failed", "Status request failed: {error}", { error }));
    setDot(els.healthDot, "gray");
    setDot(els.gatewayDot, "gray");
    els.metricStatusText.textContent = t("status.unreachable", "Unreachable");
    els.metricGatewayText.textContent = t("status.unknown", "Unknown");
  }
  try {
    qr = await bridge.apiGet("qr");
    qr.lastError = status.lastError;
    qr._status = status.status;
    renderQr(qr);
  } catch (error) {
    log("error", tf("log.qr_failed", "QR code request failed: {error}", { error }));
  }
  const isConnected = status.ready || qr.ready || status.status === "connected";
  els.liveDot.style.animation = isConnected ? "pulse-dot 2s ease-in-out infinite" : "none";
  els.liveDot.style.opacity = isConnected ? "1" : "0.3";
}

function refresh() {
  if (!refreshPromise) {
    refreshPromise = refreshOnce().finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

function startCountdown() {
  countdown = 5;
  if (countdownTimer) clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    countdown -= 1;
    els.refreshCountdown.textContent = `${countdown}s`;
    if (countdown <= 0) {
      countdown = 5;
      refresh().catch(() => {});
    }
  }, 1000);
}

function handleRefresh() {
  countdown = 5;
  refresh().catch((error) => log("error", tf("log.refresh_failed", "Refresh failed: {error}", { error })));
}

els.refreshBtn.addEventListener("click", handleRefresh);
els.refreshQrBtn?.addEventListener("click", async () => {
  if (["logged_out", "session_invalid", "qr_expired", "error"].includes(currentConnectionStatus)) {
    log("warn", t("log.session_expired", "Login session expired. Creating a fresh session…"));
    clearPairCode({ hide: true });
    try {
      await bridge.apiPost("session/reset", {});
    } catch (error) {
      log("error", tf("log.session_reset_failed", "Could not create a fresh login session: {error}", { error }));
    }
    setTimeout(() => refresh().catch(() => {}), 1000);
    return;
  }
  handleRefresh();
});

els.pairCodeForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pairCodeRequestPending || Date.now() < pairCodeCooldownUntil || els.pairCodePanel.hidden) return;
  const phone = normalizePairingPhone(els.pairPhone.value);
  if (!phone) {
    setPairCodeStatus(t("pairing.invalid_phone", "Enter a valid international phone number including the country or region code."), "error");
    els.pairPhone.focus();
    return;
  }
  const requestToken = ++pairCodeRequestToken;
  pairCodeRequestPending = true;
  if (els.pairCodeValue) els.pairCodeValue.textContent = "";
  if (els.pairCodeResult) els.pairCodeResult.hidden = true;
  setPairCodeStatus(t("pairing.request_pending", "Requesting a pairing code from WhatsApp…"), "pending");
  startPairCodeCooldown();
  updatePairCodeControls();
  try {
    const request = bridge.apiPost("pair-code", { phone });
    els.pairPhone.value = "";
    const result = await request;
    if (requestToken !== pairCodeRequestToken || els.pairCodePanel.hidden) return;
    const code = String(result?.code || result?.pairingCode || "").trim();
    if (!code || code.length > 32 || /[\r\n]/.test(code)) throw new Error("Invalid pairing-code response");
    els.pairCodeValue.textContent = code;
    els.pairCodeResult.hidden = false;
    setPairCodeStatus(t("pairing.generated", "Pairing code generated. Complete the connection on your phone soon."), "success");
    els.pairCodeValue.focus();
    log("info", t("log.pair_generated", "A new phone pairing code was generated (sensitive value not logged)"));
  } catch (error) {
    if (requestToken !== pairCodeRequestToken || els.pairCodePanel.hidden) return;
    console.error("WhatsApp pairing-code request failed:", error);
    setPairCodeStatus(t("pairing.failed", "Could not get a pairing code. Check the number and Gateway status, then try again."), "error");
    log("error", t("log.pair_failed", "Pairing-code request failed (sensitive value not logged)"));
  } finally {
    if (requestToken === pairCodeRequestToken) {
      pairCodeRequestPending = false;
      updatePairCodeControls();
    }
  }
});

els.pairPhone?.addEventListener("input", () => {
  if (els.pairCodeValue) els.pairCodeValue.textContent = "";
  if (els.pairCodeResult) els.pairCodeResult.hidden = true;
  setPairCodeStatus();
});

els.copyPairCodeBtn?.addEventListener("click", async () => {
  const code = els.pairCodeValue?.textContent || "";
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    setPairCodeStatus(t("pairing.copied", "Pairing code copied."), "success");
  } catch (_) {
    setPairCodeStatus(t("pairing.copy_failed", "Automatic copy failed. Select and copy the pairing code manually."), "error");
  }
});

els.restartBtn.addEventListener("click", async () => {
  clearPairCode({ hide: true });
  log("warn", t("log.restart_start", "Restarting connection…"));
  try {
    const result = await bridge.apiPost("restart", {});
    log("info", tf("log.restart_result", "Restart result: {status}", { status: result.status || "ok" }));
  } catch (error) {
    log("error", tf("log.restart_failed", "Restart failed: {error}", { error }));
  }
  setTimeout(() => refresh().catch(() => {}), 1500);
});

els.logoutBtn.addEventListener("click", async () => {
  if (logoutConfirmGate.activate() === "armed") {
    armLogoutConfirmation();
    return;
  }
  if (logoutConfirmTimer) clearTimeout(logoutConfirmTimer);
  logoutConfirmTimer = null;
  restoreLogoutButton();
  clearPairCode({ hide: true });
  log("warn", t("log.logout_start", "Logging out…"));
  try {
    const result = await bridge.apiPost("logout", {});
    log("info", tf("log.logout_result", "Logout result: {status}", { status: result.status || "ok" }));
  } catch (error) {
    log("error", tf("log.logout_failed", "Logout failed: {error}", { error }));
  }
  setTimeout(() => refresh().catch(() => {}), 2500);
});

els.checkUpdateBtn?.addEventListener("click", () => {
  checkUpdate().catch((error) => log("error", tf("log.update_check_failed", "Update check failed: {error}", { error })));
});

els.installUpdateBtn?.addEventListener("click", async () => {
  if (!updateInfo?.updateAvailable || updateInfo.busy) return;
  const release = updateInfo.release || {};
  const candidateToken = String(release.candidateToken || "").trim();
  const latest = String(updateInfo.latestVersion || release.version || "").replace(/^v/, "");
  if (!candidateToken || !latest) {
    log("error", t("log.update_candidate_missing", "The current update candidate is missing its identity token; checking the Release again"));
    await checkUpdate();
    return;
  }
  if (updateConfirmGate.activate() === "armed") {
    armUpdateConfirmation();
    return;
  }
  if (updateConfirmTimer) clearTimeout(updateConfirmTimer);
  updateConfirmTimer = null;
  updateStartedHere = true;
  updatePollStartedAt = Date.now();
  els.installUpdateBtn.disabled = true;
  els.checkUpdateBtn.disabled = true;
  log("warn", tf("log.update_install_start", "Installing the confirmed v{version} Release artifact…", { version: latest }));
  try {
    const data = await bridge.apiPost("update/install", {
      candidateToken,
      expectedVersion: latest,
    });
    renderUpdate(data);
    log("info", t("log.update_transaction_started", "Update transaction started"));
  } catch (error) {
    console.warn("WhatsApp update request interrupted:", error);
    log("warn", tf("log.update_request_interrupted", "Update request was interrupted; checking durable transaction state: {error}", { error }));
  }
  pollUpdateStatus();
});

els.clearLogBtn.addEventListener("click", () => {
  clearChildren(els.eventLog);
  const empty = document.createElement("div");
  empty.className = "log-empty";
  empty.textContent = t("event_log.cleared", "Log cleared");
  els.eventLog.appendChild(empty);
});

function rerenderLocalizedState({ localeChanged = false } = {}) {
  if (localeChanged) {
    disarmUpdateConfirmation({ rerender: false });
    disarmLogoutConfirmation({ restore: false });
  }
  applyStaticI18n();
  captureActionButtonNodes();
  updatePairCodeControls();
  if (lastRuntimeData) renderRuntime(lastRuntimeData);
  if (lastStatusData) {
    renderDashboard(lastStatusData);
    renderSession(lastStatusData);
  }
  if (lastQrData) renderQr(lastQrData);
  if (updateInfo) renderUpdate(updateInfo);
}

await bridge.ready();
activeLocale = bridge.getLocale?.() || "en-US";
rerenderLocalizedState();
bridge.onContext?.((context) => {
  const nextLocale = context?.locale || bridge.getLocale?.() || activeLocale;
  const localeChanged = nextLocale !== activeLocale;
  activeLocale = nextLocale;
  rerenderLocalizedState({ localeChanged });
  if (localeChanged) refresh().catch(() => {});
});
log("info", t("log.page_loaded", "Management page loaded"));
startCountdown();
await refresh().catch((error) => log("error", tf("log.init_failed", "Initialization failed: {error}", { error })));
await initializeUpdate();
