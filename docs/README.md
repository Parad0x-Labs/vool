---
description: VOOL is a desktop AI assistant that runs AI models locally on your computer or connects to cloud models with your own key. Free, private, for Mac, Windows and Linux.
---

# VOOL

VOOL is a desktop AI assistant. It installs as an application on your computer and can
run AI models locally on that machine, or connect to a cloud AI provider using your own
API key. It keeps a readable record of every action it takes.

It is free, works offline with local models, and runs on macOS, Windows and Linux.

These docs cover installing VOOL, connecting a model, and the concepts behind how it
handles your files, your memory, and your keys.

## Start here

| If you want to | Read |
| --- | --- |
| Get VOOL running | [Install](getting-started/install.md) |
| Understand the first screen | [First run](getting-started/first-run.md) |
| Point it at a model | [Connect a model](getting-started/connect-a-model.md) |
| Control what spending can happen | [Spending limits](guides/spending-limits.md) |
| Know what leaves your machine | [Local and cloud](concepts/local-and-cloud.md) |

## What VOOL is

* **A desktop application.** It installs on your computer — not a browser tab, not a
  hosted service.
* **Works offline.** With a local model selected, VOOL needs no internet connection.
* **Local by default.** With a local model selected, prompts and files are processed
  on your machine.
* **Model-agnostic.** Local runtimes and cloud providers are both selectable. You supply
  the cloud key; it stays on your machine.
* **Inspectable.** Every tool call is recorded with its inputs and results, so a run can
  be read back after the fact.

{% hint style="warning" %}
VOOL for macOS is out as a beta for Apple silicon. Beta installers are unsigned and the
macOS build is not notarized — macOS will ask you to confirm on first launch. See
[Release status](trust/release-status.md) before installing.
{% endhint %}

## Two modes, stated plainly

VOOL distinguishes exactly two states, and the interface always shows which one is active:

1. **Local** — the work stays on this computer.
2. **Cloud** — the selected context is sent directly to the provider you selected,
   from your machine, using your key.

There is no third state, and there is no Parad0x-operated relay in between. The detail
is in [Data handling](trust/data-handling.md).

## Getting help

* [FAQ](help/faq.md) — the questions that come up most
* [Troubleshooting](help/troubleshooting.md) — when something does not start
* [Discord](https://discord.gg/V9NkjP3Fzz) — ask the team
