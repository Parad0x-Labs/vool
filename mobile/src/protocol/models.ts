// Typed API models — the one shared contract between the phone app and the
// desktop (core/web/api/mobile_companion_api.py). Field names are the wire
// names; nothing here re-derives or renames.

export interface PairingStartResponse {
  ok: true;
  pairing_id: string;
  code: string;
  expires_in_seconds: number;
  expires_at_epoch: number;
  challenge: string;
  desktop_fingerprint: string;
  desktop_short_fingerprint: string;
  qr_uri: string;
  pairing_page: string;
  manual_entry: { pairing_id: string; code: string };
}

/** Server-held pre-claim values the phone signs in its proof of possession. */
export interface PairingChallenge {
  ok: true;
  pairing_id: string;
  challenge: string;
  expires_at_epoch: number;
  desktop_fingerprint: string;
  desktop_public_key: string;
}

export interface DesktopProof {
  payload: {
    kind: "vool.mobile.pairing.claim.v1";
    pairing_id: string;
    device_id: string;
    grant_id: string;
    desktop_fingerprint: string;
  };
  signature: string;
}

export interface PairingClaimResponse {
  ok: true;
  device_id: string;
  grant_id: string;
  grant_secret: string;
  envelope: string;
  scope_kind: string;
  desktop_fingerprint: string;
  desktop_short_fingerprint: string;
  desktop_public_key: string;
  desktop_proof: DesktopProof;
}

export interface CompanionOk<T> {
  ok: true;
  action: string;
  device_id: string;
  result: T;
}

export interface CompanionError {
  ok: false;
  error: string;
  code: string;
}

export type CompanionResponse<T> = CompanionOk<T> | CompanionError;

export interface DeviceInfo {
  device_id: string;
  name: string;
  platform: string;
  status: string;
  paired_at: string;
  last_seen_at: string;
  desktop_fingerprint: string;
  desktop_short_fingerprint: string;
}

export interface ChatSummary {
  chat_id: string;
  title: string | null;
  updated_at: string | null;
  turn_count: number | null;
  archived: boolean;
}

export interface ChatMessage {
  role: string;
  content: string;
  attachments?: Array<{ id: string; name: string; kind: string; media_type: string; size_bytes: number }>;
  ts?: string;
  answer_state?: string;
  request_id?: string;
}

export interface SendResult {
  chat_id: string;
  reply: string;
  done_reason: string | null;
  commit: { type?: string; request_id?: string };
  model: string | null;
}

export interface AttachmentRecord {
  id: string;
  session_id: string;
  name: string;
  kind: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  state: string;
  turn_id: string | null;
  created_at: string;
  outcome: string | null;
}

export interface PendingApproval {
  approval_id: string;
  task_id: string | null;
  intent: string | null;
  action: string | null;
  affected_resources: string[] | null;
  expected_side_effects: string[] | null;
  reversible: boolean | null;
  scope_options: string[] | null;
  planned_actions: Array<Record<string, string>> | null;
  planned_action_count: number | null;
  diff_preview: string | null;
  raised_at: number | string | null;
  expires_at: number | string | null;
}

export interface ApprovalRecord {
  approval_id: string;
  status: string;
  scope: string;
  intent: string | null;
  task_id: string | null;
  [key: string]: unknown;
}

export interface StatusSnapshot {
  desktop: {
    display_name: string;
    runtime_started_at: string | null;
    version: Record<string, unknown>;
    fingerprint: string;
    short_fingerprint: string;
  };
  model: {
    current: string;
    cloud_model: string;
    cloud_state: string;
    cloud_detail: string;
    checked_at: string;
  };
  relay: { enabled: boolean; mode: string };
  proof_chip_note: string;
}

export interface TurnProof {
  schema: string;
  bound: boolean;
  session_id: string;
  request_id: string;
  state: "VERIFIED" | "RECORDED" | "INCOMPLETE" | "UNVERIFIED";
  state_reasons: string[];
  compact: { actions: number; sources: number; cost: { tokens: unknown; usd: unknown; source: string }; state: string };
  expanded?: Record<string, unknown>;
}

export interface NotificationEvent {
  seq?: number;
  event_type: string;
  message?: string;
  turn_key?: string;
  session_id?: string;
  mobile_kind: "completion" | "failure" | "info";
  [key: string]: unknown;
}

export interface PollResult {
  events: NotificationEvent[];
  next_after: number;
  session: string;
}
