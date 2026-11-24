---
description: The five modes, what VOOL may do alone, what every approval button covers, phone and chat app rights, and the preview before a cloud model sees your files.
---

# Permissions

VOOL decides what may run before anything runs. The model can ask for an action; the mode you
picked and your answers decide whether it happens. Reading is treated differently from
changing, and secrets, payments and security settings are guarded in every mode.

## The modes

Pick the mode in the menu next to the message box. A new chat starts in **Manual**.

| Mode | Runs without asking | Asks first | Never |
| --- | --- | --- | --- |
| Manual | Reading and listing files, safe commands, and public read-only web lookups | Every change: file edits, commands, network actions, settings, Git, deployment and messages | Reading stored secrets |
| Review edits | The same as Manual | The same as Manual. Each batch of edits is shown as a reviewable change before it is applied | Reading stored secrets |
| Plan | Reading and listing files, and safe commands | Nothing: it only reads and proposes a plan | Every change, every network action |
| Auto | Reading, creating and editing files, commands, network and browser actions, and model providers | Deleting, overwriting existing files, installing dependencies, changing provider settings, Git commit, push, merge and reset, deployment, sending messages, payments, and changing settings | Reading stored secrets, changing security settings, and any action whose effect VOOL cannot identify |
| Bypass permissions | Everything except the four protected classes | Payments | Reading stored secrets, changing security settings, and any action whose effect VOOL cannot identify |

A mode, a skill or a plugin can only narrow what is offered. None of them grants an action the
mode does not allow.

## What VOOL may do alone

Separate from the mode, one setting says how often VOOL stops to ask for ordinary steps. You pick it
during setup, and you can change it in **Settings → Privacy & Permissions → What VOOL may do alone**:

| Choice | What it means |
| --- | --- |
| Just do routine work | Fewest questions. Routine steps on this computer run without asking. This is the starting setting. |
| Ask before risky changes | Routine work runs; anything that deletes or changes a lot asks first. |
| Ask me first | It stops and asks before every action. Slowest, safest. |

Whatever you pick, sending a message, posting, anything private and spending money always ask you
first. The mode still sets the outer limit: a choice here never allows what the mode does not.

## Answering a permission request

When an action needs you, a **Permission needed** bar opens above the message box. It names
the exact action: the command line that will run, or the files that will change, whether the
change can be undone, and a preview of the edits. Nothing runs until you answer, and the request
waits for you even if VOOL restarts. A request that ran out of time shows as expired and cannot
be approved.

The bar offers only the choices that fit the action:

| Button | What it allows |
| --- | --- |
| Allow once | This one exact action. |
| Allow for this task | The same exact action again during this task, in the same mode. Offered for actions that are not file edits. |
| Allow all *N* planned changes for this request | The file changes this request has already planned, the exact files listed under Review details, and nothing else. |
| Allow workspace edits in this chat | Reads, new files and edits inside this chat's workspace, for eight hours or until VOOL restarts or the chat is deleted. |
| Allow in this project | Reads and file writes inside the project's folder. This stays for the project, also after a restart and in new chats. |
| Review details | Shows the full request before you decide. |
| Deny | The action does not run. |

Whatever you allow, deletes, moves, overwrites, commands, network actions, secrets and payments
still ask separately each time, unless the mode itself allows them.

## Bypass permissions

Bypass runs the available tools without individual prompts, inside a scope and for a time you
choose:

* **Scope:** the current task only, this chat session, or this project.
* **Duration:** 15 or 30 minutes, 1, 2, 4 or 8 hours, a custom length from 15 to 1440 minutes,
  or *Until I turn it off*, for this chat only.

A timed bypass works in any chat. *Until I turn it off* needs the chat to be linked to a project
folder, and that folder cannot be your whole home folder, the top of the disk, or a folder that
holds VOOL's own data. While bypass is on, a banner shows its scope and when it ends, and its
**×** turns it off at once; the chat returns to Manual. Deleting the chat ends its bypass too.
Bypass never grants access outside the workspace and cannot let a model widen its own
permissions. Payments still ask, and secrets and security settings stay out of reach.

## Phones and chat apps

A paired phone or a connected chat app can only do what you switched on for it in
**Settings → Phone and chat apps**, for example **Use tools on my computer**, **Look things up
online**, or for a chat app **Reminders, calendar, notes and contacts**. That switch is your answer
for what it covers, so an action from that phone or app that the switch covers is not asked again on
the computer screen, where the person holding the phone cannot see it.

Money, secrets, VOOL's own settings and actions whose effect VOOL cannot identify are never covered
by a phone or chat app right. Payments from a phone go through its Wallet tab and are approved on the
computer. A phone limited to chosen folders can only reach files inside them. See
[Phone](../guides/phone.md).

## Before a cloud model sees your files

When a request to a cloud model would carry files from your project, VOOL first shows
**See what leaves your computer**: the destination, the size, and the exact sections it would send,
such as files attached here, remembered information and tool results. Nothing is sent until you
choose:

* **Send as shown** sends exactly what you see.
* **Send only these parts** lets you leave sections out.
* **Replace names and details** swaps personal details for placeholders before sending.
* **Use a local model instead** answers on this computer.
* **Don't send** stops it.

The preview also appears, whatever the setting, when the request holds sensitive content, when a
project rule says to ask, or when privacy rules kept part of it on your computer. The setting is
**Show me what leaves before cloud models see my files** in
**Settings → Privacy & Permissions → Before files go to cloud**. It is on to start with. The same
place holds **Project cloud rules**: for each folder in a project, **Stays on this computer**,
**Ask each time** or **May go to cloud**.

After an answer, the line under it says where it was answered and how much text went online. See
[Check your VOOL](../guides/check-your-vool.md#where-each-answer-was-answered).

## Payments and spending

Approving an action never approves a payment. Payments go through the wallet's own proposal and
approval, with its limits, in every mode. Paid model calls are held by your spending limits and price
guards before the provider is contacted; see [Check your VOOL](../guides/check-your-vool.md#ai-spending). See [Wallet](../guides/wallet.md) and
[Spending limits and price guards](../guides/spending-limits.md).

## When an action is refused

A refused action changed nothing, and the message says what was refused and why. The receipt
under the answer shows it as **Not done**, or **Waiting for your OK** while it waits for you (see
[Receipts](receipts.md)). The codes
you may see are [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) for a change outside the allowed
folder, and [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) for a tool this mode does
not offer. See [Errors and error codes](../reference/errors.md).
