import React, {useEffect, useRef} from "react";
import {Animated, Easing, View} from "react-native";
import Icon from "./Icon.tsx";
import {C} from "./theme.ts";

/** Two meshed gears turning against each other: the computer is working on the reply. */
export default function Gears({size = 20, color = C.soft}: {size?: number; color?: string}) {
  const turn = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(turn, {toValue: 1, duration: 2400, easing: Easing.linear, useNativeDriver: true}));
    loop.start();
    return () => loop.stop();
  }, [turn]);
  const forward = turn.interpolate({inputRange: [0, 1], outputRange: ["0deg", "360deg"]});
  const backward = turn.interpolate({inputRange: [0, 1], outputRange: ["22deg", "-338deg"]});
  const small = Math.round(size * 0.72);
  return (
    <View style={{width: size + small * 0.78, height: size + small * 0.35}}>
      <Animated.View style={{position: "absolute", left: 0, top: 0, transform: [{rotate: forward}]}}>
        <Icon name="cog" size={size} color={color} />
      </Animated.View>
      <Animated.View style={{position: "absolute", left: size * 0.8, top: size * 0.55, transform: [{rotate: backward}]}}>
        <Icon name="cog" size={small} color={color} />
      </Animated.View>
    </View>
  );
}
