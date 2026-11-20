import React from "react";
import {Pressable, StyleSheet, Text, View} from "react-native";

import {tr} from "../i18n/index.ts";
import {trace} from "../diagnostics/trace.ts";
import {RECORDING_MINUTES, setRecordingAllowed, useRecordingAllowed} from "../security/screenRecording.ts";
import SettingsCard from "../ui/SettingsCard.tsx";
import {C} from "../ui/theme.ts";

/** For a demo: lets the Vault, Wallet and Approvals be recorded. Off by default; ends by itself. */
export default function ScreenRecordingSettings() {
  const on = useRecordingAllowed();
  const toggle = () => {trace("screen recording", {on: !on}); setRecordingAllowed(!on);};
  return <SettingsCard icon="record-rec" title={tr("Screen recording")}>
    <Pressable style={[styles.row, styles.rule]} onPress={toggle} accessibilityRole="switch" accessibilityState={{checked: on}}
      android_ripple={{color: C.ripple}}>
      <View style={styles.fill}><Text style={styles.text}>{tr("Allow screen recording")}</Text></View>
      <Text style={[styles.value, on && {color: C.wait}]}>{on ? tr("On") : tr("Off")}</Text>
    </Pressable>
    <View style={[styles.row, styles.rule]}>
      <Text style={styles.note}>{on
        ? tr("The Vault, Wallet and Approvals can now be recorded and seen in the app switcher. This stops by itself after {minutes} minutes or when VOOL closes.", {minutes: RECORDING_MINUTES})
        : tr("The Vault, Wallet and Approvals stay out of screen recordings, screenshots and the app switcher. Switch this on for a demo.")}</Text>
    </View>
  </SettingsCard>;
}

const styles = StyleSheet.create({
  row: {flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, minHeight: 52, paddingVertical: 12},
  rule: {borderTopWidth: 1, borderTopColor: C.line},
  fill: {flex: 1},
  text: {color: C.ink, fontSize: 15},
  note: {color: C.mute, fontSize: 12, marginTop: 2, flex: 1},
  value: {color: C.mute, fontSize: 14, fontWeight: "600"},
});
