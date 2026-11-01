# Handback: no-profile InvalidTag silent quit (delivery/no-profile-invalidtag-20261101)

Branch `delivery/no-profile-invalidtag-20261101`, base `main` `411efdb2` (v0.7.6).

## Root cause

Two defects stacked:

1. **Silent legacy-store adoption.** On macOS with no VOOL profile
   (`~/Library/Application Support/VOOL` missing), both the generated `.app` launcher and
   `core.runtime_paths.native_runtime_home()` adopted the pre-rename
   `~/Library/Application Support/NULLA/runtime` store implicitly (the 2026-09-19
   "upgrade reuse" rule: `installer/bundle/build_macos_app.sh` launcher heredoc, before this
   fix; `core/runtime_paths.py` `native_runtime_home`/`user_runtime_default`). Linux mirrored
   it via `user_runtime_default()` reusing `~/.nulla_runtime`.
2. **Undecryptable record killed the boot silently.** The adopted (or otherwise damaged)
   profile holds `data/keys/node_signing_key.json` sealed under a secret the current runtime
   does not have. `network.signer._load_encrypted_seed()` raised a raw
   `cryptography.exceptions.InvalidTag` from `AESGCM.decrypt` — uncaught, so the daemon died
   with a bare traceback in `logs/native-runtime-child.log`; the window host saw only
   `returncode` (`installer/bundle/native_runtime_supervisor.py`, "runtime child exited
   before readiness") and `installer/bundle/vool_window.py main()` returned 1 without any
   window or dialog. Same failure family is documented in the repo's own `conftest.py`
   (a record sealed under one protection, opened under another = InvalidTag).

## What changed

**Fix 1 — no VOOL profile = clean first-run, never a silent NULLA read** (the 2026-09-19
reuse rule is repealed by the 2026-11-01 first-run law; an explicit `VOOL_HOME`/`NULLA_HOME`
still wins, and old stores are left byte-identical on disk):

- `core/runtime_paths.py:62` `user_runtime_default()` → always `~/.vool_runtime` (Linux
  parity). `core/runtime_paths.py:192` `native_runtime_home()` → darwin always
  `…/Application Support/VOOL/runtime` (`:208`), win32 always `%LOCALAPPDATA%/VOOL` (`:211`).
- `installer/bundle/build_macos_app.sh:913` launcher selects the canonical home only; when a
  legacy store exists the launch log names it as "not used" (`:994`) — visible, not silent.
- `installer/install_vool.sh:8` default home is `~/.vool_runtime`; no legacy adoption.
- Docs updated: `docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md` (two rows),
  `docs/UPGRADE_NULLA_TO_VOOL.md` (Runtime home section + new undecryptable-record rule).
- Deliberately untouched: the dev-checkout `.nulla_local` reuse (`core/runtime_paths.py:14`,
  pre-rename checkout lane, not a user profile) and the Desktop `Nulla-skills-plugins`
  plugin-folder reuse (`core/plugin_catalog.py`, its own contract, comment corrected only).

**Fix 2 — a decrypt failure at startup is a plain message and untouched data:**

- `network/signer.py:514` new `SigningKeyUnreadable(RuntimeError)` with a plain, path-free
  message (`_UNREADABLE_SEED_MESSAGE`); raised at `:543`/`:573` when the keyring pointer or
  encrypted-seed record fails AES-GCM authentication (`InvalidTag`) or is malformed
  (`ValueError`). The bad record is never archived, rewritten, or replaced by a new identity.
- `apps/vool_api_server.py:812-818` `main()` catches exactly that type during boot, prints
  `VOOL cannot start: …` to stderr, writes a typed record `data/startup_blocker.json`
  (`vool.startup_blocker.v1`, schema/reason/message/pid, `_write_startup_blocker` `:613`),
  and exits with code **86**. A boot that gets past `create_app` deletes any stale record
  (`_clear_startup_blocker` `:639`, called at `:707`).
- `installer/bundle/native_runtime_supervisor.py:219` `_read_startup_blocker()` — accepted
  only when the record's `pid` equals the dead child's pid; `:298-303` embeds the plain
  message in the `NativeRuntimeError` and exposes `supervisor.startup_blocker`.
- `installer/bundle/vool_window.py:1684-1688` — when the supervisor carries the blocker, the
  window host shows a native alert before exiting 1: macOS `osascript display alert`
  (`:1587`), Linux zenity/kdialog, Windows `MessageBoxW` — and always logs it.

## Tests

All with the repo-pinned toolchain (ruff 0.17.2, pytest 9.1.1, Python 3.11.15), serial runs:

| Suite | Result |
|---|---|
| `tests/test_no_profile_invalidtag_startup.py` (new, 17 tests) | 17 passed |
| `tests/test_runtime_paths.py` | 51 passed (with test_nulla_to_vool_compatibility.py) |
| `tests/test_nulla_to_vool_compatibility.py` | (in the 51 above) |
| `tests/test_rename_upgrade_compatibility.py` | 9 passed |
| `tests/test_signer_key_storage.py` + `tests/test_native_runtime_supervisor.py` | 53 passed |
| `tests/test_macos_app_packaging.py` | 41 passed |
| `tests/test_app_lifecycle_ownership.py` + install_receipt + webkit profile + keychain auth | 44 passed |

Total: **215 passed, 0 failed**. Ruff 0.17.2 clean on all touched files and on the whole
repo. `bash -n` clean on both changed shell scripts. Full-suite `pytest --collect-only`:
49,378 collected, 13 collection errors — identical count and set on a pristine BASE
checkout (missing optional test deps in the local venv: playwright etc.); no import breakage
from this change.

New coverage pins both required paths: missing VOOL profile → canonical home with the legacy
store byte-identical (darwin/win32/linux + real generated launcher + real install-script
prologue), and foreign-sealed / damaged encrypted records → typed `SigningKeyUnreadable`,
byte-identical profile, no archival; plus daemon exit 86 + blocker record, supervisor pid
matching (a foreign process's record never speaks for the child), and the per-OS dialog.

**Three existing tests changed what they check, on the owner's explicit order** (the
behavior they pinned was the bug's owning cause): `test_runtime_paths.py::
test_native_default_reuses_legacy_profile_before_creating_state` → renamed `…_never_adopts_a_legacy_profile`;
`test_nulla_to_vool_compatibility.py::test_legacy_user_home_is_reused_not_duplicated` → renamed
`…_is_never_adopted_implicitly`; `test_rename_upgrade_compatibility.py::
test_launcher_reuses_a_legacy_nulla_runtime_home` → renamed `…_leaves_a_legacy_nulla_store_unadopted`.
Each old assertion's opposite edge is now pinned; nothing was skipped, deleted, or weakened.

## Unknowns / notes

- The exact field trigger of the InvalidTag (which secret sealed the adopted record) is a
  class, not a single case — wrong ambient passphrase, regenerated `key_storage.passphrase`,
  or a damaged record all land in the same typed path now; the fix covers the class.
- Upgraders from the pre-rename app get a fresh profile and keep their old data untouched in
  the NULLA store (visible in the launch log); re-attaching the old store is now an explicit
  `VOOL_HOME` choice. This intentionally repeals docs' 2026-09-19 reuse promise, per brief.
- The Linux startup blocker message surfaces via zenity/kdialog when present, otherwise the
  log only (no dialog tool on a bare Linux box); macOS is the dialog-primary lane of the brief.
- Environment note for local runs: the repo venv needs `zstandard`, `xxhash`, `aiohttp`,
  `httpx2` beyond `pip install -e .` for some suites/collection.
