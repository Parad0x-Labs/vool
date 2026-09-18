"""Non-source evidence cannot alter an implementation conclusion.

ARGUS killed an earlier version of this suite by mutating `_is_source_path` to `lambda p: True`
and watching all 73 conductor tests stay green. The mutation is plainly behaviour-changing --
measured on this repository, "retry" returns 16 matches (all source) filtered and 60 (41
non-source) unfiltered, including `AGENT_HANDOVER.md`, `CLAUDE.md` and `CURRENT_STATE.json` -- so
the suite, not the mutation, was at fault.

**Why it survived.** `tests/test_conductor_multi_intent.py` patches
`core.conductor.operations._search_workspace`, which is the function that *performs* the filtering.
Every workspace test ran with the filter replaced by a fixture, so no test could observe it working
or failing. A mock placed at the seam under test measures the mock.

**The fix in this file.** Nothing in `core.conductor.operations` is patched. The fake is installed
one layer lower, at `NodeContext.run_tool_intent` -- the injected tool seam that stands in for
`execute_tool_intent` -- so the real `_search_workspace`, the real `_is_source_path`, the real
`_workspace_run` ranking and the real `_conclusion_run` scoping all execute. The fixture returns a
workspace whose documentation deliberately contradicts its source.

**What is asserted.** The conclusion's *evidence* (`outcome.result`), not only its rendered
sentence. A mutation that filters what is displayed while letting unfiltered matches into the
evidence input would leave the prose intact, so prose-only assertions would miss it.

**The contract this pins.** `_SOURCE_EXTENSIONS` admits code and excludes prose and state files --
`.md`, `.json`, `.txt`. That excludes `.json` even where a project uses JSON for configuration, and
that is deliberate rather than an oversight: the question these nodes answer is "what does this
project *do*", and a JSON file describes rather than implements. If that contract is ever widened,
these tests should be changed with it, on purpose.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.conductor.node import ConductorNode, NodeLifecycle
from core.conductor.operations import (
    _SOURCE_EXTENSIONS,
    _conclusion_expand,
    _conclusion_run,
    _search_workspace,
    _workspace_expand,
    _workspace_run,
)
from core.conductor.registry import NodeContext
from tests.conductor_product import compose_product

# --------------------------------------------------------------------------------------
# A workspace whose documentation contradicts its source
# --------------------------------------------------------------------------------------
#
# Shaped after this repository's real state, which is what makes it a fair test: `retry` is
# implemented in source and discussed at far greater length in project prose, and `circuit breaker`
# exists as a class that the retry path does not use while the delivery notes talk about it
# constantly.

SOURCE_RETRY = "src/provider_retry.py"
SOURCE_POLICY = "src/retry_policy.py"
SOURCE_CLIENT = "src/http_client.ts"
SOURCE_LEGACY = "src/legacy_retry.go"
SOURCE_BREAKER = "src/circuit_breaker.py"

DOC_CLAUDE = "CLAUDE.md"
DOC_HANDOVER = "AGENT_HANDOVER.md"
DOC_STATE = "VOOL-DELIVERY/CURRENT_STATE.json"
DOC_BOARD = "VOOL-DELIVERY/STATUS_BOARD.md"
DOC_MATRIX = "docs/APPLE_CAPABILITY_MATRIX.md"
DOC_FOO = "docs/foo.md"
DOC_NOTES = "notes/retry-design.txt"

NON_SOURCE_PATHS = frozenset(
    {DOC_CLAUDE, DOC_HANDOVER, DOC_STATE, DOC_BOARD, DOC_MATRIX, DOC_FOO, DOC_NOTES}
)

#: term -> every file the raw (unfiltered) workspace search would return.
#: `circuit` / `breaker` are the load-bearing rows: one source file has the class, the RETRY source
#: files do not, and six documentation files discuss it at length.
_RAW_INDEX: dict[str, list[str]] = {
    "retry": [
        SOURCE_RETRY, SOURCE_POLICY, SOURCE_CLIENT, SOURCE_LEGACY,
        DOC_CLAUDE, DOC_HANDOVER, DOC_STATE, DOC_BOARD, DOC_MATRIX, DOC_FOO, DOC_NOTES,
    ],
    "retries": [SOURCE_POLICY, DOC_HANDOVER, DOC_STATE],
    "provider": [SOURCE_RETRY, SOURCE_CLIENT, DOC_CLAUDE, DOC_STATE, DOC_HANDOVER],
    "implementation": [DOC_CLAUDE, DOC_HANDOVER],
    "circuit": [SOURCE_BREAKER, DOC_CLAUDE, DOC_STATE, DOC_BOARD, DOC_MATRIX, DOC_FOO],
    "breaker": [SOURCE_BREAKER, DOC_CLAUDE, DOC_STATE, DOC_BOARD, DOC_MATRIX, DOC_FOO],
}


class _Execution:
    """The shape `execute_tool_intent` returns, reduced to what `_search_workspace` reads."""

    def __init__(self, matches: list[dict[str, Any]]) -> None:
        self.ok = True
        self.details = {
            "observation": {
                "schema": "tool_observation_v1",
                "intent": "workspace.search_text",
                "ok": True,
                "matches": matches,
            }
        }


class _Workspace:
    """A fake tool seam. Returns raw, UNFILTERED matches exactly as the real search would."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def __call__(self, payload: dict) -> _Execution:
        assert payload["intent"] == "workspace.search_text"
        query = str(payload["arguments"]["query"])
        self.queries.append(query)
        paths = _RAW_INDEX.get(query, [])
        return _Execution(
            [{"path": path, "line": 12, "snippet": f"... {query} ..."} for path in paths]
        )


@pytest.fixture()
def workspace() -> _Workspace:
    return _Workspace()


@pytest.fixture()
def ctx(workspace: _Workspace) -> NodeContext:
    return NodeContext(run_tool_intent=workspace)


def _investigate(ctx: NodeContext, clause: str) -> dict[str, Any]:
    """Drive the REAL investigation operation: expand -> run. Nothing patched."""
    expanded = _workspace_expand(clause)
    assert expanded, f"the investigation adapter found nothing to act on in {clause!r}"
    node = ConductorNode(
        node_id="t:workspace_investigation:x",
        operation="workspace_investigation",
        request_text=clause,
        arguments=expanded[0],
    )
    return _workspace_run(node, ctx)


def _conclude(ctx: NodeContext, clause: str, dependency_result: dict[str, Any]) -> dict[str, Any]:
    """Drive the REAL conclusion operation against a real investigation result."""
    expanded = _conclusion_expand(clause)
    assert expanded, f"the conclusion adapter found nothing to act on in {clause!r}"
    node = ConductorNode(
        node_id="t:conclusion:x",
        operation="conclusion",
        request_text=clause,
        arguments=expanded[0],
        depends_on=("t:workspace_investigation:x",),
    )
    scoped = NodeContext(
        run_tool_intent=ctx.run_tool_intent,
        dependency_results={"t:workspace_investigation:x": dependency_result},
    )
    return _conclusion_run(node, scoped)


# --------------------------------------------------------------------------------------
# 1. Source says NO, documentation says YES -- source wins
# --------------------------------------------------------------------------------------


def test_documentation_claiming_a_circuit_breaker_cannot_make_the_conclusion_say_yes(ctx) -> None:
    """The exact class that produced a false conclusion on a live drive.

    Six documentation files discuss the circuit breaker. The files that actually implement retry do
    not contain one. The conclusion must be NO, and its evidence must reach that from source alone.
    """
    investigation = _investigate(ctx, "inspect the provider retry implementation")
    conclusion = _conclude(
        ctx, "whether the retry implementation has a circuit breaker", investigation
    )

    assert conclusion["present_in_scope"] is False
    # The one place it does live is a source file, and the answer says so -- that is the honest
    # shape of "the class exists, this path does not use it".
    assert conclusion["exists_elsewhere"] is True
    assert conclusion["elsewhere_files"] == [SOURCE_BREAKER]

    for bucket in ("scope_files", "in_scope_files", "elsewhere_files"):
        assert not (set(conclusion[bucket]) & NON_SOURCE_PATHS), (bucket, conclusion[bucket])


def test_the_investigation_evidence_itself_contains_no_documentation(ctx) -> None:
    """Asserted on `result`, not on the rendered sentence.

    A mutation that filters only what is DISPLAYED while letting unfiltered matches into the
    evidence input leaves the prose looking correct. The evidence is where it has to be caught.
    """
    investigation = _investigate(ctx, "inspect the provider retry implementation")

    assert set(investigation["files"]) <= {SOURCE_RETRY, SOURCE_POLICY, SOURCE_CLIENT, SOURCE_LEGACY}
    assert not (set(investigation["files"]) & NON_SOURCE_PATHS)
    assert not (set(investigation["file_terms"]) & NON_SOURCE_PATHS)
    # Ranked by term coverage: the file carrying BOTH "provider" and "retry" leads.
    assert investigation["files"][0] == SOURCE_RETRY


def test_a_documentation_only_feature_is_absent_rather_than_confirmed(ctx) -> None:
    """When the term lives ONLY in prose, the conclusion must be a flat no.

    Distinct from the case above: there is no source file at all to point at, so both
    `present_in_scope` and `exists_elsewhere` must be False rather than the answer reaching for the
    documentation to have something to say.
    """
    raw_backup = dict(_RAW_INDEX)
    try:
        _RAW_INDEX["circuit"] = [DOC_CLAUDE, DOC_STATE, DOC_BOARD, DOC_MATRIX, DOC_FOO]
        _RAW_INDEX["breaker"] = [DOC_CLAUDE, DOC_STATE, DOC_BOARD, DOC_MATRIX, DOC_FOO]
        investigation = _investigate(ctx, "inspect the provider retry implementation")
        conclusion = _conclude(
            ctx, "whether the retry implementation has a circuit breaker", investigation
        )
    finally:
        _RAW_INDEX.clear()
        _RAW_INDEX.update(raw_backup)

    assert conclusion["present_in_scope"] is False
    assert conclusion["exists_elsewhere"] is False
    assert conclusion["elsewhere_files"] == []


# --------------------------------------------------------------------------------------
# 2. Documentation outnumbers source -- the conclusion stays scoped to source
# --------------------------------------------------------------------------------------


def test_retry_evidence_stays_source_scoped_even_though_prose_outnumbers_it(ctx, workspace) -> None:
    """`retry` returns 4 source files and 7 documentation files from the raw search.

    Ranking by hit count without filtering would hand the answer to whichever file repeats the word
    most, and prose repeats it most by nature -- that is the mechanism, not an accident of this
    fixture.
    """
    investigation = _investigate(ctx, "inspect the provider retry implementation")

    raw_paths = set(_RAW_INDEX["retry"]) | set(_RAW_INDEX["provider"])
    assert len(raw_paths & NON_SOURCE_PATHS) >= 5, "fixture must have prose outnumbering source"

    assert not (set(investigation["files"]) & NON_SOURCE_PATHS)
    assert all(path.startswith("src/") for path in investigation["files"])
    # The search really ran -- otherwise this test could pass against an empty code path.
    assert workspace.queries, "no search was performed"


# --------------------------------------------------------------------------------------
# 3. Positive control -- every production source extension is admitted
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("extension", sorted(_SOURCE_EXTENSIONS))
def test_every_declared_source_extension_survives_the_real_filter(extension, ctx, workspace) -> None:
    """Drives `_search_workspace`, not `_is_source_path`, over production's own extension set.

    Parametrized from `_SOURCE_EXTENSIONS` itself so adding a language to production without
    admitting it here is impossible, and removing one turns this red.
    """
    path = f"src/module.{extension}"
    _RAW_INDEX["__probe__"] = [path]
    try:
        found = _search_workspace(ctx, "__probe__")
    finally:
        _RAW_INDEX.pop("__probe__", None)

    assert [match["path"] for match in found["matches"]] == [path]


def test_an_uppercase_source_extension_is_still_source(ctx) -> None:
    _RAW_INDEX["__probe__"] = ["src/Legacy.PY", "src/Widget.TS"]
    try:
        found = _search_workspace(ctx, "__probe__")
    finally:
        _RAW_INDEX.pop("__probe__", None)
    assert len(found["matches"]) == 2


# --------------------------------------------------------------------------------------
# 4. Negative control -- prose and state files are refused
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        DOC_CLAUDE,
        DOC_HANDOVER,
        DOC_STATE,
        DOC_BOARD,
        DOC_MATRIX,
        DOC_FOO,
        DOC_NOTES,
        "README",              # no extension at all
        "Makefile",
        "package.json",        # config JSON is excluded too -- see the module docstring
        "requirements.txt",
        "notes.MD",
    ],
)
def test_non_source_paths_never_reach_the_evidence(path, ctx) -> None:
    _RAW_INDEX["__probe__"] = [path]
    try:
        found = _search_workspace(ctx, "__probe__")
    finally:
        _RAW_INDEX.pop("__probe__", None)
    assert found["matches"] == []


def test_a_mixed_result_keeps_the_source_half_and_drops_the_rest(ctx) -> None:
    """Both halves matter: dropping everything would also pass a "no prose" assertion."""
    _RAW_INDEX["__probe__"] = [SOURCE_RETRY, DOC_CLAUDE, SOURCE_CLIENT, DOC_STATE, DOC_FOO]
    try:
        found = _search_workspace(ctx, "__probe__")
    finally:
        _RAW_INDEX.pop("__probe__", None)
    assert [match["path"] for match in found["matches"]] == [SOURCE_RETRY, SOURCE_CLIENT]


# --------------------------------------------------------------------------------------
# End to end through the scheduler, so the wiring is covered too
# --------------------------------------------------------------------------------------


def test_the_whole_investigation_and_conclusion_run_source_scoped_through_the_scheduler(
    workspace,
) -> None:
    """The same property, driven through `run_conductor_plan` and `compose_answer`.

    The unit drives above prove the operations; this proves nothing between them re-introduces the
    unfiltered matches -- including the scheduler's dependency plumbing, which is what hands the
    investigation's file set to the conclusion.
    """
    import json

    from core.conductor.planner import plan_conductor_turn
    from core.conductor.scheduler import run_conductor_plan

    prompt = (
        "What is 137 x 29? Also inspect the provider retry implementation "
        "and tell me whether the retry implementation has a circuit breaker."
    )
    reply = json.dumps(
        [
            {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
            {
                "request": "inspect the provider retry implementation",
                "operation": "workspace_investigation",
                "depends_on": [],
            },
            {
                "request": "whether the retry implementation has a circuit breaker",
                "operation": "conclusion",
                "depends_on": [1],
            },
        ]
    )
    plan = plan_conductor_turn(prompt, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is not None
    outcomes = run_conductor_plan(plan, context=NodeContext(run_tool_intent=workspace))
    composed = compose_product(plan, outcomes)

    conclusion = next(o for o in outcomes if o.node.operation == "conclusion")
    assert conclusion.state is NodeLifecycle.SUCCEEDED
    assert conclusion.result["present_in_scope"] is False
    for bucket in ("scope_files", "in_scope_files", "elsewhere_files"):
        assert not (set(conclusion.result[bucket]) & NON_SOURCE_PATHS)

    investigation = next(o for o in outcomes if o.node.operation == "workspace_investigation")
    assert not (set(investigation.result["files"]) & NON_SOURCE_PATHS)

    assert composed.complete
    assert "3973" in composed.text
    # The answer must not name a documentation file as evidence about an implementation.
    for path in NON_SOURCE_PATHS:
        assert path not in composed.text
