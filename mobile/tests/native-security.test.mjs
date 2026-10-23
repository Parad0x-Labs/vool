import {test} from "node:test";
import assert from "node:assert/strict";
import {generateDeviceKeypair} from "../src/protocol/ed25519.ts";

test("key generation refuses without the platform CSPRNG", () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  try {
    Object.defineProperty(globalThis, "crypto", {value: undefined, configurable: true});
    assert.throws(() => generateDeviceKeypair(), /Secure random numbers are unavailable/);
  } finally {
    Object.defineProperty(globalThis, "crypto", previous);
  }
});
