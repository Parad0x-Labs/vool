---
description: Everything you can do in a VOOL chat — goals, queued messages, stopping, attachments, importing and exporting chats, pins, search, projects, files, the command palette, council review, charts and the local chat address.
---

# Chat features

A chat is where you ask VOOL for things. This page lists what the chat window can do. For
attachments and private chats in detail, see [Chats and attachments](../concepts/chats-and-attachments.md).

## Send, queue and stop

* Type in the message box and press `Enter` (`Shift Enter` for a new line). The answer
  streams in as it is written.
* **Queue while VOOL works.** While an answer is running, **Send** becomes **Wait and send**.
  Your message waits its turn and runs when the current answer is done. Waiting messages
  show above the message box; cancel one with its ×. A message that is already running
  cannot be cancelled this way. Waiting messages survive a restart of VOOL.
* **Stop.** Press `Esc`, or use the stop control on the working card. VOOL stops the turn on
  your computer. Part of the work may already have run, so check what changed before you
  start it again.
* **Fix your last message.** In an empty message box, press `↑` to bring back your last
  message, change it and send it again.

## Work toward a goal

For a job that takes several steps, type `/goal` followed by what you want done, for example
`/goal tidy the Downloads folder into dated folders`. VOOL makes a plan and works through it
across turns. A **Goal** card above the message box shows the state (**Planning**,
**Running**, **Waiting for approval**, **Paused**, **Completed** and so on) and has
**Pause**, **Resume**, **Stop** and **Resume step**.

* `/goal status`, `/goal pause`, `/goal resume` and `/goal stop` do the same from the message
  box. `/goal revise …`, or a message that starts with `goal:`, changes the active goal.
* One goal per chat. Every step still asks for approval as your mode says.
* A step counts as done only when what it was meant to do really happened, not because the
  model said so.

## Attach files

Use the paperclip next to the message box, or drag files onto it. Up to 8 files per message,
10 MB per file and 25 MB per message. Details, file types and refusals are in
[Chats and attachments](../concepts/chats-and-attachments.md).

## Chat list, projects and search

* **+ New** starts a chat. **+ Project** picks a folder; chats in a project work in that
  folder and stay apart from other projects. See [Workspaces](../concepts/workspaces.md).
* In the chat list, each chat has **Rename**, **Archive** and **Delete**. Archived chats are
  under **Archived** and can be brought back with **Unarchive**. Deleting a chat removes it
  and everything VOOL learned in it.
* **Find a chat by name** with the box under the chat list (`⌘ F` / `Ctrl+F`).
* **Search** in the chat header searches the open chat: messages, answers and tool results
  (`⌘ Shift F` / `Ctrl+Shift+F`). `Enter` and `Shift Enter` move between matches.

## Pin messages

Hover a message and choose **Pin**. **Pinned messages** in the chat header lists the pins of
this chat; **Unpin** removes one.

## Export a chat

**Export** in the chat header saves the whole chat, every message and not only what is on
screen, as **Markdown (.md)**, **Plain text (.txt)** or **Document (.pdf)**. You can include
the time each answer arrived and list attached files by name (never their contents). How VOOL
worked things out and its activity log are not included.

To move a chat to another VOOL, or keep a signed copy, see
[Backup and recovery](../help/backup-and-recovery.md).

## Bring chats from other AI apps

Open **Settings → Backup & restore → Bring your chats from other AI apps**.

1. Choose where your chats are: an AI app VOOL found on this computer, or **A data export
   file (.zip)** you downloaded from another AI app's settings.
2. VOOL lists the chats it found. Tick the ones you want, or **Select all**, then
   **Bring in chats**.
3. Optionally, for each chat, **Bring working context**: choose the project folder it was
   about and review the instructions, memories, skills, tools and tasks found with it. Each
   kind is saved only if you tick it: instructions as rules, skills as drafts, tools turned
   off, tasks paused.

Good to know:

* VOOL reads the other app's files only when you choose it, and never changes them. Nothing
  is sent anywhere.
* Imported chats keep their visible messages, titles and dates. Hidden reasoning and tool
  messages are left out, and secrets are masked. Attachments keep their names only.
* **Use this history with a local model when I continue these chats** is off at first.
* **Undo import** removes a batch again, as long as you have not continued its chats.

## Files VOOL made

**Files** at the bottom of the chat sidebar lists everything VOOL generated, across all
chats, with a search box and sorting. Choosing a file opens it on your computer. Files in
your project folder that you made yourself are not listed there.

## Command palette

Press `⌘ K` (`Ctrl+K` on Linux) and type. The palette finds actions such as **New chat**,
**Search this chat**, **Export this chat…**, **Pinned messages**, **Choose a model…**,
**How much VOOL may do alone…**, **Council…**, **Skills & plugins…**, **Files VOOL made…**,
**Report a problem…**, **Contacts…**, **Insert a saved contact…** and the pet actions.
All keyboard shortcuts are in [Keyboard shortcuts](../reference/shortcuts.md).

## Council review

**Council** in the chat header asks several cloud models to look at one question together.
It needs at least one cloud provider key (see [Provider keys](provider-keys.md)).

1. Open **Council** and go to **Ask several models**.
2. Each seat is a role, such as **Builder (answers)**, **Critic (looks for mistakes)** or
   **Fact checker**. Pick an online model for each seat. Seats that vote decide together;
   advisors only comment.
3. Type the **Question for the council**. **Background (optional)** is anything they should
   read first; leave it empty and each seat looks on its own.
4. Press **Convene**. Each round shows which seats have answered, and the verdict card shows
   where they agree or disagree. **Stop council** ends it early.

While the council is in session, the chat's model choice is held. Past runs stay listed under
**Past runs**. On a council card in the chat, **Watch this run** lets VOOL keep an eye on it:
questions go to **Needs you** in the bell and reports to **Updates**. **Stop watching** ends
that. Model output is not final truth; the council shows evidence and disagreement so you can
judge.

## Charts and diagrams in answers

When an answer includes a chart (bar, line, pie or doughnut) or a diagram (such as a
flowchart), VOOL draws it in the chat. Drawing happens on this computer in a sealed frame,
with drawing tools that come with VOOL. You can open the source under the picture.

## Make answers your way

**Settings → General** sets how VOOL writes: **Talk style**, **Humour**, **Swearing**,
**Character** (a role VOOL plays until you clear it), **Style notes** (for example "be
concise") and **Answer language**. Under **Answers**, **Show VOOL's work** shows the steps it
took, **Deep reasoning** lets local models think first (noticeably slower, but more careful),
and **Show answer details** adds a small line under each answer saying which model or tool made
it.

## Long chats

In a long chat, VOOL keeps the most recent messages as they are and condenses older ones into
a summary for the model to read, so the chat keeps working. Key facts and decisions are kept
word for word in that summary. This happens automatically; your saved chat is not shortened.

## Dictation and voice

The microphone and voice conversation buttons are shown next to the message box, but both
are unavailable in this beta build. Type your message instead.

## Use VOOL from other apps on this computer

VOOL also answers chat requests from other apps on the same computer, as an
OpenAI-compatible or Ollama-compatible chat server. The address is in
[Configuration](../reference/configuration.md#local-chat-address-for-other-apps). A request
from another device on your network gets plain chat only: no history, no profile and no tools
on this computer.
