import React, {useState} from "react";
import {Platform, Pressable, StyleSheet, Text, View} from "react-native";

import {tr} from "../i18n/index.ts";
import {vaultNative} from "../vault/native.ts";
import Icon from "../ui/Icon.tsx";
import SettingsCard from "../ui/SettingsCard.tsx";
import {C} from "../ui/theme.ts";

/** Settings for VOOL Vault: open it, and its own icon on the home screen (which still opens behind VOOL's unlock). */
export default function VaultSettings({onOpen}: {onOpen: () => void}) {
  const [homeIcon, setHomeIcon] = useState(() => vaultNative?.homeIconOn() ?? false);
  if (!vaultNative) return null;

  const toggleHome = () => {
    const next = !homeIcon;
    void vaultNative?.setHomeIcon(next).then(() => setHomeIcon(vaultNative?.homeIconOn() ?? next)).catch(() => {});
  };

  return <SettingsCard icon="safe-square-outline" title={tr("VOOL Vault")}>
    <Pressable style={[styles.row, styles.rule]} onPress={onOpen} accessibilityRole="button" android_ripple={{color: C.ripple}}>
      <View style={styles.fill}><Text style={styles.text}>{tr("Open VOOL Vault")}</Text></View>
      <Icon name="chevron-right" size={22} color={C.faint} />
    </Pressable>
    {/* iPhone and iPad give an app one home-screen icon: the Vault is in the VOOL icon's long-press menu there. */}
    {Platform.OS === "android" ? <Pressable style={[styles.row, styles.rule]} onPress={toggleHome} accessibilityRole="switch" accessibilityState={{checked: homeIcon}}
      android_ripple={{color: C.ripple}}>
      <View style={styles.fill}><Text style={styles.text}>{tr("VOOL Vault on the home screen")}</Text></View>
      <Text style={[styles.value, homeIcon && {color: C.ok}]}>{homeIcon ? tr("On") : tr("Off")}</Text>
    </Pressable> : null}
    <View style={[styles.row, styles.rule]}>
      <Text style={styles.note}>{tr("Photos, videos and sound from your computer’s protected library. They open here without a plain copy on this phone, and only while VOOL is unlocked.")}</Text>
    </View>
  </SettingsCard>;
}

const styles = StyleSheet.create({
  row: {flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, minHeight: 52, paddingVertical: 12},
  rule: {borderTopWidth: 1, borderTopColor: C.line},
  fill: {flex: 1},
  text: {color: C.ink, fontSize: 15},
  note: {color: C.mute, fontSize: 12, marginTop: 2},
  value: {color: C.mute, fontSize: 14, fontWeight: "600"},
});
