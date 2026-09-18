// Pairing — QR payload parsing and the phone side of the pairing handshake.
//
// The QR encodes:  vool-pair://<host>[:port]?fp=<desktop fingerprint hex>&pid=<pairing_id>&code=<code>
// Manual entry (pairing id + code typed by hand) goes through the same state
// machine with the fingerprint pinned out-of-band (shown on the desktop's
// pairing card as the short fingerprint).
import { canonicalBytes } from "./canonical.ts";
import { verifyBytes } from "./ed25519.ts";
import type { DesktopProof } from "./models.ts";

export interface ParsedPairingUri {
  host: string;
  port: number | null;
  desktopFingerprint: string;
  pairingId: string;
  code: string;
  /** Expiry epoch pinned by the QR (seconds). A stale QR refuses locally. */
  expiresAtEpoch: number;
}

export function parsePairingUri(uri: string): ParsedPairingUri | null {
  const text = String(uri || "").trim();
  if (!text.toLowerCase().startsWith("vool-pair://")) return null;
  try {
    const url = new URL(text);
    const fp = (url.searchParams.get("fp") || "").toLowerCase();
    const pid = url.searchParams.get("pid") || "";
    const code = url.searchParams.get("code") || "";
    const exp = Number(url.searchParams.get("exp") || "");
    if (!fp || !pid || !code) return null;
    if (!/^[0-9a-f]{64}$/.test(fp)) return null;
    if (!/^pair_[0-9a-f]+$/.test(pid)) return null;
    if (!/^[A-HJKMNP-Z2-9]{8}$/.test(code)) return null;
    if (!Number.isFinite(exp) || exp <= 0) return null;
    return {
      host: url.hostname,
      port: url.port ? Number(url.port) : null,
      desktopFingerprint: fp,
      pairingId: pid,
      code,
      expiresAtEpoch: Math.floor(exp),
    };
  } catch {
    return null;
  }
}

/** A QR payload is usable only while unexpired; tampering with any pinned
 *  field fails parse (shape checks) or the fingerprint binding at claim. */
export function pairingUriIsFresh(parsed: ParsedPairingUri, nowMs: number = Date.now()): boolean {
  return parsed.expiresAtEpoch * 1000 > nowMs;
}

export interface DesktopIdentityCheck {
  pinnedMatches: boolean;
  signatureValid: boolean;
}

/** Verify the desktop's signed claim receipt.
 *
 *  The QR pins the desktop's FINGERPRINT (sha256 of its Ed25519 public key) —
 *  a hash cannot verify a signature, so the claim response carries the public
 *  key itself and the pin binds it: sha256(public_key) must equal the pinned
 *  fingerprint, and the receipt signature must verify under that public key.
 *  Both must hold before the phone stores any credentials — this is the
 *  "mutual" half of mutual device identity. */
export function verifyDesktopProof(
  proof: DesktopProof,
  pinnedFingerprint: string,
  desktopPublicKeyHex: string,
): DesktopIdentityCheck {
  const payload = proof?.payload;
  if (!payload || payload.kind !== "vool.mobile.pairing.claim.v1") {
    return { pinnedMatches: false, signatureValid: false };
  }
  const pinned = String(pinnedFingerprint || "").toLowerCase();
  const publicKey = String(desktopPublicKeyHex || "").toLowerCase();
  const claimed = String(payload.desktop_fingerprint || "").toLowerCase();
  let pinnedMatches = false;
  try {
    pinnedMatches = pinned.length === 64 && publicKey.length === 64 &&
      toHex(sha256(fromHex(publicKey))) === pinned && claimed === pinned;
  } catch {
    pinnedMatches = false;
  }
  let signatureValid = false;
  try {
    signatureValid = verifyBytes(publicKey, canonicalBytes(payload), fromHex(String(proof.signature || "")));
  } catch {
    signatureValid = false;
  }
  return { pinnedMatches, signatureValid };
}

import { fromHex, sha256, toHex } from "./sha256.ts";
