import React from "react";
import {Modal, Pressable, StyleSheet, Text, View} from "react-native";
import Icon, {type IconName} from "./Icon.tsx";
import {C} from "./theme.ts";
import {tr} from "../i18n/index.ts";

export interface SheetAction {key: string; label: string; icon: IconName; disabled?: boolean; onPress: () => void}

/** A short list of choices that slides up from the bottom (the "+" in the message box). */
export default function Sheet({open, onClose, actions}: {open: boolean; onClose: () => void; actions: SheetAction[]}) {
  return (
    <Modal visible={open} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <Pressable style={styles.scrim} onPress={onClose} accessibilityLabel={tr("Close")} />
      <View style={styles.sheet}>
        <View style={styles.grip} />
        {actions.map(action => (
          <Pressable key={action.key} disabled={action.disabled} style={[styles.row, action.disabled && styles.off]}
            onPress={() => {onClose(); action.onPress();}} accessibilityRole="button">
            <View style={styles.icon}><Icon name={action.icon} size={22} color={C.ink} /></View>
            <Text style={styles.label}>{action.label}</Text>
          </Pressable>
        ))}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  scrim: {flex: 1, backgroundColor: "rgba(0,0,0,0.5)"},
  sheet: {backgroundColor: C.panel, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 14, paddingTop: 10, paddingBottom: 28},
  grip: {alignSelf: "center", width: 40, height: 4, borderRadius: 2, backgroundColor: C.line, marginBottom: 10},
  row: {flexDirection: "row", alignItems: "center", gap: 16, height: 58, paddingHorizontal: 8},
  off: {opacity: 0.4},
  icon: {width: 42, height: 42, borderRadius: 21, backgroundColor: C.pill, alignItems: "center", justifyContent: "center"},
  label: {color: C.ink, fontSize: 17},
});
