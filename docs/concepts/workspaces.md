---
description: A workspace is the project folder VOOL works in. Outside it, VOOL can read your visible home folders and change files only in Desktop, Downloads and Documents.
---

# Workspaces

A workspace is the project folder a chat works in. VOOL reads and changes files there,
asking first whenever your operating mode says it should (see [Tools](tools.md)).

## Adding and removing

Pick a folder with **Choose a project folder…** in the chat, or with **+ Project** in the
chat sidebar. Deleting a project makes VOOL forget what it learned in it and moves its chats
back to General. Your files are untouched.

## Your other folders

Outside the workspace, VOOL's file tools can also reach your home folder, with fixed limits:

| Folders | What VOOL can do |
| --- | --- |
| Desktop, Downloads, Documents | Read, and change files, asking first as your operating mode says |
| Pictures, Movies (Videos on Linux), Music | Read only: list, find, open and measure, never change |
| Your other visible home folders, such as `~/Projects` | Read only |
| Hidden folders (such as `~/.ssh`), app and system data (`Library`, `Applications`, `AppData`), VOOL's own data folder | Never |

VOOL looks in these folders only when a request needs it, for example "find my tax PDF" or
"which video in Movies is the biggest". It does not index your drive in the background.

## Scoping advice

* Point a workspace at a project, not at your home directory.
* Keep credential files, key material and password stores in a password manager or a
  hidden folder, not loose in Desktop, Downloads, Documents or a project folder.
* Use a separate project per piece of work so context from one does not reach another.

{% hint style="warning" %}
Anything VOOL reads, in the project folder or in your home folders, can be included in what
is sent to your provider when a cloud model is selected. Scope deliberately.
{% endhint %}
