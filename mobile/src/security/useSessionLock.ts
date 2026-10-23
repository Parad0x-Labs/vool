import {useEffect, useMemo, useState} from "react";
import {AppState} from "react-native";
import * as LocalAuthentication from "expo-local-authentication";
import {SessionLock} from "./session.ts";
import {readIdleMinutes,saveIdleMinutes} from "./idlePreference.ts";
import {tr} from "../i18n/index.ts";

export function useSessionLock(deviceId: string | null) {
  const [, redraw] = useState(0);
  const [failed, setFailed] = useState(false);
  const [noScreenLock, setNoScreenLock] = useState(false);
  const [idleMinutes,setIdleMinutes]=useState(5);
  useEffect(()=>{let current=true;setIdleMinutes(5);if(deviceId)void readIdleMinutes(deviceId).then(value=>{if(current)setIdleMinutes(value);}).catch(()=>{});return()=>{current=false;};},[deviceId]);
  const state = useMemo(() => new SessionLock(() => redraw(x => x + 1), Date.now, idleMinutes*60_000), [deviceId,idleMinutes]);
  useEffect(() => {
    state.background(AppState.currentState || "active");
    const subscription = AppState.addEventListener("change", value => state.background(value));
    const timer = setInterval(() => state.tick(), 1000);
    return () => {subscription.remove(); clearInterval(timer);};
  }, [state]);
  return {idleMinutes, setIdleMinutes:async (value:number)=>{if(deviceId){await saveIdleMinutes(deviceId,value);setIdleMinutes(value);}}, locked: !!deviceId && state.locked, failed, noScreenLock, activity: AppState.currentState,
    touch: () => state.touch(), unlock: async () => {
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
        })).success;
      }, true); // Android's trusted passcode UI may temporarily background the activity.
      setFailed(!ok);
    } catch {setFailed(true);}
  }};
}
