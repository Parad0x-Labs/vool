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

Whether a tool runs at once or asks first depends on the mode you picked and on the action.
A new chat starts in Manual, where every change asks first. In Auto, creating and editing
files, commands and network actions run without asking, while deleting, overwriting,
installing, Git commits and pushes, deployment, sending messages, payments and changing
settings still ask. In every mode, file tools work only inside the [workspace](workspaces.md),
search and fetch tools read public resources without writing anything, and stored secrets
are never read. [Permissions](permissions.md) covers all five modes and what each approval
button allows.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

See also [Receipts](receipts.md).
