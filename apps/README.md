# apps/

This package owns process entrypoints and launch surfaces.

Rule:
entrypoints should stay thin.

They should do three things:

1. parse surface-specific arguments
2. call the canonical runtime bootstrap
3. launch the selected service or UI surface

They should not become a second home for business logic, feature policy, or storage internals.

## Local Runtime and Integration Surfaces

- `vool_api_server.py`: local API and compatible model-client runtime surface
- `vool_agent.py`: direct local agent shell
- `vool_chat.py`: minimal chat surface
- `vool_cli.py`: operator/maintenance CLI
## Retained Research Service Entrypoints

These are not required desktop startup steps. Consult [research activation](../research/README.md)
and the [subsystem status map](../docs/REPOSITORY_SCOPE.md) before running them.

- `vool_daemon.py`: experimental helper/peer-network daemon
- `brain_hive_watch_server.py`: research watch/dashboard service
- `meet_and_greet_server.py`: research meet/write service
- `meet_and_greet_node.py`: research seed-node process

## Boundary Rule

If a change needs deep feature logic, move it into `core/`, `storage/`, `tools/`, or `network/`.

`apps/` should compose the machine, not become the machine.
