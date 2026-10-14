---
description: What the VOOL AI assistant asks for the first time you open it, and what it does not.
---

# First run

The first launch sets up a workspace and asks which model to use. Nothing is sent
anywhere during setup.

## What happens

1. **A workspace is created.** A workspace is a folder VOOL is allowed to read and write.
   See [Workspaces](../concepts/workspaces.md).
2. **You choose a model.** Local runtimes already installed are detected. Cloud providers
   are listed but stay inactive until you add a key.
3. **The mode indicator appears.** It reads `LOCAL` or `CLOUD` and reflects the model
   currently selected.
4. **Certify a local model once.** Before a local model can answer, open
   **Settings → Models & Providers → Local models** and press **Certify** for it. This runs a short check on
   your machine. In 0.7.0 VOOL does not prompt you for this; a clearer prompt comes in 0.7.1.

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
