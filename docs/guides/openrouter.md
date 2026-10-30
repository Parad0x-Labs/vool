---
description: How to add an OpenRouter key to VOOL so one API key reaches many cloud AI models, and what VOOL does with it.
---

# Connect OpenRouter

OpenRouter provides access to many providers behind one key. VOOL supports adding an
OpenRouter key.

## Add the key

1. Create a key in your OpenRouter account (openrouter.ai/keys).
2. Open **Settings → API Keys** and find OpenRouter under **Find a provider**.
3. Paste the key. It is stored with your computer's own protections — on a Mac in your
   Keychain (after you allow that once), elsewhere in an encrypted file inside your VOOL
   profile — and is used to make requests **from your machine directly to OpenRouter**.

There is no browser sign-in step for OpenRouter in VOOL today: you paste the key
straight into the app.

## Billing and attribution

{% hint style="info" %}
Your OpenRouter account remains yours. VOOL sends requests directly from this computer
using your key. Parad0x does not proxy your prompts or pay for your usage. Genuine VOOL
usage may appear in VOOL's public aggregate OpenRouter statistics, but individual prompts
and identities are not published.
{% endhint %}

## Remove the key

To remove the OpenRouter key, open **Settings**, choose **API Keys**, find the OpenRouter
key under **Your keys**, and press **Remove**. This removes the key from the credential
store. Revoke the key in your OpenRouter account as well if you want it dead server-side.
