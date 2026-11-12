import {requireOptionalNativeModule} from 'expo-modules-core';
import {canonicalBytes} from '../protocol/canonical.ts';
import {sha256} from '../protocol/sha256.ts';

type NativeKey = {create(alias: string, challenge: string): Promise<Record<string, unknown>>; delete(alias: string): Promise<void>;
  setPrivateSession?(enabled: boolean): Promise<void>;
  setPrivateTyping?(): Promise<void>;
  currentKeyboard?(): Promise<string>};
const key = requireOptionalNativeModule<NativeKey>('VoolApprovalKey');
export async function prepareApprovalKey(alias: string, pairingId: string, challenge: string): Promise<Record<string, unknown> | null> {
  if (!key) return null;
  const bytes = sha256(canonicalBytes({kind:'vool.mobile.approval-key.v1',pairing_id:pairingId,challenge,device_public_key:alias}));
  const nonce = btoa(String.fromCharCode(...bytes));
  try {return await key.create(alias, nonce);} catch {return null;}
}
export async function deleteApprovalKey(alias: string) {if (key) await key.delete(alias);}
/** The keyboard app this phone uses now ("" when unknown, always "" on iPhone). */
export async function currentKeyboard(): Promise<string> {try {return String(await key?.currentKeyboard?.() || "");} catch {return "";}}
/** Ask the phone keyboard not to learn from or keep what is typed in VOOL. Best effort. */
export async function requestPrivateTyping() {try {await key?.setPrivateTyping?.();} catch { /* typing still works */ }}
export async function setNativePrivateSession(enabled: boolean) {await key?.setPrivateSession?.(enabled);}
