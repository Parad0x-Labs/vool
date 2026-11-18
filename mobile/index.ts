import { getRandomValues } from "expo-crypto";
import { registerRootComponent } from "expo";
import { Dimensions } from "react-native";
import App from "./App";
import { setTablet } from "./src/i18n/index.ts";

// Install only the platform CSPRNG. Identity generation refuses without it.
if (!globalThis.crypto) Object.defineProperty(globalThis, "crypto", {value: {getRandomValues}});
else Object.defineProperty(globalThis.crypto, "getRandomValues", {value: getRandomValues, configurable: true});

// Same test as the layout uses (src/ui/wide.ts): the shorter side of the screen.
setTablet(Math.min(Dimensions.get("screen").width, Dimensions.get("screen").height) >= 600);
registerRootComponent(App);
