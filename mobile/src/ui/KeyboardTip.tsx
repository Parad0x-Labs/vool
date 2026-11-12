import React, {useEffect, useState} from "react";
import {Linking, Modal, Platform, Pressable, StyleSheet, Text, View} from "react-native";
import Icon from "./Icon.tsx";
import {C, R} from "./theme.ts";
import {tr} from "../i18n/index.ts";
import {currentKeyboard} from "../security/approvalKey.ts";
import {OFFLINE_KEYBOARD_URL, keyboardCanGoOnline, keyboardTipSeen, rememberKeyboardTip} from "../security/keyboardTip.ts";

/** Shown once: why an offline keyboard keeps typing private, and where to get one. */
export default function KeyboardTip() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (Platform.OS !== "android") return;
    let live = true;
    void (async () => {
      if (await keyboardTipSeen()) return;
      if (!keyboardCanGoOnline(await currentKeyboard())) return;
      if (live) setOpen(true);
    })();
    return () => {live = false;};
  }, []);
  const close = () => {setOpen(false); void rememberKeyboardTip();};
  return (
    <Modal visible={open} transparent animationType="fade" onRequestClose={close} statusBarTranslucent>
      <View style={styles.scrim}>
        <View style={styles.card} accessibilityRole="alert">
          <View style={styles.icon}><Icon name="keyboard-outline" size={26} color={C.ink} /></View>
          <Text style={styles.title}>{tr("A more private keyboard")}</Text>
          <Text style={styles.body}>{tr("The keyboard on this phone can go online. Whatever you type, in VOOL or any other app, passes through it first. HeliBoard is a free, open keyboard with no internet access, so what you type stays on your phone.")}</Text>
          <Text style={styles.note}>{tr("HeliBoard is made by independent developers, not by VOOL. After installing it, open HeliBoard and follow its steps to make it your keyboard.")}</Text>
          <Pressable style={styles.primary} accessibilityRole="button" onPress={() => {close(); void Linking.openURL(OFFLINE_KEYBOARD_URL).catch(() => {});}}>
            <Text style={styles.primaryText}>{tr("Get HeliBoard")}</Text>
          </Pressable>
          <Pressable style={styles.secondary} accessibilityRole="button" onPress={close}>
            <Text style={styles.secondaryText}>{tr("Not now")}</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: {flex: 1, backgroundColor: "rgba(0,0,0,0.6)", alignItems: "center", justifyContent: "center", padding: 22},
  card: {width: "100%", maxWidth: 420, backgroundColor: C.panel, borderRadius: 24, padding: 22},
  icon: {width: 48, height: 48, borderRadius: 24, backgroundColor: C.pill, alignItems: "center", justifyContent: "center", marginBottom: 14},
  title: {color: C.ink, fontSize: 21, fontWeight: "600", marginBottom: 10},
  body: {color: C.soft, fontSize: 15, lineHeight: 22},
  note: {color: C.mute, fontSize: 13, lineHeight: 19, marginTop: 10},
  primary: {backgroundColor: C.send, borderRadius: R.pill, height: 50, alignItems: "center", justifyContent: "center", marginTop: 20},
  primaryText: {color: C.sendInk, fontSize: 16, fontWeight: "600"},
  secondary: {height: 46, alignItems: "center", justifyContent: "center", marginTop: 6},
  secondaryText: {color: C.soft, fontSize: 15},
});
