# VOOL Mobile Companion

One shared React Native (Expo) codebase for iOS and Android that pairs with a
VOOL desktop over the local network and acts as the operator's pocket surface:
chats, model status and the Proof Chip, configurable alerts, wallet views and
payment proposals. Payments and tool actions are approved on the computer
while native protected-key approval is unproven.

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
- **Every request** carries two transport proofs over the same canonical bytes (device
  id, action, parameters digest, nonce, timestamp, grant identity): the grant
  HMAC (Device Link request surface, nonce replay shield, ±120 s window) AND
  an Ed25519 signature by the registered key, verified against the key IN THE
  DEVICE RECORD (never a request-supplied key). Both are stored together. They are not independent approval factors and
  do not authorize a wallet signature.
- **Revocation** kills transport authority on the next request and durably (restarts rehydrate
  the revoked registry from `data/mobile_companion/state.json`).
- **Reinstall / key loss** destroys the identity by design: a new key is a new
  `device_id` and must re-pair.
- **The closed action catalog** (`COMPANION_ACTIONS` in
  `core/web/api/mobile_companion_api.py`) is the phone's entire surface:
  chats, attachments, approvals, status, proof, notifications, devices.me and
  devices.unpair (self-unpair only). No filesystem, no terminal, no signer, no
  credential reach of any kind.
- **Secrets**: the transport holds two secrets together in the keychain — the
  grant bearer secret and its own Ed25519 seed. API/provider/model keys never
  leave the desktop; device listings never echo either secret.
- **Receipts**: every security-relevant event is appended to a hash-chained
  journal (`GET /api/mobile/receipts`), tamper-detecting.
- **Separate transport**: phones use a dedicated, closed companion listener;
  it never serves the desktop UI or generic API. Fresh installs stay off.
  Enable it in Settings → Phone and chat apps. Turning it off closes the
  socket. Its registry commands use the live daemon runtime.
- **Sealed v2**: the app pins the computer's fingerprint from the pairing QR,
  verifies a signed encryption-key binding, and seals pairing claims and action
  bodies in both directions. Device and grant signatures remain inside the
  envelope. Reply keys are directional and replies bind the exact request.
  Wire replay protection is durable. Old app credentials require re-pairing.
  Remote plaintext companion POSTs to the desktop API are refused.
- **Session continuity**: the authenticated sealed companion may resume a
  desktop chat when history access is granted. Its turns still enter as a
  remote channel. Resuming a chat never grants owner-local tool authority.
- **Small rights**: every right for a new sealed pairing starts off; the PC explicitly grants chat. History,
  attachments, alerts, wallet viewing and payment proposals are separate PC
  permissions. Payment proposal rights require the ready PC wallet's own
  credential, checked through its existing durable throttle, and default to
  30 days. Phone limits are lower than the wallet limits and count a rolling
  24-hour window, including pending proposals and fee ceilings.
- **Payment safety**: the wallet checks the phone claim again in its atomic
  claim and transmit gates. Rights revisions and grant identity invalidate
  old requests. An uncertain proposal retry retains its immutable identity;
  it cannot resurrect a request the owner declined. The app never signs or
  broadcasts a payment. Native-asset proposals only are offered in this build.
- **Chat payments**: the separate remote-tool fence currently refuses wallet
  and paid-AI tools in phone chats. Do not bypass it by stamping a phone as
  owner-local. Direct app proposals use the scoped companion wallet actions.

## QR product flow

- The desktop pairing surface (`GET /api/mobile/pairing/page?pairing_id=…`,
  owner-local) renders a REAL QR image client-side (vendored
  qrcode-generator, MIT, inlined — no CDN) plus the manual code, pairing id
  and expiry countdown.
- The phone scans it with Expo Camera (`CameraView`, QR barcode type), opens
  it via the `vool-pair://` deep link (registered scheme; typed handler in
  `App.tsx` → the same `parsePairingUri` the scanner uses), or types the
  address, pairing id, code and computer fingerprint by hand. The fingerprint is required.
- The QR pins desktop URL, pairing id, code, expiry and fingerprint; any
  tampered field fails parse or the fingerprint binding at claim; an expired
  payload refuses locally before any network call.

## Notification delivery

- **In-app polling is the verified channel**: the notification poller reads
  the desktop's typed event stream (`notifications.poll`) and classifies
  completion/failure/attention. The durable in-app feed remains the record.
  Per-type preferences, quiet hours and read cursors are stored per device
  on the PC. Payment terminal events remain in the feed and badge while
  payment rights are on, even when general alerts are off. No push server
  is involved or needed for the
  local-network companion. The app shell drives this loop
  (`src/hooks/useNotifications.ts`): it feeds the tab badges and the coarse
  in-app ticker, and it is what the OS layer rides on.
- **OS notifications are an OPT-IN layer** (`src/notifications/osNotifications.ts`,
  `expo-notifications` local notifications, permission-requested): real code,
  coarse text by default. Per-type detail requires explicit phone opt-in and
  the corresponding PC history/wallet-view right. The phone OS may mute,
  delay or suppress delivery. **PARTIAL**:
  delivery has NOT been proven on an installed device — no iOS/Android build
  exists in this environment.

## Unpair

The phone can unpair itself: Status → This device → **Unpair this device**
(the `devices.unpair` action). The desktop revokes only the CALLING device —
a phone can never unpair another — and receipts it as `device.unpaired`; the
phone drops its credentials and can re-pair any time with a new code. The
desktop-side revoke remains available too and wins either way.

## Layout

```
mobile/
  App.tsx                  navigation shell + vool-pair:// deep-link handler + badges/ticker
  src/protocol/            PURE TypeScript — the exact code the headless proof runs
    canonical.ts           Python-byte-compatible canonical JSON
    sha256.ts              SHA-256 + HMAC-SHA256 (no WebCrypto dependency)
    ed25519.ts             seed-backed identity + the two protocol signatures
    pairing.ts             QR/deep-link payload parsing + desktop proof verification
    client.ts              MobileCompanionClient — dual proofs on every request
    notifications.ts       in-app polling loop (the verified channel)
  src/hooks/useNotifications.ts  the app-shell wiring of that loop (badges, ticker, OS bridge)
  src/notifications/       opt-in OS notifications (PARTIAL — see above)
  src/store/credentials.ts Keychain/Keystore blob (grant secret + device seed)
  src/screens/             Pair (QR scan / deep link / manual), Chats, Approvals, Status
  assets/                  official VOOL mark (from installer/assets) at Expo sizes
  scripts/generate_assets.py  deterministic asset generator
  e2e/journey.mjs          headless end-to-end journey vs a live daemon
  tests/protocol.test.mjs  FIPS vectors, Python golden vectors, cross-impl checks
  tests/jest/              jest unit tests for the app logic (client requests, poller)
```

## Build & verification

Local checks (native build and installed-device proof are separate):

```bash
cd mobile
npm ci                     # clean install from the committed package-lock.json
npm run typecheck          # tsc --noEmit — clean
npm run test:protocol      # node --test — 10 rows
npm run test:jest          # jest — app-logic units (client requests, notification poller)
npm run e2e:journey        # needs a daemon: VOOL_BASE_URL / VOOL_HOME / VOOL_PYTHON
```

**No installed-device proof is claimed.** TypeScript, protocol journeys and
served PC checks do not establish native keychain, biometric, notification
or UI behaviour. No iOS/Android application has been installed here.

## Desktop side

`core/web/api/mobile_companion_api.py` (registered once in
`core/web/api/service.py`, mirroring the media-editor delegation):

| Route | Who | Purpose |
|---|---|---|
| `GET /api/mobile/settings` / `POST /api/mobile/settings` | desktop | listener, phone permissions and freeze controls |
| `POST /api/mobile/pairing/start` | desktop (owner-local) | mint code + challenge + QR payload |
| `GET  /api/mobile/pairing/challenge` | phone | server-held values to sign |
| `GET  /api/mobile/pairing/page` | desktop | the QR pairing surface (real QR render) |
| `POST /api/mobile/pairing/claim` | phone | proof-of-possession claim, mutual identity |
| `POST /api/mobile/companion` | phone | every dual-proven action |
| `GET  /api/mobile/pairing/status` | desktop | observe a pairing attempt |
| `GET  /api/mobile/devices` / `POST /api/mobile/devices/revoke` | desktop | list / revoke devices |
| `GET  /api/mobile/receipts` | desktop | hash-chained audit journal |
| `GET  /api/mobile/info` | anyone | capability leaf (catalog + fingerprint) |

## Dependency security compatibility

The lock overrides vulnerable transitive image-size, tar, PostCSS, UUID and
Expo plist-parser releases. Expo remains on SDK 52. Added first-party modules stay on their SDK 52 versions;
the advisory baseline must be compared before and after any addition.
Two versioned `patch-package` patches adapt Metro 0.81.5 to image-size 2's
buffer/file APIs and Expo CLI 0.22.28 to tar 7's named CommonJS exports.
`npm ci` applies these patches and fails if they cannot be applied; do not use
`--ignore-scripts` for a working install. Reassess/remove the patches when upgrading
the owning Metro/Expo packages.

Run `npm run test:tooling`, `npm run test:protocol`, `npm run typecheck` and
`npm audit` after dependency changes. Tooling tests cover actual asset reads,
template extraction, the Windows JavaScript extraction path, plist/CSS and UUID
consumers. They complement native device testing; they do not replace it.
