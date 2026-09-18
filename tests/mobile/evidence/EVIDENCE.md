# Mobile Companion P1 — Amendment Evidence Bundle

Branch `build/mobile-companion-p1-20260902`, base exactly `a7b78e2a`.
Commits: P1 `a289cd63` → P1 evidence `72ae6413` → **amendment `3cfd3576`** (this
bundle). All runs below are on the committed amendment tree (journey healthz:
`commit: 3cfd35765088`, `dirty: False`).

## CORRECTION OF PRIOR EVIDENCE (explicit)

The P1 evidence bundle (commit `72ae6413`) claimed mutual device identity.
**That claim was false as stated**: PairScreen generated an Ed25519 keypair but
discarded the private half, the stored `device_seed` was never used, and
`DeviceCredentials` retained only the bearer grant — the phone's key signed
nothing, ever. The inspection finding is confirmed. The P1 bundle's journey/
unit results remain accurate for what they tested (grant HMAC, replay shield,
revocation, receipts), but its identity wording overstated the phone side.
This amendment makes the device key load-bearing and re-proves everything.

## RED → GREEN

`amendment-RED.log`: after the server change and before test updates —
**30 failed / 9 passed** (Python; every pairing/claim/auth row now demanded
proof of possession) and **1 failed / 7 passed** (Node; QR payload without
`exp`). GREEN below.

## 1. Proof of possession (the corrected security core)

Unit rows (50 passed — `unit-tests.log`) include:
- forged public key during claim (attacker signs, victim key registers):
  401 `proof_of_possession_failed`, receipt `pairing.failed/proof_of_possession`,
  and NOTHING registered under the victim's device_id;
- claim without `device_proof` → 400;
- stolen grant without device key (correct HMAC, wrong/absent Ed25519):
  401 `device_signature_invalid`, receipted;
- stolen device key without live grant (revoked): 401 `device_revoked`;
  forged grant_id under a stolen key: 401;
- key loss forces re-pairing: new key ≠ old device_id; old grant rejects the
  new key's signatures; honest re-pair works;
- restart preserves identity: same seed → same device_id, grant survives
  service rehydration, device record carries the paired public key;
- replay + clock attacks still refused (409 `replay_detected`, 401
  `timestamp_window`).

Journey (`journey.log`, live daemon, real shared client code): the claim leg
verifies proof of possession; sabotage legs on the wire — forged public key
(401 `proof_of_possession_failed`) and stolen grant secret without the device
key (401 `device_signature_invalid`) — alongside the standing replay/revocation
legs. Full journey green: pair → status → photo attachment (sha256 match) →
send (real model lane, `done_reason: stop`) → history → Proof Chip VERIFIED →
approve AND refuse → notifications (17 events) → replay 409 → revoke 401 →
receipt chain verified (11 rows).

## 2. QR product flow

- `GET /api/mobile/pairing/page` (owner-local) serves a real QR surface: the
  vendored qrcode-generator (MIT) is inlined; unit test asserts the page embeds
  the code, fingerprint and expiry plus the renderer.
- The QR URI pins URL (host:port), pairing id, code, expiry (`exp=`) and
  fingerprint; unit test `test_qr_uri_pins_url_id_code_expiry_fingerprint`.
- Camera scanning (Expo `CameraView`, QR type) and the `vool-pair://` deep
  link both route through the SAME `parsePairingUri` — Node tests pin parse
  acceptance and every tamper/staleness refusal (`exp` non-numeric, ambiguous
  chars, non-hex fingerprint, stale expiry); the journey asserts the desktop
  page embeds the same payload the QR/deep-link parser reads.
- Manual address/pairing-id/code entry remains (journey + unit paths).

## 3. Build verification (no installed-device claim)

- `package-lock.json` committed — 917 resolved pins, deterministic.
- Clean-room `npm ci` (908 packages) + `tsc --noEmit`: **PASS**.
- `expo export --platform ios` (`expo-export-ios.log`): Hermes bundle,
  **572 modules**, sha256
  `c4e906730cefa61992158b8c4f7f93c33069940e517b6aa5612ed647c6864d15`.
- `expo export --platform android` (`expo-export-android.log`): Hermes bundle,
  **571 modules**, sha256
  `d3e61fb8a489211de0cd817629d8c5e6b23f13ba40f792ffe529ab47e57d7216`.
- Assets (`asset-hashes.txt`): the official VOOL mark (composed from the
  product's own rasterizer, `installer/assets/generate_icon.py`) as
  icon/adaptive-icon/splash; regeneration is byte-deterministic (verified).
- Xcode/Android SDK are absent on this machine — **no .ipa/.apk was signed or
  installed, and no installed-device behavior is claimed.**

## 4. Notification truth

- In-app polling is labelled as the verified channel (README + code comments).
- OS completion/failure notifications added: opt-in (`expo-notifications`,
  permission-requested), coarse non-sensitive text only. **PARTIAL** — real
  code, unproven delivery (no installed device).

## 5. Cumulative regression

- `tests/mobile/` — **51 passed** (50 unit + 1 live-daemon E2E journey).
- Node `tests/protocol.test.mjs` — **10 passed** (`node-protocol-tests.log`;
  FIPS vectors, Python-generated canonical-JSON golden vectors including the
  two NEW proof-payload vectors byte-pinned against the desktop's own
  canonicalization, randomized HMAC cross-checks, Ed25519 cross-impl).
- Adjacent suites: `test_chat_attachments_api.py` + `test_mode_permission_policy.py`
  — 37 passed, 0 failed. (`test_cloud_status_endpoint.py` 2F and
  `test_vool_api_server.py` 1F remain pre-existing at the exact base,
  stash-isolated in the P1 bundle.)
- Ruff on owned files: clean.
- The vendored TweetNaCl change (removing the Node `require('crypto')` PRNG
  branch so Metro bundles it; platform CSPRNG installed explicitly in
  `ed25519.ts`) is the only functional vendor delta and is called out in the
  file.

---

## Canonical integration record — 2026-09-04 (checkpoint 12)

Landed on `build/vool-final-integration-20260903` as `424fd096` (P1 companion: pairing, chats,
approvals, proof, notifications) and `1f69d21d` (the mandatory security + build amendment:
proof of possession, real QR, build proofs), plus a one-line lint fix. The only conflict was
`core/web/api/service.py`, where the mobile GET/POST route blocks sit alongside the command
registry and wallet blocks landed at earlier checkpoints; all three are kept.

`tests/mobile/` — **51 passed** in the canonical composition.

### What this does and does not establish

Integrated and exercised here: the pairing protocol, proof-of-possession, QR generation, token
revocation, the closed action catalog, and the companion server surface behind
`/api/mobile/*` — all through the same permission and approval authorities as the desktop, with
the shared Expo/TypeScript source carried in `mobile/`.

NOT claimed, and unchanged from the lane's own record: there is **no physical-device
installation, no push-delivery and no store distribution proof** in this wave. The companion is
integrated as protocol, server and shared source; a real handset pairing over a real relay,
push notifications actually delivered, and same-session mobile sensor workflows remain
UNMEASURED. Nothing in this checkpoint upgrades that — the tests run the protocol and server in
process, not a device.
