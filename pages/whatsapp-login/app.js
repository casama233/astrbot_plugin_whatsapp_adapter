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
  // Log
  eventLog: document.getElementById("eventLog"),
  // Buttons
  refreshBtn: document.getElementById("refresh"),
  refreshQrBtn: document.getElementById("refreshQr"),
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
let updatePollAttempts = 0;

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
  queued: "已排队",
  checking: "检查中",
  available: "可更新",
  up_to_date: "已是最新",
  check_failed: "检查失败",
  downloading: "下载中",
  validating: "验证中",
  installing_dependencies: "准备依赖",
  installing: "安装中",
  reloading: "重载中",
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
  els.installUpdateBtn.textContent = isBusy ? "更新处理中…" : "立即更新";
}

async function checkUpdate({ quiet = false } = {}) {
  if (!quiet) log("info", "正在直接检查 GitHub Release…");
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
    updatePollAttempts += 1;
    if (updatePollAttempts > 120) {
      updateStartedHere = false;
      els.updateMessage.textContent = "更新状态确认超时，请重新载入页面查看最终版本。";
      log("error", "更新状态确认超时");
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
      updatePollAttempts = 0;
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

els.restartBtn.addEventListener("click", async () => {
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
  const confirmed = window.confirm("确定要登出当前 WhatsApp Web 会话并重新扫码吗？");
  if (!confirmed) return;
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
  const current = updateInfo.currentVersion || "当前版本";
  const latest = updateInfo.latestVersion || "最新版";
  const confirmed = window.confirm(
    `确定要从 v${String(current).replace(/^v/, "")} 更新到 v${String(latest).replace(/^v/, "")} 吗？\n\n` +
    "系统会先验证压缩包和依赖，再原子切换；重载失败会自动回滚。WhatsApp 登录凭证不会被删除。",
  );
  if (!confirmed) return;

  updateStartedHere = true;
  updatePollAttempts = 0;
  els.installUpdateBtn.disabled = true;
  els.checkUpdateBtn.disabled = true;
  log("warn", `开始安装 v${String(latest).replace(/^v/, "")}…`);
  try {
    const data = await bridge.apiPost("update/install", {});
    renderUpdate(data);
    log("info", data.message || "更新任务已启动");
  } catch (err) {
    // A route interruption can happen exactly when the plugin reloads. Poll the
    // persisted state before deciding whether the update actually failed.
    log("warn", `更新请求已中断，正在确认最终状态: ${err}`);
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
