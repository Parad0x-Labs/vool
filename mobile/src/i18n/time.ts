import {language} from "./index.ts";

// When a message was made, in this phone's own time zone and the app language. The computer sends ISO times in UTC;
// nothing here guesses one. No recorded time, or one that does not parse, gives "" so the screen shows nothing.

function parsed(value: unknown): Date | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? null : when;
}

function formatted(when: Date, options: Intl.DateTimeFormatOptions, timeZone?: string): string {
  const settings = timeZone ? {...options, timeZone} : options;
  try {
    return new Intl.DateTimeFormat(language(), settings).format(when);
  } catch {
    try {return new Intl.DateTimeFormat(undefined, settings).format(when);} catch {return "";}
  }
}

/** The full local date and time of a message, to the second: shown when the person taps or holds it. */
export function messageTimeText(value: unknown, timeZone?: string): string {
  const when = parsed(value);
  if (!when) return "";
  return formatted(when, {year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit"}, timeZone);
}

/** The local clock time of a step written while a long task runs. */
export function stepTimeText(value: unknown, timeZone?: string): string {
  const when = parsed(value);
  if (!when) return "";
  return formatted(when, {hour: "numeric", minute: "2-digit"}, timeZone);
}

/** A chat's last activity for the chat list: the date and time, never the raw stored text. */
export function chatUpdatedText(value: unknown, timeZone?: string): string {
  const when = parsed(value);
  if (!when) return "";
  return formatted(when, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}, timeZone);
}
