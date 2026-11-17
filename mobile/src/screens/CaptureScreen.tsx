import React, {useEffect, useRef, useState} from "react";
import {ActivityIndicator, Linking, PermissionsAndroid, Pressable, StyleSheet, Text, View} from "react-native";
import {StatusBar} from "expo-status-bar";

import {errorText, tr} from "../i18n/index.ts";
import {capture, PrivateCameraView, type CameraHandle, type CameraMode} from "../capture/native.ts";
import type {SendProblem} from "../capture/sender.ts";
import Face from "../ui/Face.tsx";
import Icon from "../ui/Icon.tsx";
import {useEdges} from "../ui/Frame.tsx";
import {C} from "../ui/theme.ts";

type Kind = "photo" | "video" | "sound";
const KINDS: Kind[] = ["photo", "video", "sound"];
const kindLabel = (kind: Kind) => kind === "photo" ? tr("Photo") : kind === "video" ? tr("Video") : tr("Sound");
const modeLabel = (mode: CameraMode) => mode === "night" ? tr("Night") : mode === "hdr" ? "HDR" : mode === "portrait" ? tr("Portrait")
  : mode === "retouch" ? tr("Retouch") : tr("Auto");

interface Props {
  waiting: number;
  sending: boolean;
  problem: SendProblem;
  /** Captures still being locked away. */
  saving: number;
  saveFailed: boolean;
  /** A capture was just handed to the saver; it is sent once it is locked away. */
  onCaptured: () => void;
  onClose: () => void;
}

async function allowed(kind: Kind): Promise<"" | "camera" | "microphone"> {
  const ask = async (permission: (typeof PermissionsAndroid.PERMISSIONS)[keyof typeof PermissionsAndroid.PERMISSIONS]) =>
    (await PermissionsAndroid.check(permission)) || (await PermissionsAndroid.request(permission)) === PermissionsAndroid.RESULTS.GRANTED;
  if (kind !== "sound" && !(await ask(PermissionsAndroid.PERMISSIONS.CAMERA))) return "camera";
  // Video records sound too when allowed; without it the video is silent rather than refused.
  if (kind === "sound" && !(await ask(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO))) return "microphone";
  if (kind === "video") await ask(PermissionsAndroid.PERMISSIONS.RECORD_AUDIO).catch(() => false);
  return "";
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return `${m}:${s < 10 ? "0" : ""}${s}`;
}

/**
 * VOOL's private camera and recorder. What it takes never goes to the phone's gallery or any cloud:
 * it is locked inside VOOL at once, sent to the computer, and deleted here only after the computer
 * confirms it has the very same file.
 */
export default function CaptureScreen({waiting, sending, problem, saving, saveFailed, onCaptured, onClose}: Props) {
  const edges = useEdges();
  const camera = useRef<CameraHandle>(null);
  const [kind, setKind] = useState<Kind>("photo");
  const [facing, setFacing] = useState<"back" | "front">("back");
  const [mode, setMode] = useState<CameraMode>("standard");
  const [modes, setModes] = useState<CameraMode[]>([]);
  const [denied, setDenied] = useState<"" | "camera" | "microphone">("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [note, setNote] = useState("");
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    let current = true;
    setReady(false);
    void allowed(kind).then(result => {if (current) {setDenied(result); setReady(!result);}}).catch(() => {if (current) setDenied("camera");});
    return () => {current = false;};
  }, [kind]);

  useEffect(() => {
    if (!recording) return;
    setSeconds(0);
    const timer = setInterval(() => setSeconds(value => value + 1), 1000);
    return () => clearInterval(timer);
  }, [recording]);

  // Leaving the screen (or the app locking) ends a sound recording and keeps what was recorded.
  useEffect(() => () => {if (capture?.soundRecording()) void capture.stopSound().then(onCaptured).catch(() => {});}, [onCaptured]);

  const stored = () => {
    setNote(tr("Saved privately. It goes to your computer, then leaves this phone."));
    onCaptured();
  };
  const failed = () => setNote(tr("Could not save it. Try again."));

  async function shutter() {
    if (busy || !capture) return;
    setBusy(true);
    setNote("");
    try {
      if (kind === "photo") {
        setFlash(true);
        setTimeout(() => setFlash(false), 120);
        await camera.current?.takePhoto();
        stored();
      } else if (kind === "video") {
        if (!recording) {await camera.current?.startVideo(); setRecording(true);}
        else {setRecording(false); await camera.current?.stopVideo(); stored();}
      } else if (!recording) {await capture.startSound(); setRecording(true);}
      else {setRecording(false); await capture.stopSound(); stored();}
    } catch {
      setRecording(false);
      failed();
    } finally {
      setBusy(false);
    }
  }

  const status = saveFailed ? tr("Could not save it. Try again.")
    : saving ? tr("Locking it away…")
    : problem === "not_allowed" ? errorText({code: "phone_right_off_or_expired"})
    : problem === "damaged" ? tr("Something arrived damaged. It stays on this phone and will be sent again.")
    : waiting && problem === "offline" ? tr("Your computer is out of reach. Everything stays locked here and goes when it answers.")
    : sending && waiting ? tr("Sending to your computer…")
    : waiting ? tr("Waiting for your computer: {count}", {count: waiting})
    : tr("Everything is on your computer.");

  if (!capture || !PrivateCameraView) return <View style={[styles.screen, styles.center, {paddingTop: edges.top}]}>
    <Text style={styles.message}>{tr("This phone cannot use the private camera.")}</Text>
    <Pressable style={styles.textButton} onPress={onClose} accessibilityRole="button"><Text style={styles.textButtonText}>{tr("Close")}</Text></Pressable>
  </View>;

  const showCamera = kind !== "sound" && ready && !denied;
  return (
    <View style={styles.screen}>
      <StatusBar style="light" />
      {showCamera ? <PrivateCameraView ref={camera} style={StyleSheet.absoluteFill} facing={facing} mode={mode} video={kind === "video"}
        onModes={event => {const list = event.nativeEvent.modes; setModes(list); if (mode !== "standard" && !list.includes(mode)) setMode("standard");}}
        onCaptured={() => stored()}
        onCameraError={() => setNote(tr("The camera could not start. Close it and try again."))} /> : null}
      {flash ? <View style={[StyleSheet.absoluteFill, styles.flash]} /> : null}
      {kind === "sound" && !denied ? <View style={[StyleSheet.absoluteFill, styles.center]}>
        <Face size={150} />
        <Text style={styles.timer}>{recording ? clock(seconds) : tr("Sound")}</Text>
      </View> : null}
      {denied ? <View style={[StyleSheet.absoluteFill, styles.center, styles.pad]}>
        <Icon name={denied === "camera" ? "camera-off-outline" : "microphone-off"} size={42} color={C.mute} />
        <Text style={styles.message}>{denied === "camera" ? tr("Allow camera access for VOOL in your phone settings.") : tr("Allow microphone access for VOOL in your phone settings.")}</Text>
        <Pressable style={styles.textButton} onPress={() => void Linking.openSettings()} accessibilityRole="button">
          <Text style={styles.textButtonText}>{tr("Open phone settings")}</Text></Pressable>
      </View> : null}

      <View style={[styles.top, {paddingTop: edges.top + 8}]}>
        <Pressable style={styles.round} onPress={onClose} hitSlop={8} accessibilityRole="button" accessibilityLabel={tr("Close")} disabled={recording}>
          <Icon name="close" size={24} color={recording ? C.faint : C.ink} />
        </Pressable>
        <View style={styles.status}>
          {saving || (sending && waiting) ? <ActivityIndicator size="small" color={C.acc} /> :
            <Icon name={saveFailed ? "alert-circle-outline" : waiting ? "lock-outline" : "check"} size={15} color={saveFailed ? C.bad : waiting ? C.acc : C.ok} />}
          <Text style={styles.statusText} numberOfLines={2}>{status}</Text>
        </View>
        {kind !== "sound" ? <Pressable style={styles.round} onPress={() => setFacing(f => f === "back" ? "front" : "back")} hitSlop={8}
          accessibilityRole="button" accessibilityLabel={tr("Switch camera")} disabled={recording}>
          <Icon name="camera-flip-outline" size={24} color={recording ? C.faint : C.ink} />
        </Pressable> : <View style={styles.round} />}
      </View>

      <View style={[styles.bottom, {paddingBottom: edges.bottom + 18}]}>
        {note ? <Text style={styles.note}>{note}</Text> : null}
        {kind === "photo" && modes.length ? <View style={styles.modes}>
          {(["standard", ...modes] as CameraMode[]).map(value => <Pressable key={value} onPress={() => setMode(value)}
            style={[styles.mode, mode === value && styles.modeOn]} accessibilityRole="button" accessibilityState={{selected: mode === value}}>
            <Text style={[styles.modeText, mode === value && styles.modeTextOn]}>{modeLabel(value)}</Text></Pressable>)}
        </View> : null}
        <Pressable onPress={() => void shutter()} disabled={busy && !recording || !!denied || (kind !== "sound" && !ready)}
          style={[styles.shutter, recording && styles.shutterRec]} accessibilityRole="button"
          accessibilityLabel={kind === "photo" ? tr("Take photo") : recording ? tr("Stop recording") : tr("Start recording")}>
          {kind === "photo" ? <View style={styles.shutterCore} /> : recording ? <View style={styles.stopCore} /> : <View style={styles.recCore} />}
        </Pressable>
        <View style={styles.kinds}>
          {KINDS.map(value => <Pressable key={value} onPress={() => {if (!recording) setKind(value);}} disabled={recording}
            accessibilityRole="button" accessibilityState={{selected: kind === value}} style={styles.kind}>
            <Text style={[styles.kindText, kind === value && styles.kindTextOn]}>{kindLabel(value)}</Text></Pressable>)}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {flex: 1, backgroundColor: "#000"},
  center: {alignItems: "center", justifyContent: "center", gap: 16},
  pad: {paddingHorizontal: 32},
  flash: {backgroundColor: "rgba(255,255,255,0.55)"},
  top: {position: "absolute", left: 0, right: 0, top: 0, flexDirection: "row", alignItems: "center", paddingHorizontal: 12, gap: 10},
  round: {width: 46, height: 46, borderRadius: 23, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(11,13,17,0.55)"},
  status: {flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 18, backgroundColor: "rgba(11,13,17,0.55)"},
  statusText: {color: C.ink, fontSize: 13, flexShrink: 1, textAlign: "center"},
  bottom: {position: "absolute", left: 0, right: 0, bottom: 0, alignItems: "center", gap: 14, paddingTop: 14, backgroundColor: "rgba(0,0,0,0.35)"},
  note: {color: C.soft, fontSize: 13, textAlign: "center", paddingHorizontal: 24},
  modes: {flexDirection: "row", flexWrap: "wrap", justifyContent: "center", gap: 8, paddingHorizontal: 12},
  mode: {paddingHorizontal: 14, paddingVertical: 7, borderRadius: 16, backgroundColor: "rgba(11,13,17,0.6)"},
  modeOn: {backgroundColor: C.acc},
  modeText: {color: C.ink, fontSize: 13, fontWeight: "600"},
  modeTextOn: {color: C.sendInk},
  shutter: {width: 78, height: 78, borderRadius: 39, borderWidth: 4, borderColor: C.ink, alignItems: "center", justifyContent: "center"},
  shutterRec: {borderColor: C.bad},
  shutterCore: {width: 62, height: 62, borderRadius: 31, backgroundColor: C.ink},
  recCore: {width: 58, height: 58, borderRadius: 29, backgroundColor: C.bad},
  stopCore: {width: 28, height: 28, borderRadius: 6, backgroundColor: C.bad},
  kinds: {flexDirection: "row", gap: 6},
  kind: {paddingHorizontal: 16, paddingVertical: 6},
  kindText: {color: C.mute, fontSize: 15, fontWeight: "600"},
  kindTextOn: {color: C.acc},
  timer: {color: C.ink, fontSize: 28, fontWeight: "600", fontVariant: ["tabular-nums"]},
  message: {color: C.ink, fontSize: 16, textAlign: "center", lineHeight: 22},
  textButton: {paddingHorizontal: 18, paddingVertical: 10, borderRadius: 18, backgroundColor: C.pill},
  textButtonText: {color: C.ink, fontSize: 15, fontWeight: "600"},
});
