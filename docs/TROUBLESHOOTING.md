# Troubleshooting

Every typed failure VOOL can raise is in the [Error Book](ERROR_BOOK.md) — generated from
`core/faults/catalog.py`, so codes and messages there are exactly what the runtime emits.
This page covers the situations that never reach a fault code.

## First open on macOS

The app is **ad-hoc signed, not notarized**. On first open macOS shows "unidentified
developer": right-click VOOL.app → **Open** → **Open** (once). This is expected for the beta;
notarization is a pending release decision, not something this build claims.

## First run looks frozen

The first run downloads a local model (a few GB). The window can sit idle for minutes — that
is the download, not a hang. Check progress in the runtime log under
`~/.vool_runtime/logs/` (or your install receipt's home).

## Which home is the runtime using?

Startup logs print the resolved home. Resolution order: `VOOL_HOME` → `NULLA_HOME` (legacy) →
install receipt → `~/.vool_runtime` (reusing `~/.nulla_runtime` when only that exists).
See [the upgrade guide](UPGRADE_NULLA_TO_VOOL.md).

## Ollama connection refused

`Failed to pull Ollama model ... Connection refused` means no local Ollama is listening on
`http://127.0.0.1:11434`. Start Ollama, or point `VOOL_OLLAMA_URL` at your instance. VOOL
falls back to deterministic planning mode when no local model is reachable.

## Cloud burst does nothing

The cloud lane is opt-in and **off** by default. Settings → Providers → add your own key, then
choose the burst mode. `ask` mode's per-call prompt is not wired yet in this build — see
[STATUS](STATUS.md).

## Wallet says a network is inactive or disabled

That is by design: the wallet is disabled by default, and the two network environments
(Mainnet / Test networks) never mix — a request for a network in the other environment is
refused with nothing created or signed. Switch deliberately in Settings → Crypto → Developer
options. The earlier claim that "mainnet is impossible by construction" predates the Crypto
Pilot and is no longer true: mainnet rows exist (native-coin transfers; the sole mainnet token,
USDC on Solana, exists for the x402 payment lane only). See the
[Wallet & Web0 guide](WALLET_WEB0_GUIDE.md) for what is enabled and how.

## Reporting bugs safely

Use the built-in bug reporter (`vool bug-report` CLI or `/api/bug-report/*`): it builds a local
redacted draft you preview and approve byte-for-byte before anything is sent. Security issues
go through [../SECURITY.md](../SECURITY.md), never a public issue.
