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

// React Native's URL is a stub: hostname, port and searchParams THROW on a phone ("not implemented"),
// while Node's full URL made every test pass. Measured on Android 2026-10-27: every pairing QR read
// as "not a pairing code" and the scanner kept scanning in silence. So this parses the string itself.
const PAIRING_URI_RE = /^vool-pair:\/\/([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::(\d{1,5}))?\/?\?([^#]*)$/i;

export function queryParams(query: string): Map<string, string> {
  const params = new Map<string, string>();
  for (const pair of query.split("&")) {
    if (!pair) continue;
    const index = pair.indexOf("=");
    const key = index < 0 ? pair : pair.slice(0, index);
    const value = index < 0 ? "" : pair.slice(index + 1);
    try {
      const name = decodeURIComponent(key.replace(/\+/g, " "));
      if (!params.has(name)) params.set(name, decodeURIComponent(value.replace(/\+/g, " ")));
    } catch {
      // a malformed escape leaves that one parameter out
    }
  }
  return params;
}

export function parsePairingUri(uri: string): ParsedPairingUri | null {
  const text = String(uri || "").trim();
  const match = PAIRING_URI_RE.exec(text);
  if (!match) return null;
  const params = queryParams(match[3]);
  const fp = (params.get("fp") || "").toLowerCase();
  const pid = params.get("pid") || "";
  const code = params.get("code") || "";
  const exp = Number(params.get("exp") || "");
  const port = match[2] ? Number(match[2]) : null;
  if (!fp || !pid || !code) return null;
  if (!/^[0-9a-f]{64}$/.test(fp)) return null;
  if (!/^pair_[0-9a-f]+$/.test(pid)) return null;
  if (!/^[A-HJKMNP-Z2-9]{8}$/.test(code)) return null;
  if (!Number.isFinite(exp) || exp <= 0) return null;
  if (port !== null && (port < 1 || port > 65535)) return null;
  return {
    host: match[1].toLowerCase(),
    port,
    desktopFingerprint: fp,
    pairingId: pid,
    code,
    expiresAtEpoch: Math.floor(exp),
  };
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
