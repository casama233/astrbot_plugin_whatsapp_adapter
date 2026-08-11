(() => {
  "use strict";

  const CONFIRM_WINDOW_MS = 10_000;

  function installTwoStepConfirm({ buttonId, armedLabel, helperId = null, helperText = "" }) {
    const button = document.getElementById(buttonId);
    if (!button) return null;

    const helper = helperId ? document.getElementById(helperId) : null;
    const originalMarkup = button.innerHTML;
    let previousHelperText = "";
    let armedUntil = 0;
    let resetTimer = null;

    function disarm({ restoreHelper = true } = {}) {
      armedUntil = 0;
      delete button.dataset.confirmArmed;
      if (resetTimer) clearTimeout(resetTimer);
      resetTimer = null;
      if (!button.disabled) button.innerHTML = originalMarkup;
      if (restoreHelper && helper && previousHelperText) {
        helper.textContent = previousHelperText;
      }
      previousHelperText = "";
    }

    button.addEventListener(
      "click",
      (event) => {
        if (button.disabled) return;

        const now = Date.now();
        if (!armedUntil || now >= armedUntil) {
          event.preventDefault();
          event.stopImmediatePropagation();
          armedUntil = now + CONFIRM_WINDOW_MS;
          button.dataset.confirmArmed = "true";
          button.textContent = armedLabel;
          if (helper) {
            previousHelperText = helper.textContent || "";
            helper.textContent = helperText;
          }
          resetTimer = setTimeout(() => disarm(), CONFIRM_WINDOW_MS);
          return;
        }

        // AstrBot Plugin Pages intentionally omit `allow-modals` from their
        // iframe sandbox. The existing page handlers still call window.confirm,
        // so make that one synchronous confirmation succeed only for this
        // already-confirmed second click, then immediately restore it.
        armedUntil = 0;
        delete button.dataset.confirmArmed;
        if (resetTimer) clearTimeout(resetTimer);
        resetTimer = null;
        previousHelperText = "";

        const previousConfirm = window.confirm;
        window.confirm = () => true;
        queueMicrotask(() => {
          window.confirm = previousConfirm;
        });
      },
      true,
    );

    return disarm;
  }

  const disarmUpdate = installTwoStepConfirm({
    buttonId: "installUpdate",
    armedLabel: "再次点击确认更新",
    helperId: "updateMessage",
    helperText: "为防误触，请在 10 秒内再次点击「确认更新」。更新过程会先验证 Release 与依赖。",
  });

  installTwoStepConfirm({
    buttonId: "logout",
    armedLabel: "再次点击确认登出",
  });

  document.getElementById("checkUpdate")?.addEventListener(
    "click",
    () => disarmUpdate?.(),
    true,
  );
})();
