---
description: Fixes for the problems that come up most.
---

# Troubleshooting

## macOS refuses to open the app

Expected — the build is not notarized. **System Settings → Privacy & Security → Open
Anyway**. Check the checksum first.

## Windows SmartScreen blocks the installer

Expected — the build is unsigned. **More info → Run anyway**, after checking the checksum.

## No local models are listed

1. Confirm the runtime is running.
2. Confirm at least one model is pulled.
3. Confirm the port matches what VOOL expects.

```bash
curl http://localhost:11434/api/tags
```

If that returns nothing, the runtime is not up.

## The OpenRouter connection does not come back to the app

The callback page hands control to VOOL through a `vool://` link. If the browser does
nothing, VOOL is not installed or not running. Start VOOL and connect again from
**Settings → Models**.

## Responses are slow

Usually model size against available memory. See
[Run a local model](../guides/local-models.md).

## Starting clean

Quit VOOL and remove the configuration directory listed in
[Configuration](../reference/configuration.md). Workspace files are not touched.
