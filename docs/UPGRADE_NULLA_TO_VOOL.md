# Upgrading from NULLA to VOOL

VOOL was developed under the internal name **NULLA**. This guide says exactly what changed,
what is read compatibly, and what stays frozen so an existing installation loses nothing.

## The one rule that governed the rename

**Rename what a person reads. Freeze what a machine keys on.** Your data — profiles, chats,
keys, wallet addresses, encrypted stores, approvals and receipts — is never rewritten or
migrated on disk by the rename. Where a name changed, the runtime *reads both*.

## Environment variables

Canonical names are now `VOOL_*`. Legacy `NULLA_*` names still work everywhere:

- **Precedence:** an explicit `VOOL_<NAME>` always wins; `NULLA_<NAME>` is honored only when
  the canonical variable is unset (`core/env_compat.py`, applied at every process entrypoint;
  `VOOL_HOME`/`NULLA_HOME` are additionally resolved directly in `core/runtime_paths.py`).
- Example: `VOOL_HOME` and `NULLA_HOME` point the runtime at the same home either way.

## Runtime home and data

- Canonical per-user home: `~/.vool_runtime`. If `~/.nulla_runtime` already exists (and the
  canonical dir does not), **it is reused as-is** — the runtime never creates a second profile
  beside your old one.
- Project-local dev home: `.vool_local`, with the same reuse rule for an existing `.nulla_local`.
- Install receipts (`install_receipt.json`) still point at the home chosen at install time;
  nothing about an installed app moves.
- Database files: canonical names are `data/vool_web0_v2.db` and `data/memory/vool_memory.db`.
  If only the legacy `nulla_web0_v2.db` / `nulla_memory.db` exists in a home, the legacy file is
  opened — never duplicated, never orphaned (`storage/db.py`).
- Install directories: `~/vool-local`, with reuse of an existing `~/nulla-local` (shell and
  PowerShell installers).
- macOS app support home: the app's per-user home is `~/Library/Application Support/VOOL/runtime`.
  When only the pre-rename `~/Library/Application Support/NULLA/runtime` exists, **it is reused
  as-is** (the generated launcher in `installer/bundle/build_macos_app.sh` resolves this before
  the runtime starts). When both exist, the canonical home is chosen deterministically and the
  launch log names both directories, so the conflict is visible. The pre-rename
  `Application Support/NULLA` **window lock and Windows mutex name stay frozen**: the VOOL
  window claims both generations' locks so an old NULLA.app window and a VOOL window never
  stack on one machine (`installer/bundle/vool_window.py`).

## Intentionally frozen identifiers (documented legacy)

These stay `nulla`-named on purpose; changing them would orphan installed-base state:

| Identifier | Why frozen |
|---|---|
| macOS bundle id `ai.nulla.desktop` (+ `.local`) | Changing it makes macOS treat VOOL as a different app (new prefs, new TCC grants, LaunchServices confusion). The app *is* named VOOL everywhere a user looks. |
| Keychain service `nulla-credentials` | Renaming it orphans every stored credential; existing keychain entries keep decrypting. |
| Info.plist keys `NULLASourceSHA` / `NULLABuildId` | Written by the build, read by the launcher staleness guard; changing breaks update detection for installed bundles. |
| `agent_name_registry` / claimed voolbook names | Peer-network identity continuity. |
| Browser localStorage `nulla.*` keys, `data-model` values | Renaming silently drops existing users' client-side settings. |
| Wire/session schemas already migrated earlier (`vool.session_bundle`) | Older exports remain readable by the same reader. |

## What was renamed

Product-owned modules and entrypoints (`apps/vool_*.py`, `core/vool_*.py`, voolbook modules),
launchers and installers (`Install_And_Run_VOOL.*`, `installer/bootstrap_vool.*`), the
voolbook feature (formerly nullabook), service names in fresh installs, all user-visible
strings, documentation and the tool catalog the model reads.

## If something looks wrong after upgrading

- Both `VOOL_HOME` and `NULLA_HOME` set? The `VOOL_` spelling wins — unset the other.
- Missing history/chats? Check which home the runtime resolved (startup log prints it) —
  a receipt-pinned install keeps using its install-time home regardless of names.
- The [troubleshooting guide](TROUBLESHOOTING.md) covers the common cases.
