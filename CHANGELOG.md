# Changelog

All notable changes to VOOL. Dates are YYYY-MM-DD. The product was developed under the
internal name NULLA before 2026-09-19; older internal history is not republished here.

## [0.6.0-beta] — 2026-09-19

First public-facing VOOL beta tree (macOS, Apple Silicon).

### Changed
- Product identity migrated NULLA → VOOL across modules, entrypoints, launchers, installers,
  UI and documentation. Compatibility: `VOOL_*` env canonical with `NULLA_*` honored; legacy
  runtime homes and database filenames reused (never duplicated); macOS bundle id
  `ai.nulla.desktop`, keychain service `nulla-credentials` and plist integrity keys frozen
  (see docs/UPGRADE_NULLA_TO_VOOL.md).
- Bundled macOS app: `VOOL.app` with entry point `Contents/MacOS/VOOL`, ad-hoc code
  signature, builder-home paths scrubbed from the bundle by a binary-safe build gate.

### Fixed
- Provider invocation manifests: canonical marker hashing is now idempotent. Previously a
  context-trimming marker re-verified after persistence was re-redacted (its sha256 digest
  matched key-shape rules) and re-hashed, failing the manifest's own canonicality check and
  blocking the model call ("provider manifest contains non-canonical persistence data").

### Known limitations
- Partial translations; language and voice controls disabled in this build.
- macOS app is ad-hoc signed, not notarized.
- Windows experimental (installer sources in-tree, no shipped artifact); Linux unsupported.
- Cloud burst `ask` mode's per-call approval prompt is not wired yet.
- Wallet/x402 spend lane disabled; mainnet impossible by construction.
