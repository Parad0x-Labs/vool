import React, {useEffect, useRef, useState} from "react";
import {Animated, Dimensions, Easing, Pressable, ScrollView, StyleSheet, Text, View} from "react-native";
import Icon, {type IconName} from "./Icon.tsx";
import {LinkLine} from "./LinkBadge.tsx";
import {C, R} from "./theme.ts";
import {tr} from "../i18n/index.ts";

export interface DrawerItem {key: string; label: string; icon: IconName; badge?: number}
export interface DrawerChat {id: string; title: string}

interface Props {
  open: boolean;
  onClose: () => void;
  items: DrawerItem[];
  active: string;
  onSelect: (key: string) => void;
  chats: DrawerChat[];
  onChat: (id: string) => void;
  onNewChat: () => void;
  topInset: number;
}

/** Side menu: where to go, the recent chats, and a New chat button. */
export default function Drawer({open, onClose, items, active, onSelect, chats, onChat, onNewChat, topInset}: Props) {
  const width = Math.min(Dimensions.get("window").width * 0.86, 360);
  const slide = useRef(new Animated.Value(0)).current;
  const [shown, setShown] = useState(open);
  useEffect(() => {
    if (open) setShown(true);
    Animated.timing(slide, {toValue: open ? 1 : 0, duration: 220, easing: Easing.out(Easing.cubic), useNativeDriver: true})
      .start(({finished}) => {if (finished && !open) setShown(false);});
  }, [open, slide]);
  if (!shown) return null;
  return (
    <View style={StyleSheet.absoluteFill} pointerEvents={open ? "auto" : "none"}>
      <Animated.View style={[StyleSheet.absoluteFill, styles.scrim, {opacity: slide}]}>
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel={tr("Close")} />
      </Animated.View>
      <Animated.View style={[styles.panel, {width, paddingTop: topInset + 18,
        transform: [{translateX: slide.interpolate({inputRange: [0, 1], outputRange: [-width, 0]})}]}]}>
        <Text style={styles.brand}>VOOL</Text>
        <View style={styles.link}><LinkLine /></View>
        <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
          {items.map(item => (
            <Pressable key={item.key} style={[styles.item, active === item.key && styles.itemActive]}
              onPress={() => onSelect(item.key)} accessibilityRole="button">
              <Icon name={item.icon} size={24} color={active === item.key ? C.ink : C.soft} />
              <Text style={[styles.itemText, active === item.key && styles.itemTextActive]}>{item.label}</Text>
              {item.badge ? <View style={styles.badge}><Text style={styles.badgeText}>{item.badge > 9 ? "9+" : String(item.badge)}</Text></View> : null}
            </Pressable>
          ))}
          {chats.length ? <>
            <View style={styles.rule} />
            <Text style={styles.section}>{tr("Recent chats")}</Text>
            {chats.map(chat => (
              <Pressable key={chat.id} style={styles.chat} onPress={() => onChat(chat.id)} accessibilityRole="button">
                <Icon name="message-outline" size={18} color={C.mute} />
                <Text style={styles.chatText} numberOfLines={1}>{chat.title}</Text>
              </Pressable>
            ))}
          </> : null}
        </ScrollView>
        <Pressable style={styles.newChat} onPress={onNewChat} accessibilityRole="button">
          <Icon name="plus" size={20} color={C.sendInk} />
          <Text style={styles.newChatText}>{tr("New chat")}</Text>
        </Pressable>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  scrim: {backgroundColor: "rgba(0,0,0,0.55)"},
  panel: {position: "absolute", left: 0, top: 0, bottom: 0, backgroundColor: C.panel, paddingHorizontal: 12, paddingBottom: 18},
  brand: {color: C.ink, fontSize: 28, fontWeight: "700", letterSpacing: 2, paddingHorizontal: 12},
  link: {paddingHorizontal: 12, paddingTop: 6, paddingBottom: 14},
  scroll: {flex: 1},
  scrollContent: {paddingBottom: 16},
  item: {flexDirection: "row", alignItems: "center", gap: 16, paddingHorizontal: 14, height: 54, borderRadius: R.pill},
  itemActive: {backgroundColor: C.pill},
  itemText: {flex: 1, color: C.soft, fontSize: 17},
  itemTextActive: {color: C.ink, fontWeight: "600"},
  badge: {backgroundColor: C.bad, borderRadius: 10, minWidth: 20, height: 20, paddingHorizontal: 6, alignItems: "center", justifyContent: "center"},
  badgeText: {color: "#fff", fontSize: 11, fontWeight: "700"},
  rule: {height: 1, backgroundColor: C.line, marginVertical: 14, marginHorizontal: 8},
  section: {color: C.mute, fontSize: 14, paddingHorizontal: 14, paddingBottom: 6},
  chat: {flexDirection: "row", alignItems: "center", gap: 14, paddingHorizontal: 14, height: 46},
  chatText: {flex: 1, color: C.ink, fontSize: 16},
  newChat: {flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, alignSelf: "flex-end",
    backgroundColor: C.send, borderRadius: R.pill, paddingHorizontal: 22, height: 52},
  newChatText: {color: C.sendInk, fontSize: 16, fontWeight: "600"},
});
