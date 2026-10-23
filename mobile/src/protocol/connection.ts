import type {DeviceCredentials} from './client.ts';

export function credentialIdentity(value: DeviceCredentials): string {
  const sealed = value as DeviceCredentials & {protocol?: string; desktopEncryptionKey?: string};
  return JSON.stringify([value.deviceId, value.grantId, value.grantSecret, value.deviceSeedHex,
    value.desktopFingerprint, sealed.protocol, sealed.desktopEncryptionKey]);
}

/** Changing the route preserves the paired desktop key; it never trusts a new PC. */
export function connectionAddress(value: string): string {
  const url = new URL(value.trim());
  const pieces = url.hostname.split('.').map(Number);
  const privateV4 = pieces.length===4 && pieces.every(x=>Number.isInteger(x)&&x>=0&&x<=255) &&
    (pieces[0]===127 || pieces[0]===10 || (pieces[0]===192&&pieces[1]===168) ||
     (pieces[0]===172&&pieces[1]>=16&&pieces[1]<=31) || (pieces[0]===100&&pieces[1]>=64&&pieces[1]<=127));
  if ((url.protocol!=="https:" && !(url.protocol==="http:"&&privateV4)) || url.username || url.password || url.search || url.hash ||
    !(/^\/?$/.test(url.pathname) || /^\/rooms\/[a-f0-9]{48,64}\/?$/.test(url.pathname))) throw new Error("Invalid connection address");
  return url.toString().replace(/\/$/, "");
}
