---
description: How VOOL protects your files and API keys — workspace boundaries, credential storage, and what a plugin can reach.
---

# Security model

## Boundaries

* **Folder limits** bound file access. VOOL reads and changes files in the chat's project
  folder. Outside it, VOOL can read your visible home folders and change files only in
  Desktop, Downloads and Documents; hidden folders, app and system data and VOOL's own data
  are never read. See [Workspaces](../concepts/workspaces.md).
* **The credential store** holds keys. Keys are not written into config files or logs.
* **Tool confirmation** bounds changes. In Manual mode every change asks first; in Auto mode
  ordinary project reads, edits and tests can run on their own, while deleting, publishing,
  money, credentials and other risky actions still ask. Commands run in a sandbox with no
  network. See [Permissions](../concepts/permissions.md).

## Your local network

VOOL listens only on this computer by default. When you set it to listen on your local
network, only this computer is treated as you. Settings, files, keys, tools and private
features answer only to it.

Another device on the network gets plain chat. It can also read one public web page at a
time. That page must be on the public internet: addresses on this computer or your local
network are refused, and each redirect is checked the same way. Web searches, research,
the browser, quotes, payments and your paid keys work only on this computer.

## Chat bridges

A Telegram or Discord bridge answers only its owner; everyone else is told once that this VOOL is locked
to its owner, and their messages are not processed. Even the owner's Discord messages get the
same limits as another device on the network. See
[Telegram and Discord](../guides/telegram-and-discord.md).

## Keys

VOOL keeps your provider keys in an encrypted file in its runtime home on this computer
(see [Configuration](../reference/configuration.md)), readable only by your user account.
Keys that VOOL already kept in the macOS Keychain on a Mac stay readable there. A key is used
from your machine to reach your provider. It is not sent to Parad0x Labs, and there is no
Parad0x-operated endpoint in the request path.

{% hint style="info" %}
The encrypted file is protected by your user account's file permissions, not by a separate
password. Keep disk encryption on (FileVault on a Mac) so a lost or stolen disk does not
expose your keys.
{% endhint %}

## Plugins

Skills and plugins extend what VOOL can do, so read one before installing it. VOOL checks
add-ons before they are turned on; see [Skills and plugins](../guides/skills-and-plugins.md).

## Reporting an issue

Report security issues privately through
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
never in a public issue. Include the version, the platform, and the steps to reproduce.

{% hint style="warning" %}
The macOS apps are ad-hoc signed and not notarized; the Linux tarball is unsigned. Updates
are signed and checked before they install. See [Release status](release-status.md).
{% endhint %}
