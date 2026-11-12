/** Local opt-in is separate from the PC's per-type delivery choices. */
export class OsPreferenceStore {
  private readonly key: string;
  constructor(deviceId: string) {this.key = 'vool.alerts.optin.' + deviceId.replace(/[^a-zA-Z0-9_-]/g, '_');}
  async load(): Promise<boolean> {
    const store = await import('expo-secure-store');
    const value = await store.getItemAsync(this.key);
    if (value === null || value === '0') return false;
    if (value !== '1') throw new Error('Saved notification choice could not be read.');
    return true;
  }
  async save(value: boolean) {
    const store = await import('expo-secure-store');
    await store.setItemAsync(this.key, value ? '1' : '0', {keychainAccessible: store.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
  }
  async clear() {const store = await import('expo-secure-store'); await store.deleteItemAsync(this.key);}
}
