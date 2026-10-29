---
description: What the VOOL AI assistant asks for the first time you open it, and what it does not.
---

# First run

The first launch sets up a workspace. It does not ask which model to use — VOOL picks one
for you. Nothing is sent anywhere during setup.

## What happens

1. **A workspace is created.** A workspace is a folder VOOL is allowed to read and write.
   See [Workspaces](../concepts/workspaces.md).
2. **A model is picked for you.** Local runtimes already installed are detected, and VOOL
   picks a local model by default. Cloud providers are listed but stay inactive until you
   add a key.
3. **The model indicator appears.** By default it reads `AUTO / VOOL Auto / Local first`:
   VOOL picks the model and starts on this computer. Pick a local model yourself and the
   badge reads `LOCAL`; pick a cloud model and it reads `CLOUD`.
4. **Certify a local model once.** Before a local model can answer, open
   **Settings → Models & Providers → Local models** and press **Certify** for it. This runs a check on
   your machine; on a computer without a graphics card the first one can take several
   minutes.

## What VOOL does not do at first run

* It does not create an account.
* It does not require a cloud key. A local model is enough to use the app.
* It does not index your whole drive. Only the workspace folder you pick is readable.

{% hint style="info" %}
You can change the workspace at any time. Removing a folder from the workspace removes
VOOL's access to it.
{% endhint %}

## Next

Continue to [Connect a model](connect-a-model.md).
