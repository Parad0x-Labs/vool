import React from "react";
import {Pressable, StyleSheet, Text, View} from "react-native";

import Icon, {type IconName} from "./Icon.tsx";
import {C} from "./theme.ts";

/**
 * One group of settings, the way the computer's Settings page shows them: a card with a header
 * (a quiet amber line icon in a tile, and the group's name), then its rows. Each row inside starts
 * with a thin rule (`cardRow` below), so rows from any screen line up under the header.
 */
export default function SettingsCard({icon, title, right, open, onToggle, children}: {
  icon: IconName; title: string; right?: React.ReactNode;
  /** A folding card: the header opens and closes it. */
  open?: boolean; onToggle?: () => void;
  children?: React.ReactNode;
}) {
  const head = <>
    <View style={styles.tile}><Icon name={icon} size={18} color={C.acc} /></View>
    <Text style={styles.title} accessibilityRole="header">{title}</Text>
    {right}
    {onToggle ? <Icon name={open ? "chevron-up" : "chevron-down"} size={22} color={C.mute} /> : null}
  </>;
  return <View style={styles.card}>
    {onToggle ? <Pressable style={styles.head} onPress={onToggle} accessibilityRole="button" accessibilityState={{expanded: !!open}}
      android_ripple={{color: C.ripple}}>{head}</Pressable> : <View style={styles.head}>{head}</View>}
    {children}
  </View>;
}

/** A row inside a card: a thin rule above it, the usual padding. */
export const cardRow = {borderTopWidth: 1, borderTopColor: C.line, paddingHorizontal: 16, paddingVertical: 12} as const;

const styles = StyleSheet.create({
  card: {backgroundColor: C.raised, borderWidth: 1, borderColor: C.line, borderRadius: 20, overflow: "hidden", marginTop: 14},
  head: {flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingTop: 14, paddingBottom: 12, backgroundColor: "rgba(240,179,90,0.035)"},
  tile: {width: 32, height: 32, borderRadius: 10, alignItems: "center", justifyContent: "center", backgroundColor: C.tint,
    borderWidth: 1, borderColor: "rgba(240,179,90,0.25)"},
  title: {flex: 1, color: C.ink, fontSize: 15, fontWeight: "700"},
});
