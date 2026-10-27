---
description: How VOOL updates itself, what happens to your data, and version compatibility.
---

# Updates and migration

## How updates work

From 0.7.5 on, VOOL updates itself: with "Go online on its own" on, it shows a notice and installs a new version with one press. Updating from 0.7.4 or 0.7.3 on a Mac: turn on "Go online on its own" (Settings > Privacy & Permissions) and press Check for updates once. On Linux, or from 0.7.2, 0.7.1 or 0.7.0: download 0.7.5 and install it over your current version. Your chats and settings live in your profile, outside the app.

With "Go online on its own" on, VOOL checks for a new version when it starts and once a
day, and shows a notice without a Check press. With it off, VOOL fetches nothing and the
header says **Update checks off**.

A signed update package runs a verified pipeline with automatic rollback:

1. **Download** — verified against a signed manifest.
2. **Staged install** — the new version is installed beside the old one.
3. **Migration** — your data is migrated to the new store version. If migration fails, your
   data is restored to the previous version and the app restarts on it: *"Your data was
   restored to the previous version."*
4. **Health check** — the new version must prove it can serve before the old one is retired.
5. **Swap** — atomic; the previous version is kept as the rollback target.

On Linux, the tarball install updates the same way. The new version is installed next to
the running one, and `current` switches to it in one step. If the new version is not
healthy, VOOL switches back to the previous one by itself. A manual **Roll back** is offered
on the Mac and for source installs, but not yet for the Linux tarball install.

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
Updates are signed by the pinned publisher key and checked before they install. The macOS
app itself is ad-hoc signed and not notarized, so macOS may ask you to confirm the first
launch of a new version.
