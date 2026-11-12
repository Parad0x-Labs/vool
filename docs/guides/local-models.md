---
description: How to run AI models locally on your own computer with VOOL — installing a local model server, choosing a model size, and working fully offline.
---

# Run a local model

## Install a runtime

VOOL talks to a local model server. The macOS app includes its own local-model runner, so
on a Mac you can skip this step. On Linux and source installs, install a server such as
Ollama, start it, and pull a model.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Point VOOL at it

Open **Settings → Models & Providers**. Running local runtimes are detected automatically. Select a
model and the mode indicator changes to Local.

## Make it ready once

A local model writes answers only after VOOL has checked it. Open
**Settings → Models & Providers → Local models** and press **Use this model** next to the model.
VOOL quietly checks, on your computer, that the model can use VOOL's tools well enough to answer
for you. The check can take a minute or two; nothing is downloaded and nobody is contacted. When it
passes, the model shows **Ready** and VOOL answers with it. Until then, VOOL does not let it write
final answers.

* **Not a good fit:** the model can't use VOOL's tools well enough. VOOL lists what you can do
  instead: use or download the model it picked for this computer, or use a cloud model.
* **Check didn't finish:** make sure Ollama is open, then press **Try again**. A slow computer may
  need a second try.

If no local model is ready when VOOL starts, it checks up to three of the models on this computer,
the one you picked first, and uses the first that passes. It skips a model too big for this
computer. A ready model always goes before one that is not ready.

## Sizing

| Model size | Memory needed | Suited to |
| --- | ---: | --- |
| 3B | ~4 GB | Short edits, quick questions |
| 7–8B | ~8 GB | General use, most coding |
| 14B | ~16 GB | Longer reasoning |
| 32B+ | 32 GB or more | Heavier work, slower |

{% hint style="info" %}
Quantised builds cut memory substantially. A 7B model at 4-bit runs comfortably in about
5 GB.
{% endhint %}

## If it is slow

* Choose a smaller or more heavily quantised model.
* Close other memory-heavy applications.
* Shorten the context — fewer files in the request means less to process.
