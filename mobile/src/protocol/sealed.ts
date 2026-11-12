import nacl from "../vendor/nacl-fast.cjs";
import { canonicalBytes } from "./canonical.ts";
import { fromHex, randomBytes, sha256, toHex } from "./sha256.ts";
import { verifyBytes } from "./ed25519.ts";
import {
  claimPairing, credentialsFromClaim, CompanionError, MobileCompanionClient, defaultFetch,
  type ClaimPairingArgs, type ClientOptions, type DeviceCredentials, type FetchLike,
} from "./client.ts";
import type { PairingChallenge } from "./models.ts";

export const SEALED_PROTOCOL = "vool.mobile.sealed.v2";
export interface SealedCredentials extends DeviceCredentials {
  protocol: typeof SEALED_PROTOCOL;
  desktopEncryptionKey: string;
}
export interface SealedChallenge extends PairingChallenge {
  sealing: { binding: {
    kind: string; public_key: string; desktop_fingerprint: string;
    pairing_id: string; challenge: string; expires_at_epoch: number;
  }; signature: string };
}

export function isSealedCredentials(value: DeviceCredentials): value is SealedCredentials {
  const sealed = value as Partial<SealedCredentials>;
  return sealed.protocol === SEALED_PROTOCOL && /^[0-9a-f]{64}$/.test(sealed.desktopEncryptionKey || "");
}

/** Standard Ed25519 seed -> Curve25519 conversion (the desktop uses libsodium). */
export function curveSecret(seedHex: string): Uint8Array {
  const seed = fromHex(seedHex);
  if (seed.length !== 32) throw new Error("Invalid device seed");
  const key = nacl.hash(seed).slice(0, 32);
  key[0] &= 248; key[31] &= 127; key[31] |= 64;
  return key;
}

export function directionalKey(shared: Uint8Array, direction: "request" | "response"): Uint8Array {
  const suffix = new TextEncoder().encode(SEALED_PROTOCOL + ":" + direction);
  const input = new Uint8Array(shared.length + suffix.length);
  input.set(shared); input.set(suffix, shared.length);
  return sha256(input);
}

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
function unbase64(text: string): Uint8Array {
  return Uint8Array.from(atob(text), c => c.charCodeAt(0));
}
export interface SealedEnvelope { protocol: string; nonce: string; ciphertext: string }
export function sealBody(value: unknown, shared: Uint8Array, direction: "request" | "response", nonce = randomBytes(24)): SealedEnvelope {
  return {protocol: SEALED_PROTOCOL, nonce: toHex(nonce),
    ciphertext: base64(nacl.secretbox(canonicalBytes(value), nonce, directionalKey(shared, direction)))};
}
export function openBody(envelope: SealedEnvelope, shared: Uint8Array, direction: "request" | "response"): Record<string, unknown> {
  if (envelope?.protocol !== SEALED_PROTOCOL) throw new Error("Sealed response required");
  const nonce = fromHex(envelope.nonce);
  if (nonce.length !== 24) throw new Error("Invalid sealed nonce");
  const plain = nacl.secretbox.open(unbase64(envelope.ciphertext), nonce, directionalKey(shared, direction));
  if (!plain) throw new Error("Sealed response authentication failed");
  return JSON.parse(new TextDecoder().decode(plain));
}

export function sealedTransport(seed: string, desktopKey: string, deviceId: string, wire: FetchLike, claim = false): FetchLike {
  const secret = curveSecret(seed);
  const shared = nacl.box.before(fromHex(desktopKey), secret);
  const publicKey = toHex(nacl.box.keyPair.fromSecretKey(secret).publicKey);
  return async (url, init) => {
    if (init.method !== "POST" || !init.body) throw new Error("Sealed transport requires a typed request");
    const envelope = {...sealBody({kind: SEALED_PROTOCOL + (claim ? ":claim" : ":request"), body: JSON.parse(init.body)}, shared, "request"),
      ...(claim ? {public_key: publicKey} : {device_id: deviceId})};
    const response = await wire(url, {...init, body: JSON.stringify(envelope)});
    const parsed = await response.json() as SealedEnvelope;
    let decoded;
    try {
      decoded = openBody(parsed, shared, "response");
      if (decoded.kind !== SEALED_PROTOCOL + ":response" || decoded.request_hash !== toHex(sha256(canonicalBytes(envelope)))) {
        throw new Error("Response does not belong to this request");
      }
    } catch (_) {
      throw new CompanionError(response.status, "sealed_response_invalid", "Can't verify the connection to your computer. Try again.");
    }
    return {status: response.status, json: async () => decoded.body};
  };
}

export class SealedMobileCompanionClient extends MobileCompanionClient {
  constructor(credentials: SealedCredentials, options: ClientOptions = {}) {
    if (!isSealedCredentials(credentials)) throw new Error("Pair this phone again to use the private connection.");
    super(credentials, {...options, fetchImpl: sealedTransport(credentials.deviceSeedHex,
      credentials.desktopEncryptionKey, credentials.deviceId, options.fetchImpl ?? defaultFetch)});
  }
}

export async function claimSealedPairing(base: string, options: ClaimPairingArgs): Promise<SealedCredentials> {
  const challenge = options.challenge as SealedChallenge;
  const binding = challenge.sealing?.binding;
  const pin = options.pinnedDesktopFingerprint?.toLowerCase();
  if (!binding || !pin || pin !== challenge.desktop_fingerprint ||
      toHex(sha256(fromHex(challenge.desktop_public_key))) !== pin ||
      binding.kind !== SEALED_PROTOCOL + ":pairing" || binding.desktop_fingerprint !== pin ||
      binding.pairing_id !== options.pairingId || binding.challenge !== challenge.challenge ||
      binding.expires_at_epoch !== challenge.expires_at_epoch || binding.expires_at_epoch * 1000 <= Date.now() ||
      !verifyBytes(challenge.desktop_public_key, canonicalBytes(binding), fromHex(challenge.sealing.signature))) {
    throw new Error("The computer's check code could not be verified. Scan its pairing code again.");
  }
  const {claim, identityOk} = await claimPairing(base, {...options,
    fetchImpl: sealedTransport(options.deviceSeedHex, binding.public_key, "", options.fetchImpl ?? defaultFetch, true)});
  if (!identityOk || claim.desktop_fingerprint !== pin) throw new Error("The computer's identity changed. Pair again.");
  return {...credentialsFromClaim(base, claim, options.deviceSeedHex), protocol: SEALED_PROTOCOL,
    desktopEncryptionKey: binding.public_key};
}
