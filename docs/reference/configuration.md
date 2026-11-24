---
description: Where VOOL stores its configuration, what the main settings mean, and the local address other apps can use.
---

# Configuration

## Locations

VOOL keeps its settings, chats, memory and records in one runtime home:

| Install | Runtime home |
| --- | --- |
| macOS app | `~/Library/Application Support/VOOL/runtime` |
| Linux tarball and source installs | `~/.vool_runtime` |

Setting `VOOL_HOME` before starting VOOL uses that folder instead.

Keys are **not** written into settings files or logs. They are kept encrypted in a separate
credential file in this runtime home, readable only by your user account; keys that VOOL
already kept in the macOS Keychain on a Mac stay there. See
[Security model](../trust/security.md#keys).

## Settings

Change settings in the app under **Settings** (`⌘ ,` or `Ctrl+,`). Its groups are
**General**, **Privacy & Permissions**, **API Keys**, **Email**, **Models & Providers**,
**Memory & Personalisation**, **Backup & restore**, **Usage & Budgets**, **Calendars**,
**Notifications**, **Crypto**, **Appearance**, **Phone and chat apps**, **Advanced** and
**About & Diagnostics**. Until setup is finished, **Complete setup** comes first; it goes away
once every step is done or you ask not to be reminded.
**Search settings** at the top finds a setting by name.

The ones that decide what VOOL may do:

* **Model** — chosen in the model menu in the chat header: VOOL Auto with a run mode
  (**Local first**, **Computer + web**, **Offline**) or an exact model. See
  [Local and cloud](../concepts/local-and-cloud.md).
* **Workspace** — the project folder a chat works in; see [Workspaces](../concepts/workspaces.md)
  for the other folders VOOL can reach.
* **Mode** — picked in the menu next to the message box: **Manual**, **Review edits**,
  **Plan**, **Auto** or **Bypass permissions**. See [Permissions](../concepts/permissions.md).
* **Web lookups** and **Go online on its own** (Privacy & Permissions) — both off on a fresh
  install. Crypto is off too until you turn it on.

How VOOL writes is under **General**: **Talk style**, **Humour**, **Swearing**, **Character**,
**Style notes**, **Answer language**, **Show VOOL's work**, **Deep reasoning** and **Show answer
details**. **Theme and text size** is under **Appearance**.

More rows people ask about:

* **Content boundaries** (Privacy & Permissions) — how cautious VOOL is about subject matter:
  **As I set them in conversation** (the default: VOOL follows what you have told it),
  **Relaxed**, **Standard** or **Strict**. It changes how careful VOOL is about topics, not
  what it may do on your computer.
* **Use the model's own tools** (Models & Providers) — off by default. When on, a cloud model
  you choose answers without VOOL's tools or web research. A few paid models then search the
  web themselves: your question goes to their provider, and that fee counts against your
  spending limit. Auto and local models keep VOOL's tools. In chat, say "use the model's own
  tools" or "use VOOL's tools"; a **Model's own tools** banner shows while it is on.
* **Email signature** (Memory & Personalisation) — added at the end of emails VOOL writes for
  you, up to 400 characters. An empty box removes it.
* **Usage & Budgets** — besides **Paid models**:
  * **Usage** shows, per model, the **Answers**, **Word pieces** and **Cost**, marked
    **Free, on this computer** or **Paid, online**.
  * **Memory kept free for your other apps** — 10 to 80%, 20% by default. VOOL never uses this
    share of your computer's memory, even during heavy work. This is a firm limit.
  * **Daily cloud use guide** — a number of word pieces a day, 0 for none. It is a guide only:
    nothing stops when it is reached.
* **Christmas hat** (Appearance) — from 1 December to 6 January the VOOL logo wears a small
  hat. Turn it off here.
* **Hide my name on screen** (Advanced) — off by default. Turn it on before you stream or share
  your screen: VOOL shows your home folder as `~` and your name and computer's name as `•••`,
  here and on your phone. Only what you see changes. Text you are typing is not hidden.
* **Extra pages** (Advanced) — **Open the activity page** shows step by step what VOOL is doing,
  in its own window. See [More in VOOL](../guides/more-in-vool.md#the-activity-panel).
* **About & Diagnostics** — **Check VOOL** (VOOL Doctor), **App updates**, **This build** (your
  exact version, to copy into a problem report) and **What VOOL can do right now** (models on
  this computer, the online connection, web search and plugins, each with whether it was
  checked).

VOOL has no product telemetry setting because it sends no product telemetry.

## Local chat address for other apps

VOOL serves its chat on this computer at `http://127.0.0.1:11435`. Other apps on the same
computer can use it like an OpenAI-compatible or Ollama-compatible server:

| Path | What it does |
| --- | --- |
| `/v1/chat/completions` | A chat turn (OpenAI-style) |
| `/api/chat` | A chat turn (Ollama-style) |
| `/v1/models`, `/api/tags` | The model list |

A request from another device on the network runs as a plain chat with no history, no profile
and no tools on this computer; see [Security model](../trust/security.md#your-local-network).

## Resetting

Quit VOOL and move the runtime home aside. Workspace files are untouched; VOOL starts
again with a fresh profile. Your saved keys move aside with it, so add them again in
**Settings → API Keys**. Keys kept in the macOS Keychain stay there until you remove them in
Keychain Access.
