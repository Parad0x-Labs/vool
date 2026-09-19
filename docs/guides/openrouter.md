---
description: How to connect OpenRouter to VOOL so one API key reaches many cloud AI models, and what the authorization page does.
---

# Connect OpenRouter

OpenRouter provides access to many providers behind one key. VOOL supports connecting an
OpenRouter account.

## Connect

1. Open **Settings → Models → OpenRouter**.
2. Choose **Connect**. Your browser opens OpenRouter's authorization page.
3. Approve. The browser returns to `vool.dev/auth/openrouter/callback`, which hands the
   authorization back to the desktop app.
4. The app completes the exchange **on your machine** and stores the key in your
   operating system's credential store.

## What the callback page does

The page at `vool.dev/auth/openrouter/callback` is a relay and nothing more. It passes
the authorization code and state to the desktop app and then clears them from the URL.

It never receives the PKCE verifier, never exchanges the code for a key, never stores a
key, and carries no analytics or third-party scripts.

## Billing and attribution

{% hint style="info" %}
Your OpenRouter account remains yours. VOOL sends requests directly from this computer
using your key. Parad0x does not proxy your prompts or pay for your usage. Genuine VOOL
usage may appear in VOOL's public aggregate OpenRouter statistics, but individual prompts
and identities are not published.
{% endhint %}

## Disconnect

**Settings → Models → OpenRouter → Disconnect** removes the key from the credential
store. Revoke the key in your OpenRouter account as well if you want it dead server-side.
