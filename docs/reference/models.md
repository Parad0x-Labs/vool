---
description: Which AI models work with VOOL — local models on your own hardware and cloud models through your provider account.
---

# Supported models

VOOL does not bundle a model — the first local model is downloaded the first time you use
it. It connects to runtimes and providers you choose.

## Local runtimes

Ollama, llama.cpp, vLLM and MLX are detected automatically. Other runtimes exposing an
OpenAI-compatible endpoint on localhost can be pointed at by hand.

## Cloud providers

Cloud models are reached with your key, from your machine. Selecting one puts the app in
cloud mode (the indicator shows the provider's name) — see [Local and cloud](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) is the broadest single connection: one key, many
providers.

## Choosing

| Work | Reasonable choice |
| --- | --- |
| Private files, no network | Local, 7B or larger |
| Everyday coding | Local 7–14B coding model |
| Long reasoning | A larger cloud model |
| Cost control | Local first, cloud when it is worth it |
