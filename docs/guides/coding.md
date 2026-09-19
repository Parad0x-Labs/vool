---
description: How to use VOOL as an AI coding assistant on your own codebase — setting the workspace, asking about code, and reviewing edits.
---

# Code with VOOL

## Set the workspace to the repository

Point the [workspace](../concepts/workspaces.md) at the project root. VOOL reads the
files it needs rather than loading the whole tree.

## Ask in terms of the code

Naming a file or a symbol gives the runtime a starting point:

> Find where the session timeout is set in `src/auth/` and explain what happens when it
> expires.

## Review before applying

Edits are proposed as diffs and applied only when you accept them. Commit before a large
change so the diff is easy to read and easy to undo.

## Local or cloud

Coding works in both modes. A 7B local coding model handles most day-to-day edits; larger
cloud models handle longer reasoning. The mode indicator tells you which is active before
you send.
