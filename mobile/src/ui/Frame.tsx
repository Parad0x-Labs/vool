import React from "react";
import {Platform, StatusBar, View, type ViewProps} from "react-native";
import {useSafeAreaInsets} from "react-native-safe-area-context";
import {C} from "./theme.ts";

/** Space the phone keeps for itself: the clock bar on top, the gesture or button bar below. */
export function useEdges(): {top: number; bottom: number} {
  const insets = useSafeAreaInsets();
  // Some phones report no top inset until the first layout; never draw under the clock bar.
  const bar = Platform.OS === "android" ? StatusBar.currentHeight ?? 24 : 0;
  return {top: Math.max(insets.top, bar), bottom: insets.bottom};
}

/** A full screen that starts below the clock bar and ends above the bottom bar. */
export default function Frame({style, children, ...rest}: ViewProps) {
  const edges = useEdges();
  return <View {...rest} style={[{flex: 1, backgroundColor: C.bg, paddingTop: edges.top, paddingBottom: edges.bottom}, style]}>{children}</View>;
}
