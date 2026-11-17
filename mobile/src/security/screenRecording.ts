import {useEffect, useState} from "react";
import * as ScreenCapture from "expo-screen-capture";

/**
 * The Vault and the money screens stay out of screen recordings, screenshots and the app switcher. For a demo the
 * owner can allow recording; it is never saved, and it ends by itself after 30 minutes or when VOOL closes.
 */
export const RECORDING_MINUTES = 30;
let allowed = false;
let timer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<(value: boolean) => void>();

export function recordingAllowed(): boolean {return allowed;}

export function setRecordingAllowed(value: boolean): void {
  if (timer) {clearTimeout(timer); timer = null;}
  allowed = value;
  if (value) timer = setTimeout(() => setRecordingAllowed(false), RECORDING_MINUTES * 60_000);
  listeners.forEach(listener => listener(value));
}

export function useRecordingAllowed(): boolean {
  const [value, setValue] = useState(allowed);
  useEffect(() => {
    listeners.add(setValue);
    setValue(allowed);
    return () => {listeners.delete(setValue);};
  }, []);
  return value;
}

/** Keeps the screen out of recordings while [active], unless the owner allowed recording. */
export function useScreenGuard(key: string, active: boolean): void {
  const open = useRecordingAllowed();
  const guard = active && !open;
  useEffect(() => {
    if (!guard) return;
    void ScreenCapture.preventScreenCaptureAsync(key).catch(() => {});
    return () => {void ScreenCapture.allowScreenCaptureAsync(key).catch(() => {});};
  }, [key, guard]);
}
