import React from "react";
import {Pressable, StyleSheet, Text, View} from "react-native";
import Icon, {type IconName} from "./Icon.tsx";
import LinkBadge from "./LinkBadge.tsx";
import {C} from "./theme.ts";
import {tr} from "../i18n/index.ts";

interface Props {
  title: string;
  /** "menu" opens the side menu; "back" returns to the list. */
  lead: "menu" | "back";
  onLead: () => void;
  onLink?: () => void;
  dot?: boolean;
  big?: boolean;
}

export default function Header({title, lead, onLead, onLink, dot, big}: Props) {
  const icon: IconName = lead === "menu" ? "menu" : "arrow-left";
  return (
    <View style={styles.bar}>
      <Pressable onPress={onLead} hitSlop={10} style={styles.lead} accessibilityRole="button"
        accessibilityLabel={lead === "menu" ? tr("Open menu") : tr("Back")}>
        <Icon name={icon} size={26} color={C.ink} />
        {dot ? <View style={styles.dot} /> : null}
      </Pressable>
      <Text style={[styles.title, big && styles.big]} numberOfLines={1}>{title}</Text>
      <LinkBadge onPress={onLink} />
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {flexDirection: "row", alignItems: "center", paddingHorizontal: 6, height: 60, gap: 4},
  lead: {width: 48, height: 48, alignItems: "center", justifyContent: "center"},
  dot: {position: "absolute", right: 9, top: 11, width: 9, height: 9, borderRadius: 5, backgroundColor: C.bad, borderWidth: 1.5, borderColor: C.bg},
  title: {flex: 1, color: C.ink, fontSize: 18, fontWeight: "600"},
  big: {fontSize: 24, fontWeight: "700", letterSpacing: 1},
});
