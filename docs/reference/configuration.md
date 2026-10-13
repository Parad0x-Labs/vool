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

Setting `VOOL_HOME` before starting VOOL uses that folder instead. Startup logs print the
home in use.

Keys are **not** stored here. They live in the operating system credential store —
Keychain on macOS, Credential Manager on Windows, Secret Service on Linux.

## Settings

Change settings in the app under **Settings**. The ones that decide what VOOL may do:

* **Model** — the active model, which decides whether a turn is LOCAL or CLOUD.
* **Workspace** — the folders the runtime may read and write.
* **Operating mode** — Manual asks before every change; Auto lets everyday edits and
  commands run, while deleting, overwriting, installing, spending and changing settings
  still ask. See [Tools](../concepts/tools.md).
* **Online features** — web access, update checks and crypto stay off on a fresh install
  until you turn them on.

VOOL has no product telemetry setting because it sends no product telemetry.

## Resetting

Quit VOOL and move the runtime home aside. Workspace files are untouched; VOOL starts
again with a fresh profile. Keys in the credential store stay until you remove them there.
