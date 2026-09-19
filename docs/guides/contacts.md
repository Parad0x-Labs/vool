---
description: Contacts — saved people and endpoints, with change review that protects every edit.
---

# Contacts

The contacts list is a local, structured address book (people, their endpoints and payment
aliases) that VOOL can use instead of asking you to re-type an address. Because an address
book feeds payments and messages, edits to it are **protected**: reviewed, confirmed, and
recorded.

## Protected edits

Changing a saved contact is a two-step flow:

1. **Review** — the change is prepared and shown. If the entry changed since you opened it
   (another window, another device), the change is refused with *"changed after this was
   reviewed; review it again. Nothing changed."*
2. **Confirm** — the change is committed only with your PIN or password in the review screen.

Every refusal states plainly that **nothing was saved or changed** — because nothing was. If
your PIN was accepted but the save itself failed, the message says exactly that: accept the
PIN, re-save before the confirmation expires.

## Guards

* One change per contact at a time; a bounded number of pending changes.
* Deleting a contact does not silently rewrite history: references to a deleted contact are
  reported as *"That contact was deleted"* — never silently re-resolved.
* Imports never partially apply: if the source could not be read, nothing was imported.

## Why this design

A typo'd payment address is unrecoverable money. The review step exists so that no model
turn, no bulk edit and no stale window can change who "Alice" resolves to without a human
confirming the exact resulting entry.
