import React, {useMemo, useRef} from "react";
import {Animated, PanResponder, StyleSheet, Text, View, type ViewStyle} from "react-native";
import Icon, {type IconName} from "./Icon.tsx";
import {C} from "./theme.ts";
import {rightToLeft} from "../i18n/index.ts";

interface Props {
  /** What a full swipe does, shown behind the row while it moves. */
  label: string;
  icon: IconName;
  onSwipe: () => void;
  style?: ViewStyle;
  children: React.ReactNode;
}

/** A row that slides away toward the start edge (left, or right in right-to-left languages) to do one thing. */
export default function SwipeRow({label, icon, onSwipe, style, children}: Props) {
  const shift = useRef(new Animated.Value(0)).current;
  const width = useRef(1);
  const swipeRef = useRef(onSwipe);
  swipeRef.current = onSwipe;
  const rtl = rightToLeft();
  const pan = useMemo(() => {
    const sign = rtl ? 1 : -1;
    // Only a clearly sideways drag toward the start edge takes over; scrolling stays with the list.
    const sideways = (dx: number, dy: number) => dx * sign > 12 && Math.abs(dx) > Math.abs(dy) * 2;
    return PanResponder.create({
      onMoveShouldSetPanResponder: (_e, g) => sideways(g.dx, g.dy),
      onMoveShouldSetPanResponderCapture: (_e, g) => sideways(g.dx, g.dy),
      onPanResponderTerminationRequest: () => false,
      onPanResponderMove: (_e, g) => shift.setValue(g.dx * sign > 0 ? g.dx : 0),
      onPanResponderRelease: (_e, g) => {
        if (g.dx * sign > width.current * 0.35 || g.vx * sign > 0.8) {
          Animated.timing(shift, {toValue: sign * width.current, duration: 160, useNativeDriver: true}).start(() => {
            swipeRef.current();
            shift.setValue(0);
          });
        } else {
          Animated.spring(shift, {toValue: 0, useNativeDriver: true, bounciness: 0}).start();
        }
      },
      onPanResponderTerminate: () => Animated.spring(shift, {toValue: 0, useNativeDriver: true, bounciness: 0}).start(),
    });
  }, [rtl, shift]);

  return (
    <View style={style} onLayout={e => {width.current = Math.max(1, e.nativeEvent.layout.width);}}>
      <View style={styles.behind}>
        <Icon name={icon} size={22} color={C.acc} />
        <Text style={styles.label} numberOfLines={1}>{label}</Text>
      </View>
      <Animated.View style={[styles.front, {transform: [{translateX: shift}]}]} {...pan.panHandlers}>
        {children}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  behind: {...StyleSheet.absoluteFillObject, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 8, paddingHorizontal: 16, backgroundColor: C.tint},
  label: {color: C.acc, fontSize: 15, fontWeight: "600"},
  front: {backgroundColor: C.bg},
});
