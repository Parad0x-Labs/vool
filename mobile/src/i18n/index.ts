import {messages, sourceIndex} from "./messages.ts";

export const languages: Record<string, string> = {en: "English", ar: "العربية", de: "Deutsch", es: "Español", fr: "Français", he: "עברית", hi: "हिन्दी", ja: "日本語", ko: "한국어", lt: "Lietuvių", pl: "Polski", pt: "Português", ru: "Русский", tr: "Türkçe", uk: "Українська", vi: "Tiếng Việt", "zh-Hans": "简体中文"};
export function resolveLanguage(tag: string): string {
  if (/^zh[-_](?:hant|tw|hk|mo)(?:[-_]|$)/i.test(tag)) return "en";
  if (tag.toLowerCase().startsWith("zh")) return "zh-Hans";
  const base = tag.split(/[-_]/)[0].toLowerCase();
  return Object.prototype.hasOwnProperty.call(languages, base) ? base : "en";
}
let locale = (() => {try {return resolveLanguage(Intl.DateTimeFormat().resolvedOptions().locale);} catch {return "en";}})();
const listeners = new Set<() => void>();
export function language() {return locale;}
export function rightToLeft() {return locale === "ar" || locale === "he";}
export function subscribeLanguage(listener: () => void) {listeners.add(listener); return () => {listeners.delete(listener);};}
export function setLanguage(value: string) {locale = resolveLanguage(value); for (const listener of listeners) listener();}
export function tr(source: string, values: Record<string, string | number> = {}): string {
  const key = Object.prototype.hasOwnProperty.call(sourceIndex, source) ? (sourceIndex as Record<string, string>)[source] : undefined;
  const catalog = (messages as Record<string, Record<string, string>>)[locale] || messages.en;
  const text = key ? catalog[key] || (messages.en as Record<string, string>)[key] || source : source;
  return text.replace(/\{(\w+)\}/g, (whole, name) => !Object.prototype.hasOwnProperty.call(values, name) ? whole : String(values[name]));
}

export function errorText(value: unknown): string {
  const error = value as {code?: string; message?: string} | null;
  const message = String(error?.message || "");
  if (Object.prototype.hasOwnProperty.call(sourceIndex, message)) return tr(message);
  if (error?.code === "device_revoked") return tr("This phone is unpaired. Pair it again on your computer.");
  if (error?.code === "sealed_response_invalid" || error?.code === "sealed_authentication_failed") return tr("The computer’s identity could not be verified. Check it before pairing again.");
  if (error?.code === "phone_right_off_or_expired" || error?.code === "phone_history_required") return tr("Change this phone’s permissions on your computer.");
  // The screen stays plain; the cause goes to the developer log (Expo terminal) for diagnosis.
  console.warn("VOOL phone error", error?.code || "", message);
  return tr("Could not complete this. Check your computer.");
}

export function notificationText(event: {event_type:string; security_notice?:unknown}): string {
  const key = event.security_notice === "device_paired" ? "phone.ui.device_paired" : event.security_notice === "device_revoked" ? "phone.ui.device_revoked" : "phone.event." + event.event_type;
  const catalog = (messages as Record<string, Record<string, string>>)[locale] || messages.en;
  return catalog[key] || catalog["phone.event.desktop_inbox"] || "VOOL";
}

export function permissionText(right:string):string {
  const catalog=(messages as Record<string,Record<string,string>>)[locale] || messages.en;
  return catalog["phone.right."+right] || right;
}
