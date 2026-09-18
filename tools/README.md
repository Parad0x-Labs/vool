# tools/

This package owns tool registration and explicit tool contracts.

The goal is determinism and auditability, not clever hidden invocation paths.

## What Lives Here

- `contracts.py`: tool contract schema
- `registry.py`: tool registration and contract lookup
- `browser/`, `web/`: built-in tool surfaces

## Tool Contract Requirements

Every tool should have explicit:

- name
- description
- input schema
- output schema
- side-effect class
- approval requirement
- timeout policy
- retry policy
- artifact emission behavior
- error contract

## Boundary Rule

Tool registration belongs here.
Feature intent-routing and orchestration do not.

Higher-level runtime/tool planning can consume these contracts, but it should not redefine them ad hoc in multiple places.
