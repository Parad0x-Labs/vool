"""Regression fence for `core.agent_runtime.workspace_intent_detection` and its call sites.

Measured live 2026-08-04 (see `VOOL-DELIVERY/RUNTIME_UPGRADE_LEDGER.md`, Findings A/B/C and the
two rejected-attempt reviews that followed). A prior QA pass found this module had ZERO test
coverage across `tests/` and its call sites in `planner.py`/`runtime_execution_tools.py` were
sabotage-checked (disabling the fail-open wiring) with the full suite staying green -- so these
assertions exist specifically to fail if that wiring, or the fuzzy-matching internals it depends
on, ever silently regress.

Every phrasing below is a REAL phrasing measured during that investigation, not a curated example
built to make the matcher look good.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.workspace_audit import looks_like_code_audit_request
from core.agent_runtime.workspace_intent_detection import (
    contains_audit_verb,
    contains_listing_intent,
    contains_overview_intent,
    plausibly_about_bound_workspace,
)

# --------------------------------------------------------------------------------------
# Finding A: audit-verb synonyms and typo tolerance.
# --------------------------------------------------------------------------------------

AUDIT_SYNONYMS = [
    "look over", "look at", "take a look at", "check", "check out", "go through", "dig into",
    "check for bugs in", "find bugs in", "check for issues in", "find issues in",
    "what's wrong with", "rate", "grade", "score", "give me your thoughts on", "roast",
]


@pytest.mark.parametrize("synonym", AUDIT_SYNONYMS)
def test_audit_synonyms_open_the_gate(synonym: str) -> None:
    assert looks_like_code_audit_request(f"{synonym} bugs.py"), synonym


CORE_VERB_TYPOS = [
    "audi t", "anlyze", "revie", "inspet", "examin", "scrutinze", "asses", "evaluete", "critque",
    "analyz",
]


@pytest.mark.parametrize("typo", CORE_VERB_TYPOS)
def test_single_letter_typos_on_core_verbs_are_tolerated(typo: str) -> None:
    assert looks_like_code_audit_request(f"{typo} this code for bugs"), typo


def test_critique_correctly_spelled_opens_the_gate_with_no_target() -> None:
    """`critique` was one of the 10 "already recognized" verbs by name, but could not actually
    open the audit gate in its most natural phrasing -- no filename, no verdict word -- because
    neither `_CODE_AUDIT_RE` nor `_QUALIFIED_CODE_AUDIT_RE` ever listed it."""

    assert looks_like_code_audit_request("critique this code")
    assert looks_like_code_audit_request("critique apps/main.py")


def test_audit_false_positives_still_do_not_fire() -> None:
    """Finding A's originally measured false-positive class: a judgment verb next to ANY
    dotted-looking token in ordinary prose is not an audit request."""

    for phrasing in (
        "rate this restaurant.com review",
        "review the contract.pdf for the lease",
        "analyze my workout.csv routine",
        "assess the risk in insurance.doc",
    ):
        assert not looks_like_code_audit_request(phrasing), phrasing


def test_the_old_literal_regex_path_gets_the_same_idiom_exclusions_as_the_new_one() -> None:
    """Live regression (2026-08-04, final-skeptic pass): `_AUDIT_SCOPE_DEMONSTRATIVE_RE` was fixed
    to reject 'project' as a bare scope noun and 'source'/'code' immediately followed by 'of', but
    the OLDER `_CODE_AUDIT_RE`/`_QUALIFIED_CODE_AUDIT_RE` path -- checked FIRST inside
    `looks_like_code_audit_request` -- never got either exclusion, so the public entry point still
    returned True for both sentences via that older path even though the newer, already-fixed path
    correctly rejected them. Asserting on the internal regex alone (as
    `test_bare_verb_plus_this_project_still_needs_more_evidence` already does) cannot catch this
    class of gap; only the public entry point sees both paths at once.
    """

    for phrasing in (
        "evaluate my source of income",
        "assess the risk in my project's budget",
    ):
        assert not looks_like_code_audit_request(phrasing), phrasing


# --------------------------------------------------------------------------------------
# Adversary-round regression: phrase-level fuzzy matching must not collide two DIFFERENT words
# that happen to share a lot of characters as a whole string.
# --------------------------------------------------------------------------------------


def test_ordinary_prose_mentioning_a_filename_is_not_an_audit() -> None:
    """'code for bugs' scored 0.81 against the vocabulary phrase 'check for bugs' as a WHOLE
    STRING (well past the old 0.8 cutoff) even though 'code' and 'check' are different words, not
    a typo of one another."""

    assert not contains_audit_verb("I saw the code for bugs.py yesterday")
    assert not looks_like_code_audit_request("I saw the code for bugs.py yesterday")
    assert not looks_like_code_audit_request(
        "we found issues in the code for bugs.py during standup"
    )


def test_a_dog_wanting_dinner_is_not_a_workspace_overview_question() -> None:
    """'what does this DOG do' scored 0.97 against the vocabulary phrase 'what does this DO' as a
    whole string, and a raw (non-word-boundary) substring check separately let 'what does this do'
    match INSIDE 'what does this dog...' because 'dog' starts with 'do'."""

    assert not plausibly_about_bound_workspace("what does this dog want for dinner")
    assert not plausibly_about_bound_workspace("what does this dog do all day")


# --------------------------------------------------------------------------------------
# QA-round regression: bare-noun / short-word fuzzy collisions in the blast-radius gate.
# --------------------------------------------------------------------------------------


BLAST_RADIUS_MESSAGES = [
    "describe a workspace where thinking feels easy",
    "my project for art class is due tomorrow, any tips",
    "is there a good workspace app for note taking",
    "where should I put my workspace desk in the room",
]


@pytest.mark.parametrize("message", BLAST_RADIUS_MESSAGES)
def test_ordinary_off_topic_messages_with_a_bound_workspace_do_not_trigger(message: str) -> None:
    assert not plausibly_about_bound_workspace(message), message
    assert not contains_listing_intent(message), message
    assert not contains_overview_intent(message), message


# --------------------------------------------------------------------------------------
# Issue 1 (adversarial regression, 2026-08-04): `plausibly_about_bound_workspace` OR'd in
# `contains_audit_verb` with NO scope/target requirement at all, so 10 of 12 completely
# topic-unrelated real messages incorrectly returned True purely because they contained a
# judgment-shaped word, with a workspace merely happening to be bound. Every one of these must now
# return False -- a bare audit verb alone, with nothing pointing at code or the workspace, is never
# sufficient.
# --------------------------------------------------------------------------------------

BARE_AUDIT_VERB_NO_WORKSPACE_SCOPE = [
    "assess my resume for the marketing role",
    "critique my poem about the ocean",
    "analyze my dream from last night",
    "inspect this used car before I buy it",
    "evaluate my job offer, should I take it",
    "grade my exam answer please",
    "score this wine for me",
    "examine this rash on my arm, is it serious",
    "review this movie for me",
    "can you rate my essay about dogs",
]


@pytest.mark.parametrize("message", BARE_AUDIT_VERB_NO_WORKSPACE_SCOPE)
def test_bare_audit_verb_without_workspace_scope_does_not_trigger(message: str) -> None:
    assert not plausibly_about_bound_workspace(message), message


def test_should_attempt_tool_intent_stays_closed_for_bare_audit_verb_without_scope(tmp_path) -> None:
    from core.execution.planner import should_attempt_tool_intent

    for message in BARE_AUDIT_VERB_NO_WORKSPACE_SCOPE:
        assert not should_attempt_tool_intent(
            message,
            task_class="chat",
            source_context={"surface": "channel", "workspace": str(tmp_path)},
        ), message


def test_audit_verb_with_workspace_scope_still_triggers() -> None:
    """The precision fix must not become a rollback: an audit verb co-occurring with an actual
    code/workspace noun, or a real code-like file target, keeps reaching the fail-open lane."""

    assert plausibly_about_bound_workspace("roast this workspace")
    assert plausibly_about_bound_workspace("review this code")
    assert plausibly_about_bound_workspace("audit api/main.py")


def test_where_and_there_do_not_fuzzy_collide_with_here() -> None:
    """'where'/'there' are one edit from 'here' (delete a letter) but are different, common
    English words -- not a typo of it."""

    assert not contains_listing_intent("where did you put the keys")
    assert not contains_listing_intent("is there a good workspace app for note taking")


def test_real_words_do_not_fuzzy_collide_with_listing_strong_words() -> None:
    """'miles'/'continents'/'inventors' sit inside the strong words' 0.8 typo neighborhood but
    are different, common English words.

    Measured live (2026-09-08 acceptance, turn 3): "How many continents are there?" inside an
    eight-question quiz made the whole turn read as a folder-listing request; the turn took the
    tool-intent lane and, with the model lane down, served "I wasn't able to turn that into a
    completed action" over eight answerable questions. Strong words are exact-only now; a
    misspelling still reaches the lane through the weak branch (see below).
    """

    for message in (
        "How many continents are there?",
        "how many miles is it to chicago?",
        "the inventors filed a patent",
        "isles of scotland",
    ):
        assert not contains_listing_intent(message), message


def test_real_listing_requests_still_trigger_including_strong_word_typos() -> None:
    """The precision cut must not become a rollback: exact strong words, weak+strong mixes and a
    strong-word typo accompanied by weak words/demonstratives all keep reaching the lane."""

    for message in (
        "show me the files in this folder",
        "what are the contents of this directory",
        "ls",
        "print the file inventory",
        "list the contetns here",
    ):
        assert contains_listing_intent(message), message


def test_plain_multi_question_quiz_stays_out_of_the_tool_intent_gate() -> None:
    from core.execution.planner import should_attempt_tool_intent

    quiz = (
        "Please answer all eight briefly: 1) What is 7 x 6? 2) How many continents are there? "
        "3) What gas do plants absorb? 4) What is 15 - 9? 5) What is the tallest mountain? "
        "6) How many sides does a hexagon have? 7) What ocean is the largest? "
        "8) What is the freezing point of water in Celsius?"
    )
    assert not should_attempt_tool_intent(
        quiz, task_class="chat_conversation", source_context={}
    )


# --------------------------------------------------------------------------------------
# QA-round regression: the dual-use extension false-positive class (Finding A, reopened one
# level down by the new exact-match verbs and markup/config extensions).
# --------------------------------------------------------------------------------------


DUAL_USE_FALSE_POSITIVES = [
    "rate my playlist.json",
    "review my notes.md",
    "check my resume.md",
    "grade my essay.md",
    "score the recipe.yaml",
    "check the theme.css",
    "rate my portfolio.html",
]


@pytest.mark.parametrize("phrasing", DUAL_USE_FALSE_POSITIVES)
def test_dual_use_extensions_need_more_than_a_bare_verb(phrasing: str) -> None:
    assert not looks_like_code_audit_request(phrasing), phrasing


# --------------------------------------------------------------------------------------
# Rejected-attempt review (2026-08-04): adversary + QA both reproduced a bare "verb + this code"
# request (no filename, no verdict word) as still missing the gate, despite `contains_audit_verb`
# already returning True for every one of these -- because `_CODE_AUDIT_RE`/
# `_QUALIFIED_CODE_AUDIT_RE` hardcode the literal verb list, and the only path that DOES call
# `contains_audit_verb` still additionally required an `_AUDIT_VERDICT_RE` match.
# --------------------------------------------------------------------------------------

BARE_VERB_PLUS_THIS_CODE = [
    # Adversary round.
    "anaylse this code", "scrutinze this code", "auditt this code", "revieww this code",
    "critiqu this code", "asssess this code", "evaluatee this code", "anlyze this code",
    "exmine this code",
    # QA round (partially overlapping typos, plus casual synonyms with no verdict word).
    "roast this code", "look over this code", "check out this code", "dig into this code",
    "what's wrong with this code", "rate this code",
    "anaylze this code", "reivew this code", "inspct this code", "examin this code",
    "critque this code",
]


@pytest.mark.parametrize("phrasing", BARE_VERB_PLUS_THIS_CODE)
def test_bare_verb_plus_this_code_opens_the_gate_with_no_verdict_word(phrasing: str) -> None:
    assert contains_audit_verb(phrasing), phrasing
    assert looks_like_code_audit_request(phrasing), phrasing


ONCE_OVER_IDIOM = ["give this repo a once-over", "once over this code please"]


@pytest.mark.parametrize("phrasing", ONCE_OVER_IDIOM)
def test_once_over_idiom_opens_the_gate(phrasing: str) -> None:
    assert contains_audit_verb(phrasing), phrasing
    assert looks_like_code_audit_request(phrasing), phrasing


def test_once_over_does_not_fire_on_an_unrelated_sentence() -> None:
    """The idiom alone, with nothing pointing at code, must not open the gate."""

    assert not looks_like_code_audit_request("once over the hill, things change")


LOG_AND_ENV_TARGETS = [
    "audit this deploy.log for suspicious entries",
    "review error.log please",
    "audit config.env for leaked secrets",
]


@pytest.mark.parametrize("phrasing", LOG_AND_ENV_TARGETS)
def test_log_and_env_are_recognized_audit_targets(phrasing: str) -> None:
    assert looks_like_code_audit_request(phrasing), phrasing


# --------------------------------------------------------------------------------------
# Issue 2 (adversarial regression, 2026-08-04): `CODE_TEXT_EXTENSIONS`/`DUAL_USE_TEXT_EXTENSIONS`
# omitted `txt`/`conf`/`ini` entirely -- not dual-use-gated, just excluded outright, with no
# decision recorded either way. All four of these extremely common real project filenames must now
# be recognized audit targets.
# --------------------------------------------------------------------------------------

TXT_CONF_INI_TARGETS = [
    "audit requirements.txt",
    "audit requirements.txt for outdated packages",
    "audit server.conf for misconfigurations",
    "check config.ini for problems",
]


@pytest.mark.parametrize("phrasing", TXT_CONF_INI_TARGETS)
def test_txt_conf_ini_are_recognized_audit_targets(phrasing: str) -> None:
    assert looks_like_code_audit_request(phrasing), phrasing


def test_bare_notes_txt_still_needs_more_than_a_bare_verb() -> None:
    """`txt` is dual-use (unlike `conf`/`ini`): a bare judgment verb plus an ordinary personal
    `.txt` document, with no explicit "audit" word and no verdict language, must not fire -- the
    same protection already given to `.md`/`.json`/`.yaml`."""

    assert not looks_like_code_audit_request("rate my notes.txt")
    assert not looks_like_code_audit_request("review my todo.txt")


def test_bare_verb_plus_this_project_still_needs_more_evidence() -> None:
    """The bare-scope path (`_AUDIT_SCOPE_DEMONSTRATIVE_RE`) matches the SHAPE "verb + this/my/
    .../workspace|project|folder|dir" -- it no longer excludes those four nouns from its own
    character class the way it used to. What keeps "roast this workspace" rejected is the shared
    `_scope_noun_is_code_related` predicate every noun-bearing path in the module now consults:
    "project"/"workspace"/"folder"/"dir" collide constantly with non-software uses ("my project
    for the art class") and need the literal word "audit" or explicit verdict language elsewhere
    in the sentence before they count. Asserted here through the PUBLIC entry point -- see
    `test_the_shared_scope_noun_predicate_is_what_every_path_consults` below for the predicate
    itself, and `test_the_old_literal_regex_path_gets_the_same_idiom_exclusions_as_the_new_one`
    above for why asserting on an internal regex object directly is exactly the anti-pattern this
    round's refactor removed: it can go green while the public function it feeds is still wrong."""

    assert not looks_like_code_audit_request("roast this workspace")
    assert not looks_like_code_audit_request("review your code of ethics")
    assert not looks_like_code_audit_request("evaluate my source of income")


def test_the_shared_scope_noun_predicate_is_what_every_path_consults() -> None:
    """Direct coverage of `_scope_noun_is_code_related` -- the ONE function all of
    `_AUDIT_ON_NOUN_RE`, `_VERB_PLUS_NOUN_RE`, `_CHECK_PLUS_NOUN_RE`, `_QUALIFIED_CODE_AUDIT_RE`
    and `_AUDIT_SCOPE_DEMONSTRATIVE_RE` gate their matches through (via `_scope_regex_matches`).
    Testing it directly, rather than only through whichever regex happens to reach it in a given
    sentence, is what makes a future change to any ONE of those five patterns get caught by the
    same fence -- the whole point of unifying three independent, drifting mechanisms into one."""

    from core.agent_runtime.workspace_audit import _scope_noun_is_code_related

    # UNAMBIGUOUS: always a code referent, regardless of what else is (or isn't) in the sentence.
    for noun in ("codebase", "implementation", "repo", "repository"):
        assert _scope_noun_is_code_related(noun, f"review this {noun}"), noun

    # IDIOM_PRONE: a code referent, except the "<noun> of <something>" idiom collision.
    assert _scope_noun_is_code_related("code", "review this code")
    assert not _scope_noun_is_code_related("code", "review your code of ethics")
    assert _scope_noun_is_code_related("source", "examine our source")
    assert not _scope_noun_is_code_related("source", "evaluate my source of income")
    assert _scope_noun_is_code_related("source code", "inspect our source code")
    assert not _scope_noun_is_code_related("source code", "audit my source code of the app")

    # AMBIGUOUS: needs the literal word "audit", or explicit verdict/bug language, ANYWHERE in the
    # sentence -- not merely adjacency to the verb that matched it.
    for noun in ("project", "workspace", "folder", "dir", "directory"):
        assert not _scope_noun_is_code_related(noun, f"assess my {noun} for the art class"), noun
        assert _scope_noun_is_code_related(noun, f"please audit my {noun}"), noun
        assert _scope_noun_is_code_related(noun, f"assess my {noun} for launch blockers"), noun

    # An unrecognized noun is never a code referent.
    assert not _scope_noun_is_code_related("restaurant", "rate this restaurant review")


# --------------------------------------------------------------------------------------
# Hunting for more "AMBIGUOUS noun + trailing qualifier" false positives after the unification --
# varying the verb, the trailing qualifier, and which of the four collision-prone nouns is used.
# None of these name code, a repo, a project's SOFTWARE, or carry any audit/verdict language.
# --------------------------------------------------------------------------------------

MORE_AMBIGUOUS_NOUN_FALSE_POSITIVES = [
    "check my workspace for clutter before the guests arrive",
    "inspect this folder of old photos before I delete it",
    "go through my dir of vacation pictures this weekend",
    "critique my project for the county fair",
    "scrutinize this workspace layout for feng shui",
    "analyze my folder structure for the wedding photos",
    "evaluate this directory of family recipes",
    "assess my workspace for ergonomics",
    "review my project idea for the hackathon pitch",
    "examine this folder before you archive it",
]


@pytest.mark.parametrize("phrasing", MORE_AMBIGUOUS_NOUN_FALSE_POSITIVES)
def test_more_ambiguous_noun_trailing_qualifier_false_positives(phrasing: str) -> None:
    assert not looks_like_code_audit_request(phrasing), phrasing


# --------------------------------------------------------------------------------------
# Round D (2026-08-04): generic trouble vocabulary ("weakness(es)"/"failure(s)"/"bug(s)"/
# "remedy/remedies"/"repair(s)"/"robust"/"sound") is real code-review English, but it is EQUALLY
# ordinary English for a knitting project, a recipe folder, or a garage workspace. The previous
# rounds' fix let an AMBIGUOUS noun's confirmation signal be "the literal word audit, OR
# `_AUDIT_VERDICT_RE` anywhere in the sentence" -- and `_AUDIT_VERDICT_RE` mixes genuinely
# code-specific terms (production-readiness/launch-blockers/read-only/verdict/security
# vulnerabilit(y|ies)/code smell) with that generic vocabulary, so any sentence pairing an
# AMBIGUOUS noun with a review verb and ANY generic trouble word returned True regardless of
# topic. Confirmed live, 100% reproducible, 9 sentences below.
#
# The fix is scoped to WHERE the match leaves room for a qualifier that could name a different
# domain (`_QUALIFIED_CODE_AUDIT_RE`'s mandatory 1-3 filler words, and the loose whole-sentence
# co-occurrence check in `looks_like_code_audit_request` that has no adjacency guarantee at all) --
# not to the noun type alone. The four TIGHT patterns (`_AUDIT_ON_NOUN_RE`, `_VERB_PLUS_NOUN_RE`,
# `_CHECK_PLUS_NOUN_RE`, `_AUDIT_SCOPE_DEMONSTRATIVE_RE`) structurally cannot capture a
# qualified noun -- nothing but a demonstrative/possessive and an optional "work(space)" filler
# fits between the verb and the noun -- so they keep the full, generous `_AUDIT_VERDICT_RE`
# unchanged: "Please evaluate this project, decide whether it is robust, and suggest repairs"
# and "inspect the workspace for production failures ... robust ... repairs" both stay True
# below, on the exact same generic vocabulary the false positives use, because their nouns are
# bare and theirs are qualified.
# --------------------------------------------------------------------------------------

ROUND_D_QUALIFIED_AMBIGUOUS_NOUN_FALSE_POSITIVES = [
    "review my grandmother's recipe folder for weaknesses",
    "evaluate my knitting project for bugs before I give it as a gift",
    "assess my garden project for weaknesses before spring",
    "scrutinize my garage workspace for repairs it needs before winter",
    "assess my budget spreadsheet folder for weaknesses before tax season",
    "evaluate my wedding planning project for failures before the big day",
    "go through my vacation itinerary project for bugs before we fly",
    "assess my art project for vulnerabilities in the composition",
    "review my cooking project's failures from last night's dinner party",
]


@pytest.mark.parametrize("phrasing", ROUND_D_QUALIFIED_AMBIGUOUS_NOUN_FALSE_POSITIVES)
def test_round_d_qualified_ambiguous_noun_with_generic_trouble_word_stays_false(phrasing: str) -> None:
    assert not looks_like_code_audit_request(phrasing), phrasing


# More of the same shape, generated to confirm the fix generalizes rather than merely patching the
# 9 measured sentences above: different generic trouble-words, different ambiguous nouns, different
# verbs, different qualifiers.
ROUND_D_GENERATED_FALSE_POSITIVES = [
    "critique my hiking trip folder for problems before we leave",
    "assess my baking project for flaws before the competition",
    "review my photography portfolio folder for weak spots",
    "examine my car maintenance project for issues this weekend",
    "go through my genealogy research folder for errors before publishing",
    "evaluate my songwriting project for weaknesses before the demo",
    "inspect my woodworking workspace for defects before the show",
    "scrutinize my tax prep folder for mistakes before filing",
    "assess my dating profile project for red flags honestly",
    "review my meal-prep folder for problems this week",
]


@pytest.mark.parametrize("phrasing", ROUND_D_GENERATED_FALSE_POSITIVES)
def test_round_d_generated_qualified_ambiguous_noun_variants_stay_false(phrasing: str) -> None:
    assert not looks_like_code_audit_request(phrasing), phrasing


# The bare-noun paths must NOT have lost the generic vocabulary they always safely relied on --
# nothing but a demonstrative/possessive sits between the verb and the noun here, so there is no
# room for a qualifier to name a different domain, and the full generous `_AUDIT_VERDICT_RE` stays
# correct.
ROUND_D_BARE_NOUN_GENERIC_VOCAB_STILL_TRUE = [
    "please assess this project, decide if it is sound, and list the bugs",
    "evaluate our workspace and tell me about any weaknesses",
    "inspect this folder and report any failures you find",
    "go through this dir and flag any bugs",
    "review the project for robustness and suggest repairs",
    "examine this workspace and give a verdict on its quality",
]


@pytest.mark.parametrize("phrasing", ROUND_D_BARE_NOUN_GENERIC_VOCAB_STILL_TRUE)
def test_round_d_bare_ambiguous_noun_keeps_the_generous_vocabulary(phrasing: str) -> None:
    assert looks_like_code_audit_request(phrasing), phrasing


def test_round_d_pinned_novelty_fixture_sentences_stay_true() -> None:
    """The exact sentences `test_workspace_audit_novelty.py` already pins as MUST-BE-TRUE that
    rely on bare-noun generic vocabulary -- re-asserted here, next to the Round D fix, so a future
    change to the AMBIGUOUS-noun predicate is caught by both fences at once."""

    assert looks_like_code_audit_request(
        "Please evaluate this project, decide whether it is robust, and suggest repairs "
        "without editing anything."
    )
    assert looks_like_code_audit_request(
        "Please inspect the workspace for production failures, decide whether it is robust, "
        "and suggest repairs without editing anything."
    )


def test_round_d_strict_mode_on_the_shared_predicate() -> None:
    """Direct coverage of the `strict` parameter added to `_scope_noun_is_code_related` for Round
    D: an AMBIGUOUS noun with only generic trouble vocabulary nearby fails in `strict` mode but
    still passes in the default (non-strict) mode used by the four tight, bare-noun patterns."""

    from core.agent_runtime.workspace_audit import _scope_noun_is_code_related

    for noun in ("project", "workspace", "folder", "dir", "directory"):
        generic_only = f"assess my {noun} for weaknesses"
        assert _scope_noun_is_code_related(noun, generic_only), noun
        assert not _scope_noun_is_code_related(noun, generic_only, strict=True), noun

        narrow_present = f"assess my {noun} for launch blockers"
        assert _scope_noun_is_code_related(noun, narrow_present), noun
        assert _scope_noun_is_code_related(noun, narrow_present, strict=True), noun

        literal_audit = f"please audit my {noun} for bugs"
        assert _scope_noun_is_code_related(literal_audit and noun, literal_audit, strict=True), noun

    # UNAMBIGUOUS and IDIOM_PRONE nouns ignore `strict` entirely.
    assert _scope_noun_is_code_related("codebase", "review this codebase", strict=True)
    assert _scope_noun_is_code_related("code", "review this code", strict=True)
    assert not _scope_noun_is_code_related("code", "review your code of ethics", strict=True)


def test_the_word_audit_itself_is_always_enough_even_on_a_dual_use_extension() -> None:
    """Pinned live scenario (`test_an_audit_cannot_monologue_its_way_out.py`): the one verb whose
    primary sense IS a formal/code review must keep working on a config file with no other
    evidence at all."""

    assert looks_like_code_audit_request("auditing config.yaml today, anything scary in it?")


# --------------------------------------------------------------------------------------
# Findings B/C: casual registers that must now reach the fail-open tool lane.
# --------------------------------------------------------------------------------------


CASUAL_WORKSPACE_PHRASINGS = [
    "yo what's up with this repo",
    "peek inside this dir for me",
    "what's the deal with this codebase",
]


@pytest.mark.parametrize("phrasing", CASUAL_WORKSPACE_PHRASINGS)
def test_casual_registers_reach_the_fail_open_lane(phrasing: str) -> None:
    assert plausibly_about_bound_workspace(phrasing), phrasing


def test_casual_idioms_alone_do_not_trigger_without_a_workspace_noun() -> None:
    """'what's up with'/'the deal with' are generic idioms and must only count together with an
    explicit demonstrative-qualified workspace noun, never alone."""

    assert not contains_overview_intent("what's up with you today")
    assert not contains_overview_intent("what's the deal with the traffic today")


# --------------------------------------------------------------------------------------
# Call-site wiring: the fail-open path actually changes `should_attempt_tool_intent` and
# `looks_like_execution_request`, not just the leaf predicate.
# --------------------------------------------------------------------------------------


def test_should_attempt_tool_intent_fails_open_with_a_bound_workspace(tmp_path) -> None:
    from core.execution.planner import should_attempt_tool_intent

    workspace = str(tmp_path)
    assert should_attempt_tool_intent(
        "what files are here",
        task_class="chat",
        source_context={"surface": "channel", "workspace": workspace},
    )
    assert should_attempt_tool_intent(
        "yo what's up with this repo",
        task_class="chat",
        source_context={"surface": "channel", "workspace": workspace},
    )


@pytest.mark.parametrize("message", [*BLAST_RADIUS_MESSAGES, "what does this dog want for dinner"])
def test_should_attempt_tool_intent_stays_closed_for_ordinary_chat(tmp_path, message: str) -> None:
    from core.execution.planner import should_attempt_tool_intent

    assert not should_attempt_tool_intent(
        message,
        task_class="chat",
        source_context={"surface": "channel", "workspace": str(tmp_path)},
    ), message


def test_looks_like_execution_request_recognizes_a_project_overview_question() -> None:
    from core.runtime_execution_tools import looks_like_execution_request

    assert looks_like_execution_request(
        "what is this project about", task_class="unknown"
    )
    assert looks_like_execution_request(
        "give me an overview of this project", task_class="unknown"
    )


def test_looks_like_execution_request_recognizes_a_folder_listing_question() -> None:
    """Live-confirmed gap (2026-08-04): 'peek inside this dir for me' passed the planner's own
    `should_attempt_tool_intent` in isolation, but a real end-to-end run still fell through to a
    tools-less, hallucinating chat reply because `should_keep_ai_first_chat_lane` gates the
    AI-first chat lane BEFORE the tool-intent gate is reached, and consults THIS function (via
    `explicit_runtime_workflow_request`) -- which had the overview hook but not the listing one."""

    from core.runtime_execution_tools import looks_like_execution_request

    assert looks_like_execution_request("what files are in this folder", task_class="unknown")
    assert looks_like_execution_request("peek inside this dir for me", task_class="unknown")


def test_looks_like_execution_request_does_not_fire_on_unrelated_chat() -> None:
    from core.runtime_execution_tools import looks_like_execution_request

    assert not looks_like_execution_request(
        "what does this dog want for dinner", task_class="unknown"
    )
    assert not looks_like_execution_request(
        "is there a good workspace app for note taking", task_class="unknown"
    )


# --------------------------------------------------------------------------------------
# Sabotage control: disabling the fail-open wiring in `planner.py` must fail one of the tests
# above, not leave the suite green. This does not re-run the whole suite (too slow here); it
# directly proves the two functions above are load-bearing on the checked scenarios by asserting
# the underlying leaf predicate is what `should_attempt_tool_intent` actually consults.
# --------------------------------------------------------------------------------------


def test_should_attempt_tool_intent_consults_the_shared_predicate(monkeypatch, tmp_path) -> None:
    """'roast this workspace' is caught ONLY by the fail-open predicate (an audit-verb synonym,
    not a listing/overview phrase, so it trips neither `has_explicit_tool_intent_request`'s own
    allowlist nor the listing/overview hooks wired into `looks_like_execution_request`), so
    disabling `plausibly_about_bound_workspace` must remove this specific True."""

    import core.agent_runtime.workspace_intent_detection as wid
    from core.execution.planner import has_explicit_tool_intent_request, should_attempt_tool_intent

    phrase = "roast this workspace"
    assert not has_explicit_tool_intent_request(phrase, task_class="chat"), (
        "test assumption broken: this phrasing must be reachable ONLY via the fail-open predicate"
    )
    monkeypatch.setattr(wid, "plausibly_about_bound_workspace", lambda text: False)

    assert not should_attempt_tool_intent(
        phrase,
        task_class="chat",
        source_context={"surface": "channel", "workspace": str(tmp_path)},
    ), "disabling the shared predicate must remove the fail-open behavior it drives"
