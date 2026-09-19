---
description: How to run AI models locally on your own computer with VOOL — installing a local model server, choosing a model size, and working fully offline.
---

# Run a local model

## Install a runtime

VOOL talks to a local model server. Install one, start it, and pull a model.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Point VOOL at it

Open **Settings → Models**. Running local runtimes are detected automatically. Select a
model and the mode indicator changes to `LOCAL`.

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
