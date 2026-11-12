---
description: VOOL is a desktop AI assistant that runs AI models locally on your computer or connects to cloud models with your own key. Free and private. Downloads for Mac (Apple Silicon and Intel) and Linux; Windows is experimental from source.
---

# VOOL

VOOL is a desktop AI assistant. It installs as an application on your computer and can
run AI models locally on that machine, or connect to a cloud AI provider using your own
API key. It keeps a readable record of every action it takes.

It is free. With a local model, your chats and files need no internet connection; live facts
such as weather or prices come from public web services only while Web lookups is on. There are
downloads for Macs (Apple Silicon and Intel) and Linux x86_64; Windows is experimental and
installs from source.

These docs cover installing VOOL, connecting a model, and the concepts behind how it
handles your files, your memory, and your keys.

## How it measures up

On 150 never-used LoCoMo memory questions, with the same model answering for every agent and the
same GPT-4o grader, VOOL's memory beat two other open-source agents:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Right answers | **82.7%** (124 / 150) | 71.3% (107) | 62.7% (94) |
| Tokens per question | **9.6k** | 51.8k | 33.5k |
| Cost per question | **$0.0033** | $0.0100 | $0.0046 |

That is 5.4 times fewer tokens than OpenClaw and 3.5 times fewer than Hermes, at a third of
OpenClaw's cost per answer. The memory shipped in VOOL 0.7 scored 123 of 150 on the same questions.
Every run, the setup and the weak spots are published: [memory benchmarks](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

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
* **Works offline for chats and files.** With a local model selected, your chats and files need
  no internet connection. Live facts such as weather or prices come from public web services only while Web lookups is on (it is off on a fresh install), and never in Local Only.
* **Local by default.** With a local model selected, prompts and files are processed
  on your machine.
* **Model-agnostic.** Local runtimes and cloud providers are both selectable. You supply
  the cloud key; it stays on your machine.
* **Inspectable.** Every tool call is recorded with its inputs and results, so a run can
  be read back after the fact.

{% hint style="warning" %}
VOOL 0.7.5-beta is out for Apple Silicon Macs, Intel Macs and Linux x86_64. The Mac apps
are not notarized yet, so macOS asks you to confirm the first time you open one; the
[Install](getting-started/install.md#first-open-on-a-mac) page shows how. See
[Release status](trust/release-status.md) before installing.
{% endhint %}

## Two ways to answer, stated plainly

VOOL answers in two ways, and the chat header always shows which way the next request
runs — for example **AUTO** with **Local first**:

1. **Local** — the work stays on this computer.
2. **Cloud** — the selected context is sent directly to the provider you selected,
   from your machine, using your key.

There is no Parad0x-operated relay in between. The detail
is in [Data handling](trust/data-handling.md).

## Getting help

* [FAQ](help/faq.md) — the questions that come up most
* [Troubleshooting](help/troubleshooting.md) — when something does not start
* [Discord](https://discord.gg/V9NkjP3Fzz) — ask the team
