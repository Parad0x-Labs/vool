// MobileCompanionClient — the phone's single door to the desktop.
//
// Every companion request is doubly proven, over the same canonical bytes:
//   1. the grant HMAC (HMAC-SHA256 with the grant bearer secret — the Device
//      Link request surface the desktop registry verifies), and
//   2. an Ed25519 signature by THIS phone's registered key (proof the physical
//      device is present — a stolen grant secret alone cannot talk).
// The fetch transport is injectable so the headless Node journey and the React
// Native app share literally this code.
import { canonicalBytes } from "./canonical.ts";
import { hmacSha256, randomBytes, toHex } from "./sha256.ts";
import { keypairFromSeed, signRequestProof } from "./ed25519.ts";
import type {
  ApprovalRecord, ChatMessage, ChatSummary, CompanionResponse, DeviceInfo,
  NotificationEvent, PairingChallenge, PairingClaimResponse, PendingApproval,
  PollResult, SendResult, StatusSnapshot, TurnProof, AttachmentRecord,
} from "./models.ts";

export interface DeviceCredentials {
  baseUrl: string;
  deviceId: string;
  grantId: string;
  grantSecret: string;
  /** The phone's Ed25519 seed (64 hex chars). The private half of the device
   *  identity — keystore-only, never on the wire after pairing. */
  deviceSeedHex: string;
  desktopFingerprint: string;
  pairedAt: string;
}

export type FetchLike = (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => Promise<{ status: number; json(): Promise<unknown> }>;

export interface ClientOptions {
  fetchImpl?: FetchLike;
  now?: () => Date;
}

export class CompanionError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message || code);
    this.status = status;
    this.code = code;
  }
}

export class MobileCompanionClient {
  private readonly creds: DeviceCredentials;
  private readonly doFetch: FetchLike;
  private readonly now: () => Date;
  private nonceCounter = 0;
  /** Derived once from the stored seed — the signing key for request proofs. */
  private readonly keypair: ReturnType<typeof keypairFromSeed>;

  constructor(credentials: DeviceCredentials, options: ClientOptions = {}) {
    this.creds = credentials;
    this.doFetch = options.fetchImpl ?? defaultFetch;
    this.now = options.now ?? (() => new Date());
    this.keypair = keypairFromSeed(credentials.deviceSeedHex);
  }

  get deviceId(): string { return this.creds.deviceId; }
  get baseUrl(): string { return this.creds.baseUrl; }
  get desktopFingerprint(): string { return this.creds.desktopFingerprint; }

  private async request<T>(action: string, params: Record<string, unknown> = {}): Promise<T> {
    const nonce = toHex(randomBytes(16)) + "-" + (++this.nonceCounter);
    const timestamp = this.now().toISOString();
    const payload = {
      grant_id: this.creds.grantId,
      verb: "vool.action",
      params: { action, ...params },
      nonce,
      timestamp,
    };
    const signature = toHex(hmacSha256(
      new TextEncoder().encode(this.creds.grantSecret),
      canonicalBytes(payload),
    ));
    const deviceSignature = signRequestProof(this.keypair.secretKeyHex, {
      deviceId: this.creds.deviceId,
      action,
      params,
      nonce,
      timestamp,
      grantId: this.creds.grantId,
    });
    const body = {
      device_id: this.creds.deviceId,
      grant_id: this.creds.grantId,
      action,
      params,
      nonce,
      timestamp,
      signature,
      device_signature: deviceSignature,
    };
    const response = await this.doFetch(this.creds.baseUrl.replace(/\/$/, "") + "/api/mobile/companion", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const parsed = (await response.json()) as CompanionResponse<T>;
    if (!parsed || parsed.ok !== true) {
      const err = parsed as { error?: string; code?: string };
      throw new CompanionError(response.status, err?.code || "unknown", err?.error || "companion request failed");
    }
    return parsed.result;
  }

  // -- actions ---------------------------------------------------------------

  devicesMe(): Promise<DeviceInfo> { return this.request<DeviceInfo>("devices.me"); }
  statusSnapshot(): Promise<StatusSnapshot> { return this.request<StatusSnapshot>("status.snapshot"); }

  async listChats(): Promise<ChatSummary[]> {
    const r = await this.request<{ chats: ChatSummary[]; total: number }>("chats.list");
    return r.chats;
  }

  prepareChat(): Promise<{ chat_id: string; canonical_chat_id: string }> {
    return this.request<{ chat_id: string; canonical_chat_id: string }>("chats.prepare");
  }

  async chatHistory(chatId: string, limit = 100): Promise<ChatMessage[]> {
    const r = await this.request<{ chat_id: string; messages: ChatMessage[] }>("chats.history", { chat_id: chatId, limit });
    return r.messages;
  }

  sendMessage(chatId: string | null, message: string, attachments: string[] = []): Promise<SendResult> {
    const params: Record<string, unknown> = { message };
    if (chatId) params.chat_id = chatId;
    if (attachments.length) params.attachments = attachments;
    return this.request<SendResult>("chats.send", params);
  }

  attachmentLimits(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("attachments.limits");
  }

  async uploadAttachment(chatId: string, filename: string, bytes: Uint8Array, opts: { mediaType?: string; kind?: "photo" | "file" } = {}): Promise<AttachmentRecord> {
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    const b64 = btoa(binary);
    const r = await this.request<{ attachment: AttachmentRecord }>("attachments.upload", {
      chat_id: chatId,
      filename,
      content_b64: b64,
      media_type: opts.mediaType ?? "",
      kind: opts.kind ?? "file",
    });
    return r.attachment;
  }

  async pendingApprovals(): Promise<PendingApproval[]> {
    const r = await this.request<{ approvals: PendingApproval[]; needs_approval: boolean }>("approvals.pending");
    return r.approvals;
  }

  resolveApproval(approvalId: string, decision: "allow" | "deny", scope: "once" | "task" | "request" | "project" = "once"): Promise<ApprovalRecord> {
    return this.request<{ approval: ApprovalRecord; decision: string }>(
      "approvals.resolve",
      { approval_id: approvalId, decision, scope },
    ).then(r => r.approval);
  }

  turnProof(chatId: string, requestId: string): Promise<TurnProof> {
    return this.request<{ proof: TurnProof }>("proof.get", { chat_id: chatId, request_id: requestId }).then(r => r.proof);
  }

  async pollNotifications(after = 0, session?: string, limit = 60): Promise<{ events: NotificationEvent[]; nextAfter: number }> {
    const params: Record<string, unknown> = { after, limit };
    if (session) params.session = session;
    const r = await this.request<PollResult>("notifications.poll", params);
    return { events: r.events, nextAfter: r.next_after };
  }
}

function defaultFetch(url: string, init: { method: string; headers: Record<string, string>; body?: string }) {
  return fetch(url, init);
}

// ---------------------------------------------------------------------------
// Pairing flow (static — works before any credentials exist)
// ---------------------------------------------------------------------------

export async function fetchPairingChallenge(baseUrl: string, pairingId: string, fetchImpl?: FetchLike): Promise<PairingChallenge> {
  const doFetch = fetchImpl ?? defaultFetch;
  const response = await doFetch(
    baseUrl.replace(/\/$/, "") + "/api/mobile/pairing/challenge?pairing_id=" + encodeURIComponent(pairingId),
    { method: "GET", headers: { accept: "application/json" } },
  );
  const parsed = (await response.json()) as PairingChallenge | { ok: false; error?: string; code?: string };
  if (!parsed || parsed.ok !== true) {
    const err = parsed as { error?: string; code?: string };
    throw new CompanionError(response.status, err?.code || "unknown", err?.error || "challenge fetch failed");
  }
  return parsed;
}

export interface ClaimPairingArgs {
  pairingId: string;
  code: string;
  deviceName: string;
  platform: string;
  /** The phone's Ed25519 identity; the seed stays in the keystore. */
  deviceSeedHex: string;
  devicePublicKeyHex: string;
  /** Fetched via fetchPairingChallenge (server-held values). */
  challenge: PairingChallenge;
  pinnedDesktopFingerprint?: string;
  fetchImpl?: FetchLike;
}

export async function claimPairing(baseUrl: string, opts: ClaimPairingArgs): Promise<{ claim: PairingClaimResponse; identityOk: boolean }> {
  const doFetch = opts.fetchImpl ?? defaultFetch;
  const { signPairingProof, keypairFromSeed: fromSeed } = await import("./ed25519.ts");
  const proof = signPairingProof(fromSeed(opts.deviceSeedHex).secretKeyHex, {
    pairingId: opts.pairingId,
    code: opts.code,
    challenge: opts.challenge.challenge,
    devicePublicKey: opts.devicePublicKeyHex,
    expiresAtEpoch: opts.challenge.expires_at_epoch,
  });
  const response = await doFetch(baseUrl.replace(/\/$/, "") + "/api/mobile/pairing/claim", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      pairing_id: opts.pairingId,
      code: opts.code,
      device_name: opts.deviceName,
      platform: opts.platform,
      device_public_key: opts.devicePublicKeyHex,
      device_proof: { algorithm: "ed25519", signature: proof },
    }),
  });
  const parsed = (await response.json()) as PairingClaimResponse | { ok: false; error?: string; code?: string };
  if (!parsed || parsed.ok !== true) {
    const err = parsed as { error?: string; code?: string };
    throw new CompanionError(response.status, err?.code || "unknown", err?.error || "pairing claim failed");
  }
  const { verifyDesktopProof } = await import("./pairing.ts");
  const pinned = opts.pinnedDesktopFingerprint || opts.challenge.desktop_fingerprint;
  const check = verifyDesktopProof(parsed.desktop_proof, pinned, parsed.desktop_public_key);
  return { claim: parsed, identityOk: check.pinnedMatches && check.signatureValid };
}

export function credentialsFromClaim(baseUrl: string, claim: PairingClaimResponse, deviceSeedHex: string): DeviceCredentials {
  return {
    baseUrl,
    deviceId: claim.device_id,
    grantId: claim.grant_id,
    grantSecret: claim.grant_secret,
    deviceSeedHex,
    desktopFingerprint: claim.desktop_fingerprint,
    pairedAt: new Date().toISOString(),
  };
}
