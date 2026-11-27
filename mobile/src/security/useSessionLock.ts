import {useEffect, useMemo, useRef, useState} from "react";
import {AppState} from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import {SessionLock} from "./session.ts";
import {readIdleMinutes,saveIdleMinutes} from "./idlePreference.ts";
import {tr} from "../i18n/index.ts";

/** hold: answers true while VOOL should not ask for the fingerprint by itself (the camera-only opening). */
export function useSessionLock(deviceId: string | null, hold: () => boolean = () => false) {
  const [, redraw] = useState(0);
  const [failed, setFailed] = useState(false);
  const [noScreenLock, setNoScreenLock] = useState(false);
  const [idleMinutes,setIdleMinutes]=useState(5);
  // The saved idle time makes a new lock; asking before it has loaded would unlock a lock that is then thrown away.
  const [idleLoaded,setIdleLoaded]=useState(false);
  useEffect(()=>{let current=true;setIdleMinutes(5);setIdleLoaded(false);if(deviceId)void readIdleMinutes(deviceId).then(value=>{if(current)setIdleMinutes(value);}).catch(()=>{}).finally(()=>{if(current)setIdleLoaded(true);});return()=>{current=false;};},[deviceId]);
  const state = useMemo(() => new SessionLock(() => redraw(x => x + 1), Date.now, idleMinutes*60_000), [deviceId,idleMinutes]);
  // Ask for the fingerprint or face by itself when VOOL opens or comes back, so a phone that
  // already knows its owner unlocks without a tap. Once per opening: a cancel or a failure
  // leaves the Unlock button, and the system's own prompt going away does not count as leaving.
  const prompting = useRef(false);
  const askOnOpen = useRef(true);
  const unlockRef = useRef<() => Promise<void>>(async () => {});
  // A new pairing is a new opening: its first lock asks by itself too. Before (sls, 2026-11-27) the ask was
  // spent on the old pairing's opening, so the first lock after pairing again waited for a tap on Unlock.
  const pairedAs = useRef(deviceId);
  if (pairedAs.current !== deviceId) {pairedAs.current = deviceId; askOnOpen.current = true;}
  const holdRef = useRef(hold);
  holdRef.current = hold;
  const maybeAsk = () => {
    if (!deviceId || !idleLoaded || !state.locked || !askOnOpen.current || prompting.current) return;
    if (holdRef.current()) return;
    if ((AppState.currentState || "active") !== "active") return;
    askOnOpen.current = false;
    void unlockRef.current();
  };
  useEffect(() => {
    state.background(AppState.currentState || "active");
    const subscription = AppState.addEventListener("change", value => {
      state.background(value);
      if (value === "background" && !prompting.current) askOnOpen.current = true;
      if (value === "active") maybeAsk();
    });
    const timer = setInterval(() => state.tick(), 1000);
    maybeAsk();
    return () => {subscription.remove(); clearInterval(timer);};
  }, [state, idleLoaded]);
  const unlock = async () => {
    prompting.current = true;
    setFailed(false);
    setNoScreenLock(false);
    try {
      const ok = await state.unlock(async () => {
        if (await LocalAuthentication.getEnrolledLevelAsync() === LocalAuthentication.SecurityLevel.NONE) {
          setNoScreenLock(true); return false;
        }
        return (await LocalAuthentication.authenticateAsync({
          promptMessage: tr("Unlock VOOL"), cancelLabel: tr("Cancel"),
          disableDeviceFallback: false, biometricsSecurityLevel: "strong",
          requireConfirmation: false, // a face that matches unlocks at once, no extra Confirm tap
        })).success;
      }, true); // Android's trusted passcode UI may temporarily background the activity.
      setFailed(!ok);
    } catch {setFailed(true);} finally {prompting.current = false;}
  };
  unlockRef.current = unlock;
  return {idleMinutes, setIdleMinutes:async (value:number)=>{if(deviceId){await saveIdleMinutes(deviceId,value);setIdleMinutes(value);}}, locked: !!deviceId && state.locked, failed, noScreenLock, activity: AppState.currentState,
    touch: () => state.touch(), unlock};
}
