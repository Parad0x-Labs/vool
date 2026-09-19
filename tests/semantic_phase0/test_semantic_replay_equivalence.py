"""Instrumentation ON and OFF answer identically, and ON adds no model calls. Proofs 1 and 2.

252 corpus turns, each driven twice -- once with `VOOL_SEMANTIC_REACH=0`, once with `=1` -- and the
two results compared field by field, response bytes included. A turn that raises is compared by its
exception type and message, because "both configurations fail the same way" is as much a part of
identical behaviour as "both succeed the same way".

That half of the proof used to ride on the blank-input crash: `""` and `"   "` raised `ValueError:
goal is required`, so the corpus supplied its own failing turn for free. The empty-turn gate fixed
that crash and the corpus now answers every one of its 252 turns, which is the correct runtime
behaviour and leaves the comparison machinery for a raising turn with nothing to exercise it. It is
exercised deliberately instead -- see `test_a_forced_fault_fails_identically_with_instrumentation_on_and_off`
-- because a proof that depends on a defect staying unfixed is a proof with an expiry date.

**Each turn gets its own fresh session id and its own fresh working directory.** Both isolations were
forced by measurement, not anticipated. The first version of this proof drove all turns under OFF and
then all under ON, and four assertions failed -- until the cause turned out to be the corpus, not the
instrumentation: `workspace_write` turns really write files, so the OFF pass created `notes.txt` in
the checkout and the ON pass then ran against a directory where it already existed. Order, not
treatment. A fresh temp cwd per run removes it.

**Differences are attributed only after a same-configuration control.** Some turns are inherently
nondeterministic -- one web-research turn made 39 outbound attempts in one run and 50 in the next,
with the flag unchanged. Attributing that to instrumentation would be false, and excluding it without
measuring would be a loosened test. So a turn whose OFF and ON runs differ is re-run twice more under
OFF: if those two also differ, the turn is nondeterministic and is reported as such; if they agree,
the difference IS the instrumentation and the proof fails. A real instrumentation bug shows up as
OFF==OFF and OFF!=ON, which this cannot absorb.

**`source_context` is compared by key set rather than dropped.** It is the caller's dict, mutated in
place by the turn, so its values legitimately differ -- but if instrumentation ever wrote into it,
the keys would diverge, and that is worth catching.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from ops.semantic_generalization_corpus import replay_texts

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    pin_volatile_machine_observations,
    reseal_network_after_function_fixtures,
)

#: Fields that differ because the experiment varies them, or because they are per-run identities.
#: Nothing is excluded for being merely inconvenient -- `response` is compared byte for byte.
#:
#: `turn_id` joined this set on measurement, not on preference. It is minted per turn from a fresh
#: `uuid4` in `core/agent_runtime/fast_command_surface.py:fast_path_result`, and on a task-bound fast
#: path it is THE SAME VALUE as `task_id` -- the identical uuid was being excluded under one key and
#: compared under the other, which is not a determinism property, it is a bookkeeping gap. Leaving it
#: in re-classified 22 fast-path turns as "nondeterministic" and pushed the excluded set from 10/252
#: to 32/252, over this proof's own cap. Excluding it puts those 22 turns BACK under byte comparison
#: rather than setting them aside; see `test_a_per_run_identity_is_the_only_thing_this_hides`, which
#: pins the two properties that make the exclusion safe.
_VARIED_BY_DESIGN = frozenset({"session_id", "task_id", "turn_id"})

#: Compared structurally rather than by value: `honesty_receipt` embeds the turn's own hashes and an
#: issue timestamp, and `source_context` is a mutated input dict.
_STRUCTURAL_ONLY = frozenset({"honesty_receipt", "source_context"})


def _drive(make_agent, text: str, session_id: str, *, flag: str) -> dict:
    """One turn under one flag setting, from a fresh agent in a fresh working directory.

    The cwd swap is what stops a `workspace_write` turn from leaving a file that the next run of the
    same turn then sees. Restored in `finally` so a raising turn cannot strand the process in a temp
    directory that is about to be deleted.
    """
    os.environ["VOOL_SEMANTIC_REACH"] = flag
    previous_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="vool_replay_") as workdir:
        try:
            os.chdir(workdir)
            result = make_agent().run_once(
                text,
                session_id_override=session_id,
                source_context={"surface": "cli", "session_id": session_id, "runtime_session_id": session_id},
            )
            return dict(result)
        except Exception as exc:
            return {"__raised__": f"{type(exc).__name__}: {exc}"}
        finally:
            os.chdir(previous_cwd)


def _comparable(result: dict) -> dict:
    return {
        key: value
        for key, value in result.items()
        if key not in _VARIED_BY_DESIGN and key not in _STRUCTURAL_ONLY
    }


def _same(left: dict, right: dict) -> bool:
    return _comparable(left) == _comparable(right) and left.get("response") == right.get("response")


#: Why a turn is not eligible for the byte-identity claim. Decided by a same-configuration control
#: run, never by whether the OFF/ON comparison happened to be inconvenient.
_ELIGIBLE = ""
_NONDETERMINISTIC = "nondeterministic"


@pytest.fixture(scope="module")
def replayed(make_agent_module):
    """Every corpus turn under OFF and ON, classified by whether its bytes can be compared at all.

    Returns rows of `(text, off, on, exclusion)`. One exclusion: `nondeterministic`, meaning the turn
    does not reproduce itself under two further OFF runs, so no OFF/ON comparison of it could mean
    anything either way.

    The obvious second exclusion -- "this turn fetched from the web" -- was tried and discarded. The
    only field available to express it, `web_calls`, turns out to be context-retrieval telemetry
    rather than outbound HTTP: it is non-zero on 197 of 252 turns, "hi" and "??" included. Using it
    would have set aside three quarters of the corpus for a property it does not describe. The real
    fetching is blocked at the socket layer instead -- see `block_outbound_network` in this package's
    conftest -- which removes the cause rather than filtering its symptom.
    """
    rows = []
    for index, text in enumerate(replay_texts()):
        off = _drive(make_agent_module, text, f"replay-{index}-off", flag="0")
        on = _drive(make_agent_module, text, f"replay-{index}-on", flag="1")
        exclusion = _ELIGIBLE
        if not _same(off, on):
            control_a = _drive(make_agent_module, text, f"replay-{index}-ctrl-a", flag="0")
            control_b = _drive(make_agent_module, text, f"replay-{index}-ctrl-b", flag="0")
            if not _same(control_a, control_b):
                exclusion = _NONDETERMINISTIC
        rows.append((text, off, on, exclusion))
    os.environ.pop("VOOL_SEMANTIC_REACH", None)
    return tuple(rows)


@pytest.fixture(scope="module")
def deterministic(replayed):
    """Turns whose bytes are the runtime's own and reproduce with the flag held constant."""
    return tuple((text, off, on) for text, off, on, exclusion in replayed if not exclusion)


def test_the_replay_corpus_is_large_enough_to_be_evidence(replayed) -> None:
    assert len(replayed) >= 200, (
        f"the replay proof needs at least 200 turns; the corpus supplied {len(replayed)}"
    )


def test_the_excluded_turns_are_named_and_few(replayed, deterministic) -> None:
    """No silent truncation. What the identity proof does not cover, it says out loud.

    The exclusion set is the one place this proof could be quietly hollowed out: if most of the
    corpus drifted into "cannot be compared", the remaining claim would be about almost nothing. So
    the count is capped and the turns are printed.
    """
    excluded = [(text, reason) for text, _off, _on, reason in replayed if reason]
    covered = len(deterministic)
    assert covered >= 200, (
        f"only {covered} of {len(replayed)} turns are byte-comparable, which is below the 200-turn "
        f"floor this proof needs. Excluded: {excluded!r}"
    )
    assert len(excluded) <= len(replayed) // 10, (
        f"{len(excluded)} of {len(replayed)} turns cannot be byte-compared. That is too much of the "
        f"corpus to set aside; investigate before trusting the identity result. Turns: {excluded!r}"
    )
    print(f"\nbyte-compared: {covered}/{len(replayed)}; excluded ({len(excluded)}): {excluded!r}")


def test_a_per_run_identity_is_the_only_thing_this_hides(make_agent_module, deterministic) -> None:
    """`turn_id` is excluded from the byte comparison. This is what makes that safe.

    An exclusion is where a proof goes to die quietly, so the two properties that justify this one
    are asserted rather than described. First: the value genuinely cannot be compared -- two runs of
    the same text under the SAME flag mint different ids, so keeping it in the comparison set could
    only ever produce false nondeterminism, which is exactly what it did (10/252 -> 32/252). Second:
    excluding the VALUE must not also excuse the FIELD. If instrumentation ever added or dropped
    `turn_id`, `_comparable` would no longer catch it, so presence is checked here directly.
    """
    # A FAST-PATH turn, deliberately: `turn_id` is minted in `fast_path_result`, so a model-lane turn
    # carries none at all and would prove nothing about the field being excluded here.
    try:
        first = _drive(make_agent_module, "weather today", "identity-ctrl-a", flag="0")
        second = _drive(make_agent_module, "weather today", "identity-ctrl-b", flag="0")
    finally:
        os.environ.pop("VOOL_SEMANTIC_REACH", None)
    assert first.get("turn_id") and second.get("turn_id"), (
        "this fast-path turn is expected to carry a turn id; if it no longer does, the exclusion "
        f"has nothing to justify: {first.get('turn_id')!r} / {second.get('turn_id')!r}"
    )
    assert first["turn_id"] != second["turn_id"], (
        "`turn_id` is excluded because it is a per-run identity. It just reproduced across two runs, "
        "so it is NOT one any more and does not belong in the excluded set"
    )

    # The field itself is still under observation, value or no value.
    drifted = [
        f"{text!r}: off={'turn_id' in off} on={'turn_id' in on}"
        for text, off, on in deterministic
        if ("turn_id" in off) != ("turn_id" in on)
    ]
    assert not drifted, (
        "instrumentation changed whether a turn carries a turn id:\n" + "\n".join(drifted[:5])
    )


def test_every_response_is_byte_identical_with_instrumentation_on_and_off(deterministic) -> None:
    differing = [
        text
        for text, off, on in deterministic
        if off.get("response") != on.get("response")
    ]
    assert not differing, (
        f"{len(differing)} of {len(deterministic)} turns answered differently with instrumentation on. "
        f"First five: {differing[:5]!r}"
    )


def test_no_corpus_turn_raises_any_more(deterministic) -> None:
    """The Blank Turn invariant, asserted here rather than assumed.

    This file used to require the opposite -- at least one corpus turn had to raise -- because `""`
    and `"   "` did, and the identical-failure comparison had no other subject. Both are answered
    now. Stating it as its own assertion means a REGRESSION back to raising turns this file red, in
    the suite whose corpus is broad enough to notice, instead of quietly restoring the old premise.
    """
    raised = [(text, off, on) for text, off, on in deterministic if "__raised__" in off or "__raised__" in on]
    assert not raised, (
        "a corpus turn raised out of `run_once`. Every one of these is answerable, and the "
        f"request-less shapes are answered by the empty-turn gate: {[text for text, _o, _n in raised]!r}"
    )


def test_a_forced_fault_fails_identically_with_instrumentation_on_and_off(make_agent_module) -> None:
    """The identical-failure half of the proof, driven by a fault instead of by a defect.

    "Both configurations fail the same way" is as much a part of identical behaviour as "both
    succeed the same way", and it is the half where a difference would matter most -- instrumentation
    that alters an exception path is instrumentation that changed the turn. The corpus no longer
    supplies a raising turn, so one is forced at a real seam and driven through the same `_drive`
    helper, under both flag settings, and the two failures are compared exactly as a corpus turn's
    would be.
    """

    def faulty_factory():
        agent = make_agent_module()

        def explode(*_args, **_kwargs):
            raise RuntimeError("front door exploded")

        agent._handle_turn_frontdoor = explode  # type: ignore[method-assign]
        return agent

    try:
        off = _drive(faulty_factory, "what is 12 x 5?", "replay-forced-fault-off", flag="0")
        on = _drive(faulty_factory, "what is 12 x 5?", "replay-forced-fault-on", flag="1")
    finally:
        os.environ.pop("VOOL_SEMANTIC_REACH", None)

    assert "__raised__" in off and "__raised__" in on, (
        f"the forced fault did not reach `run_once`'s caller: off={off!r} on={on!r}"
    )
    assert off["__raised__"] == on["__raised__"], (
        f"the same fault failed differently: off={off['__raised__']!r} on={on['__raised__']!r}"
    )


def test_every_other_turn_field_is_identical_too(deterministic) -> None:
    # Not just the prose. The route, the reason, the skips, the confidence, the workflow summary --
    # a difference in any of them is a behaviour change even when the sentence came out the same.
    problems: list[str] = []
    for text, off, on in deterministic:
        left, right = _comparable(off), _comparable(on)
        if left.keys() != right.keys():
            problems.append(f"{text!r}: key sets differ: {sorted(set(left) ^ set(right))}")
            continue
        for key in sorted(left):
            if left[key] != right[key]:
                problems.append(f"{text!r}: {key}: off={left[key]!r} on={right[key]!r}")
    assert not problems, "instrumentation changed the turn:\n" + "\n".join(problems[:10])


def test_instrumentation_writes_nothing_into_the_turns_source_context(deterministic) -> None:
    problems = [
        f"{text!r}: {sorted(set(off['source_context']) ^ set(on['source_context']))}"
        for text, off, on in deterministic
        if "source_context" in off and "source_context" in on
        and set(off["source_context"]) != set(on["source_context"])
    ]
    assert not problems, "observation must not add keys to the turn context:\n" + "\n".join(problems[:5])


def test_the_honesty_verdict_is_unchanged_by_instrumentation(deterministic) -> None:
    problems = []
    for text, off, on in deterministic:
        left = (off.get("honesty_receipt") or {}).get("verdict")
        right = (on.get("honesty_receipt") or {}).get("verdict")
        if left != right:
            problems.append(f"{text!r}: off={left!r} on={right!r}")
    assert not problems, "the turn-level receipt verdict changed:\n" + "\n".join(problems[:5])


def test_instrumentation_adds_exactly_zero_model_calls(deterministic) -> None:
    problems = [
        f"{text!r}: off={off.get('model_calls')!r} on={on.get('model_calls')!r}"
        for text, off, on in deterministic
        if off.get("model_calls") != on.get("model_calls")
    ]
    assert not problems, (
        "instrumentation changed the model-call count on "
        f"{len(problems)} turns:\n" + "\n".join(problems[:5])
    )
    total_off = sum(int(off.get("model_calls") or 0) for _text, off, _on in deterministic)
    total_on = sum(int(on.get("model_calls") or 0) for _text, _off, on in deterministic)
    assert total_on - total_off == 0, f"instrumentation added {total_on - total_off} model calls"


def test_instrumentation_adds_exactly_zero_web_calls(deterministic) -> None:
    problems = [
        f"{text!r}: off={off.get('web_calls')!r} on={on.get('web_calls')!r}"
        for text, off, on in deterministic
        if off.get("web_calls") != on.get("web_calls")
    ]
    assert not problems, "instrumentation changed the web-call count:\n" + "\n".join(problems[:5])


def test_no_module_under_core_semantic_statically_imports_a_provider() -> None:
    """A SUPPLEMENTARY check, explicitly not the proof.

    A hostile review defeated this as a proof: a dynamic import inside a function satisfies a static
    scan while making a real provider call. The proof is the runtime tripwire in
    `test_semantic_zero_provider_invocations.py`, which fires on the attempt regardless of when or
    how the import happens. This survives because a static import would still be a defect and is
    cheap to catch -- but it is worth nothing on its own, and that is why it says so here.

    `core/semantic/__init__.py` carries two deliberate exceptions, both probes that exist so the
    tripwire can be shown to fire: one reaches a provider THROUGH the registry, the other reaches an
    adapter directly, because a review defeated a registry-only detector by taking the direct path.
    They are exempted by name -- not by "any function-local import", which is the shape the first
    review used to hide a real call -- so any OTHER reach into the model layer still turns this red.
    A second assertion below proves nothing in the runtime calls either probe.
    """
    import re
    from pathlib import Path

    package = Path(__file__).resolve().parents[2] / "core" / "semantic"
    banned = re.compile(
        r"\b(?:from|import)\s+.*\b("
        r"adapters?|model_registry|ModelRegistry|memory_first_router|reasoning_engine|"
        r"ask_model|build_planner_ask_model|cloud_broker|ollama|llamacpp"
        r")\b",
        re.IGNORECASE,
    )
    exempt_functions = (
        "_probe_reaches_model_layer_for_tests",
        "_probe_registry_for_tests",
        "_probe_direct_adapter_for_tests",
    )
    offenders: list[str] = []
    for path in sorted(package.glob("*.py")):
        inside_exempt = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if any(stripped.startswith(f"def {name}") for name in exempt_functions):
                inside_exempt = True
                continue
            if inside_exempt and stripped and not line.startswith((" ", "\t")):
                inside_exempt = False
            if inside_exempt or stripped.startswith("#") or '"""' in stripped:
                continue
            if banned.search(stripped):
                offenders.append(f"{path.name}:{number}: {stripped}")
    assert not offenders, (
        "a module under core/semantic reaches the model layer statically:\n" + "\n".join(offenders)
    )

    # The exemptions are only defensible while the probes are unreachable from the runtime. A probe
    # that something calls is not a probe, it is the provider call the proof claims does not happen.
    runtime_roots = Path(__file__).resolve().parents[2]
    callers: list[str] = []
    for root in ("core", "apps", "tools", "adapters", "relay", "storage"):
        for path in (runtime_roots / root).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in exempt_functions:
                # `def <name>` is the definition itself; anything else naming it is a caller.
                for number, line in enumerate(text.splitlines(), start=1):
                    if name in line and not line.strip().startswith(f"def {name}"):
                        callers.append(f"{path.relative_to(runtime_roots)}:{number}: {line.strip()}")
    assert not callers, (
        "a test-only provider probe is reachable from the runtime:\n" + "\n".join(callers)
    )
