"""One transposed pair of letters must not make a tool unreachable.

`what is my machine scpecs?` failed twice over: no family claimed it, AND `near_miss` was False, so
the intent arbiter was never consulted either. The turn went to a model that has no way to read the
host, and the operator got prose instead of their machine's specification.

The typo corrector was already there — `difflib.get_close_matches` at cutoff 0.84 — and had nothing
to match against. `_DOMAIN_VOCAB` held mesh and hive words (`shard`, `consensus`, `replica`,
`liquefy`) and not one word naming a tool the runtime owns. Measured on 13 realistic misspellings,
12 were left uncorrected.

Two things came out of measuring rather than reading:

* the 0.84 cutoff sits just above a transposition — `serach`/`search` scores 0.833 and
  `imgae`/`image` scores 0.800, while `scpecs`/`specs` at 0.909 was already fixed. An exact
  adjacent-letter swap onto a vocabulary word is a far stronger signal than a similarity score, so
  it is checked separately instead of lowering the cutoff for the whole language;
* the widened vocabulary collided with four common English words, and the ORIGINAL mesh vocabulary
  was already colliding with six more. `whether we should build` became `weather we should build`
  and `it's hard` became `it's shard`, live, before any of this.
"""
from __future__ import annotations

import pytest

from core.input_normalizer import _DOMAIN_VOCAB, _fuzzy_domain_match, normalize_user_text

# The failure the plan named, plus the neighbours that share its shape.
TOOL_TYPOS = (
    ("what is my machine scpecs?", "specs"),
    ("machine scpecs", "specs"),
    ("show me my mahcine specs", "machine"),
    ("chekc the workspce", "workspace"),
    ("serach the web for sol price", "search"),
    ("creat a file notes.txt", "create"),
    ("show me the calender", "calendar"),
    ("generate an imgae of a cat", "image"),
    ("what skils do you have", "skills"),
    ("run a sandbxo command", "sandbox"),
    ("show me the foldr contents", "folder"),
    ("delet the temp files", "delete"),
    ("list the direcotry", "directory"),
    ("open the treminal", "terminal"),
    ("check the netowrk", "network"),
)

# Every one of these is one edit from a tool word. Rewriting any of them corrupts what the operator
# said, which is strictly worse than missing a typo.
MUST_NOT_BE_REWRITTEN = (
    # introduced by the tool vocabulary
    "ready", "research", "sell", "whether", "imagine", "machines", "device",
    "already", "researcher", "selling", "imagines", "devices",
    # pre-existing, from the original mesh/hive vocabulary
    "hard", "identify", "person", "serve", "strange", "warm",
    "harder", "identifies", "persons", "served", "strangely", "warmth",
    # measured served 2026-09-14: "my personal inbox" reached the model as "my persona inbox"
    "personal", "personally",
    # the protections that were already there
    "project", "product", "protect", "connect", "correct", "collect", "select", "please",
)


def test_every_protected_word_is_a_word_that_would_otherwise_be_rewritten() -> None:
    """A protect list nobody can reach protects nothing.

    A first draft padded in every inflection of every collision by hand. A sabotage removing four
    of them broke nothing — `_is_inflection_of` already covered them — so they were dead weight
    dressed as safety. Each remaining entry is asserted to be doing real work.
    """

    import difflib

    from core.input_normalizer import _FUZZY_PROTECT, _is_inflection_of, _transposed_domain_match

    # The original entries predate this work and are left alone; only the ones added here are held
    # to the standard, because only those were chosen with the matcher in hand.
    added_here = {"research", "sell", "hard", "identify", "person", "persons", "serve",
                  "strange", "warm", "personal"}
    assert added_here <= _FUZZY_PROTECT

    for word in sorted(added_here):
        if _transposed_domain_match(word):
            continue
        matches = difflib.get_close_matches(word, _DOMAIN_VOCAB, n=1, cutoff=0.84)
        assert matches and matches[0] != word, f"{word!r} needs no protection"
        assert not _is_inflection_of(word, matches[0]), (
            f"{word!r} is already covered by the morphology guard"
        )


@pytest.mark.parametrize("sentence,expected", TOOL_TYPOS)
def test_a_tool_typo_is_corrected(sentence: str, expected: str) -> None:
    corrected = normalize_user_text(sentence).normalized_text
    assert expected in corrected.lower(), f"{sentence!r} -> {corrected!r}"


@pytest.mark.parametrize("word", MUST_NOT_BE_REWRITTEN)
def test_an_ordinary_english_word_is_never_rewritten(word: str) -> None:
    match = _fuzzy_domain_match(word)
    assert match is None, f"{word!r} was rewritten to {match!r}"


def test_the_two_sentences_that_were_being_corrupted_live() -> None:
    """Not hypothetical. Both of these were measurably being rewritten before this change."""

    assert normalize_user_text("whether we should build an api").normalized_text.startswith(
        "whether"
    )
    assert "shard" not in normalize_user_text("this is hard to read").normalized_text


def test_the_vocabulary_actually_names_the_runtime_tools() -> None:
    """The list it replaced held `shard`, `consensus` and `liquefy`, and no tool name at all."""

    for word in ("machine", "specs", "workspace", "folder", "file", "search", "calendar",
                 "image", "skills", "plugin", "sandbox", "terminal", "network"):
        assert word in _DOMAIN_VOCAB, word

    # The ORDINARY nouns a user actually types the request with. The tool NAMES above are what the
    # runtime calls things; "how much disk SPACE do i have left" is what a person types, and one
    # slip in that word used to drop the whole turn out of the fast router.
    for word in ("space", "screen", "resolution", "version", "downloads", "battery",
                 "drives", "cores", "uptime", "settings", "running"):
        assert word in _DOMAIN_VOCAB, word


def test_a_word_that_names_no_tool_is_not_in_the_vocabulary() -> None:
    """`browser` was, and it cost a Hive task.

    Correcting `brwoser` -> `browser` is right. But no runtime tool is called that — the web family
    is `web.fetch` / `web.search` / `web.research` — and the corrected word then hijacked
    "create hive mind task: stand alone vool brwoser version" to the live-info route, so the
    operator was told live web lookup was disabled instead of getting their Hive task. A vocabulary
    entry that names no tool buys no reachability and can only cost routing.
    """

    # `battery` was in this list and has been moved OUT of it, because the rule is "names no tool"
    # and battery names one. `machine.host_state` reports battery percent and power source, and
    # `_BATTERY_RE` in core/execution/constants.py is the detector that routes to it -- so
    # correcting `batery` -> `battery` buys real reachability, which is exactly what the rule asks
    # for. It was grouped with `browser` and `weather` by association, not by the rule.
    # Its presence is asserted positively in test_the_vocabulary_actually_names_the_runtime_tools.
    for word in ("browser", "weather", "price", "docker", "processor",
                 "notification", "clipboard", "email", "picture"):
        assert word not in _DOMAIN_VOCAB, word

    assert (
        normalize_user_text(
            "create hive mind task: Task: stand alone vool brwoser version."
        ).normalized_text
        == "create hive mind task: Task: stand alone vool brwoser version."
    )


class TestTransposition:
    """The most common typing error, and the one the similarity cutoff was just too strict for."""

    @pytest.mark.parametrize(
        "typo,expected",
        [("serach", "search"), ("imgae", "image"), ("fiel", "file"), ("sepcs", "specs"),
         ("direcotry", "directory"), ("treminal", "terminal"), ("netowrk", "network")],
    )
    def test_an_adjacent_swap_onto_a_tool_word_is_corrected(self, typo, expected) -> None:
        from core.input_normalizer import _transposed_domain_match

        assert _transposed_domain_match(typo) == expected

    def test_a_word_that_does_not_swap_onto_a_tool_word_is_left_alone(self) -> None:
        from core.input_normalizer import _transposed_domain_match

        for word in ("form", "from", "trail", "trial", "casual", "causal", "quite", "quiet",
                     "their", "there", "thought", "through"):
            assert _transposed_domain_match(word) is None, word

    def test_no_vocabulary_word_transposes_onto_another_one(self) -> None:
        """The invariant that makes the swap check safe, asserted instead of guarded.

        An early `if token in _DOMAIN_VOCAB: return None` was written to stop a real tool word being
        "corrected" into a different one. A sabotage removing it broke nothing, and measuring showed
        why: no word in the vocabulary transposes onto any other, so the guard was unreachable. A
        guard no test can reach is not protection — it is code that looks like protection. The
        condition it assumed is now checked directly, so growing the vocabulary into that state
        fails here instead of silently rewriting one tool name as another.
        """

        collisions = [
            (word, word[:i] + word[i + 1] + word[i] + word[i + 2:])
            for word in _DOMAIN_VOCAB
            for i in range(len(word) - 1)
            if (word[:i] + word[i + 1] + word[i] + word[i + 2:]) != word
            and (word[:i] + word[i + 1] + word[i] + word[i + 2:]) in _DOMAIN_VOCAB
        ]
        assert collisions == [], f"vocabulary words one swap apart: {collisions}"

    def test_the_similarity_cutoff_was_genuinely_too_strict_for_these(self) -> None:
        """The measurement behind checking transposition separately rather than lowering 0.84."""

        import difflib

        assert difflib.SequenceMatcher(None, "serach", "search").ratio() < 0.84
        assert difflib.SequenceMatcher(None, "imgae", "image").ratio() < 0.84
        assert difflib.SequenceMatcher(None, "scpecs", "specs").ratio() > 0.84


class TestMorphologyIsNotATypo:
    """A typo corrector must not become a lemmatiser.

    `difflib` at 0.84 scores `created`/`create` at 0.923 and `router`/`route` at 0.909, so adding
    `create` and `route` to the vocabulary rewrote every past tense and agent noun of both.
    Measured: "update the one you created already" became "update the one you create already", and
    six Hive tests failed at once because the update detector no longer saw its own trigger phrase.

    Several were live before any of this — `message`, `route`, `node`, `server`, `storage` and
    `persona` were already in the vocabulary, so `messages`, `router`, `nodes` and `served` were
    already being rewritten. A protect list cannot keep up with that; it is a property of the
    matcher, so it is checked as one.
    """

    @pytest.mark.parametrize(
        "word",
        ["created", "deleted", "searched", "searches", "installed", "installer", "commits",
         "commands", "networks", "networked", "scripts", "scripted", "servers", "served",
         "processed", "processes", "downloaded", "downloader", "uploaded", "removed", "remover",
         "renamed", "images", "imaged", "messages", "messaged", "routes", "routed", "router",
         "nodes", "writes", "writer", "reads", "lists", "terminals", "workspaces", "filed",
         "filer", "edits", "edited", "finds", "paths", "builds", "audits", "branches", "diffs",
         "helpers", "clusters", "chunks", "agents", "bots", "disks"],
    )
    def test_an_inflected_form_is_left_alone(self, word: str) -> None:
        assert _fuzzy_domain_match(word) is None, f"{word!r} was lemmatised"

    def test_the_sentence_that_broke_six_hive_tests(self) -> None:
        text = "update the one you created already with following: Standalone VOOL integration."
        assert normalize_user_text(text).normalized_text == text

    def test_a_genuine_typo_that_is_not_an_inflection_is_still_corrected(self) -> None:
        """The guard must not be bought by refusing to correct anything."""

        from core.input_normalizer import _is_inflection_of

        assert _is_inflection_of("created", "create") is True
        assert _is_inflection_of("router", "route") is True
        assert _is_inflection_of("messages", "message") is True
        assert _is_inflection_of("scpecs", "specs") is False
        assert _is_inflection_of("mahcine", "machine") is False
        assert _is_inflection_of("delet", "delete") is False
        assert _is_inflection_of("workspce", "workspace") is False


# --------------------------------------------------------------------------------------
# Wired, not merely written
# --------------------------------------------------------------------------------------


def test_the_corrected_request_reaches_the_machine_tool() -> None:
    """A corrector with no effect on routing would prove a regex and nothing about the product."""

    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    for raw in ("what is my machine scpecs?", "show me my mahcine specs"):
        corrected = normalize_user_text(raw).normalized_text
        claimed = agent._maybe_handle_direct_machine_read_request(
            corrected,
            session_id="typo-reaches-tool",
            source_surface="api",
            source_context={"surface": "api"},
        )
        assert claimed is not None, f"{raw!r} -> {corrected!r} still reached no tool"


def test_the_bare_two_word_specs_request_now_reaches_the_tool() -> None:
    """Replaces the gap test that used to sit here, exactly as its own docstring instructed.

    That test asserted `machine specs` returned None and said: "the bare form now works - good;
    delete this test and the note in the capability matrix". A separate session fixed the planner
    gap it recorded, the assertion inverted, and this is its replacement rather than a deletion —
    the boundary it pinned is now a capability worth holding.
    """

    from apps.vool_agent import VoolAgent
    from core.agent_runtime.fast_paths_machine import looks_like_supported_machine_read_request

    assert looks_like_supported_machine_read_request("machine specs") is True

    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    claimed = agent._maybe_handle_direct_machine_read_request(
        "machine specs",
        session_id="bare-specs-now-works",
        source_surface="api",
        source_context={"surface": "api"},
    )
    assert claimed is not None, "the bare two-word form must reach the specs tool"



def test_the_machine_handler_is_given_the_corrected_text_not_the_raw_one() -> None:
    """The wiring bug the live drive caught, and the reason the typo fix looked done and was not.

    `normalize_user_text` corrects "what is my machine scpecs?" and the machine family claims the
    corrected string — both were unit-verified, and both were true. The front door then handed the
    handler `raw_user_input`, so the correction was computed and thrown away at the one call site
    that needed it. Measured on the installed build: the correctly spelled question answered in
    0.2s while the typo refused, in auto and pinned alike.

    Every test here passed while that was broken, because each one checked a piece. This checks the
    seam between them.
    """

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")

    call = source.index("machine_read = agent._maybe_handle_direct_machine_read_request(")
    argument = source[call:call + 200].split("(", 1)[1].strip().split(",", 1)[0].strip()
    assert argument == "effective_input", (
        f"the machine read handler is fed {argument!r}; a typo'd noun is only corrected in "
        "effective_input, so raw_user_input makes the whole correction dead"
    )


def test_the_write_and_download_siblings_still_get_the_raw_text() -> None:
    """They carry content to write and URLs to fetch, which must stay verbatim.

    Widening the previous fix to all four handlers would have been the easy move and the wrong one.
    """

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")

    for handler in (
        "machine_download = agent._maybe_handle_direct_machine_download_request(",
        "machine_write = agent._maybe_handle_direct_machine_write_request(",
        "machine_write_guard = agent._maybe_handle_safe_machine_write_guard(",
    ):
        call = source.index(handler)
        argument = source[call:call + 200].split("(", 1)[1].strip().split(",", 1)[0].strip()
        assert argument == "raw_user_input", f"{handler} now gets {argument!r}"


def test_the_verbs_went_in_only_after_the_sweep() -> None:
    """A short verb is the dangerous end of a 0.84 cutoff, so they waited for the measurement.

    `run` takes `ruin`, `rung` and `runt`; `show` takes `shown` and `showy`; `inspect` takes
    `insect`. Each victim is protected. My own estimate was wrong in BOTH directions before the
    sweep — I expected `turn` and `burn` to collide with `run` and they do not, because
    SequenceMatcher scores contiguous blocks rather than subsequences.
    """

    import difflib

    from core.input_normalizer import _FUZZY_PROTECT

    for verb in ("check", "show", "open", "run", "inspect", "review", "explain", "compare",
                 "describe", "analyze", "print", "count", "copy", "move", "start", "stop"):
        assert verb in _DOMAIN_VOCAB, verb

    for victim in ("ruin", "rung", "runt", "shown", "showy", "insect"):
        assert victim in _FUZZY_PROTECT, victim
        assert _fuzzy_domain_match(victim) is None, f"{victim!r} was rewritten"

    # The measurement itself, so a future edit cannot quietly drop a protection.
    assert difflib.SequenceMatcher(None, "rung", "run").ratio() > 0.84
    assert difflib.SequenceMatcher(None, "shown", "show").ratio() > 0.84
    assert difflib.SequenceMatcher(None, "insect", "inspect").ratio() > 0.84
    # ...and the two I wrongly expected to collide.
    assert difflib.SequenceMatcher(None, "turn", "run").ratio() < 0.84
    assert difflib.SequenceMatcher(None, "burn", "run").ratio() < 0.84


@pytest.mark.parametrize(
    "sentence,expected",
    [
        ("chekc the workspce please", "check the workspace please"),
        ("shwo me the machine scpecs", "show me the machine specs"),
        ("opne the treminal", "open the terminal"),
        ("insepct the folder", "inspect the folder"),
        ("compaer the two files", "compare the two files"),
        ("dsecribe this project", "describe this project"),
    ],
)
def test_a_tool_request_corrects_its_verb_and_its_noun(sentence: str, expected: str) -> None:
    """The live drive's own failure. "chekc the workspce please" corrected `workspce`, left
    `chekc`, and the turn gave up after 60s with "I couldn't map that cleanly to a real action".
    A tool request is a verb and a noun; correcting one of them is correcting neither.
    """

    assert normalize_user_text(sentence).normalized_text == expected
