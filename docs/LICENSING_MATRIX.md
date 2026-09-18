# Licensing Matrix

This is the clean internal licensing split for local repository preparation.

It is an operational structure guide, not legal advice.

## Recommended Matrix

### VOOL Core Product Code

Recommended license posture:

- `BSL 1.1`

Intended folders:

- `apps/`
- `core/`
- `network/`
- `storage/`
- `sandbox/`
- `relay/`
- `retrieval/`
- `ops/`
- `config/`
- `tests/`
- `adapters/`

These are treated as VOOL-owned product code unless a more specific file-level notice overrides them.

### Public SDKs / Examples / Bindings

Recommended future posture:

- `Apache-2.0`

Intended folders if created later:

- `sdk/`
- `client/`
- `schemas-public/`
- `examples/`
- `bindings/`
- `api-reference-samples/`

### Third-Party Libraries / Models / Weights / Tokenizers

License posture:

- original upstream license

Intended locations:

- `third_party/`
- `vendor/`, if ever needed
- `models/`, metadata only is preferred

Important rule:

- third-party assets do not become BSL just because VOOL integrates with them

## Clean Rule

License the glue, not the dependency.

That means:

- VOOL adapter code can remain under the VOOL project license
- third-party models and weights stay under their upstream terms
- user-supplied assets stay under user or upstream terms

## Distribution Reminder

Before any public release:

1. replace placeholder license files with the real texts
2. confirm folder-level intent
3. preserve third-party notices
4. do not bundle model weights by default
