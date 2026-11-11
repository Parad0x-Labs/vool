// A one-time suggestion to use a keyboard that cannot go online. Shown once per install,
// and only when the phone's current keyboard is one that can.
const SEEN_KEY = "vool.mobile.keyboard_tip_seen";
export const OFFLINE_KEYBOARD_URL = "https://f-droid.org/packages/helium314.keyboard/";

/** Keyboards that have internet access. Offline ones (HeliBoard, FlorisBoard, AOSP) never match. */
export function keyboardCanGoOnline(id: string): boolean {
  const app = id.split("/")[0].toLowerCase();
  return ["com.google.android.inputmethod", "com.touchtype.swiftkey", "com.samsung.android.honeyboard", "com.baidu.input", "com.sohu.inputmethod"]
    .some(prefix => app.startsWith(prefix));
}

export async function keyboardTipSeen(): Promise<boolean> {
  try {const store = await import("expo-secure-store"); return (await store.getItemAsync(SEEN_KEY)) === "1";}
  catch {return true;} // if we cannot remember the answer, do not nag
}

export async function rememberKeyboardTip(): Promise<void> {
  try {const store = await import("expo-secure-store"); await store.setItemAsync(SEEN_KEY, "1");} catch { /* asked again next time */ }
}
