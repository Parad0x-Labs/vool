export class NotificationCursorStore {
  private readonly key: string;
  constructor(deviceId: string) {this.key = "vool.alerts." + deviceId.replace(/[^a-zA-Z0-9_-]/g, "_");}
  async load(): Promise<number> {
    const store = await import("expo-secure-store");
    const raw = await store.getItemAsync(this.key);
    const cursor = raw === null ? 0 : Number(raw);
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new Error("Saved alert position could not be read.");
    return cursor;
  }
  async save(cursor: number) {
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new Error("Invalid alert position.");
    const store = await import("expo-secure-store");
    await store.setItemAsync(this.key, String(cursor), {keychainAccessible: store.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
  }
  async clear() {const store = await import("expo-secure-store"); await store.deleteItemAsync(this.key);}
}
