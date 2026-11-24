---
description: How to connect OpenRouter to VOOL so one API key reaches many cloud AI models.
---

# Connect OpenRouter

OpenRouter provides access to many providers behind one key. VOOL supports adding an
OpenRouter key.

## Add the key

Create a key in your OpenRouter account, paste it under **Settings → API Keys** and press
**Save key**. VOOL checks the key with OpenRouter once, then keeps it in an encrypted file on
this computer (see [Security model](../trust/security.md#keys)).

## Use it

A saved OpenRouter key makes its models available in the model menu in the chat header:

* **VOOL Auto (Local first)** starts on this computer and may use the free backup model you
  choose in that menu when the local model cannot handle a message. Auto never picks a paid
  model.
* **Pin an exact model** for a chat by choosing it in the menu. A free model runs without
  any spending check. A paid model asks you to accept its price first, and your spending
  limits apply; see [Spending limits and price guards](spending-limits.md).

If OpenRouter rejects the key, VOOL rests it for 10 minutes instead of retrying it on every
message. A model you pinned as the only way to answer is still tried.

## Billing and attribution

{% hint style="info" %}
Your OpenRouter account remains yours. VOOL sends requests directly from this computer
using your key. Parad0x does not proxy your prompts or pay for your usage. Genuine VOOL
usage may appear in VOOL's public aggregate OpenRouter statistics, but individual prompts
and identities are not published.
{% endhint %}

## Remove the key

Remove the key under **Settings → API Keys**. Revoke the key in your OpenRouter account as well if you want it dead server-side.
