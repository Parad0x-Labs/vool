# Signed atomic self-update with automatic rollback — design (2026-09-01)

Lane: `build/self-update-atomic-20260901`, base `b3f5117f`. This is the DESIGN RECORD for
the macOS-wrapper updater foundation and the platform-neutral signed release manifest that
future Windows/Linux installers will share. Implementation: `core/updater/` (new package,
zero edits to existing modules), helper script `installer/update/mac_update_helper.sh`,
release tooling `installer/update_release.py` + `installer/gen_publisher_keypair.py`,
user surface `installer/update_cli.py`.

## 0. Relationship to what already exists (revive-before-rebuild)

| Existing | Verdict |
| --- | --- |
| `core/self_update_check.py` (GitHub-releases check, Windows lane) | kept untouched; separate increment |
| `core/self_update_offer.py` + `storage/self_update_offer_store.py` (in-chat offer) | kept untouched |
| `core/self_update_state.py` (check-state persistence) | kept untouched |
| `installer/self_update.py` (detached Windows code-dir updater) | kept untouched; its fail-open-while-unpinned posture (NIA-026) is NOT inherited here |
| `core/release_channel.py` + `config/release/update_channel.json` (unsigned LOCAL release descriptor) | vocabulary reused (channel/version/protocol), not the update transport |

NIA-026 parked the fail-closed signing decision on the operator. The 2026-09-01 mission
brief ("Ed25519 manifest/artifact verification plus macOS signing/notarization checks",
"defend against … signature failure") IS that decision, on record, for THIS lane: the new
manifest-based updater **fails closed** — no pinned publisher key ⇒ no update is offered or
installed, ever.

## 1. Signed platform-neutral release manifest (v1)

One JSON document describes a release for every platform. Ed25519 (RFC 8032) signature by
the release publisher over the canonical form (all fields, `signature` removed,
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`).

```json
{
  "schema": "vool.update.manifest.v1",
  "product": "vool",
  "channel": "stable",                     // "stable" | "beta"
  "version": "0.6.0",                      // semver; -beta.N prereleases allowed on beta
  "sequence": 47,                          // monotonic per channel — the anti-replay number
  "published_at": "2026-09-01T12:00:00Z",  // ISO-8601 UTC
  "minimum_compatible": "0.4.0",           // oldest version this release can update in place
  "notes": "human-readable changelog",
  "artifacts": {
    "macos-arm64": {
      "url": "https://…/VOOL-0.6.0-macos-arm64.zip",
      "size": 123456789,
      "sha256": "<64 hex>",
      "signature": "<base64 Ed25519 over the ARTIFACT BYTES>"
    },
    "windows-x64": { … }, "linux-x64": { … }
  },
  "signature": {"key_id": "release-2026-01", "sig": "<base64 Ed25519 over canonical form>"}
}
```

Trust anchors: `config/release/trusted_publisher_keys.json` — `{key_id: hex-public-key}`
map, PUBLIC keys only (the private half never lives in this repo; the generator tool
refuses to write it here). Tests and dev sandboxes inject keys programmatically.
Env override `VOOL_UPDATE_PUBLISHER_KEYS="keyid:hex,…"` exists for release staging only;
if both are empty the updater reports `no_trusted_publisher` and offers nothing.

Defense matrix encoded in `parse_and_verify` + `decide`:

| Attack | Defense |
| --- | --- |
| Forged/modified manifest | Ed25519 over canonical form; unknown `key_id` refused |
| Replay of an old signed manifest | `sequence` must exceed the persisted per-channel high-water mark |
| Manifest rollback by a hostile mirror | same sequence high-water (an older signed manifest has a lower sequence) |
| Future-dated manifest (clock games) | `published_at` beyond now + 48h refused |
| Wrong platform artifact | decision only accepts the entry for the local platform key |
| Corrupt/partial download | exact size + sha256 + artifact Ed25519 before anything is staged for install |
| Downgrade attack | strictly-newer semver required unless a typed `DowngradeAuthorization` is supplied |
| Channel confusion | manifest channel must equal the configured channel exactly |

## 2. Separated authorities (one job each, injectable seams)

- **Check authority** — `core/updater/service.py` `UpdateCheckService`: background-thread
  manifest fetch + verify + decide; NEVER blocks startup (`start()` returns immediately);
  results land in the status store. Network only through
  `named_background_effect_scope("self_update.manifest_check")` + the one remote door.
- **Decision authority** — `core/updater/decision.py`: pure function of (verified manifest,
  installed version, channel, platform, high-water, authorizations). No I/O.
- **Download authority** — `core/updater/download.py`: resumable staged download into user
  data, disk-space preflight before transfer, size/hash/signature verification of the
  complete artifact. Scope `self_update.artifact_download`.
- **Install authority** — `core/updater/transaction.py` `UpdateFlow` + `platforms/macos`:
  preflight, pause-new-work, active-state receipt, snapshot, helper shutdown, atomic swap,
  restart, health check, rollback. Owns the on-disk journal.
- **Migration authority** — `core/updater/migrations.py`: transactional, user-data-only,
  snapshot-per-step, reverse-on-failure. Refuses data newer than the app understands.

## 3. Install transaction state machine

Journal: `<data>/update_v2/transactions/<txid>/journal.jsonl` (append + fsync per step).

```
CREATED → MANIFEST_VERIFIED → DECIDED → DOWNLOADED → ARTIFACT_VERIFIED → PREFLIGHT_OK
        → WORK_PAUSED → ACTIVE_STATE_RECEIVED → SNAPSHOT_COMPLETE → HELPER_SHUTDOWN
        → APP_STOPPED → SWAPPED → MIGRATED → RESTARTED → HEALTHY → FINALIZED
any failure ───────────────────────────────────────────────────────► ROLLED_BACK / ABORTED
```

Laws:
- Nothing user-visible changes before `PREFLIGHT_OK`.
- The previous app bundle is never deleted during a transaction: the swap moves it to a
  hidden sibling (`.VOOL.app.prior-<txid>`, same volume ⇒ rename-atomic) and pruning to
  the retention count (2) happens only after `FINALIZED`.
- Any failure after `SWAPPED` ⇒ automatic rollback (bundle + migration snapshots) and
  relaunch of the prior version; the failure receipt says so in plain language.
- **Crash recovery**: `recover_interrupted_update()` runs at startup. Journal without a
  terminal step: pre-swap ⇒ mark `ABORTED`, clear staging (prior version untouched);
  at/after `SWAPPED` ⇒ roll back and relaunch prior (a half-finished update never stays),
  recording a `crash_recovery` receipt.
- Restart while destructive work is active: the pause step refuses with
  `destructive_work_active` naming the work; only an explicit
  `DestructiveWorkResolution` (operator ack naming the receipt) proceeds.
- User data (DB, wallet, config, updater state, receipts, snapshots) lives under
  `VOOL_HOME` data dirs — the swap replaces only the `.app` path.

## 4. macOS specifics

- Bundle verification before swap: `codesign --verify --deep --strict` (required) and
  `spctl --assess --type execute` (notarization posture; required by default). The
  notarization requirement can only be relaxed per-transaction with an explicit,
  journaled reason (dev sandbox); the default posture is fail-closed and a test pins
  that ad-hoc/un-notarized bundles are refused under it.
- Atomic swap = two renames in the app's parent directory with an undo if the second
  rename fails; the staged bundle is extracted inside the same parent (`.vool-stage-<txid>`).
- External helper: `installer/update/mac_update_helper.sh` — waits for the app PID to
  exit, performs the swap renames, writes a result file, relaunches via `open -n`.
  Unit tests use an in-process fake; the sandbox e2e test executes the real script
  against temp bundles only.

## 5. Windows/Linux — contract boundary, honestly

The manifest schema, decision, download, migration, journal, health and rollback
machinery are platform-neutral and shared. `core/updater/platforms.py` exposes a
`PlatformInstaller` registry; only `macos-*` is registered today. On windows-x64 or
linux-x64 the decision reports `unsupported_installer_platform` with plain text naming
the platform — the updater never pretends an installer exists.

## 6. User-visible surface (plain language)

`core/updater/status.py` maps phases to stable plain-language strings (download percent,
verification, restart, rollback…) and persists `<data>/update_v2/status.json` so the
wrapper renders "Update ready" after restarts too. Install requires an explicit gesture:
`UpdateController.press_update(gesture=UserGesture(...))`; the CLI equivalent is
`update_cli apply --i-pressed-update`. No gesture ⇒ no install.

## 7. Test and proof plan

`tests/updater/`: trust/manifest (tamper, wrong key, replay, future-date), decision
(downgrade/channel/platform), download (resume, corrupt partial, disk preflight, size
cap), pause-work (destructive refusal + explicit resolution), migrations (forward,
mid-failure rollback, too-new data), macOS codesign (fake runner + real ad-hoc bundle),
transaction flow (every transition RED→GREEN, crash-after-step recovery, health-failure
rollback, rollback idempotence), status/service (non-blocking, plain language), sandbox
e2e over a local HTTP server with temp bundles — the real installed VOOL app is never
touched. Sabotage matrix (`tests/updater/run_sabotage_matrix.py`): disable manifest
signature enforcement, artifact signature enforcement, rollback, replay high-water,
codesign gate, downgrade guard — each must redden named tests; restores are cmp-verified.
