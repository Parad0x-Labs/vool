---
description: Fixes for the problems that come up most.
---

# Troubleshooting

## macOS refuses to open the app

Expected: the app is not notarized yet. Check the checksum first, then open **System
Settings → Privacy & Security** and click **Open Anyway**. On macOS 14 you can instead
Control-click VOOL and choose **Open**.

## Windows

There is no Windows download in this release. Windows installs from source and is
experimental; see the [install guide](../getting-started/install.md#install-from-source).

## No local models are listed

1. Confirm the runtime is running.
2. Confirm at least one model is pulled.
3. Confirm the port matches what VOOL expects.

```bash
curl http://localhost:11434/api/tags
```

If that returns nothing, the runtime is not up.

## Responses are slow

Usually model size against available memory. See
[Run a local model](../guides/local-models.md).

## An error shows a code

Every failure has a stable code. [Errors and error codes](../reference/errors.md) explains
how to read one, and the [Error Book](../ERROR_BOOK.md) says for each code what happened,
what may already have happened and what to do next.

## Starting clean

Quit VOOL and remove the runtime home folder listed in
[Configuration](../reference/configuration.md). This deletes your chats, memory, settings,
saved keys and any wallet VOOL keeps on this computer, so save what you need first: see
[Backup and recovery](backup-and-recovery.md). Workspace files are not touched.
