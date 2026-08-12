export function createTwoStepGate({ windowMs = 10_000, now = () => Date.now() } = {}) {
  let armedUntil = 0;

  return {
    activate() {
      const current = Number(now());
      if (armedUntil && current < armedUntil) {
        armedUntil = 0;
        return "confirmed";
      }
      armedUntil = current + windowMs;
      return "armed";
    },

    disarm() {
      armedUntil = 0;
    },

    isArmed() {
      return Boolean(armedUntil && Number(now()) < armedUntil);
    },

    remainingMs() {
      return Math.max(0, armedUntil - Number(now()));
    },
  };
}
