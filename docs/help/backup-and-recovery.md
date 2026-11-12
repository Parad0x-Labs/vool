---
description: What you can save, move and restore in this version of VOOL: chats, memory, keys and wallets.
---

# Backup and recovery

Everything VOOL keeps lives on your computer, in one profile folder (see
[Configuration](../reference/configuration.md) for where). Updating or reinstalling the app
never touches it. VOOL has no cloud copy of your data and no one-step backup of the whole
profile. These are the ways to save and bring back what matters.

## Chats

| You want | Use | What you get |
| --- | --- | --- |
| A copy to read or share | **Export** in the chat header | The whole chat as text or Markdown. Attached files are listed by name only. |
| A copy to bring back into VOOL | Settings, Backup & restore, **Export a chat** | One chat as a `.voolsession` file, signed by this computer. |
| A chat from that file back | Settings, Backup & restore, **Import a chat** | The chat, added beside your others. |
| Your chats from other AI apps | Settings, Backup & restore, **Bring your chats from other AI apps** | Those chats, with their names and messages. |

**Export a chat** carries the chat's messages, receipts and evidence references, never
VOOL's internal reasoning, your settings, memory or keys. Protect the file with a passphrase
when it leaves this computer: an export without one asks you to confirm first. A preview shows
exactly what will be written before anything is.

**Import a chat** shows what the file holds and who signed it before anything is added. A
file signed by a key VOOL does not know is refused unless you accept it, and the chat stays
marked untrusted. An import never changes a chat you already have: if the same chat is here,
the copy arrives as a new one.

**Bring your chats from other AI apps** reads an app on this computer or a data export file.
You tick the chats to bring in. Nothing leaves your computer and the other app's files are
never changed. VOOL keeps these chats as history: it does not learn from them or follow
anything written in them.

## Memory

Settings, Memory & Personalisation, **Export profile** saves what VOOL remembers about you as
a file named `vool-memory-` and the date. It never holds a key or a password. It is a copy to
read and keep: VOOL has no import for it.

## Keys for model providers

Provider keys are not in any export. On a new computer, add them again in Settings.

## Wallets

* **A VOOL Wallet created for a network** shows one private-key backup during setup, and only
  then. Keep it offline: anyone who has it controls the wallet. If you forget the wallet's PIN
  or password, **Forgot PIN or password?** on the wallet in Settings, Crypto restores access
  with that backup, and you choose a new PIN or password.
* **The legacy pocket** is kept by its recovery phrase, shown once when it was created.
  **Restore from phrase…** in Settings, Crypto, Other accounts brings it back from the phrase.
  It can also export its private key to Phantom or MetaMask with **Export private key**, which
  opens only with Touch ID or your Mac password.
* A wallet you only watch, or one you connected from another app, keeps no key in VOOL: there
  is nothing to back up here.

See [Wallet](../guides/wallet.md) for the full picture.

## If an update fails

An update that fails at any step leaves the app as it was. If moving your data to the new
version fails, your data is put back as it was and VOOL starts on the previous version. See
[Updates and migration](../reference/updates-migration.md).
