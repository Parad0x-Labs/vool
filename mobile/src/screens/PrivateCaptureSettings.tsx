import React, {useEffect, useState} from "react";
import {Pressable, StyleSheet, Text, View} from "react-native";

import {tr} from "../i18n/index.ts";
import {capture} from "../capture/native.ts";
import Icon from "../ui/Icon.tsx";
import SettingsCard from "../ui/SettingsCard.tsx";
import {C} from "../ui/theme.ts";

interface Props {
  waiting: number;
  saving: number;
  onOpen: () => void;
  /** Further rows for the private camera (for example the optional compression). */
  children?: React.ReactNode;
}

/** Settings for VOOL's private camera: open it, put it on the home screen or in quick settings. */
export default function PrivateCaptureSettings({waiting, saving, onOpen, children}: Props) {
  const [homeIcon, setHomeIcon] = useState(() => capture?.homeIconOn() ?? false);
  const [tileNote, setTileNote] = useState("");
  useEffect(() => {setHomeIcon(capture?.homeIconOn() ?? false);}, []);
  if (!capture) return null;

  const toggleHome = () => {
    const next = !homeIcon;
    void capture?.setHomeIcon(next).then(() => setHomeIcon(capture?.homeIconOn() ?? next)).catch(() => {});
  };
  const addTile = () => {
    void capture?.addTile().then(result => setTileNote(
      result === "added" || result === "already" ? tr("It is in your quick settings.") : result === "unsupported" ? tr("This phone cannot add it from here.") : "",
    )).catch(() => {});
  };
  const state = saving ? tr("Locking it away…") : waiting ? tr("Waiting for your computer: {count}", {count: waiting}) : tr("Everything is on your computer.");

  return <>
    <SettingsCard icon="camera-iris" title={tr("Private camera")}>
      <Pressable style={[styles.row, styles.rule]} onPress={onOpen} accessibilityRole="button" android_ripple={{color: C.ripple}}>
        <View style={styles.fill}>
          <Text style={styles.text}>{tr("Open private camera")}</Text>
          <Text style={styles.note}>{state}</Text>
        </View>
        <Icon name="chevron-right" size={22} color={C.faint} />
      </Pressable>
      <Pressable style={[styles.row, styles.rule]} onPress={toggleHome} accessibilityRole="switch" accessibilityState={{checked: homeIcon}}
        android_ripple={{color: C.ripple}}>
        <View style={styles.fill}><Text style={styles.text}>{tr("VOOL Camera on the home screen")}</Text></View>
        <Text style={[styles.value, homeIcon && {color: C.ok}]}>{homeIcon ? tr("On") : tr("Off")}</Text>
      </Pressable>
      {capture.canAddTile() ? <Pressable style={[styles.row, styles.rule]} onPress={addTile} accessibilityRole="button" android_ripple={{color: C.ripple}}>
        <View style={styles.fill}>
          <Text style={styles.text}>{tr("Add to quick settings")}</Text>
          {tileNote ? <Text style={styles.note}>{tileNote}</Text> : null}
        </View>
      </Pressable> : null}
      <View style={[styles.row, styles.rule]}>
        <Text style={styles.note}>{tr("What you take here never reaches the gallery or any cloud. It stays locked on this phone, goes to your computer, and is removed here once your computer has the very same file.")}</Text>
      </View>
    </SettingsCard>
    {children ? <SettingsCard icon="archive-arrow-down-outline" title={tr("Smaller files")}>{children}</SettingsCard> : null}
  </>;
}

const styles = StyleSheet.create({
  row: {flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, minHeight: 52, paddingVertical: 12},
  rule: {borderTopWidth: 1, borderTopColor: C.line},
  fill: {flex: 1},
  text: {color: C.ink, fontSize: 15},
  note: {color: C.mute, fontSize: 12, marginTop: 2},
  value: {color: C.mute, fontSize: 14, fontWeight: "600"},
});
