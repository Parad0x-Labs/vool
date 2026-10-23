import type {ProposalStore, SavedProposal} from "../protocol/paymentDraft.ts";
import {sha256, toHex} from "../protocol/sha256.ts";

const pending = new Map<string, Promise<unknown>>();
function serialized<T>(key: string, work: () => Promise<T>): Promise<T> {
  const next = (pending.get(key) || Promise.resolve()).catch(() => {}).then(work);
  pending.set(key, next);
  void next.finally(() => {if (pending.get(key) === next) pending.delete(key);}).catch(() => {});
  return next;
}

export class SecureProposalStore implements ProposalStore {
  private key: string;
  constructor(deviceId: string) {this.key = "vool.mobile.payment." + toHex(sha256(new TextEncoder().encode(deviceId))).slice(0, 24);}
  load() {return serialized(this.key, async () => {
    const module = await import("expo-secure-store");
    const value = await module.getItemAsync(this.key);
    return value ? JSON.parse(value) as SavedProposal : null;
  });}
  save(value: SavedProposal) {return serialized(this.key, async () => {
    const module = await import("expo-secure-store");
    const raw = JSON.stringify(value);
    if (new TextEncoder().encode(raw).length > 1800) throw new Error("This saved request is too large.");
    await module.setItemAsync(this.key, raw, {keychainAccessible: module.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
  });}
  clear(expectedKey?: string) {return serialized(this.key, async () => {
    const module = await import("expo-secure-store");
    if (expectedKey) {
      const raw = await module.getItemAsync(this.key);
      if (!raw || (JSON.parse(raw) as SavedProposal).idempotency_key !== expectedKey) return;
    }
    await module.deleteItemAsync(this.key);
  });}
}
