---
description: Tools are the actions VOOL can take, and each one is recorded.
---

# Tools

A tool is an action the runtime can take on your behalf — reading a file, running a
command, searching a folder.

## Inspectable by design

Every tool call records what was requested, what ran, and what came back. A run can be
read back afterwards without re-running it.

## Permission

Approval depends on the operating mode and the action. In Manual mode every change asks
first. In Auto mode, everyday changes — creating and editing files, running commands —
can run automatically, while deleting, overwriting, installing, spending, deploying,
sending messages, and changing settings or git history still ask. Under both modes the
standing checks remain: file tools operate only inside the [workspace](workspaces.md),
and search or fetch tools read public resources without writing anything.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

See also [Receipts](receipts.md).
