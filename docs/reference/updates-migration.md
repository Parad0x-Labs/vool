---
description: How VOOL updates itself, what happens to your data, and version compatibility.
---

# Updates and migration

## How updates work

VOOL checks for updates in the background and **never takes the running app down**. When an
update is ready you choose to install it; the installer is gesture-gated.

Every update runs a verified pipeline with automatic rollback:

1. **Download** — verified against a signed manifest.
2. **Staged install** — the new version is installed beside the old one.
3. **Migration** — your data is migrated to the new store version. If migration fails, your
   data is restored to the previous version and the app restarts on it: *"Your data was
   restored to the previous version."*
4. **Health check** — the new version must prove it can serve before the old one is retired.
5. **Swap** — atomic; the previous version is kept as the rollback target.

If any stage fails, the app is left as it was — the failure message names the stage
(`verification_failed`, `insufficient_disk_space`, …) and each stage has its own recovery
sentence.

## Data compatibility

* Your profile, chats, receipts and settings live outside the app bundle, so replacing the
  app never replaces your data.
* The local store refuses to open **downgrade**: a database written by a newer VOOL will not
  be silently read by an older one — it says so and refuses, because guessing an unknown
  store layout would corrupt it. Reinstall the newer version to read new data.
* Update-installed files and the update state itself are recorded under your profile with
  receipts, so "what version touched my data" is answerable after the fact.

## Beta channel

The current release line is a **beta** (see [Release status](../trust/release-status.md)).
Beta updates are unsigned on macOS — the OS will ask you to confirm on first launch of each
new version.
