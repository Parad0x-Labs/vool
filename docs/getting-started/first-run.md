---
description: The five setup steps the VOOL AI assistant offers the first time you open it, and what it does not do.
---

# First run

The first time you open VOOL, the chat shows **Finish setting up VOOL — five quick steps.** Press
**Finish setup** to open **Set up VOOL**. You can skip any step, and VOOL already works. Every step is
also an ordinary setting you can change later.

Nothing is sent anywhere while you set up, except a check of a cloud key with its provider if you
add one.

## The five steps

1. **Where should it think?** **On this computer** is private and free: nothing you type leaves
   this computer. **Online, with a key** gives smarter answers; you paste a key from a company you
   pay, and it is kept only on this computer. If there is no private model on this computer yet,
   the step says so and shows **How to set up a private model**. See
   [Connect a model](connect-a-model.md).
2. **Pick a folder.** VOOL can only read and change files inside the one folder you choose. Pick a
   normal folder such as Documents, not your whole disk. See
   [Workspaces](../concepts/workspaces.md).
3. **What may it do alone?** How often it stops and asks before it changes anything: **Just do
   routine work**, **Ask before risky changes** or **Ask me first**. Sending a message, posting or
   spending money always asks you first. See [Permissions](../concepts/permissions.md).
4. **What should it call you?** Optional. A first name or nickname is enough. It is kept as one
   remembered item you can edit or forget in Settings.
5. **Try three things.** Remember something about you, set a reminder and find a file. Press
   **Put it in the chat**, change the words if you like, and press Send yourself. Each try gets
   **Done. It really ran.** only when it really ran.

Each step has **Skip for now** and an example you can play. A step counts as done when the real
setting exists, wherever you set it, so a setting changed in Settings ticks its step too.

## Finish later

If you leave steps open, the chat line shows how many are done, for example
**Finish setting up VOOL — 3 of 5 done.** Settings also shows a **Complete setup** group at the top
until every step is done. Press **×** on the chat line (**Don't remind me**) to hide the reminder.
Every step is still an ordinary setting:

| Step | Where it lives later |
| --- | --- |
| Where should it think? | Settings → Models & Providers, and Settings → API Keys |
| Pick a folder | The **+ Project** button in the chat sidebar |
| What may it do alone? | Settings → Privacy & Permissions → What VOOL may do alone |
| What should it call you? | Settings → Memory & Personalisation, in the name field |
| Try three things | The chat, any time |

If no model can answer yet, the chat says **VOOL needs an AI model before it can answer.** Press
**Set up a model** to go straight to the first step.

## What VOOL does not do at first run

* It does not create an account.
* It does not require a cloud key. A local model is enough to use the app.
* It does not index your drive. It reads files only when a request needs them; see
  [Workspaces](../concepts/workspaces.md) for the folders it can reach.
* It does not go online on its own. Update checks, model downloads and calendar sync stay off until
  you turn on **Go online on its own** in Settings → Privacy & Permissions.

## After setup

To check that everything works, open **Settings → About & Diagnostics → VOOL Doctor** and press
**Run the check**. See [Check your VOOL](../guides/check-your-vool.md).

## Next

Continue to [Connect a model](connect-a-model.md).
