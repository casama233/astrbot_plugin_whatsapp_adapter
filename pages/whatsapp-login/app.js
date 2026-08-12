import { createTwoStepGate } from "./two-step-action.js";

const bridge = window.AstrBotPluginPage;

// ─── DOM refs ───
const els = {
  // Dashboard metrics
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
  // Session info
  statusText: document.getElementById("statusText"),
  selfJid: document.getElementById("selfJid"),
  selfLid: document.getElementById("selfLid"),
  authDir: document.getElementById("authDir"),
  gatewayUrl: document.getElementById("gatewayUrl"),
  runtimeStatus: document.getElementById("runtimeStatus"),
  configuredStatus: document.getElementById("configuredStatus"),
  // Policy
  policyDm: document.getElementById("policyDm"),
  policyGroup: document.getElementById("policyGroup"),
  policyAllowFrom: document.getElementById("policyAllowFrom"),
  policyGroups: document.getElementById("policyGroups"),
  // Updater
  updatePhase: document.getElementById("updatePhase"),
  currentVersion: document.getElementById("currentVersion"),
  latestVersion: document.getElementById("latestVersion"),
  updateMessage: document.getElementById("updateMessage"),
  releaseNotesWrap: document.getElementById("releaseNotesWrap"),
  releaseNotes: document.getElementById("releaseNotes"),
  // QR
  qrWrap: document.getElementById("qrWrap"),
  qrHint: document.getElementById("qrHint"),
  qrPhase: document.getElementById("qrPhase"),
  pairCodePanel: document.getElementById("pairCodePanel"),
  pairCodeForm: document.getElementById("pairCodeForm"),
  pairPhone: document.getElementById("pairPhone"),
  pairCodeStatus: document.getElementById("pairCodeStatus"),
  pairCodeResult: document.getElementById("pairCodeResult"),
  pairCodeValue: document.getElementById("pairCodeValue"),
  // Log
  eventLog: document.getElementById("eventLog"),
  // Buttons
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

// ─── State ───
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

const CONFIRM_WINDOW_MS = 10_000;
const UPDATE_POLL_MAX_MS = 30 * 60 * 1000;
const updateConfirmGate = createTwoStepGate({ windowMs: CONFIRM_WINDOW_MS });
const logoutConfirmGate = createTwoStepGate({ windowMs: CONFIRM_WINDOW_MS });
let updateConfirmTimer = null;
let logoutConfirmTimer = null;
const logoutOriginalNodes = els.logoutBtn
  ? [...els.logoutBtn.childNodes].map((node) => node.cloneNode(true))
  : [];

// ─── Helpers ───
function fmtTime() {
  const d = new Date();
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function trunc(s, n = 48) {
  if (!s) return "-";
  const t = String(s);
  return t.length > n ? t.slice(0, n) + "…" : t;
}

function plural(n, label = "项") {
  const v = Array.isArray(n) ? n.length : Number(n);
  return v === 0 ? "无" : `${v} ${label}`;
}

function setDot(el, state) {
  el.className = "status-dot" + (el.classList.contains("status-dot-sm") ? " status-dot-sm" : "");
  if (state === "green") el.classList.add("green");
  else if (state === "yellow") el.classList.add("yellow");
  else if (state === "red") el.classList.add("red");
  else el.classList.add("gray");
}

function setTag(el, state, label) {
  el.className = "status-tag";
  if (state === "green") el.classList.add("green");
  else if (state === "yellow") el.classList.add("yellow");
  else if (state === "red") el.classList.add("red");
  else el.classList.add("gray");
  el.textContent = label;
}

function clearChildren(el) {
  while (el.firstChild) el.firstChild.remove();
}

function restoreLogoutButton() {
  if (!els.logoutBtn) return;
  if (logoutOriginalNodes.length) {
    els.logoutBtn.replaceChildren(
      ...logoutOriginalNodes.map((node) => node.cloneNode(true)),
    );
  } else {
    els.logoutBtn.textContent = "登出并重新扫码";
  }
}

// Pairing credentials are intentionally kept only in the visible form/result.
// They are never added to the event log or persisted in browser storage.
const PAIR_CODE_COOLDOWN_MS = 30_000;

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
    ? "正在获取…"
    : coolingDown
      ? `请稍候 ${Math.ceil(remaining / 1000)}s`
      : "获取配对码";
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
  if (!/^[1-9][0-9]{6,14}$/.test(digits)) return "";
  return digits;
}

// ─── Log ───
function log(level, msg) {
  const empty = els.eventLog.querySelector(".log-empty");
  if (empty) empty.remove();

  const entry = document.createElement("div");
  entry.className = "log-entry";
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = fmtTime();
  const message = document.createElement("span");
  message.className = "log-msg";
  if (["info", "warn", "error"].includes(level)) message.classList.add(level);
  message.textContent = msg;
  entry.append(time, message);
  els.eventLog.appendChild(entry);
  els.eventLog.scrollTop = els.eventLog.scrollHeight;

  while (els.eventLog.children.length > 100) {
    els.eventLog.firstChild.remove();
  }
}

// ─── Render dashboard ───
function renderDashboard(data) {
  // Connection status metric
  const status = data.status || (data.ready ? "connected" : "unknown");
  currentConnectionStatus = status;
  const isReady = data.ready || status === "connected";
  const isStarting = ["starting", "pairing", "pairing_restart", "resetting", "qr_pending"].includes(status) || data.hasQr;
  setPairCodeAvailable(!isReady);
  const isError = !isReady && !isStarting;

  els.metricStatusText.textContent = isReady ? "已连接" : isStarting ? "等待连接" : "断开";
  els.metricStatusSub.textContent = status;
  setDot(els.healthDot, isReady ? "green" : isStarting ? "yellow" : "red");

  // Gateway health metric
  const gwHealthy = data.gatewayHealthy !== undefined ? data.gatewayHealthy : data.ok;
  els.metricGatewayText.textContent = gwHealthy ? "正常" : "异常";
  els.metricGatewaySub.textContent = gwHealthy && isReady ? "运行中" : "待连接";
  setDot(els.gatewayDot, gwHealthy ? (isReady ? "green" : "yellow") : "red");

  // Account metric
  els.metricAccountText.textContent = data.selfJid ? trunc(data.selfJid, 28) : "-";
  els.metricAccountSub.textContent = data.selfJid ? "WhatsApp 账号" : "未登录";

  // Uptime metric
  if (data.lastPresenceAt) {
    const t = data.lastPresenceAt.replace("T", " ").split(".")[0];
    els.metricUptimeText.textContent = t;
    els.metricUptimeSub.textContent = "最后在线";
  } else {
    els.metricUptimeText.textContent = isReady ? "在线" : "-";
    els.metricUptimeSub.textContent = isReady ? "当前在线" : "无数据";
  }
}

// ─── Render session info ───
function renderSession(data) {
  const status = data.status || (data.ready ? "connected" : "unknown");
  const isReady = data.ready || status === "connected";
  const isStarting = ["starting", "pairing", "pairing_restart", "resetting", "qr_pending"].includes(status) || data.hasQr;

  setTag(els.statusText,
    isReady ? "green" : isStarting ? "yellow" : "red",
    isReady ? "已连接" : isStarting ? "等待连接" : status);

  els.selfJid.textContent = data.selfJid || "-";
  els.selfLid.textContent = data.selfLid || "-";
  els.authDir.textContent = data.authDir || "-";
  els.gatewayUrl.textContent = data.baseUrl || "-";
  els.configuredStatus.textContent = data.config ? "✓ 已配置" : "✗ 未同步";
  if (data.runtimeRequirements) renderRuntime(data.runtimeRequirements);

  // Policy
  const cfg = data.config || {};
  els.policyDm.textContent = cfg.dmPolicy || "-";
  els.policyGroup.textContent = cfg.groupPolicy || "-";
  els.policyAllowFrom.textContent = Array.isArray(cfg.allowFrom) ? plural(cfg.allowFrom, "个号码") : "-";
  els.policyGroups.textContent = Array.isArray(cfg.groups) ? plural(cfg.groups, "个群") : "-";
}

function renderRuntime(runtime) {
  if (!runtime || !els.runtimeStatus) return;
  runtimeLoaded = true;
  els.runtimeStatus.textContent = runtime.message || (runtime.ready ? "✓ 已就绪" : "✗ 不可用");
  els.runtimeStatus.style.color = runtime.ready ? "var(--green, #25D366)" : "var(--red, #ff5c6c)";
  els.runtimeStatus.title = [
    runtime.node?.version ? `Node ${runtime.node.version}` : null,
    runtime.npm?.path ? `npm ${runtime.npm.path}` : null,
    runtime.dependenciesInstalled ? "Baileys 已安装" : "Baileys 待安装",
  ].filter(Boolean).join(" · ");
}

// ─── Independent GitHub Release updater ───
const updatePhaseLabels = {
  idle: "未检查",
  queued: "已锁定",
  checking: "检查中",
  available: "可更新",
  up_to_date: "已是最新",
  check_failed: "检查失败",
  downloading: "下载校验",
  validating: "验证中",
  installing_dependencies: "准备依赖",
  quiescing: "停止旧运行时",
  installing: "切换版本",
  reloading: "重载中",
  health_checking: "健康检查",
  rolling_back: "回滚中",
  completed: "已完成",
  failed: "更新失败",
};

function renderUpdate(data) {
  if (!data) return;
  updateInfo = data;
  const phase = data.phase || "idle";
  const isError = ["failed", "check_failed"].includes(phase);
  const isSuccess = ["up_to_date", "completed"].includes(phase);
  const isBusy = Boolean(data.busy);

  els.currentVersion.textContent = data.currentVersion ? `v${data.currentVersion}` : "-";
  els.latestVersion.textContent = data.latestVersion ? `v${data.latestVersion}` : "-";
  els.updateMessage.textContent = data.message || "直接检查 GitHub Release，不依赖官方插件市场缓存。";
  els.updatePhase.textContent = updatePhaseLabels[phase] || phase;
  els.updatePhase.className = "phase-badge";
  if (isError) els.updatePhase.classList.add("error");
  else if (isSuccess) els.updatePhase.classList.add("connected");
  else if (isBusy || data.updateAvailable) els.updatePhase.classList.add("connecting");

  const notes = data.release?.notes || "";
  els.releaseNotes.textContent = notes;
  els.releaseNotesWrap.hidden = !notes;
  els.checkUpdateBtn.disabled = isBusy;
  els.installUpdateBtn.disabled = isBusy || !data.updateAvailable;
  if (!updateConfirmGate.isArmed()) {
    els.installUpdateBtn.textContent = isBusy ? "更新处理中…" : "立即更新";
  }
}

function disarmUpdateConfirmation({ rerender = true } = {}) {
  updateConfirmGate.disarm();
  if (updateConfirmTimer) clearTimeout(updateConfirmTimer);
  updateConfirmTimer = null;
  if (rerender && updateInfo) renderUpdate(updateInfo);
}

function armUpdateConfirmation() {
  if (updateConfirmTimer) clearTimeout(updateConfirmTimer);
  const version = updateInfo?.latestVersion ? `v${String(updateInfo.latestVersion).replace(/^v/, "")}` : "该版本";
  els.installUpdateBtn.textContent = `再次点击确认更新 ${version}`;
  els.updateMessage.textContent = `已锁定 ${version} 的 Release artifact；请在 10 秒内再次点击。第二次点击只会安装这一候选。`;
  updateConfirmTimer = setTimeout(() => {
    updateConfirmGate.disarm();
    updateConfirmTimer = null;
    if (updateInfo) renderUpdate(updateInfo);
  }, CONFIRM_WINDOW_MS);
}

function disarmLogoutConfirmation() {
  logoutConfirmGate.disarm();
  if (logoutConfirmTimer) clearTimeout(logoutConfirmTimer);
  logoutConfirmTimer = null;
  if (els.logoutBtn && !els.logoutBtn.disabled) restoreLogoutButton();
}

function armLogoutConfirmation() {
  if (logoutConfirmTimer) clearTimeout(logoutConfirmTimer);
  els.logoutBtn.textContent = "再次点击确认登出";
  logoutConfirmTimer = setTimeout(disarmLogoutConfirmation, CONFIRM_WINDOW_MS);
}

async function checkUpdate({ quiet = false } = {}) {
  disarmUpdateConfirmation({ rerender: false });
  if (!quiet) log("info", "正在检查 GitHub Release 与正式 artifact digest…");
  els.checkUpdateBtn.disabled = true;
  try {
    const data = await bridge.apiPost("update/check", {});
    renderUpdate(data);
    if (!quiet) log(data.updateAvailable ? "warn" : "info", data.message || "更新检查完成");
    return data;
  } catch (err) {
    try {
      renderUpdate(await bridge.apiGet("update/status"));
    } catch (_) {
      // Keep the original network error visible when even the status route failed.
    }
    log("error", `更新检查失败: ${err}`);
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
      els.updateMessage.textContent = "前端已停止长时间轮询；后端 transaction 可能仍在运行，重新载入页面可继续读取持久状态。";
      log("warn", "更新状态轮询已超过 30 分钟并停止，但未把后端任务判定为失败");
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
        log("info", data.message || "插件更新完成");
        if (updateStartedHere) {
          updateStartedHere = false;
          setTimeout(() => window.location.reload(), 1200);
        }
        return;
      }
      if (data.phase === "failed") {
        log("error", data.message || "插件更新失败");
      }
    } catch (err) {
      // Plugin routes disappear briefly during hot reload; keep polling instead of
      // treating that expected window as an update failure.
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
  } catch (_) {
    // Fall through to a direct check when no persisted state is available.
  }
  await checkUpdate({ quiet: true });
}

// ─── Render QR ───
const LOGGED_OUT_TIMEOUT_MS = 15000;

function renderQr(data) {
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
    title.textContent = "WhatsApp 已连接";
    const hint = document.createElement("p");
    hint.style.fontSize = "0.85rem";
    hint.style.color = "#1b7a42";
    hint.textContent = "无需扫码。如需更换账号请点击「登出」。";
    placeholder.append(svg, title, hint);
    els.qrWrap.appendChild(placeholder);
    els.qrPhase.textContent = "已连接";
    els.qrPhase.className = "phase-badge connected";
    els.qrHint.textContent = "如需更换账号，请点击「登出并重新扫码」。";
    return;
  }

  if (data.qrDataUrl) {
    loggedOutSince = 0;
    clearChildren(els.qrWrap);
    const img = document.createElement("img");
    img.src = data.qrDataUrl;
    img.alt = "WhatsApp QR 码";
    els.qrWrap.appendChild(img);
    els.qrPhase.textContent = "等待扫码";
    els.qrPhase.className = "phase-badge connecting";
    els.qrHint.textContent = "二维码定期刷新。扫码失败时点击下方按钮刷新。";
    return;
  }

  // No QR yet — check for timeout in logged_out state
  const isLoggedOut = ["logged_out", "session_invalid", "qr_expired", "error"].includes(data._status);
  const now = Date.now();
  if (isLoggedOut) {
    if (loggedOutSince === 0) loggedOutSince = now;
  } else {
    loggedOutSince = 0;
  }
  const timedOut = isLoggedOut && loggedOutSince > 0 && (now - loggedOutSince) > LOGGED_OUT_TIMEOUT_MS;

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
    title.textContent = "连接失败";
    const hint = document.createElement("p");
    hint.style.fontSize = "0.82rem";
    hint.textContent = data.lastError || "无法连接到 WhatsApp 服务器，请检查网络后重试";
    const retryBtn = document.createElement("button");
    retryBtn.className = "btn btn-sm btn-outline-danger mt-2";
    retryBtn.textContent = "重试";
    retryBtn.addEventListener("click", () => {
      loggedOutSince = 0;
      clearPairCode({ hide: true });
      bridge.apiPost("session/reset", {}).then(() => {
        log("info", "已建立全新登录 session，正在等待二维码...");
        setTimeout(() => refresh().catch(() => {}), 1500);
      }).catch((err) => log("error", `重新建立登录失败: ${err}`));
    });
    placeholder.append(icon, title, hint, retryBtn);
    els.qrPhase.textContent = "连接失败";
    els.qrPhase.className = "phase-badge error";
    els.qrHint.textContent = "点击重试按钮重新连接。";
  } else {
    const spinner = document.createElement("div");
    spinner.className = "spinner";
    const title = document.createElement("p");
    const isPairing = ["pairing", "pairing_restart"].includes(data._status);
    title.textContent = isPairing ? "手机已扫码，正在完成登录..." : "正在连接 WhatsApp Web...";
    const hint = document.createElement("p");
    hint.style.fontSize = "0.82rem";
    hint.textContent = isPairing ? "请保持手机联网，不要刷新或重复扫码" : "首次启动需要数秒生成二维码";
    placeholder.append(spinner, title, hint);
    els.qrPhase.textContent = isPairing ? "正在登录" : "连接中";
    els.qrPhase.className = "phase-badge connecting";
    els.qrHint.textContent = isPairing
      ? "登录成功后页面会自动切换为已连接。"
      : "首次启动需要几秒钟连接 WhatsApp。";
  }
  els.qrWrap.appendChild(placeholder);
}

// ─── Main refresh ───
async function refreshOnce() {
  let status = {};
  let qr = {};

  if (!runtimeLoaded) {
    try {
      renderRuntime(await bridge.apiGet("runtime"));
    } catch (err) {
      if (els.runtimeStatus) {
        els.runtimeStatus.textContent = "运行环境检测失败";
        els.runtimeStatus.style.color = "var(--red, #ff5c6c)";
      }
      log("error", `运行环境检测失败: ${err}`);
    }
  }

  try {
    status = await bridge.apiGet("status");
    renderDashboard(status);
    renderSession(status);
    log("info", `状态已刷新: ${status.status || "unknown"}`);
  } catch (err) {
    log("error", `状态请求失败: ${err}`);
    setDot(els.healthDot, "gray");
    setDot(els.gatewayDot, "gray");
    els.metricStatusText.textContent = "无法连接";
    els.metricGatewayText.textContent = "未知";
  }

  try {
    qr = await bridge.apiGet("qr");
    qr.lastError = status.lastError;
    qr._status = status.status;
    renderQr(qr);
  } catch (err) {
    log("error", `二维码请求失败: ${err}`);
  }

  // Live dot
  const isConnected = status.ready || qr.ready || status.status === "connected";
  if (isConnected) {
    els.liveDot.style.animation = "pulse-dot 2s ease-in-out infinite";
    els.liveDot.style.opacity = "1";
  } else {
    els.liveDot.style.animation = "none";
    els.liveDot.style.opacity = "0.3";
  }
}

function refresh() {
  if (!refreshPromise) {
    refreshPromise = refreshOnce().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

// ─── Countdown ───
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

// ─── Events ───
function handleRefresh() {
  countdown = 5;
  refresh().catch((err) => log("error", `刷新失败: ${err}`));
}

els.refreshBtn.addEventListener("click", handleRefresh);
els.refreshQrBtn?.addEventListener("click", async () => {
  if (["logged_out", "session_invalid", "qr_expired", "error"].includes(currentConnectionStatus)) {
    log("warn", "登录状态已失效，正在建立全新登录 session...");
    clearPairCode({ hide: true });
    try {
      await bridge.apiPost("session/reset", {});
    } catch (err) {
      log("error", `重新建立登录失败: ${err}`);
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
    setPairCodeStatus("请输入含国家或地区代码的有效国际手机号码。", "error");
    els.pairPhone.focus();
    return;
  }

  const requestToken = ++pairCodeRequestToken;
  pairCodeRequestPending = true;
  if (els.pairCodeValue) els.pairCodeValue.textContent = "";
  if (els.pairCodeResult) els.pairCodeResult.hidden = true;
  setPairCodeStatus("正在向 WhatsApp 申请配对码…", "pending");
  startPairCodeCooldown();
  updatePairCodeControls();

  try {
    const request = bridge.apiPost("pair-code", { phone });
    // Remove the full number from the form as soon as the request has been handed off.
    els.pairPhone.value = "";
    const result = await request;
    if (requestToken !== pairCodeRequestToken || els.pairCodePanel.hidden) return;

    const code = String(result?.code || result?.pairingCode || "").trim();
    if (!code || code.length > 32 || /[\r\n]/.test(code)) {
      throw new Error("Invalid pairing-code response");
    }
    els.pairCodeValue.textContent = code;
    els.pairCodeResult.hidden = false;
    setPairCodeStatus("配对码已生成，请尽快在手机端完成连接。", "success");
    els.pairCodeValue.focus();
    log("info", "已生成新的手机配对码（敏感内容未记录）");
  } catch (_) {
    if (requestToken !== pairCodeRequestToken || els.pairCodePanel.hidden) return;
    setPairCodeStatus("未能获取配对码，请确认号码与 Gateway 状态后重试。", "error");
    log("error", "配对码请求失败（敏感内容未记录）");
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
    setPairCodeStatus("配对码已复制。", "success");
  } catch (_) {
    setPairCodeStatus("无法自动复制，请手动选择配对码。", "error");
  }
});

els.restartBtn.addEventListener("click", async () => {
  clearPairCode({ hide: true });
  log("warn", "正在重启连接...");
  try {
    const res = await bridge.apiPost("restart", {});
    log("info", `重启结果: ${res.status || "ok"}`);
  } catch (err) {
    log("error", `重启失败: ${err}`);
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
  log("warn", "正在登出...");
  try {
    const res = await bridge.apiPost("logout", {});
    log("info", `登出结果: ${res.status || "ok"}`);
  } catch (err) {
    log("error", `登出失败: ${err}`);
  }
  setTimeout(() => refresh().catch(() => {}), 2500);
});

els.checkUpdateBtn?.addEventListener("click", () => {
  checkUpdate().catch((err) => log("error", `更新检查失败: ${err}`));
});

els.installUpdateBtn?.addEventListener("click", async () => {
  if (!updateInfo?.updateAvailable || updateInfo.busy) return;
  const release = updateInfo.release || {};
  const candidateToken = String(release.candidateToken || "").trim();
  const latest = String(updateInfo.latestVersion || release.version || "").replace(/^v/, "");
  if (!candidateToken || !latest) {
    log("error", "当前更新候选缺少 identity token，正在重新检查 Release");
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
  log("warn", `开始安装已确认的 v${latest} Release artifact…`);
  try {
    const data = await bridge.apiPost("update/install", {
      candidateToken,
      expectedVersion: latest,
    });
    renderUpdate(data);
    log("info", data.message || "更新 transaction 已启动");
  } catch (err) {
    // A route interruption can happen exactly when the plugin reloads. Poll the
    // durable transaction state before deciding whether the update failed.
    log("warn", `更新请求已中断，正在确认持久 transaction 状态: ${err}`);
  }
  pollUpdateStatus();
});

els.clearLogBtn.addEventListener("click", () => {
  clearChildren(els.eventLog);
  const empty = document.createElement("div");
  empty.className = "log-empty";
  empty.textContent = "日志已清空";
  els.eventLog.appendChild(empty);
});

// ─── Init ───
await bridge.ready();
log("info", "管理面板已加载");
startCountdown();
await refresh().catch((err) => log("error", `初始化失败: ${err}`));
await initializeUpdate();
