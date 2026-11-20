import nacl from "../vendor/nacl-fast.cjs";
import { fromHex, hmacSha256, sha256, toHex } from "./sha256.ts";

/** The link's hashing and sealing over text, done by the phone's native code when it is there:
 *  in JavaScript a 768 KB piece took one to three seconds and held up every tap. Same bytes either way. */
export interface FastCrypto {
  sha256Hex(text: string): string;
  hmacSha256Hex(key: string, text: string): string;
  /** nacl.secretbox(utf8(text), nonce, key) as base64. */
  seal(text: string, nonceHex: string, keyHex: string): string;
  /** nacl.secretbox.open of base64, as text; null when it does not authenticate. */
  open(base64: string, nonceHex: string, keyHex: string): string | null;
}

const utf8 = (text: string) => new TextEncoder().encode(text);

function base64(bytes: Uint8Array): string {
  // in slices: one character at a time is far too slow for a large body
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000) as unknown as number[]);
  return btoa(binary);
}
function unbase64(text: string): Uint8Array {
  return Uint8Array.from(atob(text), c => c.charCodeAt(0));
}

export const scriptCrypto: FastCrypto = {
  sha256Hex: text => toHex(sha256(utf8(text))),
  hmacSha256Hex: (key, text) => toHex(hmacSha256(utf8(key), utf8(text))),
  seal: (text, nonceHex, keyHex) => base64(nacl.secretbox(utf8(text), fromHex(nonceHex), fromHex(keyHex))),
  open: (data, nonceHex, keyHex) => {
    let box: Uint8Array;
    try { box = unbase64(data); } catch { return null; }
    const plain = nacl.secretbox.open(box, fromHex(nonceHex), fromHex(keyHex));
    return plain ? new TextDecoder().decode(plain) : null;
  },
};

let current: FastCrypto = scriptCrypto;

/** Hands the link to native code; null goes back to the script version. */
export function setFastCrypto(impl: FastCrypto | null) { current = impl ?? scriptCrypto; }
export const fast: FastCrypto = {
  sha256Hex: text => current.sha256Hex(text),
  hmacSha256Hex: (key, text) => current.hmacSha256Hex(key, text),
  seal: (text, nonceHex, keyHex) => current.seal(text, nonceHex, keyHex),
  open: (data, nonceHex, keyHex) => current.open(data, nonceHex, keyHex),
};
