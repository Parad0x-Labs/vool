---
description: Where VOOL stores its configuration and what the settings mean.
---

# Configuration

## Locations

| Platform | Path |
| --- | --- |
| macOS | `~/Library/Application Support/VOOL/` |
| Windows | `%APPDATA%\VOOL\` |
| Linux | `~/.config/vool/` |

Keys are **not** stored here. They live in the operating system credential store —
Keychain on macOS, Credential Manager on Windows, Secret Service on Linux.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `model` | last used | Active model; determines LOCAL or CLOUD |
| `workspace` | none | Folders the runtime may read and write |
| `telemetry` | off | Product telemetry |
| `tool_confirm` | on | Ask before a tool changes anything |

{% hint style="warning" %}
Turning `tool_confirm` off lets the runtime modify files without asking each time. Leave
it on unless you are working in a directory you can throw away.
{% endhint %}

## Resetting

Quit VOOL and remove the configuration directory. Workspace files are untouched; only
VOOL's own settings are cleared.
