import { getRandomValues } from "expo-crypto";
import { registerRootComponent } from "expo";
import App from "./App";

// Install only the platform CSPRNG. Identity generation refuses without it.
if (!globalThis.crypto) Object.defineProperty(globalThis, "crypto", {value: {getRandomValues}});
else Object.defineProperty(globalThis.crypto, "getRandomValues", {value: getRandomValues, configurable: true});

registerRootComponent(App);
