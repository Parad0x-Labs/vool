---
description: Add, test and remove the keys VOOL uses for cloud models, web search, chain data and crypto prices.
---

# Provider keys

Cloud models, web search APIs, blockchain data and crypto prices each need a key from the
company that provides them. VOOL keeps those keys on this computer and uses each one only for
the service you added it for. Local models need no key at all.

Everything here lives in **Settings → API Keys**, in two parts: **Find a provider** and **Your
keys**.

## Find a provider

Type in **Find a provider…** or open a category:

| Category | What it connects |
| --- | --- |
| AI models | The company that runs your cloud model: OpenRouter, OpenAI (GPT), Anthropic (Claude), Groq, Google (Gemini), DeepSeek, Kimi / Moonshot, or a custom OpenAI-compatible endpoint |
| Web search | A search API for live lookups: Serper, Brave Search, Tavily, Exa, Firecrawl, Jina or You.com. Built-in search without a key stays available |
| Blockchain data | Read-only wallet, token and transaction lookups in any chat: Helius, Alchemy, Solscan, Etherscan or Moralis. These keys never sign or pay |
| Market data | A CoinGecko key raises the limits on live crypto prices. Prices keep working without one |

Each provider shows a short description, an **Official site ↗** link where one exists, and **Add
key**, which picks that provider in the form below. A provider you already added shows **Key
saved** and **Manage key**. Blockchain data providers also have **Setup steps**: where to sign up,
which key to copy, and which chains the key covers.

Plans, prices and quotas are set by each provider. Check its official site before adding credit.

## Add a key

1. Leave the provider on **Auto-detect from the key**, or choose it in the list. VOOL recognises
   most keys by how they start, on this computer, and asks you to choose when it cannot tell.
2. Paste the key. **Show** reveals what you typed; the stored key is never shown again.
3. Press **Save key**. VOOL asks that one service once whether it accepts the key. Only an
   accepted key is stored, sealed on this machine, and the field is emptied.

What a saved key does:

* **A model provider key** makes that provider's models available to choose in the model menu.
  It never turns on paid use by itself: VOOL Auto never picks a paid model, and a paid model you
  pick still goes through your spending limits.
* **A web search key** is used for live lookups while **Web lookups** is on in **Settings →
  Privacy & Permissions**. That switch is off on a fresh install.
* **A blockchain data key** is used for read-only chain lookups in any chat.
* **A market data key** is used for crypto prices. If the provider later refuses it, VOOL
  answers prices without it.

If the provider accepts the key but says its credit limit is used up, the key is stored and VOOL
tells you that requests will fail until credit is added.

**Save for later (unverified)** stores a key you cannot check right now, for example while
offline. It is sealed on this machine and listed under **Saved for later (unverified)**, and
nothing uses it until you press **Retry** and the provider accepts it. **Delete** removes it.

### A custom endpoint

Choose **Custom (OpenAI-compatible)** to use your own server or another compatible service:

* Enter its base URL, ending in `/v1`. It must be `https://`, or plain `http://` to a server on
  this computer (`127.0.0.1`), so a key never travels in clear.
* Choose where the key goes: as a Bearer token (most APIs), in a header you name, or no key for
  an open server on your own computer.
* If the endpoint answers but cannot confirm the key, VOOL offers **Use this endpoint anyway**.
  Use it only for a server you trust; your first message shows whether the key works.

To change the address, save the custom key again with the new base URL.

## Your keys

Saved keys are listed by group (model providers, web search, blockchain data, market data), each
with **Test** and **Remove**. Market data keys have **Remove** only, because they were checked
when saved.

* **Test** sends one check to that provider. A key it accepts shows a green light, and a key it
  refuses shows red. A timeout, an outage or a rate limit leaves the light as it was, because the
  key was not judged.
* **Remove** asks first, then deletes the key from this machine. Nothing is sent anywhere, and VOOL
  carries on without it.

**Managed keys**, folded at the bottom, are keys VOOL made for itself (for example the keys that
protect stored data) and connection keys saved by plugins. They are listed so nothing is hidden,
but they have no **Remove**: deleting a storage key would make protected data unreadable. Plugin
connections are managed in **Skills & Plugins**; see [Skills and plugins](skills-and-plugins.md).

## What permission or connection it needs

Saving or testing a key contacts that one provider. Nothing else does:

* Opening or reloading Settings contacts no provider and never shows a stored key.
* A key is a connection, not a permission. Spending, sending and changing files still ask
  according to your mode and limits; see [Permissions](../concepts/permissions.md).
* Keys are kept in an encrypted file on this computer. Keys that VOOL already kept in the macOS
  Keychain stay readable there. See [Security model](../trust/security.md#keys).

## What can go wrong

When a key is not stored, the message says why and *Nothing was stored*:

| Message says | What it means |
| --- | --- |
| The provider rejected this key | Check that you copied all of it and that it is a key for that provider |
| No credit or quota left | Add credit with the provider, then save again |
| Could not reach the provider, did not answer in time, or reported an outage | The key was not judged. Try again later |
| The provider is limiting requests | The key was not judged. Try again shortly |
| The address answered with a web page | Often a network sign-in page. The key was not judged |
| The endpoint redirected to another site | VOOL did not send the key there |
| The address answers without any key | It cannot confirm a key, so VOOL did not send it. Choose the service's own entry if it has one |
| VOOL cannot tell which service this key is for | Choose the service in the list and save again. Nothing was sent anywhere |

Some web search providers charge for the check itself, because it is one small real search.

## What to do next

* Choose a model — see [Connect a model](../getting-started/connect-a-model.md).
* Use one key for many cloud models — see [Connect OpenRouter](openrouter.md).
* Set what paid models may cost — see [Spending limits and price guards](spending-limits.md).
