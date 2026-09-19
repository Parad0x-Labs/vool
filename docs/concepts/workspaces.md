---
description: A workspace is the set of folders VOOL is allowed to read and write.
---

# Workspaces

A workspace is an explicit grant. VOOL can read and write inside it, and nowhere else.

## Adding and removing

Add a folder in **Settings → Workspace**. Removing it revokes access immediately; there
is no cached copy kept outside the workspace.

## Scoping advice

* Point a workspace at a project, not at your home directory.
* Keep credential files, key material, and password stores outside any workspace.
* Use a separate workspace per project so context from one does not reach another.

{% hint style="warning" %}
Anything inside a workspace can be read by a tool call, and in cloud mode can therefore
be included in what is sent to your provider. Scope deliberately.
{% endhint %}
