# Configuration reference

Safe placeholders throughout: `<path>`, `<url>`, `<key>`. Never commit real values.

## Environment variables

Canonical prefix is `VOOL_`; legacy `NULLA_` spellings are honored (lower precedence) at
every entrypoint — see [the upgrade guide](UPGRADE_NULLA_TO_VOOL.md).

| Variable | Meaning | Default |
|---|---|---|
| `VOOL_HOME` | Runtime home (data/config/logs/workspace) | `~/.vool_runtime` (reuses `~/.nulla_runtime` if only that exists) |
| `VOOL_WORKSPACE_ROOT` | Workspace root override | home `workspace/` |
| `VOOL_PROJECT_ROOT` | Source root of a packaged app (exported by launchers) | — |
| `VOOL_INSTALL_PROFILE` | `auto-recommended` / `local-only` / `local-max` | auto |
| `VOOL_OLLAMA_URL` / `VOOL_RAW_OLLAMA_API_URL` / `OLLAMA_HOST` | Local inference endpoint (in that order) | `http://127.0.0.1:11434` |
| `VOOL_WALLET_ENABLED` | Opt in to the optional Solana wallet | unset (disabled) |
| `VOOL_KEY_STORAGE_MODE` | `keychain` / `file` / passphrase modes | platform default |
| `VOOL_ENABLE_WEB` | Enable web fetch/search surfaces | off on local-only profiles |
| `VOOL_DEBUG_PROMPT` | Write prompt-debug JSONL under the home | off |
| `VOOL_PLUGINS_DIR` | Plugin pack directory | home plugins |

The full live set is larger (meet/auth, calendar, media lab, registration, updater knobs);
grep `VOOL_` in `core/` for any not listed here — they follow the same precedence rule.

## Policy file

Runtime permissions live in the policy engine (`filesystem.allow_write_workspace`,
`execution.allow_sandbox_execution`, `assist_mesh.enabled`, …) — editable via the served
settings surface, not by hand-editing, so the governed values stay consistent.

## Install receipts

`install_receipt.json` (beside an installed app) pins the runtime home chosen at install
time. It is read before any default; the rename never moves it.
