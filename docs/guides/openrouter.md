---
description: How to connect OpenRouter to VOOL so one API key reaches many cloud AI models.
---

# Connect OpenRouter

OpenRouter provides access to many providers behind one key. VOOL supports connecting an
OpenRouter account.

## Connect

Create a key in your OpenRouter account, paste it under **Settings → API Keys** and press
**Save key**. VOOL checks the key with OpenRouter once, then keeps it in an encrypted file on
this computer (see [Security model](../trust/security.md#keys)).

## Billing and attribution

{% hint style="info" %}
Your OpenRouter account remains yours. VOOL sends requests directly from this computer
using your key. Parad0x does not proxy your prompts or pay for your usage. Genuine VOOL
usage may appear in VOOL's public aggregate OpenRouter statistics, but individual prompts
and identities are not published.
{% endhint %}

## Disconnect

Remove the key under **Settings → API Keys**. Revoke the key in your OpenRouter account as well if you want it dead server-side.
