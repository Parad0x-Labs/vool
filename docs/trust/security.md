---
description: How VOOL protects your files and API keys — workspace boundaries, credential storage, and what a plugin can reach.
---

# Security model

## Boundaries

* **The workspace** bounds file access. Outside it, the runtime cannot read or write.
* **The credential store** holds keys. Keys are not written into config files or logs.
* **Tool confirmation** bounds changes. In Manual mode every change asks first; in Auto mode
  everyday edits and commands can run automatically, while deleting, overwriting, installing,
  spending and changing settings still ask. See [Tools](../concepts/tools.md).

## Keys

Your provider key is stored by the operating system — Keychain, Credential Manager, or
Secret Service — and used from your machine to reach your provider. It is not sent to
Parad0x Labs, and there is no Parad0x-operated endpoint in the request path.

## Plugins

A plugin runs with the runtime's access. That is what makes plugins useful and what makes
them worth reading before installing. See [Skills and plugins](../guides/skills-and-plugins.md).

## Reporting an issue

Report security issues privately through
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
never in a public issue. Include the version, the platform, and the steps to reproduce.

{% hint style="warning" %}
The macOS apps are ad-hoc signed and not notarized; the Linux tarball is unsigned. See
[Release status](release-status.md).
{% endhint %}
