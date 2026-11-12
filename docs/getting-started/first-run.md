---
description: What the VOOL AI assistant asks for the first time you open it, and what it does not.
---

# First run

The first launch asks four short questions. You can skip any of them and change every answer
later. Nothing is sent anywhere while you answer them, except a check of a cloud key with its
provider if you add one.

## What happens

1. **Where should it think?** On this computer with a local model, or online with a key from a
   provider you pay. See [Connect a model](connect-a-model.md).
2. **Pick a folder.** The project folder VOOL works in. See
   [Workspaces](../concepts/workspaces.md).
3. **What may it do alone?** How often VOOL stops and asks you before it changes anything. See
   [Tools](../concepts/tools.md).
4. **What should it call you?** Optional. A first name or nickname is enough, and it stays on
   this computer.

The mode indicator then reads Local, Auto or the cloud provider's name and reflects the model
currently selected.

**Certify a local model once.** Before a local model can answer, open
**Settings → Models & Providers → Local models** and press **Certify** for it. This runs a short
check on your machine.

## What VOOL does not do at first run

* It does not create an account.
* It does not require a cloud key. A local model is enough to use the app.
* It does not index your drive. It reads files only when a request needs them; see
  [Workspaces](../concepts/workspaces.md) for the folders it can reach.

{% hint style="info" %}
You can pick another project folder at any time with **+ Project** in the chat sidebar.
{% endhint %}

## Next

Continue to [Connect a model](connect-a-model.md).
