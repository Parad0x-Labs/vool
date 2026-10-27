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
import {connectionAddress} from './connection.ts';
import { hmacSha256, randomBytes, toHex } from "./sha256.ts";
import { keypairFromSeed, signRequestProof } from "./ed25519.ts";
import type {
  ApprovalRecord, ChatMessage, ChatSummary, CompanionResponse, DeviceInfo,
  NotificationEvent, PairingChallenge, PairingClaimResponse, PendingApproval,
  PollResult, SendResult, StatusSnapshot, TurnProof, AttachmentRecord, NotificationPreferences, SentFile,
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
  approvalKeyAlias?: string;
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
  private creds: DeviceCredentials;
  private readonly doFetch: FetchLike;
  private readonly now: () => Date;
  private nonceCounter = 0;
  /** Derived once from the stored seed — the signing key for request proofs. */
  private readonly keypair: ReturnType<typeof keypairFromSeed>;

  constructor(credentials: DeviceCredentials, options: ClientOptions = {}) {
    this.creds = {...credentials};
    this.doFetch = options.fetchImpl ?? defaultFetch;
    this.now = options.now ?? (() => new Date());
    this.keypair = keypairFromSeed(credentials.deviceSeedHex);
  }

  get deviceId(): string { return this.creds.deviceId; }
  get baseUrl(): string { return this.creds.baseUrl; }
  get desktopFingerprint(): string { return this.creds.desktopFingerprint; }
  setConnectionAddress(value: string) {this.creds = {...this.creds, baseUrl: connectionAddress(value)};}

  protected async request<T>(action: string, params: Record<string, unknown> = {}): Promise<T> {
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

  /** Phone-initiated unpair: revokes THIS device on the desktop (the server
   *  ignores any device_id in params — only the calling device is targeted).
   *  After it resolves, every later request fails device_revoked. */
  unpair(): Promise<{ device_id: string; status: string; revoked_at?: string }> {
    return this.request<{ device_id: string; status: string; revoked_at?: string }>("devices.unpair");
  }

  statusSnapshot(): Promise<StatusSnapshot> { return this.request<StatusSnapshot>("status.snapshot"); }

  async listChats(search = ""): Promise<ChatSummary[]> {
    const r = await this.request<{ chats: ChatSummary[]; total: number }>("chats.list", search ? {search} : {});
    return r.chats;
  }

  prepareChat(): Promise<{ chat_id: string; canonical_chat_id: string }> {
    return this.request<{ chat_id: string; canonical_chat_id: string }>("chats.prepare");
  }

  async chatHistory(chatId: string, limit = 100): Promise<ChatMessage[]> {
    const r = await this.request<{ chat_id: string; messages: ChatMessage[] }>("chats.history", { chat_id: chatId, limit });
    return r.messages;
  }

  sendMessage(chatId: string | null, message: string, attachments: string[] = [], idempotencyKey?: string): Promise<SendResult> {
    const params: Record<string, unknown> = { message };
    if (chatId) params.chat_id = chatId;
    if (attachments.length) params.attachments = attachments;
    if (idempotencyKey) params.idempotency_key = idempotencyKey;
    return this.request<SendResult>("chats.send", params);
  }

  turnProgress(key: string, after = 0): Promise<{events: NotificationEvent[]; next_after: number}> {
    return this.request("chats.progress", {idempotency_key: key, after});
  }

  cancelTurn(key: string): Promise<{state: string}> {
    return this.request("chats.cancel", {idempotency_key: key});
  }

  attachmentLimits(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("attachments.limits");
  }

  async uploadAttachment(chatId: string, filename: string, bytes: Uint8Array, opts: { mediaType?: string; kind?: "photo" | "file"; ability?: "camera" | "photos" } = {}): Promise<AttachmentRecord> {
    let binary = "";
    for (const b of bytes) binary += String.fromCharCode(b);
    const b64 = btoa(binary);
    const r = await this.request<{ attachment: AttachmentRecord }>("attachments.upload", {
      chat_id: chatId,
      filename,
      content_b64: b64,
      media_type: opts.mediaType ?? "",
      kind: opts.kind ?? "file",
      ability: opts.ability,
    });
    return r.attachment;
  }

  async listSentFiles(): Promise<SentFile[]> {
    const r = await this.request<{ files: SentFile[] }>("files.list");
    return r.files ?? [];
  }

  /** The whole file the computer sent, as base64: pieces are a multiple of 3 bytes, so they join as text. */
  async fetchSentFile(fileId: string): Promise<{ file: SentFile; base64: string }> {
    let offset = 0;
    const parts: string[] = [];
    for (;;) {
      const r = await this.request<{ file: SentFile; content_b64: string; next_offset: number; done: boolean }>(
        "files.fetch", {file_id: fileId, offset});
      parts.push(r.content_b64);
      if (r.done || r.next_offset <= offset) return { file: r.file, base64: parts.join("") };
      offset = r.next_offset;
    }
  }

  checkAbility(ability: "camera" | "photos" | "location" | "microphone"): Promise<{allowed:boolean}> {
    return this.request("abilities.check", {ability});
  }
  dictate(bytes: Uint8Array): Promise<{text:string}> {
    let binary = ""; for (const b of bytes) binary += String.fromCharCode(b);
    return this.request("abilities.dictate", {content_b64:btoa(binary),media_type:"audio/mp4"});
  }
  locationDraft(latitude:number, longitude:number): Promise<{text:string}> {
    return this.request("abilities.location", {latitude:latitude.toFixed(6),longitude:longitude.toFixed(6)});
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

  async pollNotifications(after = 0, session?: string, limit = 60): Promise<{ events: NotificationEvent[]; nextAfter: number; badge?: number; attention?: boolean }> {
    const params: Record<string, unknown> = { after, limit };
    if (session) params.session = session;
    const r = await this.request<PollResult>("notifications.poll", params);
    return { events: r.events, nextAfter: r.next_after,
      ...(typeof r.badge === "number" ? {badge: r.badge, attention: r.attention} : {})};
  }

  notificationPreferences(): Promise<{preferences: NotificationPreferences; types: string[]; mandatory: string[]; alerts_allowed: boolean}> {
    return this.request("notifications.preferences.get");
  }
  setNotificationPreferences(change: Record<string, unknown>): Promise<{preferences: NotificationPreferences; mandatory: string[]}> {
    return this.request("notifications.preferences.set", change);
  }
  notificationFeed(before = 0): Promise<{events: NotificationEvent[]; badge: number; next_before?: number}> {
    return this.request("notifications.feed", {records: true, before});
  }
  markNotificationsRead(): Promise<{badge: number}> {return this.request("notifications.read");}

  walletStatus(): Promise<{status: {enabled: boolean; accounts?: {wallet_id: string; label: string; network: string}[];
    pending?: {proposal_id: string; mobile_owned?: boolean; amount_human?: string; display_symbol?: string; destination: string; network: string; state: string}[]}; frozen: boolean}> {
    return this.request("wallet.status");
  }
  walletBalance(walletId: string): Promise<{state: string; balance_human?: string; asset?: string; reason?: string}> {
    return this.request("wallet.balances", {wallet_id: walletId});
  }
  walletReceipts(): Promise<{receipts: Record<string, unknown>[]}> {return this.request("wallet.receipts");}
  walletContacts(search: string): Promise<{contacts: {display_name: string}[]}> {return this.request("wallet.contacts", {search});}
  previewPayment(params: {destination:string; amount:string; idempotency_key:string}): Promise<import("./paymentDraft.ts").PaymentReview> {
    return this.request("wallet.preview", params);
  }
  proposePayment(params: {destination: string; amount: string; idempotency_key: string; review_token: string; warnings_acknowledged: string[]}): Promise<{proposal: {proposal_id: string}; display_amount: string; asset: string; message: string}> {
    return this.request("wallet.propose", params);
  }
  declinePayment(proposalId: string): Promise<{cancelled?: boolean; already_sent?: boolean; reason?: string}> {
    return this.request("wallet.decline", {proposal_id: proposalId});
  }
  freezePayments(): Promise<{frozen: boolean; message: string}> {return this.request("wallet.freeze");}
  pauseRights(rights: string[]): Promise<{rules: Record<string, boolean>}> {return this.request("devices.switch_off", {rights});}
  lostComputer(): Promise<{clear_credentials: boolean}> {return this.request("devices.lost_computer");}
}

export async function defaultFetch(url: string, init: { method: string; headers: Record<string, string>; body?: string }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, {...init, signal: controller.signal, redirect: "error"});
    const body: unknown = await response.json();
    return {status: response.status, json: async () => body};
  } finally {clearTimeout(timer);}
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
  approvalKey?: Record<string, unknown> | null;
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
      approval_key: opts.approvalKey || null,
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
