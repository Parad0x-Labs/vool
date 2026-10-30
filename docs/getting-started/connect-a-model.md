---
description: How to connect an AI model to VOOL — a local model running on your own computer, or a cloud provider using your own API key.
---

# Connect a model

VOOL does not ship a model. You choose one, and the choice determines whether a request
stays on your machine or leaves it.

## Local

If a local runtime is installed and running, VOOL detects it and lists its models.
Selecting one makes the indicator read Local. Setup details are in
[Run a local model](../guides/local-models.md).

## Cloud

Add a provider key in **Settings → API Keys**. The key is stored in the macOS Keychain
(on Linux and Windows, in an encrypted vault in your profile) and is used to make requests **from your machine directly to
the provider**.

Selecting a cloud model makes the indicator show the provider's name, and it changes before
any request is made.

See [Connect OpenRouter](../guides/openrouter.md) for the OpenRouter flow specifically.

## Switching

Switching models switches modes. The indicator is authoritative: if it reads Local,
the next request is handled on this computer.
