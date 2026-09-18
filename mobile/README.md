# VOOL Mobile Companion

One shared React Native (Expo) codebase for iOS and Android that pairs with a
VOOL desktop over the local network and acts as the operator's pocket surface:
chats, typed-action approvals, model/local-cloud status with the Proof Chip,
and completion/failure notifications.

## Security model

**Device identity is load-bearing — proof of possession, both directions:**

- The phone's Ed25519 keypair is generated ONCE from securely random seed
  material; only the private seed is stored (platform keystore/keychain via
  `expo-secure-store`, `WHEN_UNLOCKED_THIS_DEVICE_ONLY`), and it never crosses
  the network. `device_id = "phone:" + sha256(public_key)[:20]`.
- **Pairing**: the desktop mints a short-lived (default 5 min, clamp 60–900),
  single-use 8-character code, a per-session challenge, and a QR payload
  (`vool-pair://host:port?fp=<desktop fingerprint>&pid=..&code=..&exp=..`).
  The phone fetches the challenge and signs — with its private key — the
  canonical {challenge, pairing id, code, phone public key, expiry} payload.
  The desktop verifies BEFORE issuing any grant: a forged or replayed public
  key cannot produce that signature (401 `proof_of_possession_failed`,
  receipted). The claim response is signed by the desktop's Ed25519 authority
  key and carries the public key; the phone pins the QR fingerprint
  (sha256(key) == pin) and verifies.
- **Every request** carries TWO proofs over the same canonical bytes (device
  id, action, parameters digest, nonce, timestamp, grant identity): the grant
  HMAC (Device Link request surface, nonce replay shield, ±120 s window) AND
  an Ed25519 signature by the registered key, verified against the key IN THE
  DEVICE RECORD (never a request-supplied key). A stolen grant secret without
  the phone is useless; a stolen phone key without a live grant is useless.
- **Revocation** kills both factors instantly and durably (restarts rehydrate
  the revoked registry from `data/mobile_companion/state.json`).
- **Reinstall / key loss** destroys the identity by design: a new key is a new
  `device_id` and must re-pair.
- **The closed action catalog** (`COMPANION_ACTIONS` in
  `core/web/api/mobile_companion_api.py`) is the phone's entire surface:
  chats, attachments, approvals, status, proof, notifications, devices.me.
  No filesystem, no terminal, no signer, no credential reach of any kind.
- **Secrets**: the phone holds exactly two secrets, both keystore-only — the
  grant bearer secret and its own Ed25519 seed. API/provider/model keys never
  leave the desktop; device listings never echo either secret.
- **Receipts**: every security-relevant event is appended to a hash-chained
  journal (`GET /api/mobile/receipts`), tamper-detecting.
- **Local-network first**: the companion talks to the same daemon on the
  machine's interface; binding beyond loopback is the operator's explicit act
  (`--bind` + `VOOL_ALLOWED_HOSTS`). A remote relay is disabled unless
  explicitly configured (`VOOL_MOBILE_RELAY_ENABLED=1`); while disabled, any
  request declaring relay provenance is refused fail-closed and receipted.
- **Session-continuity law**: `/api/chat` resumes canonical desktop ids only
  for owner-local clients (anti-spoofing). The phone holds `mob-…` handles;
  the server derives the canonical session deterministically per handle.

## QR product flow

- The desktop pairing surface (`GET /api/mobile/pairing/page?pairing_id=…`,
  owner-local) renders a REAL QR image client-side (vendored
  qrcode-generator, MIT, inlined — no CDN) plus the manual code, pairing id
  and expiry countdown.
- The phone scans it with Expo Camera (`CameraView`, QR barcode type), opens
  it via the `vool-pair://` deep link (registered scheme; typed handler in
  `App.tsx` → the same `parsePairingUri` the scanner uses), or types the
  address/pairing id/code by hand.
- The QR pins desktop URL, pairing id, code, expiry and fingerprint; any
  tampered field fails parse or the fingerprint binding at claim; an expired
  payload refuses locally before any network call.

## Notification truth (honest labels)

- **In-app polling is the verified channel**: the notification poller reads
  the desktop's typed event stream (`notifications.poll`) and classifies
  completion/failure/attention. No push server is involved or needed for the
  local-network companion.
- **OS notifications are an OPT-IN layer** (`src/notifications/osNotifications.ts`,
  `expo-notifications` local notifications, permission-requested): real code,
  coarse non-sensitive text only ("A turn finished on your desktop." — never
  task content, because lock screens echo notification text). **PARTIAL**:
  delivery has NOT been proven on an installed device — no iOS/Android build
  exists in this environment.

## Layout

```
mobile/
  App.tsx                  navigation shell + vool-pair:// deep-link handler
  src/protocol/            PURE TypeScript — the exact code the headless proof runs
    canonical.ts           Python-byte-compatible canonical JSON
    sha256.ts              SHA-256 + HMAC-SHA256 (no WebCrypto dependency)
    ed25519.ts             seed-backed identity + the two protocol signatures
    pairing.ts             QR/deep-link payload parsing + desktop proof verification
    client.ts              MobileCompanionClient — dual proofs on every request
    notifications.ts       in-app polling loop (the verified channel)
  src/notifications/       opt-in OS notifications (PARTIAL — see above)
  src/store/credentials.ts Keychain/Keystore blob (grant secret + device seed)
  src/screens/             Pair (QR scan / deep link / manual), Chats, Approvals, Status
  assets/                  official VOOL mark (from installer/assets) at Expo sizes
  scripts/generate_assets.py  deterministic asset generator
  e2e/journey.mjs          headless end-to-end journey vs a live daemon
  tests/protocol.test.mjs  FIPS vectors, Python golden vectors, cross-impl checks
```

## Build & verification

Verified in this environment (Node 22, no Xcode/Android SDK):

```bash
cd mobile
npm ci                     # clean install from the committed package-lock.json
npm run typecheck          # tsc --noEmit — clean
npx expo export --platform ios --output-dir dist-ios      # Hermes bundle, 572 modules
npx expo export --platform android --output-dir dist-android  # Hermes bundle, 571 modules
npm run test:protocol      # node --test — 10 rows
npm run e2e:journey        # needs a daemon: VOOL_BASE_URL / VOOL_HOME / VOOL_PYTHON
```

**No installed-device proof is claimed**: the JS bundles compile for both
platforms (hashes recorded in the evidence bundle), but no .ipa/.apk was
signed or installed — the toolchains are absent.

## Desktop side

`core/web/api/mobile_companion_api.py` (registered once in
`core/web/api/service.py`, mirroring the media-editor delegation):

| Route | Who | Purpose |
|---|---|---|
| `POST /api/mobile/pairing/start` | desktop (owner-local) | mint code + challenge + QR payload |
| `GET  /api/mobile/pairing/challenge` | phone | server-held values to sign |
| `GET  /api/mobile/pairing/page` | desktop | the QR pairing surface (real QR render) |
| `POST /api/mobile/pairing/claim` | phone | proof-of-possession claim, mutual identity |
| `POST /api/mobile/companion` | phone | every dual-proven action |
| `GET  /api/mobile/pairing/status` | desktop | observe a pairing attempt |
| `GET  /api/mobile/devices` / `POST /api/mobile/devices/revoke` | desktop | list / revoke devices |
| `GET  /api/mobile/receipts` | desktop | hash-chained audit journal |
| `GET  /api/mobile/info` | anyone | capability leaf (catalog + fingerprint) |
