import {test, afterEach} from "node:test";
import assert from "node:assert/strict";
import {setLanguage} from "../src/i18n/index.ts";
import {chatUpdatedText, messageTimeText, stepTimeText} from "../src/i18n/time.ts";
afterEach(() => setLanguage("en"));

// The phone shows when a message was made, the way the computer's chat does on hover (parity, 2026-10-29).
const REPLY = "2026-10-28T23:27:05.412+00:00";

test("a message time is the phone's local date and time to the second", () => {
  setLanguage("en");
  const vilnius = messageTimeText(REPLY, "Europe/Vilnius");
  assert.match(vilnius, /Oct/);
  assert.match(vilnius, /29/);
  assert.match(vilnius, /1:27:05|01:27:05/);
  const tokyo = messageTimeText(REPLY, "Asia/Tokyo");
  assert.match(tokyo, /8:27:05|08:27:05/);
});

test("the words follow the app language", () => {
  setLanguage("de");
  assert.match(messageTimeText(REPLY, "Europe/Berlin"), /Okt/);
});

test("a step written during a long task shows its clock time", () => {
  assert.match(stepTimeText(REPLY, "UTC"), /11:27|23:27/);
});

test("no recorded time shows nothing, never a guess", () => {
  for (const value of [undefined, null, "", "   ", "not a time", 1730158025]) {
    assert.equal(messageTimeText(value), "");
    assert.equal(stepTimeText(value), "");
    assert.equal(chatUpdatedText(value), "");
  }
});

test("the chat list shows a readable time, not the stored text", () => {
  const text = chatUpdatedText(REPLY, "UTC");
  assert.ok(!text.includes("T23") && !text.includes("+00:00"), text);
  assert.match(text, /Oct/);
});
