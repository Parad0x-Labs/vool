import {sha256, toHex} from '../protocol/sha256.ts';
export const IDLE_MINUTES = [1,5,15] as const;
export function idleMinutes(value:unknown):number {return IDLE_MINUTES.includes(value as 1|5|15)?Number(value):5;}
export function idlePreferenceKey(deviceId:string):string {return 'vool.mobile.idle.'+toHex(sha256(new TextEncoder().encode(deviceId))).slice(0,24);}
export async function readIdleMinutes(deviceId:string):Promise<number>{
  const module=await import('expo-secure-store');const raw=await module.getItemAsync(idlePreferenceKey(deviceId));
  return idleMinutes(raw?Number(raw):5);
}
export async function saveIdleMinutes(deviceId:string,value:number):Promise<void>{
  if(!IDLE_MINUTES.includes(value as 1|5|15))throw new Error('Invalid idle time.');
  const module=await import('expo-secure-store');await module.setItemAsync(idlePreferenceKey(deviceId),String(value),{keychainAccessible:module.WHEN_UNLOCKED_THIS_DEVICE_ONLY});
}
export async function clearIdleMinutes(deviceId:string):Promise<void>{const module=await import('expo-secure-store');await module.deleteItemAsync(idlePreferenceKey(deviceId));}
