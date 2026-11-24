---
description: Which AI models work with VOOL — local models on your own hardware and cloud models through your provider account.
---

# Supported models

VOOL connects to local models on your computer and to cloud providers you choose. VOOL
suggests a local model sized to your computer and downloads it when you ask.

## Local models

VOOL runs local models through Ollama; the Mac app includes it. VOOL lists the models in
Ollama under **Settings → Models & Providers → This Mac** (or **This computer**) and checks each
one once before it answers with it. Its suggestions are Qwen models picked for this computer's
memory or graphics card and its free disk. See [Run a local model](../guides/local-models.md).

Another local server with an OpenAI-compatible address (for example
`http://127.0.0.1:PORT/v1`) can be added as a custom endpoint in **Settings → API Keys**.

## Cloud providers

Cloud models are reached with your key, from your machine. **Settings → API Keys** lists
OpenRouter, OpenAI, Anthropic, Groq, Google, DeepSeek, Kimi / Moonshot and a custom
OpenAI-compatible endpoint. See [Provider keys](../guides/provider-keys.md).

[OpenRouter](../guides/openrouter.md) is the broadest single connection: one key, many
providers, including free models.

Selecting a cloud model makes the tag on the model button read **CLOUD** — see
[Local and cloud](../concepts/local-and-cloud.md). To find models that became free or cheaper,
use **Discover models** in the model menu; see
[Notifications and model offers](../guides/notifications.md#model-offers).

## Choosing

| Work | Reasonable choice |
| --- | --- |
| Private files, no network | A local model, with the chat set to **Offline** |
| Everyday questions and coding | The local model VOOL suggests for this computer |
| Long reasoning | A larger cloud model |
| Cost control | VOOL Auto with **Local first**: free cloud only if needed |
