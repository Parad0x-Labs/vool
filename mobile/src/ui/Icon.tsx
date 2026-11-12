import React from "react";
import MaterialCommunityIcons from "@expo/vector-icons/MaterialCommunityIcons";
import {C} from "./theme.ts";

export type IconName = React.ComponentProps<typeof MaterialCommunityIcons>["name"];
export default function Icon({name, size = 24, color = C.ink}: {name: IconName; size?: number; color?: string}) {
  return <MaterialCommunityIcons name={name} size={size} color={color} />;
}
