---
description: How to connect OpenRouter to VOOL so one API key reaches many cloud AI models, and what the authorization page does.
---

# Connect OpenRouter

OpenRouter provides access to many providers behind one key. VOOL supports connecting an
OpenRouter account.

## Connect

{% hint style="warning" %}
One-click **Connect** / **Disconnect** for OpenRouter is planned for 0.8, not released yet.
{% endhint %}

In 0.7.5: create a key in your OpenRouter account, then paste it under **Settings → API Keys**.
It is stored in the macOS Keychain (on Linux and Windows, in an encrypted vault in your profile).

## What the callback page will do (planned for 0.8, not released yet)

The callback page is a relay and nothing more. It passes
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

Remove the key under **Settings → API Keys**. Revoke the key in your OpenRouter account as well if you want it dead server-side.
