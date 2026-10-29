---
description: How to run AI models locally on your own computer with VOOL — installing a local model server, choosing a model size, and working fully offline.
---

# Run a local model

## Install a runtime

VOOL uses Ollama to run local models. Ollama is a separate program, installed on its own
from [ollama.com](https://ollama.com). The macOS app includes its own local-model runner,
so on a Mac you can skip this step. On Linux and source installs, install Ollama, start
it, and pull a model.

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL picks the local model for you by default — a Qwen model sized to your computer. On
many computers that model is `qwen3:4b`.

## Point VOOL at it

Open **Settings → Models & Providers**. Running local runtimes are detected automatically,
and VOOL picks a local model for you. Choose a specific model there yourself and the
indicator badge changes to `LOCAL`.

## Certify it once

A local model writes answers only after it is certified. Open **Settings → Models & Providers → Local models**,
register the model, then choose **Certify**. Certifying runs a check on your machine; on a
computer without a graphics card the first one can take several minutes. Nothing is
downloaded and nobody is contacted. Until a model passes, VOOL does not
let it write final answers.

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
