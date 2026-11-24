---
description: How to connect an AI model to VOOL — a local model running on your own computer, or a cloud provider using your own API key.
---

# Connect a model

VOOL picks a local model by default, and you can choose another; the choice determines
whether a request stays on your machine or leaves it.

## Local

VOOL lists the models in Ollama on this computer. The Mac app includes Ollama. On Linux,
install Ollama from [ollama.com](https://ollama.com) first. Then open
**Settings → Models & Providers**, choose **This Mac** (or **This computer**), and press
**Use this model**. VOOL checks the model once on your computer before it answers with it.
If you have no model yet, the same place offers one sized to your computer, with its download
size. Details are in [Run a local model](../guides/local-models.md).

## Cloud

Add a provider key in **Settings → API Keys**. The key is kept in an encrypted file on this
computer (see [Security model](../trust/security.md#keys)) and is used to make requests
**from your machine directly to the provider**. See [Provider keys](../guides/provider-keys.md).

A saved key makes that provider's models available in the model menu in the chat header.
It never turns on paid use by itself: VOOL Auto never picks a paid model, and a paid model you
pick asks you to accept its price first.

See [Connect OpenRouter](../guides/openrouter.md) for the OpenRouter flow specifically.

## Switching

Open the model menu in the chat header. Choose a run mode for VOOL Auto (**Local first**,
**Computer + web** or **Offline**) or pin an exact model for this chat. The tag on the button
reads **AUTO**, **LOCAL** or **CLOUD**, and it changes before any request is made. See
[Local and cloud](../concepts/local-and-cloud.md).

If the model you picked cannot run (for example a cloud model your settings do not allow),
VOOL says so in one line and answers through VOOL Auto instead, naming the model that
answered. Your choice in the menu is not changed.
