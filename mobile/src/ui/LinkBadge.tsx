import React, {useEffect, useRef, useState} from "react";
import {Animated, Pressable, StyleSheet, Text, View} from "react-native";
import Icon from "./Icon.tsx";
import {C} from "./theme.ts";
import {linkState, subscribeLink, type LinkState} from "../protocol/linkState.ts";
import {tr} from "../i18n/index.ts";

export function useLink(): LinkState {
  const [state, setState] = useState<LinkState>(linkState());
  useEffect(() => {setState(linkState()); return subscribeLink(setState);}, []);
  return state;
}

export function linkLabel(state: LinkState): string {
  return state === "connected" ? tr("Connected to your computer")
    : state === "offline" ? tr("Can't reach your computer")
    : tr("Connecting to your computer…");
}

/** A computer screen: green when the computer answers, amber while trying, crossed out and red when it does not. */
export default function LinkBadge({onPress}: {onPress?: () => void}) {
  const state = useLink();
  const pulse = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (state !== "connecting") {pulse.setValue(1); return;}
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, {toValue: 0.35, duration: 700, useNativeDriver: true}),
      Animated.timing(pulse, {toValue: 1, duration: 700, useNativeDriver: true}),
    ]));
    loop.start();
    return () => loop.stop();
  }, [state, pulse]);
  const color = state === "connected" ? C.ok : state === "offline" ? C.bad : C.wait;
  return (
    <Pressable onPress={onPress} hitSlop={10} accessibilityRole="button" accessibilityLabel={linkLabel(state)} style={styles.wrap}>
      <Animated.View style={{opacity: pulse}}>
        <Icon name={state === "offline" ? "monitor-off" : "monitor"} size={26} color={color} />
      </Animated.View>
    </Pressable>
  );
}

/** The same state in words, for the menu and the Status screen. */
export function LinkLine() {
  const state = useLink();
  const color = state === "connected" ? C.ok : state === "offline" ? C.bad : C.wait;
  return (
    <View style={styles.line}>
      <Icon name={state === "offline" ? "monitor-off" : "monitor"} size={18} color={color} />
      <Text style={[styles.lineText, {color}]}>{linkLabel(state)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {width: 48, height: 48, alignItems: "center", justifyContent: "center"},
  line: {flexDirection: "row", alignItems: "center", gap: 8},
  lineText: {fontSize: 13},
});
