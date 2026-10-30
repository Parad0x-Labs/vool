---
description: Where VOOL stores its configuration and what the settings mean.
---

# Configuration

## Locations

VOOL keeps its settings, chats, memory and records in one runtime home:

| Install | Runtime home |
| --- | --- |
| macOS app | `~/Library/Application Support/VOOL/runtime` |
| Linux tarball and source installs | `~/.vool_runtime` |
| Windows (experimental, from source) | `%LOCALAPPDATA%\VOOL` |

Setting `VOOL_HOME` before starting VOOL uses that folder instead. The `vool` command prints
the home in use.

Keys are **not** stored here. On a Mac they are kept in your Mac's Keychain (after you
allow that once). On Linux and Windows they are kept in an encrypted file inside your VOOL
profile.

## Settings

Change settings in the app under **Settings**. The ones that decide what VOOL may do:

* **Model** — the active model, which decides whether a turn is LOCAL or CLOUD.
* **Workspace** — the folders the runtime may read and write.
* **Operating mode** — Manual asks before every change; Auto lets everyday edits and
  commands run, while deleting, overwriting, installing, spending and changing settings
  still ask. See [Tools](../concepts/tools.md).
* **Web access and crypto** — both stay off until you turn them on.

VOOL has no product telemetry setting because it sends no product telemetry.

## Resetting

Quit VOOL and move the runtime home aside. Workspace files are untouched; VOOL starts
again with a fresh profile. Saved keys stay where they were kept — the Mac Keychain, or the
encrypted file inside the old profile — until you remove them.
