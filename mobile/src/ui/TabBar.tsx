import React from "react";
import {Pressable, StyleSheet, Text, View} from "react-native";
import Icon, {type IconName} from "./Icon.tsx";
import {C} from "./theme.ts";

export interface TabItem {key: string; label: string; icon: IconName; iconOn: IconName; badge?: number}

/** The row at the bottom: every part of the app, one tap away. */
export default function TabBar({items, active, onSelect}: {items: TabItem[]; active: string; onSelect: (key: string) => void}) {
  return (
    <View style={styles.bar} accessibilityRole="tablist">
      {items.map(item => {
        const on = item.key === active;
        return (
          <Pressable key={item.key} style={styles.tab} onPress={() => onSelect(item.key)} android_ripple={{color: C.ripple, borderless: true}}
            accessibilityRole="tab" accessibilityState={{selected: on}} accessibilityLabel={item.label}>
            <View>
              <Icon name={on ? item.iconOn : item.icon} size={25} color={on ? C.acc : C.mute} />
              {item.badge ? <View style={styles.badge}><Text style={styles.badgeText}>{item.badge > 9 ? "9+" : String(item.badge)}</Text></View> : null}
            </View>
            <Text style={[styles.label, on && styles.labelOn]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.8}>{item.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {flexDirection: "row", borderTopWidth: 1, borderTopColor: C.line, backgroundColor: C.bg, paddingTop: 8, paddingBottom: 6, paddingHorizontal: 4},
  tab: {flex: 1, alignItems: "center", justifyContent: "center", gap: 3, minHeight: 50},
  label: {color: C.mute, fontSize: 11, fontWeight: "500", paddingHorizontal: 2},
  labelOn: {color: C.acc, fontWeight: "600"},
  badge: {position: "absolute", top: -4, right: -9, minWidth: 17, height: 17, borderRadius: 9, paddingHorizontal: 4, backgroundColor: C.bad,
    alignItems: "center", justifyContent: "center", borderWidth: 1.5, borderColor: C.bg},
  badgeText: {color: "#fff", fontSize: 10, fontWeight: "700"},
});
