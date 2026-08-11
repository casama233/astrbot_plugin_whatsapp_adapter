import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

class FakeElement {
  constructor({ html = "", text = "", disabled = false } = {}) {
    this.innerHTML = html;
    this.textContent = text;
    this.disabled = disabled;
    this.dataset = {};
    this.listeners = [];
  }

  addEventListener(type, handler, options = false) {
    this.listeners.push({ type, handler, capture: options === true || Boolean(options?.capture) });
  }

  click() {
    const event = {
      defaultPrevented: false,
      immediatePropagationStopped: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      stopImmediatePropagation() {
        this.immediatePropagationStopped = true;
      },
    };
    const listeners = this.listeners
      .filter((listener) => listener.type === "click")
      .sort((a, b) => Number(b.capture) - Number(a.capture));
    for (const listener of listeners) {
      listener.handler(event);
      if (event.immediatePropagationStopped) break;
    }
    return event;
  }
}

async function loadCompat() {
  const source = await readFile(
    new URL("../pages/whatsapp-login/sandbox-confirm.js", import.meta.url),
    "utf8",
  );
  const install = new FakeElement({ html: "<svg></svg>立即更新", text: "立即更新" });
  const logout = new FakeElement({ html: "<svg></svg>登出并重新扫码", text: "登出并重新扫码" });
  const check = new FakeElement({ html: "检查更新", text: "检查更新" });
  const updateMessage = new FakeElement({ text: "发现新版本 v0.2.33" });
  const elements = new Map([
    ["installUpdate", install],
    ["logout", logout],
    ["checkUpdate", check],
    ["updateMessage", updateMessage],
  ]);
  const timers = new Map();
  let nextTimer = 1;
  const nativeConfirm = () => false;
  const window = { confirm: nativeConfirm };
  const context = {
    document: { getElementById: (id) => elements.get(id) ?? null },
    window,
    Date,
    queueMicrotask,
    setTimeout(callback) {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  vm.runInNewContext(source, context, { filename: "sandbox-confirm.js" });
  return { install, logout, check, updateMessage, window, nativeConfirm, timers };
}

test("plugin page loads sandbox confirmation helper before app module", async () => {
  const html = await readFile(new URL("../pages/whatsapp-login/index.html", import.meta.url), "utf8");
  const helper = html.indexOf('src="./sandbox-confirm.js"');
  const app = html.indexOf('src="./app.js"');
  assert.ok(helper >= 0, "sandbox confirmation helper must be loaded");
  assert.ok(app > helper, "helper must register capture listeners before app.js handlers");
});

test("update confirmation uses two clicks without depending on iframe modals", async () => {
  const { install, updateMessage, window, nativeConfirm } = await loadCompat();
  let appCalls = 0;
  let appConfirmed = false;
  install.addEventListener("click", () => {
    appCalls += 1;
    appConfirmed = window.confirm("update?");
  });

  const first = install.click();
  assert.equal(first.defaultPrevented, true);
  assert.equal(appCalls, 0);
  assert.equal(install.dataset.confirmArmed, "true");
  assert.equal(install.textContent, "再次点击确认更新");
  assert.match(updateMessage.textContent, /10 秒内再次点击/);

  const second = install.click();
  assert.equal(second.defaultPrevented, false);
  assert.equal(appCalls, 1);
  assert.equal(appConfirmed, true);
  assert.equal(install.dataset.confirmArmed, undefined);

  await Promise.resolve();
  assert.equal(window.confirm, nativeConfirm, "native confirm must be restored after the event turn");
});

test("checking again disarms a pending update confirmation", async () => {
  const { install, check, updateMessage } = await loadCompat();
  install.click();
  assert.equal(install.dataset.confirmArmed, "true");
  check.click();
  assert.equal(install.dataset.confirmArmed, undefined);
  assert.match(install.innerHTML, /立即更新/);
  assert.equal(updateMessage.textContent, "发现新版本 v0.2.33");
});

test("logout confirmation gets the same sandbox-safe two-click guard", async () => {
  const { logout, window } = await loadCompat();
  let appCalls = 0;
  let appConfirmed = false;
  logout.addEventListener("click", () => {
    appCalls += 1;
    appConfirmed = window.confirm("logout?");
  });

  logout.click();
  assert.equal(appCalls, 0);
  assert.equal(logout.textContent, "再次点击确认登出");

  logout.click();
  assert.equal(appCalls, 1);
  assert.equal(appConfirmed, true);
});
