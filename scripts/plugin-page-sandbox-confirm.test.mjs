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

  makeEvent() {
    return {
      defaultPrevented: false,
      immediatePropagationStopped: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      stopImmediatePropagation() {
        this.immediatePropagationStopped = true;
      },
    };
  }

  click() {
    const event = this.makeEvent();
    const listeners = this.listeners
      .filter((listener) => listener.type === "click")
      .sort((a, b) => Number(b.capture) - Number(a.capture));
    for (const listener of listeners) {
      listener.handler(event);
      if (event.immediatePropagationStopped) break;
    }
    return event;
  }

  async clickWithMicrotaskCheckpoint() {
    const event = this.makeEvent();
    const listeners = this.listeners
      .filter((listener) => listener.type === "click")
      .sort((a, b) => Number(b.capture) - Number(a.capture));
    for (const listener of listeners) {
      listener.handler(event);
      // Browsers may perform a microtask checkpoint after an event callback
      // before invoking the next listener in the same click dispatch.
      await Promise.resolve();
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
    setTimeout(callback, delay = 0) {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  vm.runInNewContext(source, context, { filename: "sandbox-confirm.js" });
  return { install, logout, check, updateMessage, window, nativeConfirm, timers };
}

function runZeroDelayTimers(timers) {
  for (const [id, timer] of [...timers]) {
    if (timer.delay !== 0) continue;
    timers.delete(id);
    timer.callback();
  }
}

test("plugin page loads sandbox confirmation helper before app module", async () => {
  const html = await readFile(new URL("../pages/whatsapp-login/index.html", import.meta.url), "utf8");
  const helper = html.indexOf('src="./sandbox-confirm.js"');
  const app = html.indexOf('src="./app.js"');
  assert.ok(helper >= 0, "sandbox confirmation helper must be loaded");
  assert.ok(app > helper, "helper must register capture listeners before app.js handlers");
});

test("update confirmation survives a browser-style microtask checkpoint between listeners", async () => {
  const { install, updateMessage, window, nativeConfirm, timers } = await loadCompat();
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

  const second = await install.clickWithMicrotaskCheckpoint();
  assert.equal(second.defaultPrevented, false);
  assert.equal(appCalls, 1);
  assert.equal(appConfirmed, true);
  assert.equal(install.dataset.confirmArmed, undefined);
  assert.notEqual(window.confirm, nativeConfirm, "confirm shim must survive the event dispatch");

  runZeroDelayTimers(timers);
  assert.equal(window.confirm, nativeConfirm, "native confirm must be restored in the next task");
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

test("logout confirmation survives the same listener timing", async () => {
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

  await logout.clickWithMicrotaskCheckpoint();
  assert.equal(appCalls, 1);
  assert.equal(appConfirmed, true);
});
