# Model Provider Policy

VOOL is the system.

External models are worker or teacher backends only.

## Purpose

This document defines the operational policy for the VOOL model execution layer.

It exists to keep the model layer:

- model-agnostic,
- local-first,
- memory-first,
- license-safe,
- and separated from canonical shared knowledge.

## Core Rule

Model providers do not become VOOL.

VOOL still owns:

- routing,
- memory,
- policy,
- persona,
- validation,
- shared coordination logic,
- candidate promotion rules,
- and final response shaping.

Providers only supply bounded helper output.

## Execution Order

The intended runtime order is:

1. Human input normalization
2. Task classification
3. Tiered context loading
4. Memory-first retrieval
5. Provider routing by capability, health, trust, and cost class
6. Structured output validation
7. Candidate knowledge recording
8. Existing review or promotion gates
9. Final response shaping

## Setup Principle

Easy setup should not mean unsafe setup.

The preferred first local provider path is:

- a user-supplied local model,
- exposed through an OpenAI-compatible local endpoint,
- configured by manifest,
- and left disabled until the user enables it.

The same adapter contract should be able to talk to:

- LM Studio,
- other OpenAI-compatible local runtimes,
- local bridges,
- or compatible remote runtimes

without changing VOOL core logic.

## Memory First

Before any provider call, VOOL should prefer:

- exact cached candidate hits,
- strong local memory,
- tiered relevant context,
- and metadata-first shared memory

over fresh provider execution.

Provider execution should be the fallback, not the first reflex.

## Candidate Knowledge Rule

All provider output is candidate knowledge first.

It must not automatically become:

- canonical local memory,
- canonical shared knowledge,
- or automatic shard advertisement

without existing review or validation gates.

## License Boundary Rule

VOOL adapter code is VOOL code.

Third-party runtimes, model code, tokenizers, and weights remain under their upstream licenses.

Provider manifests must declare:

- provider name,
- model name,
- source type,
- license name,
- license reference,
- whether weights are bundled,
- redistribution status,
- runtime dependency,
- and notes.

Unknown-license providers should not be silently enabled by default.

## Fast And Cheap User Experience

The intended user experience is:

- local memory hits first,
- free local model second,
- paid fallback last,
- no bundled weights required,
- and no forced cloud dependency for normal use.

That is how VOOL stays cheap to run while still feeling fast and useful.
