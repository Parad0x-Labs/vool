---
description: How to use VOOL as an AI coding assistant on your own codebase — setting the workspace, asking about code, and reviewing edits.
---

# Code with VOOL

## Set the workspace to the repository

Press **+ Project** in the chat sidebar and pick the project root (see
[Workspaces](../concepts/workspaces.md)). VOOL reads the files it needs rather than loading
the whole tree.

## Ask in terms of the code

Naming a file or a symbol gives VOOL a starting point:

> Find where the session timeout is set in `src/auth/` and explain what happens when it
> expires.

VOOL can read and search the project, change files, run tests, lint and formatters, and run
commands in a sandbox with no network, each as your mode allows.

## Choose how VOOL may act

The mode menu next to the message box sets what runs without asking:

| Mode | What happens |
| --- | --- |
| **Manual** | Answers and safe reads run directly. File edits, commands, network actions, settings, Git and messages ask first. |
| **Review edits** | VOOL prepares edit diffs; each batch waits for your approval before it is applied. |
| **Plan** | Strictly read-only. VOOL looks at the project and proposes a plan; nothing changes. |
| **Auto** | Ordinary project reads, edits, tests and corrections run on their own. Deleting, publishing, money, credentials and other risky actions still ask. |
| **Bypass permissions** | Tools run without individual prompts in a scope and for a time you approve. |

Commit before a large change so the diff is easy to read and easy to undo. Details are in
[Permissions](../concepts/permissions.md).

## Undo VOOL's last change

Ask "undo the last change you made" and VOOL puts back the files it changed most recently in
this chat's project: changed files return to how they were, and files it created are removed.
It asks first, as your mode says, and tells you which files it restored or removed.

This covers only changes VOOL itself made and kept track of in this chat. It cannot undo your
own edits or changes made by other programs, so commit before a large change as well.

## Long jobs

For work that takes several steps, start it with `/goal` and VOOL keeps working toward it
across turns, with a card that shows each step. See
[Chat features](chat-features.md#work-toward-a-goal).

## Local or cloud

Coding works with local and cloud models. Larger models handle longer reasoning better.
The model button in the chat header tells you which is active before you send: **LOCAL** for
a local model, **CLOUD** for a cloud one, and by default **AUTO** (VOOL Auto) when VOOL
picks.
