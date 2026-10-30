import type {DeviceCredentials} from './client.ts';

export function credentialIdentity(value: DeviceCredentials): string {
  const sealed = value as DeviceCredentials & {protocol?: string; desktopEncryptionKey?: string};
  return JSON.stringify([value.deviceId, value.grantId, value.grantSecret, value.deviceSeedHex,
    value.desktopFingerprint, sealed.protocol, sealed.desktopEncryptionKey]);
}

/** Changing the route preserves the paired desktop key; it never trusts a new PC.
 *  Parsed by hand: React Native's URL throws on hostname/protocol (see pairing.ts). */
export function connectionAddress(value: string): string {
  const match = /^(https?):\/\/([A-Za-z0-9.-]+)(?::(\d{1,5}))?(\/[^?#]*)?$/i.exec(value.trim());
  if (!match) throw new Error("Invalid connection address");
  const protocol = match[1].toLowerCase();
  const hostname = match[2].toLowerCase();
  const port = match[3] ? Number(match[3]) : null;
  const pathname = match[4] || "/";
  const pieces = hostname.split('.').map(Number);
  const privateV4 = pieces.length===4 && pieces.every(x=>Number.isInteger(x)&&x>=0&&x<=255) &&
    (pieces[0]===127 || pieces[0]===10 || (pieces[0]===192&&pieces[1]===168) ||
     (pieces[0]===172&&pieces[1]>=16&&pieces[1]<=31) || (pieces[0]===100&&pieces[1]>=64&&pieces[1]<=127));
  if ((protocol!=="https" && !(protocol==="http"&&privateV4)) || (port!==null && (port<1||port>65535)) ||
    !(/^\/?$/.test(pathname) || /^\/rooms\/[a-f0-9]{48,64}\/?$/.test(pathname))) throw new Error("Invalid connection address");
  const defaultPort = (protocol==="https" && port===443) || (protocol==="http" && port===80);
  return `${protocol}://${hostname}${port!==null && !defaultPort ? ":" + port : ""}${pathname}`.replace(/\/$/, "");
}
