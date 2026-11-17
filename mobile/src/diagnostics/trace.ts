import {AppState} from "react-native";
import {capture} from "../capture/native.ts";

/**
 * A plain trace of what was tapped and what VOOL did, for finding a fault with the owner: screen names, events,
 * timings and short codes only. Never message text, file names, addresses, keys or picture data. Each line goes to
 * the phone's own log (tag VoolCapture) and is kept briefly in memory to be sent to the computer's trace file.
 */
type Detail = Record<string, string | number | boolean | null | undefined>;

const pending: string[] = [];
const started = Date.now();

function clock(): string {
  const now = new Date();
  const pad = (value: number, size = 2) => String(value).padStart(size, "0");
  return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(now.getMilliseconds(), 3)}`;
}

function plain(value: Detail[string]): string {
  if (value === null || value === undefined) return "-";
  return String(value).replace(/[^\w.:/%+-]/g, "_").slice(0, 40);
}

export function trace(event: string, detail: Detail = {}): void {
  const parts = Object.entries(detail).map(([key, value]) => `${key}=${plain(value)}`);
  const line = [clock(), `+${Date.now() - started}ms`, event, ...parts].join(" ");
  try {capture?.trace?.(line);} catch {/* the trace never breaks the app */}
  pending.push(line);
  if (pending.length > 600) pending.splice(0, pending.length - 600);
}

/** Lines not yet on the computer, at most 200 at a time; [] when there are none. */
export function takeTrace(): string[] {
  return pending.splice(0, 200);
}

/** Puts lines back in front when sending them failed (the computer was out of reach). */
export function keepTrace(lines: string[]): void {
  pending.unshift(...lines);
  if (pending.length > 600) pending.splice(600);
}

// The app's own thread: a tick every 100 ms; a tick that comes late means taps wait that long too.
// Timers stop while VOOL is in the background, so that gap is not counted.
let last = Date.now();
setInterval(() => {
  const now = Date.now();
  const late = now - last - 100;
  if (late >= 250 && (AppState.currentState || "active") === "active") trace("js busy", {ms: late});
  last = now;
}, 100);
AppState.addEventListener("change", () => {last = Date.now();});
