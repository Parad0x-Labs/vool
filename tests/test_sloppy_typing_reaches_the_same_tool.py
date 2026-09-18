"""A sloppily typed question must reach the handler its clean form reaches.

A blind tester found the cliff: one or two typos were tolerated, three dropped the turn out of the
fast router into a ~60s model path that failed about 60% of the time. "how mcuh disk sapce do i
havee left" failed 3/3 while the clean form answered in 0.1s.

The cause was not the COUNT of typos. Two earlier passes had added the tool NAMES and the tool
VERBS to the vocabulary, and a user types neither -- they type the ordinary nouns the question is
made of. `space`, `screen`, `resolution`, `version`, `downloads`, `battery`, `drives`, `cores`,
`uptime` and `settings` were in no list, so one slip in the only word carrying the question was
enough. Of the three typos in that sentence only ONE mattered: `sapce`. `mcuh` and `havee` are
ordinary English and stay misspelled, because the router reads the tool phrase, not the sentence.

Every pair below is asserted BOTH WAYS against the same routing probe. A pair passes when the
sloppy form claims exactly what the clean form claims -- including when neither claims anything,
which keeps this a parity test rather than a demand that every phrasing route.
"""
from __future__ import annotations

import pathlib

import pytest

from core.agent_runtime.intent_claims import probe_claims
from core.execution.constants import machine_display_intent, machine_largest_intent
from core.input_normalizer import _DOMAIN_VOCAB, _TYPO_REWRITES, normalize_user_text

SYSTEM_WORDS = pathlib.Path("/usr/share/dict/words")

# (sloppy, clean). Three or more typos in most of them, which is the reported cliff.
PAIRS = (
    ("how mcuh disk sapce do i havee left", "how much disk space do i have left"),
    ("whats my scren resolutin", "whats my screen resolution"),
    ("how mcuh ram does this machien have", "how much ram does this machine have"),
    ("shwo me the biggest fiels on my drivee", "show me the biggest files on my drive"),
    ("wat is my machien specs", "what is my machine specs"),
    ("list teh runnign programms", "list the running programs"),
    ("hwo much fre space is left on the disc", "how much free space is left on the disk"),
    ("check my dsik usaeg", "check my disk usage"),
    ("waht verison of the app am i runnign", "what version of the app am i running"),
    ("show me my downlaods foldr", "show me my downloads folder"),
    ("whats on my dekstop", "whats on my desktop"),
    ("how many drivs do i have", "how many drives do i have"),
    ("wich apps are usign the most memroy", "which apps are using the most memory"),
    ("what cpu and how many coers", "what cpu and how many cores"),
    ("is my batery chargign", "is my battery charging"),
    ("how big is my documnts foldr", "how big is my documents folder"),
    ("waht is the uptme of this machien", "what is the uptime of this machine"),
    ("shwo the dispaly resoultion", "show the display resolution"),
    ("tell me my sytem specks", "tell me my system specs"),
    ("how much storag capcity is free", "how much storage capacity is free"),
)

# The pairs that must land on a real fast-path family, not merely land together. Without this the
# parity assertion above could be satisfied by routing nothing at all.
MUST_REACH_A_HANDLER = tuple(
    pair
    for pair in PAIRS
    if pair[0]
    not in {
        # Four phrasings where the CLEAN form reaches no family either. They are parity-tested
        # above and listed here as measured gaps in the detectors, not in the typo corrector:
        #   "what version of the app am i running"   - no version family claims it
        #   "whats on my desktop"                    - listing phrase not recognised bare
        #   "how big is my documents folder"         - a size question about a folder
        #   "how much storage capacity is free"      - "capacity" is not a disk space cue
        "waht verison of the app am i runnign",
        "whats on my dekstop",
        "how big is my documnts foldr",
        "how much storag capcity is free",
    }
)


def _claims(text: str) -> set[str]:
    """Which families read this message, after normalization -- the front door's own view."""
    normalized = normalize_user_text(text).normalized_text
    families = {claim.family for claim in probe_claims(normalized)}
    if machine_display_intent(normalized):
        families.add("display")
    if machine_largest_intent(normalized):
        families.add("largest")
    return families


@pytest.mark.parametrize(("sloppy", "clean"), PAIRS, ids=[p[0] for p in PAIRS])
def test_the_sloppy_form_routes_exactly_where_the_clean_form_routes(sloppy: str, clean: str) -> None:
    assert _claims(sloppy) == _claims(clean), (sloppy, _claims(sloppy), _claims(clean))


@pytest.mark.parametrize(
    ("sloppy", "clean"), MUST_REACH_A_HANDLER, ids=[p[0] for p in MUST_REACH_A_HANDLER]
)
def test_both_forms_actually_reach_a_handler(sloppy: str, clean: str) -> None:
    assert _claims(clean), clean
    assert _claims(sloppy), sloppy


def test_it_is_the_word_that_carries_the_question_that_matters_not_the_typo_count() -> None:
    """The finding that explains the cliff, asserted rather than asserted about.

    Of the three typos in the reported sentence only `sapce` decided the route: the disk phrase
    table reads "disk space", and nothing downstream cares how "much" or "have" are spelled. So
    the routing-critical repair is the NOUN, and it is enough on its own.
    """
    from core.agent_runtime.intent_claims import probe_claims

    only_the_noun_is_wrong = "how much disk sapce do i have left"
    corrected = normalize_user_text(only_the_noun_is_wrong).normalized_text
    assert "disk space" in corrected
    assert {c.family for c in probe_claims(corrected)} == {"disk_usage"}

    # And a misspelling of ordinary English that is in neither table is left exactly alone --
    # the corrector repairs what the router reads, it does not rewrite the sentence.
    untouched = normalize_user_text("how much disk space do i habve left").normalized_text
    assert "habve" in untouched
    assert {c.family for c in probe_claims(untouched)} == {"disk_usage"}


@pytest.mark.skipif(not SYSTEM_WORDS.exists(), reason="no system dictionary on this host")
def test_no_exact_typo_key_is_a_real_english_word() -> None:
    """_TYPO_REWRITES is exact-match, and that is only safe while every key is a NON-word.

    `doe`/`dose` -> `does`, `wat` -> `what` and `manny` -> `many` were all drafted into that table
    and taken back out because each is a real dictionary word. This is the rule that made them
    obviously wrong, checked rather than remembered.
    """
    words = {
        line.strip().lower()
        for line in SYSTEM_WORDS.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip().isalpha()
    }
    offenders = sorted(key for key in _TYPO_REWRITES if key in words)
    assert offenders == [], offenders


@pytest.mark.skipif(not SYSTEM_WORDS.exists(), reason="no system dictionary on this host")
def test_every_collision_the_new_vocabulary_creates_is_a_word_nobody_types() -> None:
    """The sweep, kept as a test: no COMMON English word may be rewritten by the vocabulary.

    The words below are the ones the 235,976-word dictionary sweep -- and the 2.5M-surface-form
    inflection sweep after it -- turned up as real collisions of the ordinary nouns. Each is in
    _FUZZY_PROTECT. If a future vocabulary entry re-captures one, this fails rather than the user
    finding it: `sing` -> `using`, `scores` -> `cores` and `conversions` -> `versions` are all
    within the 0.84 cutoff.
    """
    from core.input_normalizer import _fuzzy_domain_match

    must_survive = (
        "pace", "aversion", "diversion", "erosion", "inversion", "reversion",
        "evolution", "revolution", "solution", "buttery", "derive", "dive", "drivel",
        "driven", "documentary", "seize", "sage", "supplication", "commuter", "compute",
        "copter", "systemic", "mode", "misplay", "incapacity", "settling",
        "computed", "computes", "chores", "scores", "ores", "deprives", "derives",
        "dries", "drivers", "programmer", "programmers", "programme", "revolutions",
        "solutions", "sittings", "stings", "seizes", "conversions", "perversions",
        "applicators", "cunning", "gunning", "pruning", "sunning", "ruining",
        "sing", "suing", "musing", "busing", "fusing", "rerunning", "skillset",
        # pre-existing protections, re-asserted so a vocabulary change cannot quietly undo them
        "shared", "hard", "project", "research", "person", "warm", "insect", "shown",
    )
    rewritten = {
        word: _fuzzy_domain_match(word)
        for word in must_survive
        if _fuzzy_domain_match(word) is not None
    }
    assert rewritten == {}, rewritten


def test_three_letter_entries_are_kept_out_of_the_vocabulary() -> None:
    """`ram`, `cpu` and `gpu` cost collisions and buy nothing.

    _fuzzy_domain_match returns early on any token shorter than four characters, so no typo of a
    three-letter word is correctable in either direction. Adding `ram` alone would put `cram`,
    `dram`, `gram`, `pram`, `ramp`, `ream`, `roam` and `tram` inside the cutoff for no reachability
    at all -- measured, against the intuition that the shortest words were the safest.
    """
    for word in ("ram", "cpu", "gpu"):
        assert word not in _DOMAIN_VOCAB, word
    from core.input_normalizer import _fuzzy_domain_match

    for word in ("cram", "dram", "gram", "pram", "ramp", "ream", "roam", "tram"):
        assert _fuzzy_domain_match(word) is None, word
