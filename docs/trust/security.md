---
description: How VOOL protects your files and API keys — workspace boundaries, credential storage, and what a plugin can reach.
---

# Security model

## Boundaries

* **The workspace** bounds file access. Outside it, the runtime cannot read or write.
* **The credential store** holds keys. Keys are not written into config files or logs.
* **Tool confirmation** bounds changes. Modifying actions ask before running.

## Keys

Your provider key is stored by the operating system — Keychain, Credential Manager, or
Secret Service — and used from your machine to reach your provider. It is not sent to
Parad0x Labs, and there is no Parad0x-operated endpoint in the request path.

## Plugins

A plugin runs with the runtime's access. That is what makes plugins useful and what makes
them worth reading before installing. See [Skills and plugins](../guides/skills-and-plugins.md).

## Reporting an issue

Send security reports to `security@parad0xlabs.com`. Include the version, the platform,
and the steps to reproduce.

{% hint style="warning" %}
Current builds are beta and unsigned; the macOS build is not notarized. See
[Release status](release-status.md).
{% endhint %}
