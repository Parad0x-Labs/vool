// Device identity — the phone's Ed25519 keypair, generated ONCE from securely
// random seed material and reconstructed from the stored seed thereafter.
//
// THE LAW THIS MODULE ENFORCES: the private seed lives only in the platform
// keystore/keychain (src/store/credentials.ts, device-only accessibility) and
// never crosses the network. Two signatures ride the protocol:
//   1. pairing claim  — Ed25519 over the server-held challenge, pairing id,
//      code, this public key and expiry (proof of possession: a forged or
//      replayed public key cannot produce it);
//   2. every request — Ed25519 over the same canonical bytes the grant HMAC
//      protects (device id, action, params digest, nonce, timestamp, grant
//      identity), so a stolen grant secret without the phone is useless.
//
// Backed by the vendored TweetNaCl (mobile/src/vendor/nacl-fast.cjs, MIT — see
// tweetnacl-LICENSE). Pure JS, so the same calls run on iOS, Android and the
// headless Node journey. The .cjs extension keeps Node (type: module) loading
// it as CommonJS — the UMD file has no ESM named exports; Metro/RN resolve
// .cjs identically.
import nacl from "../vendor/nacl-fast.cjs";
import { fromHex, sha256, toHex } from "./sha256.ts";
import { canonicalBytes } from "./canonical.ts";

// The vendored file ships without a PRNG (see its vendor note); install the
// platform CSPRNG — getRandomValues exists on iOS (Hermes + expo-crypto
// polyfill), Android and Node.
nacl.setPRNG(function (x: Uint8Array, n: number) {
  const bytes = new Uint8Array(n);
  globalThis.crypto.getRandomValues(bytes);
  x.set(bytes);
});

export interface DeviceKeypair {
  publicKeyHex: string;
  secretKeyHex: string;
  seedHex: string;
}

/** Deterministically reconstruct the identity from a stored 32-byte seed. */
export function keypairFromSeed(seedHex: string): DeviceKeypair {
  const kp = nacl.sign.keyPair.fromSeed(fromHex(seedHex));
  return {
    publicKeyHex: toHex(kp.publicKey),
    secretKeyHex: toHex(kp.secretKey),
    seedHex: seedHex.toLowerCase(),
  };
}

/** Generate a brand-new identity (secure randomness). Store the seed. */
export function generateDeviceKeypair(): DeviceKeypair {
  const seed = nacl.randomBytes(32);
  return keypairFromSeed(toHex(seed));
}

export function deviceIdFor(publicKeyHex: string): string {
  return "phone:" + toHex(sha256(fromHex(publicKeyHex))).slice(0, 20);
}

export function signBytes(secretKeyHex: string, message: Uint8Array): Uint8Array {
  return nacl.sign.detached(message, fromHex(secretKeyHex));
}

export function verifyBytes(publicKeyHex: string, message: Uint8Array, signature: Uint8Array): boolean {
  try {
    return nacl.sign.detached.verify(message, signature, fromHex(publicKeyHex));
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// The two protocol signatures
// ---------------------------------------------------------------------------

/** Pairing claim proof-of-possession payload — byte-identical construction to
 *  the desktop's verifier (core/web/api/mobile_companion_api.py). */
export function pairingProofPayload(args: {
  pairingId: string;
  code: string;
  challenge: string;
  devicePublicKey: string;
  expiresAtEpoch: number;
}): Uint8Array {
  return canonicalBytes({
    kind: "vool.mobile.pairing.proof.v1",
    pairing_id: args.pairingId,
    code: args.code,
    challenge: args.challenge,
    device_public_key: args.devicePublicKey,
    expires_at: args.expiresAtEpoch,
  });
}

export function signPairingProof(secretKeyHex: string, args: {
  pairingId: string;
  code: string;
  challenge: string;
  devicePublicKey: string;
  expiresAtEpoch: number;
}): string {
  return toHex(signBytes(secretKeyHex, pairingProofPayload(args)));
}

/** Per-request proof payload — covers the same canonical bytes the grant HMAC
 *  protects: device id, action, parameters digest, nonce, timestamp, grant. */
export function requestProofPayload(args: {
  deviceId: string;
  action: string;
  params: Record<string, unknown>;
  nonce: string;
  timestamp: string;
  grantId: string;
}): Uint8Array {
  return canonicalBytes({
    kind: "vool.mobile.request.v1",
    device_id: args.deviceId,
    action: args.action,
    params_sha256: toHex(sha256(canonicalBytes(args.params))),
    nonce: args.nonce,
    timestamp: args.timestamp,
    grant_id: args.grantId,
  });
}

export function signRequestProof(secretKeyHex: string, args: {
  deviceId: string;
  action: string;
  params: Record<string, unknown>;
  nonce: string;
  timestamp: string;
  grantId: string;
}): string {
  return toHex(signBytes(secretKeyHex, requestProofPayload(args)));
}
