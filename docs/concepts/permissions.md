---
description: The five modes, what each lets VOOL do without asking, and what every approval button covers.
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

## Payments and spending

Approving an action never approves a payment. Payments go through the wallet's own proposal and
approval, with its limits, in every mode. Paid model calls are held by your spend caps and price
guards before the provider is contacted. See [Wallet](../guides/wallet.md) and
[Spending limits and price guards](../guides/spending-limits.md).

## When an action is refused

A refused action changed nothing, and the message says what was refused and why. The codes
you may see are [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) for a change outside the allowed
folder, and [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) for a tool this mode does
not offer. See [Errors and error codes](../reference/errors.md).
