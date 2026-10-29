---
description: How to connect an AI model to VOOL — a local model running on your own computer, or a cloud provider using your own API key.
---

# Connect a model

VOOL does not ship a model. It picks a local model by default, and you can choose another;
the choice determines whether a request stays on your machine or leaves it.

## Local

A local model runs on this computer. If a local runtime is installed and running, VOOL
detects it and picks a local model for you. Choose a specific model yourself under
**Settings → Models & Providers**; the indicator badge then reads `LOCAL`. Setup details are in
[Run a local model](../guides/local-models.md).

## Cloud

Open **Settings**, choose **API Keys**, and add your key under **Your keys**. Then pick
your model under **Models & Providers**. The key is stored in your operating system's
credential store and is used to make requests **from your machine directly to
the provider**.

Selecting a cloud model changes the indicator badge to `CLOUD` before
any request is made.

See [Connect OpenRouter](../guides/openrouter.md) for the OpenRouter flow specifically.

## Switching

Pick a different model and the indicator follows. It is authoritative: if the badge reads
`LOCAL`, the next request is handled on this computer. By default it reads `AUTO` —
`VOOL Auto`, `Local first`.
