import React from "react";
import {Image} from "react-native";

const FACE = require("../../assets/face.png");

/** The VOOL drawing with its soft glow. */
export default function Face({size = 96}: {size?: number}) {
  return <Image source={FACE} style={{width: size, height: size}} resizeMode="contain" accessibilityIgnoresInvertColors />;
}
