const bridge = window.AstrBotPluginPage;

const statusBadge = document.getElementById("statusBadge");
const statusText = document.getElementById("statusText");
const selfJid = document.getElementById("selfJid");
const authDir = document.getElementById("authDir");
const qrWrap = document.getElementById("qrWrap");
const qrHint = document.getElementById("qrHint");
const output = document.getElementById("output");

function setOutput(value) {
  output.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function renderStatus(data) {
  const status = data.status || (data.ready ? "connected" : "unknown");
  statusText.textContent = status;
  selfJid.textContent = data.selfJid || "-";
  authDir.textContent = data.authDir || "-";

  statusBadge.className = "badge";
  if (data.ready || status === "connected") {
    statusBadge.classList.add("badge-ok");
    statusBadge.textContent = "已连接";
  } else if (data.hasQr || status === "starting") {
    statusBadge.classList.add("badge-warn");
    statusBadge.textContent = "等待扫码";
  } else {
    statusBadge.classList.add("badge-muted");
    statusBadge.textContent = status;
  }
}

function renderQr(data) {
  qrWrap.innerHTML = "";
  if (data.ready) {
    qrWrap.innerHTML = '<div class="placeholder">WhatsApp 已连接，无需扫码。</div>';
    qrHint.textContent = "如需更换账号，请点击「登出并重新扫码」。";
    return;
  }
  if (data.qrDataUrl) {
    const image = document.createElement("img");
    image.alt = "WhatsApp login QR code";
    image.src = data.qrDataUrl;
    qrWrap.appendChild(image);
    qrHint.textContent = "二维码通常会定期刷新，扫码失败时点击刷新或重启连接。";
    return;
  }
  qrWrap.innerHTML = '<div class="placeholder">暂未收到二维码。请确认 Gateway 正在运行，或点击「重启连接」。</div>';
  qrHint.textContent = "首次启动需要几秒钟连接 WhatsApp Web。";
}

async function refresh() {
  const status = await bridge.apiGet("status");
  renderStatus(status);
  const qr = await bridge.apiGet("qr");
  renderQr(qr);
  setOutput({ status, qr: { ...qr, qr: qr.qr ? "<hidden>" : null, qrDataUrl: qr.qrDataUrl ? "<data-url>" : null } });
}

await bridge.ready();
await refresh().catch((error) => setOutput(String(error)));

document.getElementById("refresh").addEventListener("click", () => {
  refresh().catch((error) => setOutput(String(error)));
});

document.getElementById("restart").addEventListener("click", async () => {
  setOutput(await bridge.apiPost("restart", {}));
  setTimeout(() => refresh().catch((error) => setOutput(String(error))), 1500);
});

document.getElementById("logout").addEventListener("click", async () => {
  const confirmed = window.confirm("确定要登出当前 WhatsApp Web 会话并重新扫码吗？");
  if (!confirmed) return;
  setOutput(await bridge.apiPost("logout", {}));
  setTimeout(() => refresh().catch((error) => setOutput(String(error))), 2500);
});

setInterval(() => {
  refresh().catch(() => undefined);
}, 5000);
