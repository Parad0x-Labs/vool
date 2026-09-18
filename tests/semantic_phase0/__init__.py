"""Semantic routing Phase-0 proofs.

A package rather than a bare directory, and for a specific reason: this directory carries its own
`conftest.py`, and without `__init__.py` pytest imports it under the same module name as
`tests/conftest.py` in prepend import mode. The collision is silent in a single-directory run and
shows up only under CI's sharded invocation, where the shard that owns the replay file reported
`fixture 'make_agent_module' not found` while the same file passed on its own. `tests/gauntlet`
carries an `__init__.py` for the same reason.
"""
