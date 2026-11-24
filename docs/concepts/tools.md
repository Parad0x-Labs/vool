---
description: Tools are the actions VOOL can take, and each one is recorded.
---

# Tools

A tool is an action the runtime can take on your behalf — reading a file, running a
command, searching a folder.

## Where tools come from

Most tools come with VOOL: files, notes, email, search, the browser, PDFs and more. Plugins you
install and switch on can add their own (see [Skills and plugins](../guides/skills-and-plugins.md)).
**Let chats use plugin tools**, under **Skills & Plugins → My add-ons → Plugins**, turns all
plugin tools on or off at once.

A skill never adds a tool on its own. A skill from someone else can only narrow which tools are
offered while it is in use. Only a skill you made in VOOL, or one whose tools you allowed on its
card, can bring the tools it lists.

## How VOOL picks a tool

Each tool carries a short card in plain words: when a person needs it, a few example asks, and
asks it is not for. VOOL compares your message with those cards on your own computer, with no
model involved, and offers the tools that fit. The model still decides whether to call one, and
offering a tool changes nothing about whether it has to ask first.

## Inspectable by design

Every tool call records what was requested, what ran, and what came back. A run can be
read back afterwards without re-running it.

## Permission

Whether a tool runs at once or asks first depends on the mode you picked and on the action.
A new chat starts in Manual, where every change asks first. In Auto, creating and editing
files, commands and network actions run without asking, while deleting, overwriting,
installing, Git commits and pushes, deployment, sending messages, payments and changing
settings still ask. In every mode, file tools work in the [workspace](workspaces.md) and in
the home folders listed there, with the limits shown there. Search and fetch tools read
public resources without writing anything, and stored secrets are never read. [Permissions](permissions.md) covers all five modes and what each approval
button allows.

For example, a record can read: "Read the file main.py: 412 lines."

See also [Receipts](receipts.md).
