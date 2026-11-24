---
description: Fixes for the problems that come up most.
---

# Troubleshooting

## macOS refuses to open the app

Expected: the app is not notarized yet. Check the checksum first, then open **System
Settings → Privacy & Security** and click **Open Anyway**. On macOS 14 you can instead
Control-click VOOL and choose **Open**.

## No local models are listed

1. Confirm the local model runtime is installed — on Linux, install
   [Ollama](https://ollama.com) first if you have not
   (see [Run a local model](../guides/local-models.md#install-a-runtime)).
2. Confirm the runtime is running.
3. Confirm at least one model is pulled.
4. Open **Settings → Models & Providers → This Mac** (or **This computer**). If it says it
   can't see the models, make sure Ollama is open, then reopen Settings.

## A local model is listed but does not answer

VOOL checks each local model once before it answers with it. Press **Use this model** next to
it. **Check didn't finish** means Ollama was not open or the computer was slow: press **Try
again**. **Not a good fit** means the model cannot use VOOL's tools well enough; use the model
VOOL suggests, or a cloud model.

## A PDF will not open on Linux

VOOL reads PDFs only inside a sandbox. It uses bubblewrap where it can start, and otherwise
the kernel's Landlock sandbox (stock Ubuntu 24.04 has it). On a system with neither, install
bubblewrap, or paste the text into the chat instead.

## Live facts (weather, prices, news) are not looked up

Turn on **Web lookups** in **Settings → Privacy & Permissions**, and make sure the chat is not
set to **Offline** in the model menu.

## Responses are slow, or the computer is slow

Usually model size against available memory. See
[Run a local model](../guides/local-models.md).

You can also ask VOOL in any chat:

* "Why is my Mac so slow?" or "what's using my memory?" shows how much memory is in use right
  now and what is using the most.
* "Free up memory" or "unload models" lets go of models held in memory and shows the numbers
  before and after.
* "Clear my temp files" shows what it would remove and asks before it removes anything.

**Settings → Usage &
Budgets → Memory kept free for your other apps** sets how much memory VOOL always leaves for
your other apps.

## Reporting a problem

**Settings → About & Diagnostics** has **Check VOOL** (VOOL Doctor), **This build**, your
exact version to copy into a problem report, and **What VOOL can do right now**, which lists
the models on this computer, the online connection, web search and plugins, each with
whether it was checked. Something not checked yet says so; that does not mean it is broken.

## An error shows a code

Every failure has a stable code. [Errors and error codes](../reference/errors.md) explains
how to read one, and the [Error Book](../ERROR_BOOK.md) says for each code what happened,
what may already have happened and what to do next.

## Starting clean

Quit VOOL and remove the runtime home folder listed in
[Configuration](../reference/configuration.md). This deletes your chats, memory, settings,
saved keys and any wallet VOOL keeps on this computer, so save what you need first: see
[Backup and recovery](backup-and-recovery.md). Workspace files are not touched.
