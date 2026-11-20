import React, {useEffect, useRef, useState} from "react";
import {Animated, StyleSheet, Text, View} from "react-native";
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

/** How the computer stands, in words: a green dot when it answers, amber and pulsing while trying, red when it does not. */
export function LinkLine({small}: {small?: boolean}) {
  const state = useLink();
  const pulse = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (state !== "connecting") {pulse.setValue(1); return;}
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(pulse, {toValue: 0.3, duration: 700, useNativeDriver: true}),
      Animated.timing(pulse, {toValue: 1, duration: 700, useNativeDriver: true}),
    ]));
    loop.start();
    return () => loop.stop();
  }, [state, pulse]);
  const color = state === "connected" ? C.ok : state === "offline" ? C.bad : C.wait;
  return (
    <View style={styles.line} accessibilityLabel={linkLabel(state)}>
      <Animated.View style={[styles.dot, {backgroundColor: color, opacity: pulse}]} />
      <Text style={[styles.text, small && styles.small, state !== "connected" && {color}]} numberOfLines={1}>{linkLabel(state)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  line: {flexDirection: "row", alignItems: "center", gap: 7},
  dot: {width: 8, height: 8, borderRadius: 4},
  text: {color: C.mute, fontSize: 13.5, flexShrink: 1},
  small: {fontSize: 12},
});
