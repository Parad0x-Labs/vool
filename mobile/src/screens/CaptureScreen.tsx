import React, {useEffect, useRef, useState} from "react";
import {ActivityIndicator, Animated, Linking, PermissionsAndroid, Pressable, ScrollView, StyleSheet, Text, View} from "react-native";
import {StatusBar} from "expo-status-bar";

import {errorText, tr} from "../i18n/index.ts";
import {capture, PHOTO_FILTERS, PrivateCameraView, type CameraHandle, type CameraMode, type PhotoFilter} from "../capture/native.ts";
import {loadCameraPrefs, megapixels, saveCameraPrefs, type CameraPrefs} from "../capture/cameraPrefs.ts";
import SettingsCard, {cardRow} from "../ui/SettingsCard.tsx";
import type {SendProblem} from "../capture/sender.ts";
import Face from "../ui/Face.tsx";
import Icon from "../ui/Icon.tsx";
import {RevealScrollView} from "../ui/Reveal.tsx";
import {useEdges} from "../ui/Frame.tsx";
import {C} from "../ui/theme.ts";
import {trace} from "../diagnostics/trace.ts";

type Kind = "photo" | "video" | "sound";
const KINDS: Kind[] = ["photo", "video", "sound"];
const kindLabel = (kind: Kind) => kind === "photo" ? tr("Photo") : kind === "video" ? tr("Video") : tr("Sound");
const modeLabel = (mode: CameraMode) => mode === "night" ? tr("Night") : mode === "hdr" ? "HDR" : mode === "portrait" ? tr("Portrait")
  : mode === "retouch" ? tr("Retouch") : tr("Auto");
const filterLabel = (filter: PhotoFilter) => filter === "warm" ? tr("Warm") : filter === "cool" ? tr("Cool") : filter === "vivid" ? tr("Vivid")
  : filter === "fade" ? tr("Fade") : filter === "mono" ? tr("Mono") : filter === "noir" ? tr("Noir") : tr("None");
// A colour hint for each filter chip (the viewfinder shows the real thing).
const FILTER_SWATCH: Record<PhotoFilter, string> = {none: "#3a3f4a", warm: "#d9a066", cool: "#6f9fd8", vivid: "#e0654f", fade: "#b9ab9c", mono: "#9a9a9a", noir: "#2b2b2b"};

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
  /** More settings shown at the end of the camera's settings (the optional smaller files). */
  extraSettings?: React.ReactNode;
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
 *
 * The camera's settings follow the GrapheneOS Camera app (MIT, GrapheneOS); see docs/THIRD_PARTY_LICENSES.md.
 */
/** Photos being taken at once in a burst (each holds a large image until it is written). */
const MAX_IN_FLIGHT = 3;

export default function CaptureScreen({waiting, sending, problem, saving, saveFailed, onCaptured, onClose, extraSettings}: Props) {
  const edges = useEdges();
  const camera = useRef<CameraHandle>(null);
  const [kind, setKind] = useState<Kind>("photo");
  const [facing, setFacing] = useState<"back" | "front">("back");
  const [mode, setMode] = useState<CameraMode>("standard");
  const [modes, setModes] = useState<CameraMode[]>([]);
  const [denied, setDenied] = useState<"" | "camera" | "microphone">("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  // Set at once, unlike state: a second press arriving in the same moment never takes a second photo.
  const pressing = useRef(false);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [note, setNote] = useState("");
  // The white flash runs on the native side, so it shows for every press even while the app is busy.
  const flash = useRef(new Animated.Value(0)).current;
  const lastTap = useRef(0);
  const inFlight = useRef(0);
  const [prefs, setPrefs] = useState<CameraPrefs>(loadCameraPrefs);
  const [sheet, setSheet] = useState(false);
  const [filters, setFilters] = useState(false);
  const [zoom, setZoom] = useState<{ratio: number; min: number; max: number} | null>(null);
  const [focus, setFocus] = useState<{x: number; y: number} | null>(null);
  // The screen stays upright; the round buttons' symbols turn with the phone, like the phone's own camera.
  const [turn, setTurn] = useState(0);
  const spin = {transform: [{rotate: `${turn}deg`}]};
  const [countdown, setCountdown] = useState(0);
  const [area, setArea] = useState({width: 0, height: 0});
  const [sizes, setSizes] = useState<{sizes: string[]; sensorMax: string}>({sizes: [], sensorMax: ""});
  const change = (patch: Partial<CameraPrefs>) => {
    trace("camera setting", {keys: Object.keys(patch).join("+")});
    setPrefs(current => {const next = {...current, ...patch}; saveCameraPrefs(next); return next;});
  };
  useEffect(() => {
    let current = true;
    void capture?.photoSizes(facing).then(found => {if (current) setSizes(found);}).catch(() => {});
    return () => {current = false;};
  }, [facing]);
  useEffect(() => {
    if (!focus) return;
    const timer = setTimeout(() => setFocus(null), 900);
    return () => clearTimeout(timer);
  }, [focus]);

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

  // The self-timer counts down before a photo, or before a recording starts.
  async function wait(seconds: number) {
    for (let left = seconds; left > 0; left--) {
      setCountdown(left);
      await new Promise(done => setTimeout(done, 1000));
    }
    setCountdown(0);
  }

  /** A photo for every press: presses in a burst are not held back, the camera takes them in order. */
  function snap() {
    const now = Date.now();
    if (now - lastTap.current < 80) {trace("screen: a second press in the same moment was ignored"); return;}
    lastTap.current = now;
    if (inFlight.current >= MAX_IN_FLIGHT) {trace(`screen: press not taken, ${MAX_IN_FLIGHT} photos still being taken`); return;}
    inFlight.current++;
    setNote("");
    flash.setValue(1);
    Animated.timing(flash, {toValue: 0, duration: 160, useNativeDriver: true}).start();
    camera.current?.takePhoto()
      .then(() => {trace(`screen: photo taken ${Date.now() - now} ms after its press, ${inFlight.current - 1} more on the way`); stored();})
      .catch(() => failed())
      .finally(() => {inFlight.current--;});
  }

  async function shutter() {
    trace("shutter press", {kind, recording, busy, pressing: pressing.current, inFlight: inFlight.current, timer: prefs.timer || 0});
    if (kind === "photo" && !prefs.timer) {snap(); return;}
    if (pressing.current) {trace("screen: a second press in the same moment was ignored"); return;}
    if (busy || !capture) return;
    pressing.current = true;
    try {await shutterOnce();} finally {pressing.current = false;}
  }

  async function shutterOnce() {
    if (!capture) return;
    if (prefs.timer && !recording && kind !== "sound") {
      setBusy(true);
      await wait(prefs.timer);
      setBusy(false);
    }
    setBusy(true);
    setNote("");
    try {
      if (kind === "photo") {
        flash.setValue(1);
        Animated.timing(flash, {toValue: 0, duration: 160, useNativeDriver: true}).start();
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
  const flashWord = prefs.flash === "on" ? tr("On") : prefs.flash === "auto" ? tr("Auto") : tr("Off");
  // Where the photo sits on screen (the viewfinder fits the photo, not the screen).
  const chosen = prefs.photoSize.split("x").map(Number);
  const tall = kind === "video" ? 16 / 9 : chosen.length === 2 && chosen[0] && chosen[1] ? Math.max(chosen[0], chosen[1]) / Math.min(chosen[0], chosen[1])
    : prefs.aspect === "16:9" ? 16 / 9 : 4 / 3;
  const frameHeight = Math.min(area.height, area.width * tall);
  const frame = {left: 0, top: (area.height - frameHeight) / 2, width: area.width, height: frameHeight};
  const biggest = sizes.sizes[0] ? sizes.sizes[0].split("x").map(Number) : [];
  const sensor = sizes.sensorMax.split("x").map(Number);
  const sensorNote = biggest.length === 2 && sensor.length === 2 && sensor[0] * sensor[1] > biggest[0] * biggest[1] * 1.3
    ? tr("Your phone keeps {mp} for its own camera app.", {mp: megapixels(sizes.sensorMax)}) : "";
  return (
    <View style={styles.screen} onLayout={event => setArea(event.nativeEvent.layout)}>
      <StatusBar style="light" />
      {showCamera ? <PrivateCameraView ref={camera} style={StyleSheet.absoluteFill} facing={facing} mode={mode} video={kind === "video"}
        flash={prefs.flash} aspect={prefs.aspect} photoSize={facing === "back" ? prefs.photoSize : "max"} videoQuality={prefs.videoQuality}
        steady={prefs.steady} mirror={prefs.mirror} filter={kind === "photo" ? prefs.filter : "none"}
        onZoom={event => setZoom(event.nativeEvent)} onFocus={event => setFocus(event.nativeEvent)}
        onTurn={event => setTurn(event.nativeEvent.degrees)}
        onModes={event => {const list = event.nativeEvent.modes; setModes(list); if (mode !== "standard" && !list.includes(mode)) setMode("standard");}}
        onCaptured={event => (event.nativeEvent as {ok?: boolean}).ok === false ? failed() : stored()}
        onCameraError={() => setNote(tr("The camera could not start. Close it and try again."))} /> : null}
      {showCamera && prefs.grid ? <Grid lines={prefs.grid} rect={frame} /> : null}
      {focus ? <View pointerEvents="none" style={[styles.focusRing, {left: focus.x - 34, top: focus.y - 34}]} /> : null}
      {countdown ? <View pointerEvents="none" style={[StyleSheet.absoluteFill, styles.center]}><Text style={styles.countdown}>{countdown}</Text></View> : null}
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, styles.flash, {opacity: flash}]} />
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
          <View style={spin}><Icon name="close" size={24} color={recording ? C.faint : C.ink} /></View>
        </Pressable>
        <View style={styles.status}>
          {saving || (sending && waiting) ? <ActivityIndicator size="small" color={C.acc} /> :
            <Icon name={saveFailed ? "alert-circle-outline" : waiting ? "lock-outline" : "check"} size={15} color={saveFailed ? C.bad : waiting ? C.acc : C.ok} />}
          <Text style={styles.statusText} numberOfLines={2}>{status}</Text>
        </View>
        {kind !== "sound" ? <Pressable style={styles.round} hitSlop={8} accessibilityRole="button" accessibilityLabel={`${tr("Flash")}: ${flashWord}`}
          onPress={() => change({flash: kind === "video" ? (prefs.flash === "on" ? "off" : "on") : prefs.flash === "off" ? "auto" : prefs.flash === "auto" ? "on" : "off"})}>
          <View style={spin}><Icon name={kind === "video" ? (prefs.flash === "on" ? "flashlight" : "flashlight-off") : prefs.flash === "on" ? "flash" : prefs.flash === "auto" ? "flash-auto" : "flash-off"}
            size={22} color={prefs.flash === "on" || (kind === "photo" && prefs.flash === "auto") ? C.acc : C.ink} /></View>
        </Pressable> : null}
        <Pressable style={styles.round} onPress={() => setSheet(true)} hitSlop={8} accessibilityRole="button" accessibilityLabel={tr("Camera settings")} disabled={recording}>
          <View style={spin}><Icon name="tune-variant" size={22} color={recording ? C.faint : C.ink} /></View>
        </Pressable>
      </View>

      <View style={[styles.bottom, {paddingBottom: edges.bottom + 18}]}>
        {note ? <Text style={styles.note}>{note}</Text> : null}
        {kind === "photo" && modes.length ? <View style={styles.modes}>
          {(["standard", ...modes] as CameraMode[]).map(value => <Pressable key={value} onPress={() => setMode(value)}
            style={[styles.mode, mode === value && styles.modeOn]} accessibilityRole="button" accessibilityState={{selected: mode === value}}>
            <Text style={[styles.modeText, mode === value && styles.modeTextOn]}>{modeLabel(value)}</Text></Pressable>)}
        </View> : null}
        {kind === "photo" && filters ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
          {PHOTO_FILTERS.map(value => <Pressable key={value} onPress={() => change({filter: value})} style={styles.filter}
            accessibilityRole="button" accessibilityState={{selected: prefs.filter === value}}>
            <View style={[styles.swatch, {backgroundColor: FILTER_SWATCH[value]}, prefs.filter === value && styles.swatchOn]} />
            <Text style={[styles.filterText, prefs.filter === value && styles.modeTextOnDark]}>{filterLabel(value)}</Text>
          </Pressable>)}
        </ScrollView> : null}
        {showCamera && zoom && zoom.max > 1.01 ? <View style={styles.zooms}>
          {zoomStops(zoom).map(stop => {
            const on = Math.abs(zoom.ratio - stop) < 0.05 || (stop === nearestStop(zoom) && !zoomStops(zoom).some(s => Math.abs(zoom.ratio - s) < 0.05));
            return <Pressable key={stop} onPress={() => void camera.current?.setZoom(stop)} style={[styles.zoom, on && styles.zoomOn]} accessibilityRole="button">
              <Text style={[styles.zoomText, on && styles.modeTextOnDark]}>{on && Math.abs(zoom.ratio - stop) >= 0.05 ? zoom.ratio.toFixed(1) : stop < 1 ? stop.toFixed(1) : stop.toFixed(0)}×</Text>
            </Pressable>;
          })}
        </View> : null}
        <View style={styles.shutterRow}>
          {kind === "photo" ? <Pressable style={[styles.round, filters && styles.roundOn]} onPress={() => setFilters(open => !open)} hitSlop={8}
            accessibilityRole="button" accessibilityLabel={tr("Filter")} accessibilityState={{expanded: filters}}>
            <View style={spin}><Icon name="palette-outline" size={22} color={prefs.filter !== "none" ? C.acc : C.ink} /></View>
          </Pressable> : <View style={styles.round} />}
          <Pressable onPress={() => void shutter()} disabled={busy && !recording || !!denied || (kind !== "sound" && !ready)}
            onTouchStart={() => trace("shutter touch", {kind, recording, busy, ready, denied: denied || ""})}
            style={({pressed}) => [styles.shutter, recording && styles.shutterRec, pressed && styles.shutterDown]} accessibilityRole="button"
            accessibilityLabel={kind === "photo" ? tr("Take photo") : recording ? tr("Stop recording") : tr("Start recording")}>
            {kind === "photo" ? <View style={styles.shutterCore} /> : recording ? <View style={styles.stopCore} /> : <View style={styles.recCore} />}
          </Pressable>
          {kind !== "sound" ? <Pressable style={styles.round} onPress={() => setFacing(f => f === "back" ? "front" : "back")} hitSlop={8}
            accessibilityRole="button" accessibilityLabel={tr("Switch camera")} disabled={recording}>
            <View style={spin}><Icon name="camera-flip-outline" size={24} color={recording ? C.faint : C.ink} /></View>
          </Pressable> : <View style={styles.round} />}
        </View>
        <View style={styles.kinds}>
          {KINDS.map(value => <Pressable key={value} onPress={() => {if (!recording) setKind(value);}} disabled={recording}
            accessibilityRole="button" accessibilityState={{selected: kind === value}} style={styles.kind}>
            <Text style={[styles.kindText, kind === value && styles.kindTextOn]}>{kindLabel(value)}</Text></Pressable>)}
        </View>
      </View>

      {sheet ? <View style={[StyleSheet.absoluteFill, styles.sheetBack]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={() => setSheet(false)} accessibilityRole="button" accessibilityLabel={tr("Close")} />
        <View style={[styles.sheet, {paddingBottom: edges.bottom + 12, maxHeight: Math.max(320, area.height * 0.82)}]}>
          <View style={styles.sheetHead}>
            <Text style={styles.sheetTitle}>{tr("Camera settings")}</Text>
            <Pressable style={styles.round} onPress={() => setSheet(false)} accessibilityRole="button" accessibilityLabel={tr("Close")}>
              <Icon name="close" size={22} color={C.ink} /></Pressable>
          </View>
          <RevealScrollView contentContainerStyle={styles.sheetBody}>
            <SettingsCard icon="camera-outline" title={tr("Photo")}>
              <Choice label={tr("Flash")} value={prefs.flash} onPick={flash => change({flash})} items={[["off", tr("Off")], ["auto", tr("Auto")], ["on", tr("On")]]} />
              <Choice label={tr("Timer")} value={prefs.timer} onPick={timer => change({timer})} items={[[0, tr("Off")], [3, "3 s"], [10, "10 s"]]} />
              <Choice label={tr("Grid")} value={prefs.grid} onPick={grid => change({grid})} items={[[0, tr("Off")], [3, "3×3"], [4, "4×4"]]} />
              <Choice label={tr("Photo shape")} value={prefs.aspect} onPick={aspect => change({aspect, photoSize: "max"})} items={[["4:3", "4:3"], ["16:9", "16:9"]]} />
              {sizes.sizes.length ? <Choice label={tr("Photo size")} value={prefs.photoSize} onPick={photoSize => change({photoSize})}
                items={[["max", tr("Largest")] as [string, string], ...sizes.sizes.slice(0, 5).map(size => [size, `${megapixels(size)} · ${size.replace("x", "×")}`] as [string, string])]} /> : null}
              {sensorNote ? <View style={cardRow}><Text style={styles.sheetNote}>{sensorNote}</Text></View> : null}
              <Choice label={tr("Mirror selfies")} value={prefs.mirror} onPick={mirror => change({mirror})} items={[[false, tr("Off")], [true, tr("On")]]} />
            </SettingsCard>
            <SettingsCard icon="video-outline" title={tr("Video")}>
              <Choice label={tr("Video quality")} value={prefs.videoQuality} onPick={videoQuality => change({videoQuality})}
                items={[["highest", tr("Best")], ["2160", "4K"], ["1080", "1080p"], ["720", "720p"]]} />
              <Choice label={tr("Steady video")} value={prefs.steady} onPick={steady => change({steady})} items={[[true, tr("On")], [false, tr("Off")]]} />
            </SettingsCard>
            {extraSettings}
          </RevealScrollView>
        </View>
      </View> : null}
    </View>
  );
}

/** One setting in the camera's panel: its name, then its choices as pills. */
function Choice<T extends string | number | boolean>({label, value, items, onPick}: {label: string; value: T; items: [T, string][]; onPick: (value: T) => void}) {
  return <View style={cardRow}>
    <Text style={styles.choiceLabel}>{label}</Text>
    <View style={styles.pills}>
      {items.map(([key, text]) => <Pressable key={String(key)} onPress={() => onPick(key)} style={[styles.pill, value === key && styles.pillOn]}
        accessibilityRole="radio" accessibilityState={{selected: value === key}}>
        <Text style={[styles.pillText, value === key && styles.modeTextOnDark]}>{text}</Text>
      </Pressable>)}
    </View>
  </View>;
}

/** Grid lines over the part of the screen the photo covers. */
function Grid({lines, rect}: {lines: number; rect: {left: number; top: number; width: number; height: number}}) {
  const steps = Array.from({length: lines - 1}, (_, i) => (i + 1) / lines);
  return <View pointerEvents="none" style={[styles.grid, rect]}>
    {steps.map(step => <View key={`v${step}`} style={[styles.gridV, {left: `${step * 100}%`}]} />)}
    {steps.map(step => <View key={`h${step}`} style={[styles.gridH, {top: `${step * 100}%`}]} />)}
  </View>;
}

/** The zoom buttons: the wide lens when the phone has one, 1×, and 2× (or the most it has). */
function zoomStops(zoom: {min: number; max: number}): number[] {
  const stops = [];
  if (zoom.min < 0.95) stops.push(Math.round(zoom.min * 10) / 10);
  stops.push(1);
  if (zoom.max >= 2) stops.push(2);
  if (zoom.max >= 5) stops.push(5);
  return stops;
}

function nearestStop(zoom: {ratio: number; min: number; max: number}): number {
  return zoomStops(zoom).reduce((best, stop) => stop <= zoom.ratio + 0.05 ? stop : best, zoomStops(zoom)[0]);
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
  shutterDown: {transform: [{scale: 0.9}]},
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
  roundOn: {backgroundColor: "rgba(240,179,90,0.22)"},
  shutterRow: {flexDirection: "row", alignItems: "center", justifyContent: "space-between", alignSelf: "stretch", paddingHorizontal: 34},
  filters: {gap: 12, paddingHorizontal: 16},
  filter: {alignItems: "center", gap: 6},
  swatch: {width: 44, height: 44, borderRadius: 22, borderWidth: 2, borderColor: "rgba(241,238,232,0.25)"},
  swatchOn: {borderColor: C.acc, borderWidth: 3},
  filterText: {color: C.soft, fontSize: 12, fontWeight: "600"},
  modeTextOnDark: {color: C.acc},
  zooms: {flexDirection: "row", gap: 8, backgroundColor: "rgba(11,13,17,0.55)", borderRadius: 20, padding: 4},
  zoom: {minWidth: 40, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center", paddingHorizontal: 8},
  zoomOn: {backgroundColor: "rgba(240,179,90,0.18)"},
  zoomText: {color: C.ink, fontSize: 13, fontWeight: "700"},
  focusRing: {position: "absolute", width: 68, height: 68, borderRadius: 34, borderWidth: 2, borderColor: C.acc},
  countdown: {color: C.ink, fontSize: 96, fontWeight: "700", textShadowColor: "rgba(0,0,0,0.6)", textShadowRadius: 12},
  grid: {position: "absolute"},
  gridV: {position: "absolute", top: 0, bottom: 0, width: StyleSheet.hairlineWidth, backgroundColor: "rgba(241,238,232,0.55)"},
  gridH: {position: "absolute", left: 0, right: 0, height: StyleSheet.hairlineWidth, backgroundColor: "rgba(241,238,232,0.55)"},
  sheetBack: {backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end"},
  sheet: {backgroundColor: C.bg, borderTopLeftRadius: 24, borderTopRightRadius: 24, borderWidth: 1, borderColor: C.line},
  sheetHead: {flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingTop: 14},
  sheetTitle: {color: C.ink, fontSize: 18, fontWeight: "700"},
  sheetBody: {paddingHorizontal: 16, paddingBottom: 12},
  sheetNote: {color: C.mute, fontSize: 12, lineHeight: 17},
  choiceLabel: {color: C.ink, fontSize: 15, marginBottom: 8},
  pills: {flexDirection: "row", flexWrap: "wrap", gap: 8},
  pill: {paddingVertical: 7, paddingHorizontal: 13, borderRadius: 16, borderWidth: 1, borderColor: C.line, backgroundColor: C.pill},
  pillOn: {borderColor: C.acc, backgroundColor: C.tint},
  pillText: {color: C.ink, fontSize: 13, fontWeight: "600"},
  message: {color: C.ink, fontSize: 16, textAlign: "center", lineHeight: 22},
  textButton: {paddingHorizontal: 18, paddingVertical: 10, borderRadius: 18, backgroundColor: C.pill},
  textButtonText: {color: C.ink, fontSize: 15, fontWeight: "600"},
});
