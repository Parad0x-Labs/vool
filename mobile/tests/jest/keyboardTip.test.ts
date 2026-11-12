import {keyboardCanGoOnline} from "../../src/security/keyboardTip";

describe("keyboard tip", () => {
  it("is offered for keyboards that can go online", () => {
    expect(keyboardCanGoOnline("com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME")).toBe(true);
    expect(keyboardCanGoOnline("com.touchtype.swiftkey/com.touchtype.KeyboardService")).toBe(true);
  });
  it("is not offered for offline keyboards or when the keyboard is unknown", () => {
    expect(keyboardCanGoOnline("helium314.keyboard/.latin.LatinIME")).toBe(false);
    expect(keyboardCanGoOnline("dev.patrickgold.florisboard/.FlorisImeService")).toBe(false);
    expect(keyboardCanGoOnline("")).toBe(false);
  });
});
