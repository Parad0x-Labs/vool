---
description: What the VOOL AI assistant asks for the first time you open it, and what it does not.
---

# First run

The first launch sets up a workspace. It does not ask which model to use — VOOL picks one
for you. Nothing is sent anywhere during setup — unless you choose Connect, when the one
key check goes to that provider alone.

## What happens

1. **A workspace is created.** A workspace is a folder VOOL is allowed to read and write.
   See [Workspaces](../concepts/workspaces.md).
2. **You choose a model.** Local runtimes already installed are detected. Cloud providers
   are listed but stay inactive until you add a key.
3. **The mode indicator appears.** It reads Local, Auto or the cloud provider's name and reflects the model
   currently selected.
4. **Certify a local model once.** Before a local model can answer, open
   **Settings → Models & Providers → Local models** and press **Certify** for it (if a check
   was already recorded, the button reads **Re-run certification**). This runs a check on
   your machine; on a computer without a graphics card the first one can take several
   minutes.

## What VOOL does not do at first run

* It does not create an account.
* It does not require a cloud key. A certified local model is enough to use the app (on
  Linux, after you install Ollama).
* It does not index your whole drive. Only the workspace folder you pick is readable.

{% hint style="info" %}
You can change the workspace at any time. Removing a folder from the workspace removes
VOOL's access to it.
{% endhint %}

## Next

Continue to [Connect a model](connect-a-model.md).
