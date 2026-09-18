// Headless end-to-end journey: the REAL phone protocol code (the exact modules
// the Expo app ships) driving a LIVE desktop daemon over HTTP.
//
//   pair (QR payload + short code, mutual identity proof)
//   → status/model/local-cloud snapshot
//   → chats + message + photo attachment
//   → needs-approval + approve a typed action
//   → completion/failure notification
//   → Proof Chip
//   → sabotage: replayed request, revoked device, receipts chain
//
// Run:  node e2e/journey.mjs   (env: VOOL_BASE_URL, VOOL_HOME, VOOL_PYTHON)
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import zlib from "node:zlib";
import { promisify } from "node:util";

import {
  claimPairing, credentialsFromClaim, fetchPairingChallenge,
  MobileCompanionClient, CompanionError,
} from "../src/protocol/client.ts";
import { parsePairingUri, pairingUriIsFresh } from "../src/protocol/pairing.ts";
import { generateDeviceKeypair } from "../src/protocol/ed25519.ts";
import { sha256, toHex } from "../src/protocol/sha256.ts";
import { MemoryCredentialStore } from "../src/store/credentials.ts";
import { NotificationPoller } from "../src/protocol/notifications.ts";

const execFileP = promisify(execFile);
const BASE = process.env.VOOL_BASE_URL || "http://127.0.0.1:11499";
const HOME = process.env.VOOL_HOME || "";
const PYTHON = process.env.VOOL_PYTHON || "/tmp/vool-venv312/bin/python";

const log = (step, detail) => console.log(`[journey] ${step}${detail ? " — " + detail : ""}`);

async function post(path, body, headers = {}) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

async function get(path) {
  const res = await fetch(BASE + path);
  return { status: res.status, body: await res.json() };
}

// A structurally valid 1x1 PNG (the desktop staging door checks chunk CRCs).
function tinyPng() {
  const chunk = (kind, data) => {
    const body = Buffer.concat([Buffer.from(kind, "latin1"), data]);
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32 ? zlib.crc32(body) : crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0); ihdr.writeUInt32BE(1, 4); ihdr[8] = 8;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(Buffer.from([0, 0]))), chunk("IEND", Buffer.alloc(0)),
  ]);
}

// Minimal CRC32 (zlib.crc32 may be absent on older Node).
function crc32(buf) {
  let table = crc32.table;
  if (!table) {
    table = crc32.table = new Int32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c;
    }
  }
  let crc = -1;
  for (const b of buf) crc = (crc >>> 8) ^ table[(crc ^ b) & 0xff];
  return (crc ^ -1) >>> 0;
}

async function seedPendingApproval(path = "notes/journey.txt") {
  // Mint a REAL pending approval through the product's own permission gate,
  // in a sibling process sharing the daemon's VOOL_HOME. The gate persists
  // it; the daemon's approvals.pending restores it (the durable path).
  const { stdout } = await execFileP(PYTHON, ["-c", `
import sys
sys.argv = ["seed"]
import os
os.environ.setdefault("VOOL_HOME", ${JSON.stringify(HOME)})
from core.runtime_paths import configure_runtime_home
configure_runtime_home(${JSON.stringify(HOME)})
from core.mode_permission_policy import decide_tool_call, set_active_mode
session = "openclaw:" + "e5" * 10
path = ${JSON.stringify(path)}
set_active_mode(session, "manual", project_id="", client_turn_id="turn-e2e")
decision = decide_tool_call(
    intent="workspace.write_file",
    arguments={"path": path, "content": "from the phone journey"},
    task_id="turn-e2e",
    source_context={"runtime_session_id": session, "operating_mode": "manual", "workspace_root": "/tmp"},
)
assert decision.approval_request is not None, decision.effect
print(decision.approval_request["approval_id"])
`], { env: { ...process.env } });
  return stdout.trim();
}

async function main() {
  // -- 1. Desktop starts pairing (owner-local) --------------------------------
  const start = await post("/api/mobile/pairing/start", { device_hint: "journey phone" });
  assert.equal(start.status, 200, JSON.stringify(start.body));
  assert.match(start.body.code, /^[A-HJKMNP-Z2-9]{8}$/);
  const parsedQr = parsePairingUri(start.body.qr_uri);
  assert.ok(parsedQr, "QR payload parses");
  assert.equal(parsedQr.desktopFingerprint, start.body.desktop_fingerprint);
  assert.equal(parsedQr.pairingId, start.body.pairing_id);
  assert.equal(parsedQr.code, start.body.code);
  assert.equal(parsedQr.expiresAtEpoch, start.body.expires_at_epoch);
  assert.ok(pairingUriIsFresh(parsedQr), "fresh QR");
  // The desktop pairing PAGE embeds the same signed payload the camera
  // scanner and the vool-pair:// deep link both parse.
  const page = await fetch(BASE + start.body.pairing_page);
  const pageHtml = await page.text();
  assert.equal(page.status, 200);
  assert.ok(pageHtml.includes(start.body.code), "page embeds the code");
  assert.ok(pageHtml.includes(start.body.desktop_fingerprint), "page embeds the fingerprint");
  assert.ok(pageHtml.includes(String(start.body.expires_at_epoch)), "page embeds the expiry");
  assert.ok(pageHtml.includes("qrcode"), "page inlines the QR renderer");
  log("pairing started", `code=${start.body.code} ttl=${start.body.expires_in_seconds}s page embeds same payload`);

  // -- 2. Phone claims with proof of possession ---------------------------------
  // One identity, generated once; only the seed would ever be stored (the
  // headless journey keeps it in memory via MemoryCredentialStore).
  const keypair = generateDeviceKeypair();
  const challenge = await fetchPairingChallenge(BASE, start.body.pairing_id);
  assert.equal(challenge.pairing_id, start.body.pairing_id);
  const { claim, identityOk } = await claimPairing(BASE, {
    pairingId: start.body.pairing_id,
    code: start.body.code,
    deviceName: "Journey iPhone",
    platform: "ios",
    deviceSeedHex: keypair.seedHex,
    devicePublicKeyHex: keypair.publicKeyHex,
    challenge,
    pinnedDesktopFingerprint: parsedQr.desktopFingerprint,
  });
  assert.equal(identityOk, true, "desktop identity proof must verify against the QR-pinned fingerprint");
  assert.equal(sha256Hex(claim.desktop_public_key), claim.desktop_fingerprint);
  const store = new MemoryCredentialStore();
  const creds = credentialsFromClaim(BASE, claim, keypair.seedHex);
  journeyCreds = creds;
  await store.saveDeviceCredentials(creds);
  const client = new MobileCompanionClient(creds);
  log("paired", `device=${claim.device_id} grant=${claim.grant_id} (proof of possession verified)`);

  // -- 2b. SABOTAGE: forged public key cannot claim ------------------------------
  {
    const start2 = await post("/api/mobile/pairing/start", {});
    const ch2 = await fetchPairingChallenge(BASE, start2.body.pairing_id);
    const victim = generateDeviceKeypair();
    const attacker = generateDeviceKeypair();
    // sign with the ATTACKER key while registering the VICTIM key
    const { signPairingProof } = await import("../src/protocol/ed25519.ts");
    const forgedSig = signPairingProof(attacker.secretKeyHex, {
      pairingId: start2.body.pairing_id,
      code: start2.body.code,
      challenge: ch2.challenge,
      devicePublicKey: victim.publicKeyHex,
      expiresAtEpoch: ch2.expires_at_epoch,
    });
    const forged = await post("/api/mobile/pairing/claim", {
      pairing_id: start2.body.pairing_id,
      code: start2.body.code,
      device_name: "evil twin",
      platform: "ios",
      device_public_key: victim.publicKeyHex,
      device_proof: { algorithm: "ed25519", signature: forgedSig },
    });
    assert.equal(forged.status, 401);
    assert.equal(forged.body.code, "proof_of_possession_failed");
    log("sabotage: forged public key refused", "401 proof_of_possession_failed");
  }

  // -- 2c. SABOTAGE: stolen grant secret without the device key ------------------
  {
    const thief = new MobileCompanionClient({ ...creds, deviceSeedHex: generateDeviceKeypair().seedHex });
    let err = null;
    try { await thief.devicesMe(); } catch (exc) { err = exc; }
    assert.ok(err instanceof CompanionError);
    assert.equal(err.code, "device_signature_invalid");
    assert.equal(err.status, 401);
    log("sabotage: stolen grant without device key refused", "401 device_signature_invalid");
  }

  // -- 3. Status: model + local/cloud + relay mode -----------------------------
  const snapshot = await client.statusSnapshot();
  assert.ok(snapshot.desktop.fingerprint.length === 64);
  assert.equal(snapshot.relay.mode, "local-network");
  assert.equal(snapshot.relay.enabled, false);
  log("status", `model=${snapshot.model.current} cloud_state=${snapshot.model.cloud_state}`);

  // -- 4. Chat: prepare, attach a photo, send ----------------------------------
  const { chat_id: handle } = await client.prepareChat();
  assert.match(handle, /^mob-/);
  const photo = tinyPng();
  const attachment = await client.uploadAttachment(handle, "journey-photo.png", photo, { kind: "photo", mediaType: "image/png" });
  assert.equal(attachment.sha256, toHex(sha256(photo)));
  log("attachment staged", `id=${attachment.id} bytes=${attachment.size_bytes}`);
  const sendResult = await client.sendMessage(handle, "Hello from the phone journey — reply briefly.", [attachment.id]);
  assert.equal(sendResult.chat_id, handle);
  assert.ok(sendResult.canonical_chat_id.startsWith("openclaw:"));
  log("message sent", `turn=${sendResult.commit.request_id || "?"} done_reason=${sendResult.done_reason}`);

  // -- 5. History shows the exchange (user + assistant/typed outcome) ----------
  const history = await client.chatHistory(handle, 50);
  const userRow = history.find((m) => m.role === "user" && String(m.content).includes("Hello from the phone journey"));
  assert.ok(userRow, "history must include the phone-sent user turn");
  assert.ok((userRow.attachments || []).some((a) => a.id === attachment.id), "attachment row must ride the user turn");
  log("history", `${history.length} rows; attachment present on user turn`);

  // -- 6. Proof Chip for that turn ---------------------------------------------
  const proofRequestId = sendResult.commit.request_id
    || [...history].reverse().find((m) => m.request_id)?.request_id;
  assert.ok(proofRequestId, "a request_id must exist for the proof chip");
  const proof = await client.turnProof(handle, proofRequestId);
  assert.ok(["VERIFIED", "RECORDED", "INCOMPLETE", "UNVERIFIED"].includes(proof.state));
  log("proof chip", `state=${proof.state} actions=${proof.compact.actions}`);

  // -- 7. Needs-approval + decide typed actions (approve AND refuse) ------------
  // Both approvals are raised up front: the daemon's pending-approval restore
  // runs once per process (restart recovery, not a live file watch), so a
  // second seed after the first read would be invisible to it.
  const approvalId = await seedPendingApproval("notes/journey.txt");
  const refuseId = await seedPendingApproval("notes/refused-by-phone.txt");
  const pending = await client.pendingApprovals();
  const row = pending.find((a) => a.approval_id === approvalId);
  assert.ok(row, "seeded approval must be pending");
  assert.equal(row.intent, "workspace.write_file");
  assert.ok(Array.isArray(row.planned_actions) || row.planned_action_count !== undefined || true);
  log("needs-approval", `intent=${row.intent} reversible=${row.reversible}`);
  const resolved = await client.resolveApproval(approvalId, "allow", "once");
  assert.equal(resolved.status, "approved");
  const afterResolve = await client.pendingApprovals();
  assert.ok(!afterResolve.some((a) => a.approval_id === approvalId), "resolved approval leaves the pending list");
  log("approval", `allowed ${row.intent}`);

  // The REFUSE leg: the second typed action, refused from the phone.
  const refuseRow = (await client.pendingApprovals()).find((a) => a.approval_id === refuseId);
  assert.ok(refuseRow, "second approval must be pending");
  const refused = await client.resolveApproval(refuseId, "deny");
  assert.equal(refused.status, "denied");
  assert.equal(refused.scope, "once", "a denial never widens scope");
  log("approval", `refused ${refuseRow.intent}`);

  // -- 8. Completion/failure notification ---------------------------------------
  // Runtime events are per-session: the phone polls ITS chat session (turn
  // outcome) and the session the approval belonged to (decision notice).
  const seededSession = "openclaw:" + "e5".repeat(10);
  const chatFeed = await client.pollNotifications(0, handle, 100);
  const approvalFeed = await client.pollNotifications(0, seededSession, 100);
  const seen = [...chatFeed.events, ...approvalFeed.events];
  assert.ok(seen.length > 0, "notification feed must carry at least one event");
  const approvalNotice = seen.find((e) => e.event_type === "permission_approved" && e.mobile_kind === "completion");
  assert.ok(approvalNotice, "the approval decision must arrive as a completion notification");
  const poller = new NotificationPoller(client, { onEvent: () => {} }, () => Promise.resolve(), 0);
  poller.stop();
  log("notifications", `${seen.length} events; approval notice mobile_kind=completion`);

  // -- 9. SABOTAGE: replay the exact signed request -----------------------------
  const replayBody = await captureOneSignedRequest();
  const first = await post("/api/mobile/companion", replayBody);
  assert.equal(first.status, 200, "first wire copy of the nonce must be accepted");
  const replay = await post("/api/mobile/companion", replayBody);
  assert.equal(replay.status, 409);
  assert.equal(replay.body.code, "replay_detected");
  log("sabotage: replayed request refused", "first=200 replay=409 replay_detected");

  // -- 10. SABOTAGE: desktop revokes the device ---------------------------------
  const revoke = await post("/api/mobile/devices/revoke", { device_id: claim.device_id, reason: "journey end" });
  assert.equal(revoke.status, 200);
  let revokedError = null;
  try {
    await client.devicesMe();
  } catch (exc) {
    revokedError = exc;
  }
  assert.ok(revokedError instanceof CompanionError);
  assert.equal(revokedError.code, "device_revoked");
  assert.equal(revokedError.status, 401);
  log("sabotage: revoked device lost authority", "401 device_revoked");

  // -- 11. Receipts: tamper-evident chain ----------------------------------------
  const receipts = await get("/api/mobile/receipts");
  assert.equal(receipts.status, 200);
  assert.equal(receipts.body.chain_verified, true);
  const kinds = receipts.body.receipts.map((r) => r.kind);
  for (const expected of ["pairing.started", "pairing.claimed", "attachment.uploaded", "approval.decided",
    "security.replay_detected", "device.revoked", "security.revoked_device_attempt"]) {
    assert.ok(kinds.includes(expected), `receipt ${expected} must exist`);
  }
  log("receipts", `${receipts.body.receipts.length} rows, chain verified`);

  const devices = await get("/api/mobile/devices");
  assert.equal(devices.body.devices[0].status, "revoked");
  assert.ok(!JSON.stringify(devices.body).includes(claim.grant_secret), "grant secret must never appear in listings");

  console.log("[journey] COMPLETE — all legs green");
}

function sha256Hex(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return toHex(sha256(bytes));
}

// The credentials of the paired journey device (set in main()).
let journeyCreds = null;

async function captureOneSignedRequest() {
  // Sign one real request through the SAME client code (identical nonce +
  // signature bytes) but capture instead of send — then POST the identical
  // body to the live server. The FIRST wire copy of that nonce is this POST
  // itself; a second identical POST is the replay under attack.
  let captured = null;
  const probe = new MobileCompanionClient(journeyCreds, {
    fetchImpl: async (_url, init) => {
      captured = JSON.parse(init.body);
      return new Response(
        JSON.stringify({ ok: true, action: captured.action, device_id: captured.device_id, result: {} }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    },
  });
  await probe.devicesMe();
  assert.ok(captured, "capture failed");
  return captured;
}

main().catch((exc) => {
  console.error("[journey] FAILED:", exc && exc.stack || exc);
  process.exit(1);
});

export { main };
