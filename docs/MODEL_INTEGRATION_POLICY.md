# Model Integration Policy

**VOOL may integrate with external models, but external model code and weights always remain under their original licenses unless explicitly stated otherwise by their upstream source.**

This document defines how VOOL integrates with external AI models, runtimes, APIs, and user-supplied weights while keeping licensing, ownership, and system boundaries clear.

## Purpose

VOOL is designed to be model-agnostic.

That means VOOL may use one or more external models for tasks such as:

- summarization
- classification
- normalization
- candidate knowledge generation
- routing assistance
- lightweight reasoning support

VOOL itself is not defined by one specific model vendor or one specific base model. The long-term goal is for VOOL to remain the persistent personal intelligence layer while model providers remain replaceable.

Operationally, VOOL remains the system:

- router
- memory layer
- validation layer
- policy layer
- persona layer
- shared coordination logic

External models are worker or teacher backends only.

## Core Principle

VOOL may integrate with external models, but external model code, weights, tokenizers, runtimes, and provider services always remain under their original licenses unless explicitly stated otherwise by their upstream source.

Using a third-party model with VOOL does not relicense that model under the VOOL project license.

## What VOOL Owns

VOOL owns and licenses its own original integration logic, including:

- model adapter code written for VOOL
- provider manifests written for VOOL
- orchestration logic
- memory / index integration
- candidate-shard handling
- confidence and provenance plumbing
- policy and routing rules

These components remain under the license chosen for VOOL’s own codebase unless explicitly marked otherwise.

## What VOOL Does Not Relicense

VOOL does not automatically take ownership of or relicense:

- third-party model weights
- tokenizer files
- third-party runtime binaries
- remote provider APIs
- vendored third-party source code
- user-supplied model assets

Those remain governed by their original upstream terms.

## Preferred Integration Method

The preferred integration pattern is adapter boundaries, not code copying.

Recommended methods:

- local subprocess adapters
- local model-path adapters
- OpenAI-compatible HTTP adapters
- optional dependency adapters
- provider manifests with explicit license metadata

This keeps licensing boundaries clear and reduces the risk of mixing incompatible terms directly into VOOL core.

## Bundling Policy

By default, VOOL should not bundle third-party model weights in the main repository.

Preferred approach:

- users provide local model paths
- or users connect to external model runtimes
- or users configure compatible remote or local APIs

This keeps the repo smaller, lowers redistribution risk, and makes license handling cleaner.

## Candidate Knowledge Rule

Outputs from external models are treated as candidate knowledge, not automatic truth.

Before becoming durable memory or shared-index-advertised knowledge, model output should be handled through existing VOOL controls such as:

- source tagging
- confidence scoring
- provenance attachment
- versioning
- review / validation gates
- policy checks

The safe rule is:

external model suggests -> VOOL evaluates -> memory stores -> shared surfaces advertise metadata only if allowed

This prevents raw model output from becoming blind canonical shared knowledge.

## License Metadata Requirements

Every integrated provider or model should have a manifest entry including:

- provider name
- model name
- source type
- declared license name
- license reference
- whether weights are bundled or user-supplied
- whether redistribution is known to be allowed
- operational notes

If a provider entry is missing license metadata, VOOL should warn clearly at startup or registration time.

## GPL / AGPL Policy

VOOL core should avoid directly incorporating GPL or AGPL code unless the project intentionally chooses to comply with those stronger copyleft obligations for the affected distribution.

If support is needed for GPL or AGPL tools, the preferred pattern is:

- subprocess boundary
- local service boundary
- external API boundary

This helps keep the core licensing boundary clear.

## Public Integration Surfaces

If VOOL provides reusable SDKs, bindings, schemas, or example clients, those may be licensed more permissively than the core system if the project chooses.

This can make ecosystem adoption easier without changing the licensing of VOOL core itself.

## User Responsibility

Users are responsible for complying with the upstream license terms of any third-party model, runtime, API, or asset they connect to VOOL.

VOOL support for a provider does not automatically grant:

- redistribution rights
- commercial usage rights
- sublicensing rights
- hosting rights

Those depend on the original upstream terms.

## Maintainer Rules

When adding a new model integration:

1. Prefer an adapter over vendoring upstream code.
2. Record license metadata before enabling the provider by default.
3. Preserve all required notices and attribution.
4. Do not relabel third-party assets as VOOL-owned.
5. Keep third-party integrations optional where possible.
6. Treat model outputs as candidate knowledge unless verified through policy and review.

## Recommended Folder Policy

VOOL adapter code: under the VOOL project license  
Public SDKs/examples, if split: permissive license if desired  
Third-party notices: tracked in `docs/THIRD_PARTY_LICENSES.md` and `third_party/NOTICES/`  
Model manifests: tracked separately from the main core logic  
Bundled third-party assets: avoid unless necessary and clearly documented

## Simple Summary

VOOL should stay:

- model-agnostic
- local-first
- licensing-clean
- adapter-based
- explicit about provenance
- careful about what becomes shared knowledge

The model can change.

The personal intelligence layer should remain stable.

## Disclaimer

This file is an operational repository policy and implementation guide.

It is not legal advice.
