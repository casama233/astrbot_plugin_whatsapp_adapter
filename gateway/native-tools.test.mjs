import assert from "node:assert/strict";
import test from "node:test";

import { generateWAMessageContent } from "@whiskeysockets/baileys";

import {
  buildContactContent,
  buildEventContent,
  buildPollContent,
  eventDetailsFromMessage,
} from "./native-tools.mjs";


test("builds a sanitized native contact accepted by Baileys rc14", async () => {
  const content = buildContactContent({
    displayName: "Alice\nAdmin",
    phoneNumber: "+852 1234-5678",
    organization: "Example, Inc.; HK",
  });
  const contact = content.contacts.contacts[0];

  assert.equal(contact.displayName, "Alice\nAdmin");
  assert.match(contact.vcard, /FN:Alice\\nAdmin/);
  assert.match(contact.vcard, /ORG:Example\\, Inc\.\\; HK/);
  assert.match(contact.vcard, /waid=85212345678:\+85212345678/);

  const generated = await generateWAMessageContent(content, {});
  assert.equal(generated.contactMessage.displayName, "Alice\nAdmin");
  assert.equal(generated.contactsArrayMessage, null);
});


test("builds timezone-resolved native event dates accepted by Baileys rc14", async () => {
  const content = buildEventContent({
    name: "Shenzhen trip",
    description: "Meet at the station",
    startTimestampMs: 1_786_755_600_000,
    endTimestampMs: 1_786_766_400_000,
    locationName: "Shenzhen",
    locationAddress: "Guangdong",
    extraGuestsAllowed: false,
  });

  assert.ok(content.event.startDate instanceof Date);
  assert.ok(content.event.endDate instanceof Date);
  const generated = await generateWAMessageContent(content, {});
  assert.equal(Number(generated.eventMessage.startTime), 1_786_755_600);
  assert.equal(Number(generated.eventMessage.endTime), 1_786_766_400);
  assert.equal(generated.eventMessage.location.name, "Shenzhen");
});


test("builds a validated native poll and rejects unsafe bounds", async () => {
  const content = buildPollContent({
    name: "Lunch?",
    options: ["Dim sum", "Noodles"],
    selectableCount: 1,
  });
  const generated = await generateWAMessageContent(content, {});
  assert.equal(generated.pollCreationMessageV3.name, "Lunch?");
  assert.deepEqual(
    generated.pollCreationMessageV3.options.map((option) => option.optionName),
    ["Dim sum", "Noodles"],
  );

  assert.throws(
    () => buildPollContent({ name: "Bad", options: ["A", "a"] }),
    /unique/,
  );
  assert.throws(
    () => buildPollContent({ name: "Bad", options: ["A", "B"], selectableCount: 3 }),
    /between 0 and 2/,
  );
});


test("rejects malformed contact and event payloads before socket send", () => {
  assert.throws(
    () => buildContactContent({ displayName: "Alice", phoneNumber: "javascript:alert(1)" }),
    /invalid format/,
  );
  assert.throws(
    () => buildEventContent({ name: "Trip", startTimestampMs: "not-a-date" }),
    /must be finite/,
  );
  assert.throws(
    () => buildEventContent({
      name: "Trip",
      startTimestampMs: 2_000,
      endTimestampMs: 1_000,
    }),
    /must be later/,
  );
});

test("normalizes inbound native event details into JSON-safe metadata", async () => {
  const generated = await generateWAMessageContent(
    buildEventContent({
      name: "Shenzhen trip",
      description: "Meet at the station",
      startTimestampMs: 1_786_755_600_000,
      endTimestampMs: 1_786_766_400_000,
      locationName: "Shenzhen",
      locationAddress: "Guangdong",
      extraGuestsAllowed: true,
    }),
    {},
  );
  generated.eventMessage.hasReminder = true;
  generated.eventMessage.reminderOffsetSec = 900;

  assert.deepEqual(eventDetailsFromMessage(generated), {
    name: "Shenzhen trip",
    description: "Meet at the station",
    location: {
      name: "Shenzhen",
      address: "Guangdong",
      url: "",
      latitude: null,
      longitude: null,
    },
    joinLink: "",
    startTime: 1_786_755_600,
    endTime: 1_786_766_400,
    isCanceled: false,
    extraGuestsAllowed: true,
    isScheduleCall: false,
    hasReminder: true,
    reminderOffsetSec: 900,
  });
  assert.equal(eventDetailsFromMessage({ conversation: "not an event" }), null);
});
