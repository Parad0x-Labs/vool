"""The vocabulary measurement cannot be moved by reformatting it or gamed by adding to it.

Proofs 5, 6 and 7. Three properties, and the reason each one is load-bearing:

* **Tamper (5).** Adding a keyword must not raise the generalization score. If it could, the cheapest
  way to improve the number would be to type more keywords -- which is the dependence the number
  exists to measure, so the metric would reward exactly what it is meant to discourage.
* **Restyle (6).** The same vocabulary written as a tuple, a frozenset or a packed regex must measure
  the same. Otherwise the first person who wanted a better number would repack a table and change
  nothing real.
* **Runtime/plugin coverage (7).** Vocabulary arriving from a registration or a manifest must appear
  in the inventory. Otherwise the total falls whenever words move out of a literal and into a
  plugin, and a reorganisation reads as a reduction.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.semantic.generalization import CorpusCase, score_corpus
from core.semantic.lexical_authority import (
    KIND_RUNTIME_REGISTRATION,
    AuthorityInventory,
    build_inventory,
    normalize_term,
    scan_source_file,
    terms_from_pattern,
)

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def inventory() -> AuthorityInventory:
    return build_inventory(REPO_ROOT)


def _fixed_route(_text: str) -> str:
    """A deterministic stand-in executor for the properties that are about scoring, not routing.

    The tamper and monotonicity proofs are claims about the arithmetic of the score under a change
    to the inventory. Holding the routing result fixed isolates that: any movement in the score must
    then come from the inventory change, which is the thing under test.
    """
    return "live_weather"


def _cases() -> list[CorpusCase]:
    return [
        CorpusCase("a1", "weather_now", "how warm is it outside right now", "live_weather", "plain"),
        CorpusCase("a2", "weather_now", "temp outside", "live_weather", "terse"),
        CorpusCase("b1", "jacket", "do i need a jacket for tallinn tomorrow", "live_weather", "indirect"),
        CorpusCase("b2", "jacket", "packing for tallinn, warm coat or not", "live_weather", "indirect"),
        CorpusCase("c1", "boat", "is tomorrow too rough to take the boat out", "live_weather", "indirect"),
        CorpusCase("c2", "boat", "sailing tomorrow, will it be calm enough", "live_weather", "indirect"),
    ]


# --------------------------------------------------------------------------------------
# A controlled inventory, for the claims that are about the score's ARITHMETIC.
#
# The tamper proofs were written first against the real repo inventory and both of them passed while
# the guard they were supposed to protect was deleted -- caught by
# `scripts/semantic_phase0_mutation_matrix.py`, not by reading them. The reason: the probe words
# ("jacket", "boat", "sailing") are already in the runtime's real tables, so the classes were covered
# before the tamper and after it, and the manipulation changed nothing at all. Vacuous, and green.
#
# These use invented tokens that cannot be in any inventory, over an inventory built from scratch, so
# every term that matters is one the test put there.
# --------------------------------------------------------------------------------------

_SYNTHETIC = {
    "correct_one": "vroomble",
    "correct_two": "quixtal",
    "failing": "zblorp",
}


def _synthetic_inventory(*terms: str) -> AuthorityInventory:
    from core.semantic.lexical_authority import KIND_LITERAL_COLLECTION, AuthorityEntry

    return AuthorityInventory(
        entries=(AuthorityEntry("source:test_probe", "_PROBE", KIND_LITERAL_COLLECTION, tuple(terms)),)
    )


def _synthetic_cases() -> list[CorpusCase]:
    """Three classes: two the runtime routes correctly, one it gets wrong."""
    return [
        CorpusCase("s1", "correct_one", f"please {_SYNTHETIC['correct_one']} the thing", "route_a", "plain"),
        CorpusCase("s2", "correct_one", f"{_SYNTHETIC['correct_one']} it", "route_a", "terse"),
        CorpusCase("s3", "correct_two", f"kindly {_SYNTHETIC['correct_two']} that", "route_a", "polite"),
        CorpusCase("s4", "correct_two", f"{_SYNTHETIC['correct_two']} now", "route_a", "terse"),
        CorpusCase("s5", "failing", f"{_SYNTHETIC['failing']} the other one", "route_b", "plain"),
        CorpusCase("s6", "failing", f"go {_SYNTHETIC['failing']}", "route_b", "terse"),
    ]


def _routes_a_correctly(_text: str) -> str:
    """Always `route_a` -- which is correct for two of the three synthetic classes and wrong for one.

    That mix is the whole point: the `failing` class expects `route_b` and therefore never routes
    correctly, so covering it with a keyword is the manipulation that separates a total-classes
    denominator from a held-out one. A stand-in that got everything right could not tell them apart.
    """
    return "route_a"


# --------------------------------------------------------------------------------------
# Proof 6 -- restyle equivalence
# --------------------------------------------------------------------------------------


def test_the_same_vocabulary_measures_the_same_as_a_tuple_a_frozenset_and_a_regex(tmp_path) -> None:
    terms = ("price", "cost", "worth", "valuation", "quote")
    written = {
        "tuple_form": 'TERMS = ("price", "cost", "worth", "valuation", "quote")\n',
        "list_form": 'TERMS = ["price", "cost", "worth", "valuation", "quote"]\n',
        "set_form": 'TERMS = {"price", "cost", "worth", "valuation", "quote"}\n',
        "frozenset_form": 'TERMS = frozenset({"price", "cost", "worth", "valuation", "quote"})\n',
        "dict_form": 'TERMS = {"price": 1, "cost": 1, "worth": 1, "valuation": 1, "quote": 1}\n',
        "regex_form": 'import re\nTERMS = re.compile(r"\\b(price|cost|worth|valuation|quote)\\b")\n',
        "regex_noncapturing": 'import re\nTERMS = re.compile(r"\\b(?:price|cost|worth|valuation|quote)\\b")\n',
    }
    measured: dict[str, frozenset[str]] = {}
    for name, source in written.items():
        path = tmp_path / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        entries = scan_source_file(path)
        assert entries, f"{name} produced no inventory entry at all"
        measured[name] = frozenset(term for entry in entries for term in entry.terms)

    expected = frozenset(terms)
    for name, found in measured.items():
        assert found == expected, f"{name} measured {sorted(found)}, expected {sorted(expected)}"

    counts = {name: len(found) for name, found in measured.items()}
    assert len(set(counts.values())) == 1, f"syntax changed the count: {counts}"


def test_packing_a_table_into_a_regex_does_not_hide_its_terms() -> None:
    # The specific game: ten words in a tuple are obviously ten words; the same ten inside one
    # `re.compile` literal look like one string.
    packed = terms_from_pattern(r"\b(gold|silver|brent|wti|copper|platinum|palladium|nickel|zinc|tin)\b")
    assert len(packed) == 10


def test_regex_structure_is_not_mistaken_for_vocabulary() -> None:
    # `[a-z]{2,}` and `\d+` are structure. Counting them as terms would inflate the number, which is
    # the opposite failure and just as wrong.
    assert terms_from_pattern(r"^[a-z]{2,}\d+$") == ()
    assert terms_from_pattern(r"(\d{4})-(\d{2})") == ()


def test_a_pattern_that_cannot_be_decomposed_is_still_counted_once(tmp_path) -> None:
    # Nothing may vanish by being hard to parse. That would make the number improve exactly when the
    # code got more opaque.
    path = tmp_path / "opaque.py"
    path.write_text('import re\nGATE = re.compile(r"^(?=.*[0-9])(?=.*[a-z]).{8,}$")\n', encoding="utf-8")
    entries = scan_source_file(path)
    assert entries, "an undecomposable pattern must still produce an entry"
    assert entries[0].terms == ("<pattern:GATE>",)


def test_normalization_collapses_spelling_variants_of_one_term() -> None:
    for variant in ("Price", "  price  ", "PRICE", r"\bprice\b"):
        assert normalize_term(variant) == "price"


# --------------------------------------------------------------------------------------
# Proof 5 -- tamper: adding vocabulary cannot buy score
# --------------------------------------------------------------------------------------


def test_adding_a_keyword_never_raises_the_generalization_score() -> None:
    cases = _synthetic_cases()
    empty = _synthetic_inventory()
    before = score_corpus(cases, inventory=empty, execute=_routes_a_correctly)
    assert before.covered_classes == 0, "nothing may be covered before the tamper, or it proves nothing"

    tampered = empty.with_extra_terms("_GAMED", (_SYNTHETIC["correct_one"],))
    after = score_corpus(cases, inventory=tampered, execute=_routes_a_correctly)

    assert after.covered_classes == 1, "the tamper must actually land, or the comparison is vacuous"
    assert after.score < before.score, (
        f"adding a keyword must SPEND generalization, not earn it: {before.score} -> {after.score}"
    )
    assert after.total_classes == before.total_classes, "the denominator must not move"


def test_covering_the_class_the_runtime_gets_wrong_cannot_raise_the_score() -> None:
    """The subtler game, and the one the denominator exists to defeat.

    Cover the class the runtime routes WRONG, so it leaves the held-out pool and stops dragging the
    average down. With every class in the denominator this buys nothing. With a held-out denominator
    it would take the number from 2/3 to 2/2 -- which is why `score` divides by `total_classes` and
    `held_out_accuracy` is documented as unsafe to target.
    """
    cases = _synthetic_cases()
    empty = _synthetic_inventory()
    before = score_corpus(cases, inventory=empty, execute=_routes_a_correctly)
    assert (before.generalized_classes, before.total_classes, before.held_out_classes) == (2, 3, 3)

    # Cover exactly the failing class.
    tampered = empty.with_extra_terms("_GAMED", (_SYNTHETIC["failing"],))
    after = score_corpus(cases, inventory=tampered, execute=_routes_a_correctly)
    assert (after.generalized_classes, after.total_classes, after.held_out_classes) == (2, 3, 2)

    assert after.score <= before.score, (
        f"covering a failing class raised the score: {before.score} -> {after.score}"
    )
    # And the number that WOULD have moved, named so nobody mistakes it for the score.
    assert after.held_out_accuracy > before.held_out_accuracy


def test_the_score_is_monotone_over_a_sequence_of_keyword_additions() -> None:
    cases = _synthetic_cases()
    growing = _synthetic_inventory()
    scores = [score_corpus(cases, inventory=growing, execute=_routes_a_correctly).score]
    for index, word in enumerate(_SYNTHETIC.values()):
        growing = growing.with_extra_terms(f"_GAMED_{index}", (word,))
        scores.append(score_corpus(cases, inventory=growing, execute=_routes_a_correctly).score)
    assert scores == sorted(scores, reverse=True), f"the score must never increase: {scores}"
    assert scores[-1] < scores[0], "and the additions must actually have moved it, or this is vacuous"


def test_renaming_a_trigger_table_to_a_stopword_name_does_not_move_the_score(tmp_path, inventory) -> None:
    """The gaming vector found while building this, closed by keying function words to exact symbols.

    Coverage subtraction makes the score go UP, so anything that decides "this word is not a trigger"
    is a lever in the favourable direction. If that decision keyed off a name pattern, renaming
    `_WEATHER_MARKERS` to `_WEATHER_STOPWORDS` would raise the number with no behaviour change.
    """
    cases = _synthetic_cases()
    words = ", ".join(f'"{word}"' for word in _SYNTHETIC.values())

    trigger = tmp_path / "trigger.py"
    trigger.write_text(f"MARKERS = ({words})\n", encoding="utf-8")
    renamed = tmp_path / "renamed.py"
    renamed.write_text(f"MARKERS_STOPWORDS = ({words})\n", encoding="utf-8")

    as_trigger = AuthorityInventory(entries=tuple(scan_source_file(trigger)))
    as_stopwords = AuthorityInventory(entries=tuple(scan_source_file(renamed)))

    # The rename must not change what counts as covered vocabulary...
    assert as_trigger.coverage_terms == as_stopwords.coverage_terms, (
        "renaming a trigger table to a stopword name changed the coverage set"
    )
    left = score_corpus(cases, inventory=as_trigger, execute=_routes_a_correctly)
    right = score_corpus(cases, inventory=as_stopwords, execute=_routes_a_correctly)
    # ...and the tables must genuinely be covering something, or equality is trivially true.
    assert left.covered_classes == 3, "the probe table must cover every class, or this proves nothing"
    assert left.score == right.score, (
        f"renaming a table to a stopword name moved the score from {left.score} to {right.score}"
    )


# --------------------------------------------------------------------------------------
# Proof 7 -- runtime and plugin vocabulary cannot evade measurement
# --------------------------------------------------------------------------------------


def test_runtime_registered_vocabulary_appears_in_the_inventory(inventory) -> None:
    runtime_terms = inventory.terms_by_kind(KIND_RUNTIME_REGISTRATION)
    assert runtime_terms, "no runtime registration reached the inventory at all"
    registration_entries = [e for e in inventory.entries if e.kind == KIND_RUNTIME_REGISTRATION]
    assert len(registration_entries) >= 50, (
        f"only {len(registration_entries)} registered contracts were measured; the built-in registry "
        "alone declares far more, so something is being skipped"
    )


def test_a_capability_registered_at_runtime_is_measured_like_a_builtin(inventory) -> None:
    from core import tool_registry
    from core.runtime_tool_contracts import RuntimeToolContract
    from core.semantic.lexical_authority import runtime_registration_entries

    novel = RuntimeToolContract(
        intent="probe.telemetry_snapshot",
        description="capture a zzqxlt telemetry snapshot for the operator",
        tool_surface="probe",
        capability_id="probe.telemetry",
        capability_claim="captures a telemetry snapshot",
        supported=True,
        unsupported_reason="",
        input_schema={"window": "string optional"},
        output_schema={"snapshot": "object"},
        side_effect_class="read_only",
        approval_requirement="none",
        timeout_policy="30s",
        retry_policy="none",
        artifact_emission="none",
        error_contract="structured",
        source="plugin:probe",
    )
    before = {term for entry in runtime_registration_entries() for term in entry.terms}
    assert "zzqxlt" not in before, "the probe word must not already be present, or this proves nothing"

    tool_registry.register(novel)
    try:
        after_entries = runtime_registration_entries()
        after = {term for entry in after_entries for term in entry.terms}
        assert "zzqxlt" in after, (
            "vocabulary arriving from a runtime registration escaped measurement -- moving words "
            "into a manifest would then read as a reduction in lexical dependence"
        )
        provenances = {e.provenance for e in after_entries if "zzqxlt" in e.terms}
        assert provenances == {"runtime:plugin:probe"}, (
            f"the new vocabulary must be attributed to its source, got {provenances}"
        )
    finally:
        tool_registry.unregister("probe.telemetry_snapshot")

    assert "zzqxlt" not in {term for entry in runtime_registration_entries() for term in entry.terms}


def test_module_level_bare_string_authority_is_measured(tmp_path) -> None:
    from core.semantic.lexical_authority import scan_source_file

    path = tmp_path / "bare.py"
    path.write_text('MARKER = "stormterm"\n', encoding="utf-8")
    entries = scan_source_file(path)
    assert len(entries) == 1
    assert entries[0].symbol == "MARKER"
    assert entries[0].terms == ("stormterm",)


def test_plugin_discovery_uses_the_real_loader_contract_and_measures_triggers(
    tmp_path, monkeypatch
) -> None:
    import json

    from core import plugin_catalog
    from core.semantic.lexical_authority import build_inventory

    plugin = tmp_path / "plugins" / "probe-plugin"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "probe-skill").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "probe-plugin"}), encoding="utf-8"
    )
    (plugin / "skills" / "probe-skill" / "SKILL.md").write_text(
        "---\nname: probe-skill\ndescription: Routes stormterm requests\n"
        "triggers: [stormterm, rainterm]\n---\nDo the work.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_catalog, "plugins_root", lambda: tmp_path)
    inventory = build_inventory(
        tmp_path, discovery="declared", files=(), include_runtime=False, include_plugins=True
    )
    assert inventory.unavailable_sources == ()
    assert {"stormterm", "rainterm"} <= inventory.terms


def test_plugin_load_and_manifest_failures_make_the_inventory_unmeasured(
    tmp_path, monkeypatch
) -> None:
    from core import plugin_catalog, plugin_skills
    from core.semantic.generalization import CorpusCase, score_corpus
    from core.semantic.lexical_authority import build_inventory

    plugin = tmp_path / "plugins" / "broken-plugin"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "broken-skill").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text("{broken", encoding="utf-8")
    (plugin / "skills" / "broken-skill" / "SKILL.md").write_text(
        "---\nname: broken\ntriggers: [stormterm\n---\nbody\n", encoding="utf-8"
    )
    monkeypatch.setattr(plugin_catalog, "plugins_root", lambda: tmp_path)

    def _explode(_root, *, plugin_id=""):
        raise RuntimeError(f"cannot load {plugin_id}")

    monkeypatch.setattr(plugin_skills, "load_skills", _explode)
    inventory = build_inventory(
        tmp_path, discovery="declared", files=(), include_runtime=False, include_plugins=True
    )
    assert inventory.is_complete is False
    assert any("manifest" in item for item in inventory.unavailable_sources)
    assert any("load" in item for item in inventory.unavailable_sources)
    report = score_corpus(
        [CorpusCase("x", "x", "stormterm", "route", "plain")],
        inventory=inventory,
        execute=lambda _text: "route",
    )
    assert report.score is None and report.score_is_publishable is False


def test_runtime_discovery_failure_is_unmeasured_not_zero(tmp_path, monkeypatch) -> None:
    from core import tool_registry
    from core.semantic.lexical_authority import build_inventory

    monkeypatch.setattr(
        tool_registry, "registered_tools", lambda: (_ for _ in ()).throw(RuntimeError("down"))
    )
    inventory = build_inventory(
        tmp_path, discovery="declared", files=(), include_runtime=True, include_plugins=False
    )
    assert inventory.entries == ()
    assert inventory.is_complete is False
    assert inventory.unavailable_sources == ("runtime:registered_tools:RuntimeError",)


# --------------------------------------------------------------------------------------
# The measurement itself
# --------------------------------------------------------------------------------------


def test_a_case_that_raises_fails_its_class_rather_than_being_dropped(inventory) -> None:
    # If a crashing case were skipped, the score would improve every time the runtime broke.
    def _explodes(text: str) -> str:
        if "boat" in text:
            raise RuntimeError("router on fire")
        return "live_weather"

    report = score_corpus(_cases(), inventory=inventory, execute=_explodes)
    assert report.executed_cases == len(_cases()), "no case may vanish from the denominator"
    assert report.errors, "the failure must be recorded, not swallowed"
    boat = next(result for result in report.classes if result.equivalence_class == "boat")
    assert boat.is_routed_correctly is False


def test_one_failing_paraphrase_fails_its_whole_class(inventory) -> None:
    # A class is several ways of saying one thing. Getting one phrasing right and its paraphrase
    # wrong is precisely the failure being measured, so it cannot count as a pass.
    def _only_terse(text: str) -> str:
        return "live_weather" if text == "temp outside" else "conversation"

    report = score_corpus(_cases(), inventory=inventory, execute=_only_terse)
    weather_now = next(r for r in report.classes if r.equivalence_class == "weather_now")
    assert weather_now.is_routed_correctly is False


def test_the_report_records_which_style_of_rephrasing_was_lost(inventory) -> None:
    def _plain_only(text: str) -> str:
        return "live_weather" if text == "how warm is it outside right now" else "conversation"

    report = score_corpus(_cases(), inventory=inventory, execute=_plain_only)
    by_style = report.by_style()
    assert by_style["plain"]["correct"] == 1
    assert by_style["indirect"]["correct"] == 0, "indirect phrasings were lost and must be reported so"


# --------------------------------------------------------------------------------------
# The gaming vectors a hostile review proved. Each one is a way to make the number better
# without changing one line of routing behaviour.
# --------------------------------------------------------------------------------------


def test_splitting_a_table_across_a_concatenation_does_not_hide_it(tmp_path) -> None:
    """`("a","b") + ("c","d")` is not a Tuple node, so the scanner used to see nothing at all."""
    whole = tmp_path / "whole.py"
    whole.write_text('MARKERS = ("alpha", "beta", "gamma", "delta")\n', encoding="utf-8")
    split = tmp_path / "split.py"
    split.write_text('MARKERS = ("alpha", "beta") + ("gamma", "delta")\n', encoding="utf-8")
    three_way = tmp_path / "three.py"
    three_way.write_text('MARKERS = ("alpha",) + ("beta", "gamma") + ("delta",)\n', encoding="utf-8")

    measured = {
        name: frozenset(t for e in scan_source_file(path) for t in e.terms)
        for name, path in (("whole", whole), ("split", split), ("three", three_way))
    }
    assert measured["whole"] == {"alpha", "beta", "gamma", "delta"}
    for name, terms in measured.items():
        assert terms == measured["whole"], f"{name} measured {sorted(terms)}"


def test_a_table_assembled_from_names_is_recorded_rather_than_vanishing(tmp_path) -> None:
    path = tmp_path / "assembled.py"
    path.write_text("MARKERS = _BASE + _EXTRA\n", encoding="utf-8")
    entries = scan_source_file(path)
    assert entries, "a table being assembled must leave a trace even with no literal of its own"
    assert entries[0].kind == "opaque"


def test_moving_authority_into_an_unlisted_helper_does_not_hide_it(tmp_path) -> None:
    """The curated `ROUTING_AUTHORITY_FILES` list was the whole scan, so a table moved into a module
    nobody had listed disappeared from the measurement. Wide discovery is the repair."""
    from core.semantic.lexical_authority import WIDE_DISCOVERY_ROOTS, build_inventory

    root = tmp_path / "repo"
    (root / "core" / "agent_runtime").mkdir(parents=True)
    (root / "core" / "agent_runtime" / "unlisted_helper.py").write_text(
        'HIDDEN_MARKERS = ("zzhidden", "qqhidden")\n', encoding="utf-8"
    )
    assert "core" in WIDE_DISCOVERY_ROOTS

    declared = build_inventory(root, discovery="declared", include_runtime=False, include_plugins=False)
    wide = build_inventory(root, discovery="wide", include_runtime=False, include_plugins=False)
    # The DEFAULT must be the wide sweep. Narrowing the default is the same defect as never having
    # widened it, and a test that always passes `discovery=` explicitly would not notice.
    default = build_inventory(root, include_runtime=False, include_plugins=False)
    assert "zzhidden" in default.terms, "wide discovery must be the default, not an opt-in"

    assert "zzhidden" not in declared.terms, "the curated list genuinely does not reach it"
    assert "zzhidden" in wide.terms, "wide discovery must find vocabulary in an unlisted module"
    assert "zzhidden" in wide.coverage_terms


def test_runtime_and_plugin_vocabulary_is_covered_not_merely_counted() -> None:
    """It used to be counted in the totals and excluded from COVERAGE, so moving words into a
    manifest was a free improvement to the score."""
    from core.semantic.lexical_authority import KIND_RUNTIME_REGISTRATION, AuthorityEntry

    inventory = AuthorityInventory(
        entries=(
            AuthorityEntry("runtime:plugin:probe", "probe.x", KIND_RUNTIME_REGISTRATION, ("zzqxlt",)),
        )
    )
    assert "zzqxlt" in inventory.terms
    assert "zzqxlt" in inventory.coverage_terms, "counted but not covered is the asymmetry to close"
    assert inventory.covers("please zzqxlt the widget") == ("zzqxlt",)


def test_there_is_no_exclusion_list_a_developer_could_absorb_triggers_into() -> None:
    """`_FUNCTION_WORD_SYMBOLS` was a lever: subtracting terms shrinks coverage, which raises the
    score, so real trigger vocabulary could be absorbed through it."""
    import core.semantic.lexical_authority as module

    assert not hasattr(module, "_FUNCTION_WORD_SYMBOLS")
    assert not hasattr(AuthorityInventory, "function_words")

    from core.semantic.lexical_authority import KIND_FILLER, AuthorityEntry

    # Even a table labelled filler contributes to coverage now.
    inventory = AuthorityInventory(
        entries=(AuthorityEntry("source:x", "_STOPWORDS", KIND_FILLER, ("vroomble",)),)
    )
    assert "vroomble" in inventory.coverage_terms


def test_the_inventory_reports_what_it_could_not_measure(tmp_path) -> None:
    """Silence about unmeasured authority reads as absence of authority."""
    from core.semantic.lexical_authority import build_inventory

    root = tmp_path / "repo"
    (root / "core").mkdir(parents=True)
    (root / "core" / "broken.py").write_text("def f(:\n", encoding="utf-8")
    inventory = build_inventory(root, include_runtime=False, include_plugins=False)
    report = inventory.to_dict()
    assert "core/broken.py" in report["unmeasured"]["unreadable_files"]
    assert report["unmeasured"]["note"], "the bound on discovery must be stated, not implied"


def test_computed_vocabulary_is_opaque_rather_than_guessed(tmp_path) -> None:
    """The end of AST whack-a-mole.

    A hostile review hid vocabulary three ways -- `tuple("a b".split())`, `dict.fromkeys([...])`,
    and a regex packed with `"|".join([...])`. The first was worse than missing: the scanner walked
    into it and recorded ONE term, `"stormterm rainterm"`, which matches nothing. Anything computed
    is now opaque by construction rather than guessed at.
    """
    path = tmp_path / "hidden.py"
    path.write_text(
        'import re\n'
        'A = tuple("stormterm rainterm".split())\n'
        'B = dict.fromkeys(["snowterm", "hailterm"])\n'
        'C = re.compile("|".join(["fogterm", "mistterm"]))\n',
        encoding="utf-8",
    )
    entries = {entry.symbol: entry for entry in scan_source_file(path)}
    assert set(entries) == {"A", "B", "C"}, "nothing may vanish entirely"
    for symbol, entry in entries.items():
        assert entry.kind == "opaque", f"{symbol} was guessed at instead of admitted unreadable"
        assert entry.terms == (f"<computed:{symbol}>",)

    inventory = AuthorityInventory(entries=tuple(entries.values()))
    assert inventory.is_complete is False
    assert inventory.covers("stormterm here") == (), "an opaque table cannot claim to cover anything"


def test_making_authority_opaque_cannot_improve_the_metric() -> None:
    """The invariant, stated as arithmetic.

    Coverage is what stops a class counting as generalized, so removing coverage RAISES the score --
    which made every hiding trick pay. The repair is not another scanner: an incomplete inventory
    withholds the scalar entirely, so hiding vocabulary produces no number rather than a better one.
    """
    from core.semantic.lexical_authority import KIND_LITERAL_COLLECTION, KIND_OPAQUE, AuthorityEntry

    cases = [
        CorpusCase("a", "k", "please vroomble it", "route_a", "plain"),
        CorpusCase("b", "k", "vroomble now", "route_a", "terse"),
    ]
    readable = AuthorityInventory(
        entries=(AuthorityEntry("s", "_T", KIND_LITERAL_COLLECTION, ("vroomble",)),)
    )
    hidden = AuthorityInventory(entries=(AuthorityEntry("s", "_T", KIND_OPAQUE, ("<computed:_T>",)),))

    before = score_corpus(cases, inventory=readable, execute=lambda _t: "route_a")
    after = score_corpus(cases, inventory=hidden, execute=lambda _t: "route_a")

    assert before.score_is_publishable is True
    assert after.score_is_publishable is False
    assert after.score is None, "an incomplete inventory must produce no scalar at all"
    assert after.score_lower_bound <= (before.score or 0.0)
    assert after.unmeasured_detail, "and it must say what went unmeasured"


# --------------------------------------------------------------------------------------------
# The real production source, not a synthetic fixture. A hostile reviewer found the synthetic
# cases all passing while an actual routing table was measured as the fragment ('es',).
# --------------------------------------------------------------------------------------------

_ABOUT_RE_SOURCE = "core/agent_runtime/fast_paths_skill.py"


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def test_the_real_about_re_is_opaque_not_a_fake_complete_vocabulary() -> None:
    """`_ABOUT_RE` in production is `re.compile(<literal + NAME + literal>)`.

    The scanner used to take the regex special case, harvest the literal fragments of a pattern it
    could not fully read, and publish `('es',)` -- two characters, matching nothing the real regex
    matches -- while leaving the entry NOT opaque so the inventory still called itself complete for
    it. The real triggers (`what is a`, `what are`, `should i`, `explain`, `tell me about`, ...)
    were invisible and nothing said so.

    Measured against the shipped file, so a rewrite of that regex re-runs this.
    """
    from core.semantic.lexical_authority import KIND_OPAQUE, scan_source_file

    path = _repo_root() / _ABOUT_RE_SOURCE
    source = path.read_text(encoding="utf-8")
    assert "_ABOUT_RE = re.compile(" in source, (
        f"{_ABOUT_RE_SOURCE} no longer defines _ABOUT_RE the way this regression is written for"
    )

    entries = {entry.symbol: entry for entry in scan_source_file(path)}
    entry = entries.get("_ABOUT_RE")
    assert entry is not None, "a real routing table may never vanish from the inventory"
    assert entry.kind == KIND_OPAQUE, (
        f"_ABOUT_RE is assembled from a literal and a NAME, so its vocabulary cannot be read; "
        f"the scanner reported kind={entry.kind} terms={entry.terms}"
    )
    assert entry.terms == ("<computed:_ABOUT_RE>",)
    # The specific lie that shipped: a short fragment presented as the table's vocabulary.
    assert "es" not in entry.terms, "a pattern fragment is not vocabulary"
    for term in entry.terms:
        assert term.startswith("<"), f"{term!r} reads as a real trigger word and is not one"


def test_the_real_inventory_reports_itself_incomplete_and_says_how_much() -> None:
    """The tree-wide reading, taken from the shipped source rather than a fixture."""
    from core.semantic.lexical_authority import build_inventory

    inventory = build_inventory(_repo_root())
    assert inventory.is_complete is False, (
        "this runtime contains vocabulary that cannot be statically read; an inventory that calls "
        "itself complete is claiming otherwise"
    )
    assert len(inventory.opaque_entries) >= 400, (
        f"only {len(inventory.opaque_entries)} opaque entries -- a sudden drop means the scanner "
        "started guessing at computed vocabulary again rather than admitting it cannot read it"
    )
    readable = {t for e in inventory.entries for t in e.terms if not t.startswith("<")}
    assert len(readable) > 5000, "the readable half of the measurement disappeared"


def test_every_computed_shape_is_opaque_and_never_vanishes(tmp_path) -> None:
    """The full attack list, each shape driven through the real scanner.

    Two outcomes are acceptable per shape: recovered completely, or recorded opaque. Vanishing is
    not, because a table that is absent leaves `is_complete` True while its words route turns.
    """
    from core.semantic.lexical_authority import KIND_OPAQUE, AuthorityInventory, scan_source_file

    shapes = {
        "RE_BINOP": 're.compile(r"(?:alpha" + SUFFIX + r"|beta)")',
        "RE_NAME": "re.compile(PATTERN_TEXT)",
        "RE_JOIN": 're.compile("|".join(["gamma", "delta"]))',
        "LIST_COMP": '[w for w in ("epsilon", "zeta")]',
        "SET_COMP": '{w for w in ("eta", "theta")}',
        "DICT_COMP": '{w: 1 for w in ("iota", "kappa")}',
        "GEN_EXP": 'tuple(w for w in ("lambdaa", "mu"))',
        "FROMKEYS": 'dict.fromkeys(["nu", "xi"])',
        "TUPLE_SPLIT": 'tuple("omicron pi".split())',
        "HELPER": "_make_terms()",
        "CONCAT": '("rho" + "sigma",)',
        "FSTRING": 'f"tau{SUFFIX}upsilon"',
    }
    path = tmp_path / "hostile_vocab.py"
    path.write_text(
        "import re\nSUFFIX = 'x'\nPATTERN_TEXT = 'phi|chi'\n"
        "def _make_terms():\n    return ('psi', 'omega')\n"
        + "\n".join(f"{name} = {expr}" for name, expr in shapes.items()),
        encoding="utf-8",
    )

    entries = {entry.symbol: entry for entry in scan_source_file(path)}

    # The property is about VOCABULARY, not symbol names. `HELPER = _make_terms()` has no entry of
    # its own, and that is correct: the words are recorded once at their source, under the helper.
    # What may never happen is words being absent AND nothing marked unreadable.
    readable = {term for entry in entries.values() for term in entry.terms if not term.startswith("<")}
    assert {"psi", "omega"} <= readable, (
        "a helper's returned vocabulary must be measured at its source, not dropped"
    )

    aliased_to_a_measured_source = {"HELPER"}
    vanished = [name for name in shapes if name not in entries and name not in aliased_to_a_measured_source]
    assert not vanished, f"these authority sources vanished instead of being recorded: {vanished}"

    guessed = {
        name: entries[name].terms
        for name in shapes
        if name in entries and entries[name].kind != KIND_OPAQUE
    }
    assert not guessed, f"these computed sources were guessed at rather than admitted unreadable: {guessed}"

    inventory = AuthorityInventory(entries=tuple(entries.values()))
    assert inventory.is_complete is False
    assert inventory.covers("alpha beta gamma epsilon rho sigma") == (), (
        "an unreadable table cannot claim to cover anything"
    )


def test_a_regex_whose_only_unreadable_part_is_a_name_is_opaque(tmp_path) -> None:
    """Isolates the unresolved-NAME check.

    The mutation matrix caught this: `_ABOUT_RE` has BOTH a bare name and a string concatenation, so
    either guard alone keeps it opaque and neither is proven load-bearing by it. Here the pattern is
    a single Name -- nothing is concatenated -- so only the name check can reach it.
    """
    from core.semantic.lexical_authority import KIND_OPAQUE, scan_source_file

    path = tmp_path / "name_only.py"
    path.write_text("import re\nPATTERN = 'alphaterm|betaterm'\nONLY_A_NAME = re.compile(PATTERN)\n",
                    encoding="utf-8")
    entries = {entry.symbol: entry for entry in scan_source_file(path)}
    entry = entries.get("ONLY_A_NAME")
    assert entry is not None, "a table whose pattern is a name must not vanish from the inventory"
    assert entry.kind == KIND_OPAQUE, f"recorded as {entry.kind} with terms {entry.terms}"

    # A collection carrying a bare name is the same problem one layer out.
    path2 = tmp_path / "name_in_tuple.py"
    path2.write_text("OTHER = ('gammaterm',)\nMIXED = ('deltaterm', OTHER)\n", encoding="utf-8")
    mixed = {entry.symbol: entry for entry in scan_source_file(path2)}["MIXED"]
    assert mixed.kind == KIND_OPAQUE, (
        f"a collection holding an unresolved name cannot be read completely; got {mixed.terms}"
    )


def test_a_bare_string_concatenation_is_opaque(tmp_path) -> None:
    """Isolates the assembled-string check on a BinOp that is the whole value.

    `TERMS = "storm" + "term"` is one term, `stormterm`. Reporting `('storm', 'term')` invents two
    triggers that match nothing and loses the one that matches. Inside a tuple this is caught by the
    decomposability guard; as a bare value it reaches the BinOp branch, which needs its own check.
    """
    from core.semantic.lexical_authority import KIND_OPAQUE, scan_source_file

    path = tmp_path / "bare_concat.py"
    path.write_text('SUFFIX = "term"\nJOINED = "storm" + "term"\nWITH_NAME = "rain" + SUFFIX\n',
                    encoding="utf-8")
    entries = {entry.symbol: entry for entry in scan_source_file(path)}

    joined = entries.get("JOINED")
    assert joined is not None, "an assembled string table must not vanish"
    assert joined.kind == KIND_OPAQUE, f"recorded as {joined.kind} with terms {joined.terms}"
    assert "storm" not in joined.terms and "term" not in joined.terms, (
        f"fragments of an assembled string are not terms: {joined.terms}"
    )

    with_name = entries.get("WITH_NAME")
    assert with_name is not None and with_name.kind == KIND_OPAQUE


def test_vocabulary_hidden_in_dict_values_is_still_measured(tmp_path) -> None:
    """A mapping's values are vocabulary as often as its keys are.

    Reading only the keys meant a table could be hidden by moving it across the colon: the words
    left coverage and the entry stayed non-opaque, so the inventory did not even call itself
    incomplete for it.
    """
    from core.semantic.lexical_authority import AuthorityInventory, scan_source_file

    path = tmp_path / "values.py"
    path.write_text('ROUTES = {"k1": "alphaterm", "k2": "betaterm"}\n', encoding="utf-8")
    entry = next(iter(scan_source_file(path)))
    assert "alphaterm" in entry.terms and "betaterm" in entry.terms, entry.terms
    assert AuthorityInventory(entries=(entry,)).covers("alphaterm here") == ("alphaterm",)
