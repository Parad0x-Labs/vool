// Device credential storage — iOS Keychain / Android Keystore via
// expo-secure-store on device; an in-memory store for the headless Node
// journey and unit tests.
//
// ONE blob, device-only accessibility: the grant bearer secret AND the phone's
// Ed25519 seed (the private half of the device identity). Neither ever
// touches AsyncStorage, a file, or the network after pairing. Deleting the
// blob (reinstall, "unpair") destroys the device identity — a new pairing is
// required, by design.
import type { DeviceCredentials } from "../protocol/client.ts";

export interface CredentialStore {
  loadDeviceCredentials(): Promise<DeviceCredentials | null>;
  saveDeviceCredentials(creds: DeviceCredentials): Promise<void>;
  clearDeviceCredentials(): Promise<void>;
}

/** Headless/test store — process memory only. */
export class MemoryCredentialStore implements CredentialStore {
  private creds: DeviceCredentials | null = null;

  async loadDeviceCredentials(): Promise<DeviceCredentials | null> { return this.creds; }
  async saveDeviceCredentials(creds: DeviceCredentials): Promise<void> { this.creds = creds; }
  async clearDeviceCredentials(): Promise<void> { this.creds = null; }
}

/**
 * On-device store. Kept behind a dynamic import so the protocol core (and the
 * headless journey) never pulls react-native modules into a Node process.
 */
export class SecureCredentialStore implements CredentialStore {
  async loadDeviceCredentials(): Promise<DeviceCredentials | null> {
    const SecureStore = await import("expo-secure-store");
    const raw = await SecureStore.getItemAsync("vool.mobile.credentials");
    return raw ? (JSON.parse(raw) as DeviceCredentials) : null;
  }

  async saveDeviceCredentials(creds: DeviceCredentials): Promise<void> {
    const SecureStore = await import("expo-secure-store");
    await SecureStore.setItemAsync(
      "vool.mobile.credentials",
      JSON.stringify(creds),
      // Keychain: device-only, no iCloud backup of the bearer secret or the
      // device identity seed.
      { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY },
    );
  }

  async clearDeviceCredentials(): Promise<void> {
    const SecureStore = await import("expo-secure-store");
    await SecureStore.deleteItemAsync("vool.mobile.credentials");
  }
}

export function credentialStoreForEnvironment(headless: boolean): CredentialStore {
  return headless ? new MemoryCredentialStore() : new SecureCredentialStore();
}
