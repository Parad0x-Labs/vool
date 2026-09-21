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

Tools that change something ask first. Tools that only read operate inside the
[workspace](workspaces.md).

```text
tool: read_file
path: src/main.py
result: 412 lines
```

See also [Receipts](receipts.md).
