---
description: How VOOL remembers you across chats, how you see and change what it keeps, and how forget works.
---

# Memory

VOOL remembers what you tell it and carries it into later chats. Everything it remembers is kept
on this computer. You can see every item, change it, pause it, export it and forget it.

## How well it remembers

On 150 never-used questions from the LoCoMo long-conversation benchmark, the memory shipped in
VOOL 0.7 answered 123 right (82.0%). In the head-to-head round on the same questions, with the same
model answering for all three, VOOL got 124 (82.7%), OpenClaw 107 (71.3%) and Hermes 94 (62.7%),
while sending 3.5 to 5.4 times fewer tokens per question. Every run is published:
[memory benchmarks](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Telling VOOL to remember

Say it in a chat, in your own words: "Remember that I like short answers." Everyday wording such as
"keep in mind that..." works too. VOOL answers **Locked in. I'll remember that.** only when the item
was really saved.

Under the answer, a receipt line reads back the save: **Done, checked** means VOOL found the item in
its memory after saving it. If a save was refused, the line says **Not done**. See
[Receipts](receipts.md).

VOOL does not save:

* passwords, PINs, keys and other secrets;
* anything said in a private chat (see below);
* words written by an agent or pasted from somewhere else, as if they were your own;
* something you already asked it to forget.

## Recall across chats

Ask about your own facts in any chat: "What is my hotel booking code?" VOOL looks in this order:

1. what it saved for you;
2. this chat;
3. your other chats and projects.

When the answer comes from another chat, VOOL quotes your own words from it and names the chat, its
project and the day, then offers to remember it. Only things you wrote count. A question you asked,
or an earlier VOOL answer, is never used as the source.

When nothing is found, VOOL says so plainly: **I don't have that saved. If you tell me, I'll
remember it.**

Ask "what do you remember?" to see the list in the chat.

## Standing instructions

Tell VOOL how to work from now on, and it keeps that rule in every later chat:

* "From now on, use kilometres."
* "Always put the total first."
* "Never add a summary line."

A correction such as "No, use kilometres, not miles" is kept as a rule too. One-off wording
("this time", "just for now", "in this chat") is not kept. To drop a rule, say so: "stop adding the
summary line" or "forget the rule about units".

Rules belong to the workspace where you gave them. A rule given in one workspace does not reach
another.

## Where an item applies

Each remembered item has a scope. You can change it in Settings with **Move scope**:

| Scope | Applies |
| --- | --- |
| Everywhere | In every chat |
| Work | In work chats only |
| Personal | In personal chats only |
| One project | Only in that project's chats |
| One chat | Only in that chat. It never leaves it. |

When two items disagree, the narrower one wins: one chat, then one project, then work or personal,
then everywhere. Deleting a project makes VOOL forget what it learned in it.

When VOOL only guesses a preference from how you talk, it does not save it straight away. It asks you
in the chat, and you choose to save it, edit it, or keep it for that chat only.

## Private chats

Say "private chat" or "don't record this". From that message on, nothing in the chat is saved: not
the messages, not a summary, not memory. Private messages show a small ghost, and the chat shows
**Private chat: nothing is saved**. Say "turn recording back on" to save again. VOOL cannot recall
the private part later.

## See and change what VOOL remembers

Open **Settings → Memory & Personalisation → About you**.

* **What VOOL remembers** lists every item with its value, scope, where it came from and when it
  was last used. Each item has **Edit**, **Forget**, **Move scope** and **Restore previous**.
* **Pause memory** stops VOOL learning anything new. It keeps what it knows. **Resume memory** turns
  learning back on.
* **Export profile** gives you every active item as one document.
* **What should VOOL call you?** sets your name.
* **Facts VOOL learned in conversation** lists facts picked up in chats, with where each came from.
  **forget** removes exactly that one.

A forgotten item in Settings can be brought back with **Undo** for a few seconds.

Nothing VOOL remembers gives it permission to read, send, post or spend. Permissions are separate;
see [Permissions](permissions.md).

## Forget

Say "forget X", for example "Forget what I told you about Ilona's favourite tree." VOOL then:

* erases the words from its memory;
* erases them from this chat's saved records, including its run history, pinned messages, logs,
  undo copies and update backups;
* keeps a forget list so the words cannot come back through recall, an import or a model answer.

The forget list does not hold your words. It holds only scrambled fingerprints made with a key kept
on this computer, so VOOL can recognise the words again without storing them.

After a forget, asking about it gets **I have no record of that; it was forgotten.**

VOOL confirms with **Forget applied. Removed N memory entries.** If a copy could not be changed, or
a message waiting in the queue still holds the words, the reply says the forget was not fully
applied and names what is left.

## Good to know

* Memory is local. It is not synced to a server.
* With a cloud model, remembered things that fit the question can be part of what is sent. The line
  under the answer says when that happened; see
  [Check your VOOL](../guides/check-your-vool.md#where-each-answer-was-answered) and
  [Local and cloud](local-and-cloud.md).
* Another chat keeps its own messages after a forget, but VOOL will not recall or quote the
  forgotten words from it.
* Words are matched without regard to case. A forgotten number matches only where it stands as its
  own number: forgetting "12" erases "code 12" and "B12", but not "room 312", "12.5" or "12:30".
* Deleting memory files by hand is not a way to forget. Use the forget command or Settings.
