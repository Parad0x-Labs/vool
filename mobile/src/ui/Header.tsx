import React from "react";
import {Pressable, StyleSheet, Text, View} from "react-native";
import Icon from "./Icon.tsx";
import {LinkLine} from "./LinkBadge.tsx";
import {C} from "./theme.ts";
import {rightToLeft, tr} from "../i18n/index.ts";

interface Props {
  title: string;
  /** Inside a chat: an arrow back to the list. Without it, the large title of a tab. */
  onBack?: () => void;
  /** Tapping the connection line opens Settings. */
  onLink?: () => void;
}

/** The top of every screen: its name, and how the computer stands right now. */
export default function Header({title, onBack, onLink}: Props) {
  if (!onBack) return (
    <View style={styles.tab}>
      <Text style={styles.big} numberOfLines={1} accessibilityRole="header">{title}</Text>
      <Pressable onPress={onLink} hitSlop={8} accessibilityRole="button"><LinkLine /></Pressable>
    </View>
  );
  return (
    <View style={styles.bar}>
      <Pressable onPress={onBack} hitSlop={10} style={styles.lead} accessibilityRole="button" accessibilityLabel={tr("Back")}>
        <Icon name={rightToLeft() ? "chevron-right" : "chevron-left"} size={30} color={C.ink} />
      </Pressable>
      <Pressable style={styles.fill} onPress={onLink} accessibilityRole="button">
        <Text style={styles.title} numberOfLines={1}>{title}</Text>
        <LinkLine small />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: {flex: 1},
  tab: {paddingHorizontal: 20, paddingTop: 12, paddingBottom: 12, gap: 5},
  big: {color: C.ink, fontSize: 32, fontWeight: "700", letterSpacing: -0.6},
  bar: {flexDirection: "row", alignItems: "center", paddingHorizontal: 6, minHeight: 58, gap: 2, borderBottomWidth: 1, borderBottomColor: C.line},
  lead: {width: 46, height: 46, alignItems: "center", justifyContent: "center"},
  title: {color: C.ink, fontSize: 17, fontWeight: "600", marginBottom: 2},
});
