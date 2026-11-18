import {test, afterEach} from "node:test";
import assert from "node:assert/strict";
import {messages, sourceIndex} from "../src/i18n/messages.ts";
import {tr, language, setLanguage, setTablet, rightToLeft, errorText} from "../src/i18n/index.ts";
afterEach(() => setLanguage("en"));

test("native catalogs cover all 17 languages and preserve parameter names", () => {
  assert.equal(Object.keys(messages).length, 17);
  const keys = Object.keys(messages.en).sort();
  const placeholders = value => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort();
  for (const catalog of Object.values(messages)) {
    assert.deepEqual(Object.keys(catalog).sort(), keys);
    for (const key of keys) assert.deepEqual(placeholders(catalog[key]), placeholders(messages.en[key]), key);
  }
  for (const [source, key] of Object.entries(sourceIndex)) assert.equal(messages.en[key], source);
});
test("money safety copy follows the chosen locale without changing authority values", () => {
  setLanguage("lt-LT");
  assert.equal(language(), "lt");
  assert.equal(tr("Completed payments cannot be undone."), "Užbaigtų mokėjimų atšaukti negalima.");
  assert.equal(errorText({code: "phone_right_off_or_expired"}), "Kompiuteryje atidarykite Nustatymus, tada Telefonas ir pokalbių programos, pasirinkite, ką šis telefonas gali daryti, ir paspauskite Atlikta.");
  assert.equal(tr("0.009 SOL"), "0.009 SOL");
  setLanguage("ar"); assert.equal(rightToLeft(), true);
  setLanguage("he-IL"); assert.equal(rightToLeft(), true);
  setLanguage("zh-Hant"); assert.equal(language(), "en");
  setLanguage("zh-TW"); assert.equal(language(), "en");
  setLanguage("constructor"); assert.equal(language(), "en");
  assert.equal(tr("{constructor}"), "{constructor}");
  setLanguage("xx"); assert.equal(tr("Wallet"), "Wallet");
});

test("a tablet is called a tablet in English, and the computer's own button names stay as they are", () => {
  setLanguage("en");
  setTablet(true);
  try {
    assert.equal(tr("Saved on this phone."), "Saved on this tablet.");
    assert.equal(errorText({code: "phone_right_off_or_expired"}), "On your computer, open Settings, then Phone and chat apps, choose what this tablet may do and press Done.");
    assert.match(tr("On your computer, open Settings, choose Phone and chat apps, then Pair a phone. Scan its code or enter the details below."), /choose Phone and chat apps, then Pair a phone\./);
    setLanguage("de"); assert.equal(tr("Save to phone"), "Auf dem Telefon speichern");
  } finally {setTablet(false); setLanguage("en");}
  assert.equal(tr("Saved on this phone."), "Saved on this phone.");
});
