export function envDurationMs(name, fallback, minimum, maximum) {
  const raw = Number(process.env[name]);
  const value = Number.isFinite(raw) && raw > 0 ? raw : fallback;
  return Math.min(Math.max(Math.floor(value), minimum), maximum);
}

export async function withDeadline(promise, timeoutMs, label) {
  let timer;
  try {
    return await Promise.race([
      Promise.resolve(promise),
      new Promise((_, reject) => {
        // A deadline is part of the operation's correctness contract. Keep the
        // timer referenced so a stalled promise cannot outlive the watchdog just
        // because no other event-loop handles happen to be active.
        timer = setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

export function pipeWithWatchdog(
  stream,
  writeStream,
  {
    maxBytes,
    idleTimeoutMs,
    totalTimeoutMs,
    overflowMessage = "inbound media exceeds size limit",
  },
) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let writtenBytes = 0;
    let idleTimer;
    let totalTimer;

    const cleanupTimers = () => {
      clearTimeout(idleTimer);
      clearTimeout(totalTimer);
    };
    const finish = (error = null) => {
      if (settled) return;
      settled = true;
      cleanupTimers();
      if (error) {
        try { writeStream.destroy(); } catch {}
        try { stream.destroy?.(); } catch {}
        reject(error);
      } else {
        resolve(writtenBytes);
      }
    };
    const armIdleTimer = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(
        () => finish(new Error("inbound media stream idle timed out")),
        idleTimeoutMs,
      );
    };

    totalTimer = setTimeout(
      () => finish(new Error("inbound media total download timed out")),
      totalTimeoutMs,
    );
    armIdleTimer();

    writeStream.on("error", finish);
    stream.on("error", finish);
    stream.on("data", (chunk) => {
      writtenBytes += chunk.length;
      armIdleTimer();
      if (writtenBytes > maxBytes) finish(new Error(overflowMessage));
    });
    writeStream.on("finish", () => finish());
    stream.pipe(writeStream);
  });
}

export function writeBoundedSse(client, payload, maxBufferedBytes) {
  const text = String(payload || "");
  const buffered = Math.max(0, Number(client?.writableLength || 0));
  const nextBytes = Buffer.byteLength(text, "utf8");
  if (buffered + nextBytes > maxBufferedBytes) {
    try { client.destroy?.(); } catch {}
    return false;
  }
  try {
    client.write(text);
    return true;
  } catch {
    try { client.destroy?.(); } catch {}
    return false;
  }
}

export async function settleWithin(promises, timeoutMs) {
  let timer;
  try {
    const result = await Promise.race([
      Promise.allSettled(promises).then(() => true),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(false), timeoutMs);
      }),
    ]);
    return Boolean(result);
  } finally {
    clearTimeout(timer);
  }
}
