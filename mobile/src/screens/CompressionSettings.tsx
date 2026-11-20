import React, {useCallback, useEffect, useState} from "react";
import {Pressable, StyleSheet, Text, TextInput, View} from "react-native";

import {tr} from "../i18n/index.ts";
import {capture} from "../capture/native.ts";
import Icon from "../ui/Icon.tsx";
import {C} from "../ui/theme.ts";

/**
 * Rows for the private camera list: optional compression before sending.
 * Off by default (originals go to the computer). On = "Best": best quality in the best format
 * this phone can make. "Custom" opens every setting for people who want them.
 */

type Preset = "best" | "balanced" | "small" | "custom";
interface Tuning {
  photoFormat: "auto" | "heic" | "jpeg";
  photoQuality: number;
  photoMaxEdge: number;
  videoCodec: "auto" | "hevc" | "avc" | "av1";
  videoBitsPerPixel: number;
  videoSourceCap: number;
  videoMbps: number;
  videoMaxHeight: number;
  audioCodec: "auto" | "opus" | "aac";
  audioKbps: number;
}
interface Caps {heic: boolean; hevc: boolean; av1: boolean; opus: boolean}
interface Summary {items: number; compressed: number; keptOriginal: number; failed: number; savedBytes: number}
interface State {
  enabled: boolean;
  preset: Preset;
  custom: Tuning;
  stripLocation: boolean;
  stripCameraInfo: boolean;
  minSavingPercent: number;
  capabilities: Caps;
  summary: Summary;
}

const PRESETS: {key: Preset; label: string; hint: string}[] = [
  {key: "best", label: "Best", hint: "Highest quality, in the best format this phone can make."},
  {key: "balanced", label: "Balanced", hint: "Smaller files, very close to the original."},
  {key: "small", label: "Smallest", hint: "Much smaller. Photos up to 2560 px, video up to 1080p."},
  {key: "custom", label: "Custom", hint: "Choose every setting yourself."},
];

function read(): State | null {
  try {
    const raw = capture?.getCompressionSettings() ?? "";
    return raw ? JSON.parse(raw) as State : null;
  } catch {
    return null;
  }
}

function size(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(0, bytes / 1e6).toFixed(1)} MB`;
}

export default function CompressionSettings() {
  const [state, setState] = useState<State | null>(read);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {setState(read());}, [open]);

  const save = useCallback((next: Partial<State>) => {
    if (!capture || !state) return;
    const merged = {...state, ...next};
    try {
      const raw = capture.setCompressionSettings(JSON.stringify({
        enabled: merged.enabled, preset: merged.preset, custom: merged.custom,
        stripLocation: merged.stripLocation, stripCameraInfo: merged.stripCameraInfo,
        minSavingPercent: merged.minSavingPercent,
      }));
      setState(raw ? JSON.parse(raw) as State : read());
      setError("");
    } catch {
      setError(tr("The setting could not be saved."));
      setState(read());
    }
  }, [state]);

  if (!capture || !state) return null;
  const preset = PRESETS.find(p => p.key === state.preset) ?? PRESETS[0];
  const caps = state.capabilities;
  const t = state.custom;
  const setT = (patch: Partial<Tuning>) => save({custom: {...t, ...patch}});

  return <>
    <Pressable style={[styles.row, styles.rule]} onPress={() => setOpen(!open)} accessibilityRole="button"
      accessibilityState={{expanded: open}} android_ripple={{color: C.ripple}}>
      <View style={styles.fill}>
        <Text style={styles.text}>{tr("Make files smaller before sending")}</Text>
        {state.summary.items > 0 ? <Text style={styles.note}>{tr("Saved {size} so far", {size: size(state.summary.savedBytes)})}</Text> : null}
      </View>
      <Text style={[styles.value, state.enabled && {color: C.ok}]}>{state.enabled ? tr(preset.label) : tr("Off")}</Text>
      <Icon name={open ? "chevron-up" : "chevron-down"} size={22} color={C.faint} />
    </Pressable>

    {open ? <View style={[styles.panel, styles.rule]}>
      <Toggle label={tr("Compress")} on={state.enabled} onPress={() => save({enabled: !state.enabled})} />
      <Text style={styles.note}>{tr("Off sends the originals. When on, anything that would not get much smaller, or cannot be compressed, is sent as the original.")}</Text>

      {state.enabled ? <>
        <Text style={styles.label}>{tr("Quality")}</Text>
        {PRESETS.map(p => <Pressable key={p.key} style={[styles.choice, state.preset === p.key && styles.chosen]}
          onPress={() => save({preset: p.key})} accessibilityRole="radio" accessibilityState={{selected: state.preset === p.key}}>
          <Text style={styles.text}>{tr(p.label)}</Text>
          <Text style={styles.note}>{tr(p.hint)}</Text>
        </Pressable>)}

        <Text style={styles.label}>{tr("Privacy")}</Text>
        <Toggle label={tr("Remove location")} on={state.stripLocation} onPress={() => save({stripLocation: !state.stripLocation})} />
        <Toggle label={tr("Remove camera model")} on={state.stripCameraInfo} onPress={() => save({stripCameraInfo: !state.stripCameraInfo})} />

        {state.preset === "custom" ? <>
          <Text style={styles.label}>{tr("Photos")}</Text>
          <Options value={t.photoFormat} onPick={v => setT({photoFormat: v as Tuning["photoFormat"]})}
            items={[["auto", tr("Auto")], ["heic", caps.heic ? "HEIC" : tr("HEIC (not on this phone)")], ["jpeg", "JPEG"]]} />
          <Num label={tr("Quality, 1 to 100")} value={t.photoQuality} onSet={v => setT({photoQuality: v})} />
          <Num label={tr("Longest side in pixels, 0 keeps it")} value={t.photoMaxEdge} onSet={v => setT({photoMaxEdge: v})} />

          <Text style={styles.label}>{tr("Video")}</Text>
          <Options value={t.videoCodec} onPick={v => setT({videoCodec: v as Tuning["videoCodec"]})}
            items={[["auto", tr("Auto")], ["hevc", caps.hevc ? "HEVC" : tr("HEVC (not on this phone)")],
              ["av1", caps.av1 ? "AV1" : tr("AV1 (not on this phone)")], ["avc", "H.264"]]} />
          <Options value={String(t.videoMaxHeight)} onPick={v => setT({videoMaxHeight: Number(v)})}
            items={[["0", tr("Keep size")], ["2160", "4K"], ["1440", "1440p"], ["1080", "1080p"], ["720", "720p"]]} />
          <Num label={tr("Fixed bitrate in Mbit/s, 0 is automatic")} value={t.videoMbps} decimals onSet={v => setT({videoMbps: v})} />
          <Num label={tr("Automatic: bits per pixel")} value={t.videoBitsPerPixel} decimals onSet={v => setT({videoBitsPerPixel: v})} />
          <Num label={tr("At most this share of the original bitrate")} value={t.videoSourceCap} decimals onSet={v => setT({videoSourceCap: v})} />

          <Text style={styles.label}>{tr("Sound")}</Text>
          <Options value={t.audioCodec} onPick={v => setT({audioCodec: v as Tuning["audioCodec"]})}
            items={[["auto", tr("Auto")], ["opus", caps.opus ? "Opus" : tr("Opus (not on this phone)")], ["aac", "AAC"]]} />
          <Num label={tr("Bitrate in kbit/s")} value={t.audioKbps} onSet={v => setT({audioKbps: v})} />

          <Text style={styles.label}>{tr("Keep the original")}</Text>
          <Num label={tr("When it saves less than this %")} value={state.minSavingPercent} onSet={v => save({minSavingPercent: v})} />
        </> : null}
      </> : null}

      {state.summary.items > 0 ? <Text style={styles.note}>{tr("{done} made smaller, {kept} sent as the original, {failed} could not be compressed.", {
        done: state.summary.compressed, kept: state.summary.keptOriginal, failed: state.summary.failed})}</Text> : null}
      {error ? <Text style={styles.bad}>{error}</Text> : null}
    </View> : null}
  </>;
}

function Toggle({label, on, onPress}: {label: string; on: boolean; onPress: () => void}) {
  return <Pressable style={styles.line} onPress={onPress} accessibilityRole="switch" accessibilityState={{checked: on}}>
    <Text style={[styles.text, styles.fill]}>{label}</Text>
    <Text style={[styles.value, on && {color: C.ok}]}>{on ? tr("On") : tr("Off")}</Text>
  </Pressable>;
}

function Options({value, items, onPick}: {value: string; items: [string, string][]; onPick: (v: string) => void}) {
  return <View style={styles.options}>
    {items.map(([k, label]) => <Pressable key={k} onPress={() => onPick(k)} style={[styles.pill, value === k && styles.chosen]}
      accessibilityRole="radio" accessibilityState={{selected: value === k}}>
      <Text style={styles.small}>{label}</Text>
    </Pressable>)}
  </View>;
}

function Num({label, value, onSet, decimals}: {label: string; value: number; onSet: (v: number) => void; decimals?: boolean}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return <View style={styles.line}>
    <Text style={[styles.text, styles.fill]}>{label}</Text>
    <TextInput style={styles.input} value={text} onChangeText={setText} keyboardType={decimals ? "decimal-pad" : "number-pad"}
      onEndEditing={() => {
        const n = decimals ? parseFloat(text) : parseInt(text, 10);
        if (Number.isFinite(n)) onSet(n); else setText(String(value));
      }} />
  </View>;
}

const styles = StyleSheet.create({
  row: {flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, minHeight: 52, paddingVertical: 12},
  rule: {borderTopWidth: 1, borderTopColor: C.line},
  panel: {paddingHorizontal: 16, paddingVertical: 12, gap: 10},
  line: {flexDirection: "row", alignItems: "center", gap: 12, minHeight: 40},
  fill: {flex: 1},
  text: {color: C.ink, fontSize: 15},
  small: {color: C.ink, fontSize: 13},
  note: {color: C.mute, fontSize: 12, marginTop: 2},
  bad: {color: C.bad, fontSize: 12},
  value: {color: C.mute, fontSize: 14, fontWeight: "600"},
  label: {color: C.mute, fontSize: 12, fontWeight: "600", textTransform: "uppercase", letterSpacing: 1, marginTop: 8},
  choice: {padding: 12, borderRadius: 14, borderWidth: 1, borderColor: C.line},
  chosen: {borderColor: C.acc, backgroundColor: C.tint},
  options: {flexDirection: "row", flexWrap: "wrap", gap: 8},
  pill: {paddingVertical: 6, paddingHorizontal: 12, borderRadius: 16, borderWidth: 1, borderColor: C.line, backgroundColor: C.pill},
  input: {minWidth: 72, paddingVertical: 4, paddingHorizontal: 8, borderRadius: 8, borderWidth: 1, borderColor: C.line,
    color: C.ink, textAlign: "right"},
});
