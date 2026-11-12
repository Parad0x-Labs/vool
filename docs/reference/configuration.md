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

Keys are **not** written into settings files or logs. They are kept encrypted in a separate
credential file in this runtime home, readable only by your user account; keys that VOOL
already kept in the macOS Keychain on a Mac stay there. See
[Security model](../trust/security.md#keys).

## Settings

Change settings in the app under **Settings**. The ones that decide what VOOL may do:

* **Model** — the active model, which decides whether a turn is LOCAL or CLOUD.
* **Workspace** — the project folder a chat works in; see [Workspaces](../concepts/workspaces.md)
  for the other folders VOOL can reach.
* **Operating mode** — picked in the menu next to the message box, not here. Manual asks
  before every change; Auto lets everyday edits and commands run, while deleting,
  overwriting, installing, spending and changing settings still ask. See
  [Permissions](../concepts/permissions.md).
* **Web access and crypto** — both stay off until you turn them on.

VOOL has no product telemetry setting because it sends no product telemetry.

## Resetting

Quit VOOL and move the runtime home aside. Workspace files are untouched; VOOL starts
again with a fresh profile. Your saved keys move aside with it, so add them again in
**Settings → API Keys**. Keys kept in the macOS Keychain stay there until you remove them in
Keychain Access.
