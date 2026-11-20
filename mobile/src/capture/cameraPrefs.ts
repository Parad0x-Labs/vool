import {capture, type FlashMode, type PhotoFilter, PHOTO_FILTERS} from "./native.ts";

/** The private camera's own settings. Kept on the phone only. */
export interface CameraPrefs {
  flash: FlashMode;
  timer: 0 | 3 | 10;
  grid: 0 | 3 | 4;
  aspect: "4:3" | "16:9";
  photoSize: string;
  videoQuality: "highest" | "2160" | "1080" | "720";
  steady: boolean;
  mirror: boolean;
  filter: PhotoFilter;
}

export const DEFAULT_CAMERA_PREFS: CameraPrefs = {
  flash: "off", timer: 0, grid: 0, aspect: "4:3", photoSize: "max", videoQuality: "highest", steady: true, mirror: false, filter: "none",
};

const pick = <T,>(value: unknown, allowed: readonly T[], fallback: T): T => allowed.includes(value as T) ? value as T : fallback;

/** Reads the saved settings; anything unknown or damaged falls back to the default. */
export function parseCameraPrefs(raw: string): CameraPrefs {
  let data: Record<string, unknown> = {};
  try {data = raw ? JSON.parse(raw) as Record<string, unknown> : {};} catch {data = {};}
  if (!data || typeof data !== "object") data = {};
  const d = DEFAULT_CAMERA_PREFS;
  return {
    flash: pick(data.flash, ["off", "auto", "on"] as const, d.flash),
    timer: pick(data.timer, [0, 3, 10] as const, d.timer),
    grid: pick(data.grid, [0, 3, 4] as const, d.grid),
    aspect: pick(data.aspect, ["4:3", "16:9"] as const, d.aspect),
    photoSize: typeof data.photoSize === "string" && /^(max|\d{2,5}x\d{2,5})$/.test(data.photoSize) ? data.photoSize : d.photoSize,
    videoQuality: pick(data.videoQuality, ["highest", "2160", "1080", "720"] as const, d.videoQuality),
    steady: typeof data.steady === "boolean" ? data.steady : d.steady,
    mirror: typeof data.mirror === "boolean" ? data.mirror : d.mirror,
    filter: pick(data.filter, PHOTO_FILTERS, d.filter),
  };
}

export function loadCameraPrefs(): CameraPrefs {
  try {return parseCameraPrefs(capture?.getCameraPrefs() ?? "");} catch {return DEFAULT_CAMERA_PREFS;}
}

export function saveCameraPrefs(prefs: CameraPrefs): void {
  try {capture?.setCameraPrefs(JSON.stringify(prefs));} catch {/* kept for this session only */}
}

/** "4000x3000" -> "12 MP" */
export function megapixels(size: string): string {
  const [w, h] = size.split("x").map(Number);
  if (!w || !h) return "";
  const mp = (w * h) / 1e6;
  return `${mp >= 10 ? Math.round(mp) : mp.toFixed(1).replace(/\.0$/, "")} MP`;
}
