# Third-Party Licenses

This project contains original code developed for VOOL, along with optional integrations that may interface with third-party software, libraries, models, runtimes, or external assets.

## Licensing Overview

### VOOL Core

Unless otherwise noted, the original VOOL core codebase is licensed under the Business Source License 1.1 (`BSL 1.1`).

This includes the main product code such as:

- core orchestration
- swarm / networking logic
- storage and indexing
- meet-and-greet services
- proof / trust systems
- human input adaptation
- local runtime and service wrappers

### Public SDKs / Examples / Integration Helpers

Where explicitly marked, certain public-facing developer tools may be licensed under Apache License 2.0 to allow easier third-party integration and adoption.

These may include:

- SDKs
- client bindings
- protocol helpers
- sample integrations
- example client code

### Third-Party Dependencies

Third-party libraries, model runtimes, tokenizers, weights, and other external assets remain under their original upstream licenses.

These are not relicensed under the VOOL core license.

## Important Rule

Our license applies to our original code.

Third-party code, models, and assets keep their original licenses.

Using or interfacing with a third-party model or runtime does not change that third-party license.

## Typical Third-Party Categories

### Python Libraries

Common dependencies such as networking, cryptography, validation, ML tooling, and utility libraries remain under their own respective licenses.

Examples may include:

- Apache-2.0
- MIT
- BSD-style licenses
- other permissive licenses

### Optional Model Integrations

VOOL may support external model providers or user-supplied local models through adapters.

Examples:

- local model runtimes
- OpenAI-compatible APIs
- user-supplied model paths
- optional Transformers-based integrations
- optional open-weight model families such as Qwen

These integrations do not change the upstream license of:

- model code
- model weights
- tokenizer files
- vendor runtimes

### User-Supplied Assets

Any models, weights, tokenizers, or external files supplied by the end user remain under the user's chosen or upstream source terms.

VOOL does not claim ownership over those assets.

## Notices And Attribution

Where required by upstream licenses, this repository preserves:

- license texts
- copyright notices
- attribution notices
- NOTICE files, where applicable

These are stored in:

- `LICENSES/`
- `third_party/NOTICES/`

## No Automatic Redistribution Rights

Support for a third-party model or runtime inside VOOL does not imply that VOOL grants redistribution rights for that third-party asset.

Users are responsible for complying with the original license terms of any third-party software, model, or weight they use with VOOL.

## GPL / AGPL Boundary Policy

VOOL core should avoid directly incorporating GPL or AGPL code unless the project intentionally chooses to comply with those stronger copyleft obligations for the affected distribution.

If support is provided for GPL or AGPL tools, the preferred pattern is:

- subprocess boundary
- local service boundary
- network / API boundary

This helps keep licensing boundaries clear.

## How To Read This File

If a component was written as part of VOOL itself, assume it is covered by the project's declared license unless explicitly marked otherwise.

If a component comes from a third party, its original upstream license still applies.

If a folder or file has its own license header or separate license file, that more specific notice takes precedence.

## Maintainer Guidance

When adding a new dependency, model integration, or vendored asset:

- record the dependency name
- record the upstream source
- record the upstream license
- preserve required notices
- do not relabel third-party assets as BSL
- keep optional integrations modular where possible

## Placeholder Dependency Register

Update this section as integrations are added.

### Example Entry Format

Name: Example Dependency  
Type: Library / Model / Runtime / Asset  
Upstream: Example upstream project  
License: MIT / Apache-2.0 / etc.  
Bundled: Yes / No  
Redistributed by VOOL: Yes / No  
Notes: Optional runtime only / user-supplied / adapter-only / etc.

## Current / Planned Optional Integrations

### Qwen-family model integrations

Type: Model integration  
Bundled: No, recommended  
Redistributed by VOOL: No, recommended  
Notes: User-supplied or externally fetched under upstream terms

### Transformers-based adapter

Type: Python library integration  
Bundled: Dependency-managed  
Redistributed by VOOL: Only as dependency metadata, not relicensed  
Notes: Optional adapter path only

### OpenAI-compatible API adapters

Type: Integration adapter  
Bundled: Yes, our adapter code  
Redistributed by VOOL: Yes, our code only  
Notes: Adapter code is ours; remote provider services remain under their own terms

## Disclaimer

This document is an operational licensing guide for the repository structure.

It is not legal advice.
