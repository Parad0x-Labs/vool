"""Hermetic child-process boundary for the release gauntlet.

`bootstrap_runtime_services` has no teardown. Four sessions of tracing found three separate
in-memory/on-disk states it establishes and never releases -- the provider registry, the mesh
daemon thread, and `ensure_public_hive_auth` writing `agent-bootstrap.json` into the config home --
and each fix uncovered another. Enumerating them is unbounded work; destroying the interpreter that
holds them is bounded and total.

So the gauntlet runs in a child process, one child per SCENARIO GROUP, and the parent never imports
the runtime at all. The OS reclaims whatever bootstrap established, whether or not anyone has found
it yet.

The group -- not the test -- is the unit of isolation, because a scenario's meaning lives across its
turns: cross-chat isolation is only a real claim when ALPHA and BETA share one bootstrapped runtime.

MIGRATION STATUS -- the boundary is proven; three legacy files are not yet on it.

Measured after this lane, against the original contamination target
(`tests/test_must_keep_is_a_guarantee_not_a_sort_key.py`):

    target alone ................................ 15 passed
    tests/gauntlet/test_hermetic_boundary.py -> target .... 30 passed   <- boundary holds
    tests/gauntlet/test_harness_control.py   -> target .... 1 failed    <- legacy leaks

So the child boundary does prevent the contamination, and what still poisons the canonical suite is
the three files that continue to bootstrap IN-PROCESS:

    test_harness_control.py
    test_public_cross_chat_isolation.py
    test_release_conversation_gauntlet.py

Each calls `H.install(monkeypatch)` + `H.runtime()` in the parent interpreter. Porting them onto
`run_group` is the remaining work; it was deliberately out of scope here (the brief said not to
implement new G scenarios and asked only for ONE ported cross-chat group). Until they move, the
4-shard canonical run stays red on exactly one test, for exactly this reason -- not because the
boundary is unsound.
"""
