# Changelog

All notable changes to VOOL. Dates are YYYY-MM-DD. Earlier internal development
history is not republished here.

## [0.7.0-beta] — 2026-10-13

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.0-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta).

### Added
- **Intel Mac and Linux builds.**
  - Separate self-contained macOS x86_64 and arm64 apps. The launcher checks your CPU and macOS version and explains a mismatch instead of failing.
  - A self-contained Linux x86_64 package. It needs no sudo or system Python, and adds menu and desktop shortcuts.
- **Bundled local-model runner.** The macOS app ships the runner its local models need, per CPU slice.
- **Signed one-press updates with rollback** for each supported platform. The update feed starts with 0.7.1; 0.7.0 is installed by download:
  - the v0.7 release publisher key is pinned;
  - update checks need explicit consent;
  - your current data is kept on rollback.
- **User rules.** Global, chat and project rules; "what are my rules"; remove by number. Forget also reaches rules you gave in plain words.
- **Chain data keys.**
  - Add your own Helius, Alchemy or Solscan key, and get read-only chain data in chat. An Etherscan key is also accepted.
  - The keys share the provider key flow.
  - With no key, no network call is made.
- **Guided X setup** with read-only checks: a Bearer Token or your own app keys. X stays drafting-only.
- **Bug reports.** Report and 👎 on every answer.
  - The report is built on your computer and shown to you before sending.
  - Cards, PINs, phone numbers, wallet addresses and your user name are scrubbed.
  - Open on GitHub files a prefilled public report, with no token needed.
- **Skill Codex** on the Add-ons page:
  - signed skill packages, with Download for inspection, Install without enabling, and Install and enable;
  - ten main categories with subcategories.
  - Browsing shows "not open yet" in this version.
- **Add-on store.** Reviewed add-ons with local add-on security checks.
- **Notifications.**
  - Notifications and model offers share one inbox.
  - A task-finished chime, with a persistent sound setting.
- **Zcash private invoices** with a watch-only viewing key (opt-in, off by default).
- **Localization.** The interface, first run and settings are translated across 16 shipped catalogs besides English.

### Changed
- **Memory.**
  - Recall works on a fresh install within the local time budget.
  - Forget and absence answers are tightened.
  - Scoped erasure covers stored memory carriers.
- **Local-model time limits** scale once on machines with no GPU.
- **Chat header.** One place for model and run-mode controls. Activity controls stay reachable.
- **Crypto.** Mainnet is the default once crypto is turned on. Test networks move to Developer options.
- **Provider keys.** Setup is organized by models, search and security.
- **Pets.** The original artwork is back, pets are larger, and they show chat activity bubbles. Hit areas stay clear of controls.
- **Installers.** They no longer set up an outside agent runtime; VOOL's own runtime does the work.
- **The old public VoolBook site pages are retired.**
- **A pinned paid model** runs its own tool-selection rounds.

### Fixed
- **Personal assistant.**
  - Reminders and calendar fall back to your profile's time zone instead of UTC.
  - Past reminder times are refused.
  - Common "when" phrases are read correctly.
  - Apple Calendar all-day events are no longer a day off.
  - Gmail nested and HTML-only bodies are read.
  - A missing calendar is reported as such.
  - The calendar and note tools receive the request text.
- **IMAP.** Connections verify the server certificate and have a bounded timeout.
- **Live prices.** A follow-up like "and ETH?" keeps every asset you already asked about.
- **Claims.** A time phrase inside a claim ("as of today") is treated as framing, not as what the claim is about.
- **Image generation.** Stopping it only stops VOOL's own image process.
- **Recall routing.** "What did I tell you about X" is answered from the conversation and memory, not the web.
- **Chat lists.** Numbered lists no longer restart at 1, and Copy labels show in your language.
- **Update menu.** It is no longer clipped.
- **Native app.** A slow health answer from a running local runtime no longer closes the app.
- **Linux.** Shutdown cleanup is fixed.
- **Wallet payments.**
  - Each x402 offer is paid at most once, whatever the URL spelling.
  - The spend is held from proposal to settlement.
  - Every refusal leaves a receipt.
  - An unknown outcome is never re-paid.
- **Zcash.** Payments in an unknown pool are reported as uncertain, not unpaid. Receipts are recorded exactly once.

### Security
- Recovery phrases typed as plain words are redacted. More key shapes are masked, including EVM and Zcash viewing keys.
- Text parsers were rewritten to run in linear time, so long input cannot stall them.
- Sandboxed jobs cannot read external volumes, and their child processes are cleaned up.
- Plugin approval is bound to the package code and its dependencies, and rechecked when it runs.
- Wallet connections are pinned to approved DNS answers.
- Patched dependency releases are pinned.

### Known limitations
- x402 for websites works on test networks only in 0.7.0 (Solana Devnet, Base Sepolia, Ethereum Sepolia and BNB testnet). One exception: the pay-per-call option in the model marketplace in Settings can pay from the Solana Mainnet Crypto Pilot wallet. Each call waits for your approval within your wallet limits and shows the 0.1% VOOL fee. It has not yet been tested on a real chain. Mainnet x402 for websites is planned for 0.7.1.
- Skill Codex browsing is not open yet.
- X is drafting plus guided read-only setup; trending is hidden.
- The macOS apps are not notarized.
- No in-app update is offered for 0.7.0 itself; one-press updates start with 0.7.1.
- Before a local model can answer, open Settings > Models & Providers > **Local models** and press **Certify** for it once. A clearer prompt comes in 0.7.1.
- On Intel Macs, local models run on the CPU and may be too slow to pass the one-time Certify check or to answer in time, so use a cloud model there for now. We tested the Intel build under Rosetta on an Apple Silicon Mac, not yet on an Intel Mac.
- Saying "don't record this" does not stop a chat from being saved yet. Ask VOOL to forget it afterwards.
- Dragging the pet out of the VOOL window does not move it to the desktop. Use right-click > **Move to desktop**, or the button in Settings. A fix is planned for 0.7.1.

## [0.6.0-beta] — 2026-09-19

First public-facing VOOL beta tree (macOS, Apple Silicon).

### Changed
- Initial public tree: runtime, memory, tool loop, permissions, receipts, providers and the
  bundled macOS app (`VOOL.app`, entry point `Contents/MacOS/VOOL`, ad-hoc code signature,
  builder-home paths scrubbed from the bundle by a binary-safe build gate). Legacy
  compatibility identifiers are frozen deliberately — see
  docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md for the engineering record.

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
