import React, {useEffect, useState} from "react";
import {Dimensions, Keyboard, LayoutAnimation, Platform, StatusBar, View, type KeyboardEvent, type ViewProps} from "react-native";
import {useSafeAreaInsets} from "react-native-safe-area-context";
import {C} from "./theme.ts";

/** Space the phone keeps for itself: the clock bar on top, the gesture or button bar below. */
export function useEdges(): {top: number; bottom: number} {
  const insets = useSafeAreaInsets();
  // Some phones report no top inset until the first layout; never draw under the clock bar.
  const bar = Platform.OS === "android" ? StatusBar.currentHeight ?? 24 : 0;
  return {top: Math.max(insets.top, bar), bottom: insets.bottom};
}

/** How much of the window the keyboard covers. Android shrinks the window by itself; iPhone and iPad do not. */
export function useKeyboardCover(): number {
  const [cover, setCover] = useState(0);
  useEffect(() => {
    if (Platform.OS !== "ios") return;
    const moved = (event: KeyboardEvent, hidden = false) => {
      const next = hidden ? 0 : Math.max(0, Math.round(Dimensions.get("window").height - event.endCoordinates.screenY));
      if (event.duration) LayoutAnimation.configureNext({duration: event.duration, update: {type: LayoutAnimation.Types.keyboard}});
      setCover(next);
    };
    const changes = Keyboard.addListener("keyboardWillChangeFrame", event => moved(event));
    const hides = Keyboard.addListener("keyboardWillHide", event => moved(event, true));
    return () => {changes.remove(); hides.remove();};
  }, []);
  return cover;
}

/** A full screen that starts below the clock bar and ends above the bottom bar, or above the keyboard while it is open. */
export default function Frame({style, children, ...rest}: ViewProps) {
  const edges = useEdges();
  const keyboard = useKeyboardCover();
  return <View {...rest} style={[{flex: 1, backgroundColor: C.bg, paddingTop: edges.top, paddingBottom: Math.max(edges.bottom, keyboard)}, style]}>{children}</View>;
}
