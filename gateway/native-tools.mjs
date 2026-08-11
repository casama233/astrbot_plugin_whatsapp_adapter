const MAX_EVENT_DURATION_MS = 366 * 24 * 60 * 60 * 1000;

function text(value, label, maximum, { required = true } = {}) {
  const normalized = String(value ?? "").replaceAll("\u0000", "").trim();
  if (required && !normalized) throw new TypeError(`${label} is required`);
  if (normalized.length > maximum) {
    throw new TypeError(`${label} must not exceed ${maximum} characters`);
  }
  return normalized;
}

function escapeVCardText(value) {
  return String(value || "")
    .replaceAll("\\", "\\\\")
    .replaceAll("\r\n", "\\n")
    .replaceAll("\r", "\\n")
    .replaceAll("\n", "\\n")
    .replaceAll(";", "\\;")
    .replaceAll(",", "\\,");
}

export function buildContactContent(body) {
  const displayName = text(body?.displayName, "contact displayName", 100);
  const rawPhone = String(body?.phoneNumber || "").trim();
  if (!/^[+0-9 ()-]+$/.test(rawPhone)) {
    throw new TypeError("contact phoneNumber has an invalid format");
  }
  const digits = rawPhone.replace(/\D/g, "");
  if (digits.length < 7 || digits.length > 15) {
    throw new TypeError("contact phoneNumber must contain 7 to 15 digits");
  }
  const phoneNumber = `+${digits}`;
  const organization = text(
    body?.organization,
    "contact organization",
    100,
    { required: false },
  );
  const escapedName = escapeVCardText(displayName);
  const lines = [
    "BEGIN:VCARD",
    "VERSION:3.0",
    `N:${escapedName};;;;`,
    `FN:${escapedName}`,
  ];
  if (organization) lines.push(`ORG:${escapeVCardText(organization)}`);
  lines.push(`TEL;type=CELL;type=VOICE;waid=${digits}:${phoneNumber}`, "END:VCARD");

  return {
    contacts: {
      displayName,
      contacts: [{ displayName, vcard: lines.join("\r\n") }],
    },
  };
}

function timestampDate(value, label) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds)) throw new TypeError(`${label} must be finite`);
  const date = new Date(milliseconds);
  if (!Number.isFinite(date.getTime())) throw new TypeError(`${label} is invalid`);
  return date;
}

export function buildEventContent(body) {
  const name = text(body?.name, "event name", 100);
  const startDate = timestampDate(body?.startTimestampMs, "event startTimestampMs");
  let endDate;
  if (body?.endTimestampMs !== undefined && body?.endTimestampMs !== null) {
    endDate = timestampDate(body.endTimestampMs, "event endTimestampMs");
    if (endDate <= startDate) {
      throw new TypeError("event endTimestampMs must be later than startTimestampMs");
    }
    if (endDate.getTime() - startDate.getTime() > MAX_EVENT_DURATION_MS) {
      throw new TypeError("event duration must not exceed 366 days");
    }
  }
  if (
    body?.extraGuestsAllowed !== undefined
    && typeof body.extraGuestsAllowed !== "boolean"
  ) {
    throw new TypeError("event extraGuestsAllowed must be boolean");
  }

  const event = {
    name,
    startDate,
    description: text(
      body?.description,
      "event description",
      2048,
      { required: false },
    ) || undefined,
    endDate,
    extraGuestsAllowed: body?.extraGuestsAllowed === true,
  };
  const locationName = text(
    body?.locationName,
    "event locationName",
    200,
    { required: false },
  );
  const locationAddress = text(
    body?.locationAddress,
    "event locationAddress",
    500,
    { required: false },
  );
  if (locationName || locationAddress) {
    event.location = {
      name: locationName || undefined,
      address: locationAddress || undefined,
    };
  }
  return { event };
}

export function buildPollContent(body) {
  const name = text(body?.name, "poll name", 255);
  if (!Array.isArray(body?.options)) throw new TypeError("poll options must be an array");
  const values = body.options.map((option) => text(option, "poll option", 100));
  if (values.length < 2 || values.length > 12) {
    throw new TypeError("poll requires 2 to 12 options");
  }
  if (new Set(values.map((value) => value.toLocaleLowerCase())).size !== values.length) {
    throw new TypeError("poll options must be unique");
  }
  const selectableCount = Number(body?.selectableCount ?? body?.selectable_count ?? 1);
  if (
    !Number.isInteger(selectableCount)
    || selectableCount < 0
    || selectableCount > values.length
  ) {
    throw new TypeError(`poll selectableCount must be between 0 and ${values.length}`);
  }
  return { poll: { name, values, selectableCount } };
}

function optionalFiniteNumber(value) {
  if (value === undefined || value === null) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/** Convert an inbound Baileys EventMessage into JSON-safe Gateway metadata. */
export function eventDetailsFromMessage(message) {
  const event = message?.eventMessage;
  if (!event || typeof event !== "object") return null;
  const location = event.location && typeof event.location === "object"
    ? {
        name: String(event.location.name || ""),
        address: String(event.location.address || ""),
        url: String(event.location.url || ""),
        latitude: optionalFiniteNumber(event.location.degreesLatitude),
        longitude: optionalFiniteNumber(event.location.degreesLongitude),
      }
    : null;

  return {
    name: String(event.name || ""),
    description: String(event.description || ""),
    location,
    joinLink: String(event.joinLink || ""),
    startTime: optionalFiniteNumber(event.startTime),
    endTime: optionalFiniteNumber(event.endTime),
    isCanceled: event.isCanceled === true,
    extraGuestsAllowed: event.extraGuestsAllowed === true,
    isScheduleCall: event.isScheduleCall === true,
    hasReminder: event.hasReminder === true,
    reminderOffsetSec: optionalFiniteNumber(event.reminderOffsetSec),
  };
}
