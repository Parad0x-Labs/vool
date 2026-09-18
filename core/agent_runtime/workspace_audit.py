"""The deterministic read-only workspace-audit executor.

Classifying a request as an audit does not make an audit happen. This runs a deterministic sequence
of existing workspace.* tools whose REAL outputs become the findings, with a runtime event per
step. Empty workspaces stop at the structure preflight. Validation is selected from detected root
manifests instead of assuming every project is Python and invoking VOOL's own interpreter.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from core.agent_runtime.file_target_contract import is_real_file_target
from core.agent_runtime.source_audit import analyze_sources, is_source_path, is_test_path
from core.agent_runtime.workspace_intent_detection import (
    DUAL_USE_TEXT_EXTENSIONS,
    contains_audit_verb,
    has_code_like_extension,
)
from core.execution.constants import normalize_current_scope_reference
from core.tool_arg_redaction import redact_tool_arguments

# ---------------------------------------------------------------------------------------------
# ONE shared predicate for "does this scope noun, in this sentence, actually mean the code/repo the
# operator is bound to -- or is it an ordinary-English false positive". This replaces three
# independent regex mechanisms (`_CODE_AUDIT_RE`, `_QUALIFIED_CODE_AUDIT_RE`,
# `_AUDIT_SCOPE_DEMONSTRATIVE_RE`) that each used to bake their OWN partial copy of this decision
# directly into a character class, and shipped the same false-positive bug three separate times
# because a fix to one never propagated to the others (2026-08-04):
#
#   Round A: `_AUDIT_SCOPE_DEMONSTRATIVE_RE` learned the "code of ethics" / "source of income"
#            idiom exclusion; `_CODE_AUDIT_RE`/`_QUALIFIED_CODE_AUDIT_RE` never did, so "evaluate
#            my source of income" and "assess the risk in my project's budget" still slipped
#            through those two paths.
#   Round B: `_QUALIFIED_CODE_AUDIT_RE` dropped "project" from its noun list, and
#            `_AUDIT_SCOPE_DEMONSTRATIVE_RE` already excluded "project"/"workspace"/"folder"/
#            "dir(ectory)" outright -- but `_CODE_AUDIT_RE`'s OWN tight-adjacency alternative
#            still listed "project" with no trailing-qualifier check, so "assess my project for
#            the art class", "review my project for the science fair", "evaluate my project before
#            the deadline", and "examine my project with fresh eyes" all still returned True. The
#            same bug lived a second time in `_CODE_AUDIT_RE`'s separate "check <noun>"
#            alternative ("check my project for the art class" reproduced it too).
#
# Patching a fourth spot would only move the next gap to a fifth. The fix instead is architectural:
# every scope noun this module recognizes -- code/codebase/implementation/source(s)/source code/
# repo(sitory)/project/workspace/folder/dir(ectory) -- falls into exactly ONE of three buckets, and
# every regex below matches the noun's SHAPE only (a bare, uncategorized alternation) and then asks
# this ONE function whether the specific match means code here. There is now exactly one place that
# holds an opinion about a scope noun's safety, and one future fix lands everywhere at once.
#
#   UNAMBIGUOUS  -- "codebase", "implementation", "repo"/"repository". Nobody uses these words to
#                   mean anything other than software. Always a code referent.
#   IDIOM_PRONE  -- "code", "source(s)", "source code". Always mean software EXCEPT the fixed
#                   idiom "<noun> of <something>" ("code of ethics", "source of income"), where
#                   "of X" names a different referent entirely.
#   AMBIGUOUS    -- "project", "workspace", "folder", "dir(ectory)". Used constantly for ordinary,
#                   non-software things ("my project for the art class", "clean up this folder",
#                   "which dir do I save the photos in"). Bare adjacency to a review verb is never
#                   enough evidence for one of these on its own -- the sentence needs independent
#                   confirmation: either the one verb whose primary sense IS a code review
#                   ("audit"/"audited"/"auditing"), or explicit bug/verdict/production language
#                   (`_AUDIT_VERDICT_RE`). This is exactly what closes the Round B gap: "assess my
#                   project for the art class" carries neither, so it is now rejected wherever the
#                   noun "project" is matched, not just in the one path someone happened to fix.
#
#                   Round C (2026-08-04) found the next gap one layer down: generic trouble
#                   vocabulary ("weakness(es)", "failure(s)", "bug(s)", "remedy/remedies",
#                   "repair(s)", "robust", "sound") is real, correct code-review English too, but
#                   it is ALSO completely ordinary English for a knitting project, a recipe folder,
#                   or a garage workspace -- "review my grandmother's recipe folder for weaknesses"
#                   passed for the same reason "assess my project for launch-blockers" correctly
#                   does. The vocabulary alone cannot carry the decision; where it matters is
#                   whether the SHAPE of the match already guarantees the noun is bare (nothing
#                   but a demonstrative/possessive between the verb and the noun -- the four tight
#                   patterns below) or leaves room for an intervening qualifier that names a
#                   different domain entirely (`_QUALIFIED_CODE_AUDIT_RE`'s 1-3 filler words, and
#                   the loose whole-sentence co-occurrence check in `looks_like_code_audit_request`,
#                   which has no adjacency guarantee at all). The four bare/tight patterns keep the
#                   full, generous `_AUDIT_VERDICT_RE` -- a domain qualifier has no ROOM to appear
#                   in their shape, so the generic vocabulary was never the actual gap there. The
#                   two patterns that DO leave room for a qualifier pass `strict=True`, which swaps
#                   in `_STRICT_AUDIT_VERDICT_RE` -- genuinely code-specific terms only (see the
#                   block comment above that pattern) -- so a domain-qualified AMBIGUOUS noun needs
#                   the literal word "audit" or one of those specific terms, not any word that
#                   merely sounds like trouble.
_UNAMBIGUOUS_SCOPE_NOUNS = frozenset({"codebase", "implementation", "repo", "repository"})
_IDIOM_PRONE_SCOPE_NOUNS = frozenset({"code", "source", "sources", "source code"})
_AMBIGUOUS_SCOPE_NOUNS = frozenset({"project", "workspace", "folder", "dir", "directory"})

# Shape-only patterns: each just recognizes "one of the words in this bucket", with no opinion
# about safety baked in. Longer/more specific alternatives are listed first purely so a capturing
# group prefers the fuller phrase ("source code" over "source") when both are present -- regex
# backtracking still finds the right alternative regardless of order if a later `\b` fails, the
# same way it always did in this file.
_UNAMBIGUOUS_SCOPE_NOUN_PATTERN = r"(?:codebase|implementation|repo(?:sitory)?)"
_IDIOM_PRONE_SCOPE_NOUN_PATTERN = r"(?:source\s+code|sources?|code)"
_AMBIGUOUS_SCOPE_NOUN_PATTERN = r"(?:project|workspace|folder|dir(?:ectory)?)"
_ANY_SCOPE_NOUN_PATTERN = (
    rf"(?:{_UNAMBIGUOUS_SCOPE_NOUN_PATTERN}|{_IDIOM_PRONE_SCOPE_NOUN_PATTERN}|{_AMBIGUOUS_SCOPE_NOUN_PATTERN})"
)

# The one verb whose primary sense IS a code review -- used by the shared predicate as the
# AMBIGUOUS-noun bucket's "independent confirmation" signal, and reused wherever the rest of this
# module needs the same literal check (previously a private inline regex duplicated at the dual-use
# extension check below).
_LITERAL_AUDIT_WORD_RE = re.compile(r"\baudit(?:s|ed|ing)?\b", re.IGNORECASE)


def _scope_noun_is_code_related(noun_text: str, full_sentence: str, *, strict: bool = False) -> bool:
    """The ONE decision of whether a matched scope noun means code/repo here, or is an ordinary-
    English false positive -- see the bucket comment above. `full_sentence` is the WHOLE normalized
    request, not just the text around the match, because the extra evidence an AMBIGUOUS noun needs
    (the word "audit" itself, or verdict/bug language) can sit anywhere in the sentence, not
    necessarily next to the noun (e.g. "inspect the workspace for production failures").

    `strict` narrows what counts as "verdict/bug language" for an AMBIGUOUS noun from the full,
    generous `_AUDIT_VERDICT_RE` down to `_STRICT_AUDIT_VERDICT_RE` -- see the block comment above
    those two patterns for why a caller would ever want the narrower one. UNAMBIGUOUS and
    IDIOM_PRONE nouns ignore `strict` entirely: their safety was never a function of which
    vocabulary happens to be nearby.
    """
    noun = re.sub(r"\s+", " ", str(noun_text or "").strip().lower())
    # "project plan", "project timeline", "project budget": the noun is the head of a management
    # compound, not a codebase, whatever verdict word follows ("see if the timeline is sound").
    if noun in _AMBIGUOUS_SCOPE_NOUNS and re.search(
        rf"\b{re.escape(noun)}\s+(?:plans?|timelines?|budgets?|deadlines?|proposals?|briefs?|manager|management|schedule)\b",
        full_sentence,
        re.IGNORECASE,
    ):
        return False
    if noun in _IDIOM_PRONE_SCOPE_NOUNS:
        return not bool(re.search(rf"\b{re.escape(noun)}\s+of\b", full_sentence, re.IGNORECASE))
    if noun in _UNAMBIGUOUS_SCOPE_NOUNS:
        return True
    if noun in _AMBIGUOUS_SCOPE_NOUNS:
        verdict_re = _STRICT_AUDIT_VERDICT_RE if strict else _AUDIT_VERDICT_RE
        return (
            bool(_LITERAL_AUDIT_WORD_RE.search(full_sentence))
            or bool(verdict_re.search(full_sentence))
            or bool(_CODE_STRUCTURE_EVIDENCE_RE.search(full_sentence))
        )
    return False


def _scope_regex_matches(pattern: re.Pattern[str], text: str, *, strict: bool = False) -> bool:
    """True when `pattern` matches `text` and, for whichever match captured a `noun` group, that
    noun passes `_scope_noun_is_code_related` against the whole sentence. A pattern with no `noun`
    group at all (a shape that is already decisive without one, e.g. containing the literal word
    "audit") matches on presence alone. Iterates every match `pattern` can find, not just the
    first, so one candidate noun failing the predicate does not hide a second, valid one elsewhere
    in the same sentence. `strict` is forwarded to `_scope_noun_is_code_related` unchanged -- see
    its docstring.
    """
    for match in pattern.finditer(text):
        groups = match.groupdict()
        if "noun" not in groups or groups["noun"] is None:
            return True
        if _scope_noun_is_code_related(groups["noun"], text, strict=strict):
            return True
    return False


# A DEEP code/repository audit -- distinct from the light "what's in this folder" overview. Fires on
# an explicit audit / review-the-code intent so it runs the real tool sequence, not a listing.
#
# Every alternative here contains the literal word "audit" (or the equally decisive "code review"),
# which is exactly the AMBIGUOUS-noun bucket's own "independent confirmation" signal -- so these
# shapes need no noun capture or predicate call: none of the MUST-BE-FALSE sentences measured across
# six rounds contain the word "audit" at all, and this bucket cannot raise that false-positive class
# by construction.
_AUDIT_DECISIVE_RE = re.compile(
    r"\b(?:full|deep|thorough|code|repo|repository|security|workspace)\s+audit\b"
    r"|\bcode\s+review\b"
    # Frustrated follow-ups after a shallow response must stay on the audit lane.
    r"|\b(?:asking|asked)\b.{0,36}\baudit\b"
    r"|\baudit\b.{0,36}\b(?:not|instead\s+of)\b.{0,36}\b(?:structure|listing|summary|tree)\b"
    r"|\b(?:run|do|perform|need|want)\b.{0,48}\b(?:production[\s-]*grade\s+)?audit\b",
    re.IGNORECASE,
)

# "[run] audit [on] [this/the/my/our/its] [work/workspace] <noun>". The noun still goes through the
# shared predicate (rather than being assumed safe purely because "audit" precedes it) so this file
# has exactly one opinion about what a scope noun means, never two -- in practice an AMBIGUOUS noun
# here always passes, because the literal "audit" the shape itself requires IS the predicate's own
# confirmation signal for that bucket.
_AUDIT_ON_NOUN_RE = re.compile(
    r"\b(?:run\s+(?:an?\s+|the\s+)?)?audit\s+(?:on\s+)?(?:(?:this|the|my|our|its)\s+)?"
    rf"(?:work(?:ing|space)?\s+)?(?P<noun>{_ANY_SCOPE_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# "analyse/review/inspect/... [this/the/my/our/its] [work/workspace] <noun>" -- tight adjacency, no
# filler words between the verb and the noun. This is the exact shape Round B's bug lived in: the
# noun alternation used to hardcode `project` as always-safe here with no trailing-qualifier check,
# so "assess my project for the art class" matched on "assess my project" and never looked at what
# followed. Routing the captured noun through `_scope_noun_is_code_related` closes that -- an
# AMBIGUOUS noun caught by this shape now needs the same independent confirmation every other path
# requires, wherever in the sentence it appears.
_VERB_PLUS_NOUN_RE = re.compile(
    r"\b(?:analy[sz]e|review|inspect|examine|scrutinize|assess|evaluate|critique|go\s+through)\s+"
    r"(?:(?:this|the|my|our|its)\s+)?(?:work(?:ing|space)?\s+)?"
    rf"(?P<noun>{_ANY_SCOPE_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# "check [the/this/my/our] <noun> [for obvious failures/bugs/...]" -- the second place Round B's bug
# lived (its own copy of the same hardcoded, unchecked "project"): "check my project for the art
# class" reproduced the identical false positive through this alternative instead of the one above.
_CHECK_PLUS_NOUN_RE = re.compile(
    r"\bcheck\s+(?:the\s+|this\s+|my\s+|our\s+)?"
    rf"(?P<noun>{_ANY_SCOPE_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# Natural project asks often qualify the artifact between the demonstrative and the code noun:
# "review this relay implementation", "inspect our payment gateway source code". The tight-adjacency
# shapes above only accept an adjacent target ("review this implementation"), which made these
# ordinary variants bypass the workspace tools and leak stale conversational context into the answer
# model. Keep this supplement deliberately scoped to an explicit demonstrative or possessive plus at
# most three qualifier tokens; broad conceptual asks such as "explain audit logging implementation
# patterns" must still stay on the normal discussion lane.
#
# Unlike before, AMBIGUOUS nouns are not excluded from this pattern's shape at all -- the shared
# predicate is what keeps "assess the risk in my project's budget" (verb=assess, demonstrative=the,
# filler="risk in my", noun=project, no audit word or verdict language anywhere) rejected, the same
# way it is rejected everywhere else a scope noun can be matched. The filler room between the
# demonstrative and the noun is exactly where Round A's bug lived; the predicate closes it by
# meaning, not by keeping the noun out of the pattern's reach.
#
# Round C (2026-08-04): the filler this pattern's own shape REQUIRES (1-3 tokens, mandatory, not
# optional) is exactly the qualifier that can name a different domain outright -- "my grandmother's
# recipe [folder]", "my garden [project]", "my garage [workspace]". Every currently-known real
# audit phrasing that reaches this pattern with an AMBIGUOUS noun and a real qualifier (as opposed
# to "the background job runner [codebase]", where the noun itself is UNAMBIGUOUS and the qualifier
# is irrelevant to the predicate) turned out to need the literal word "audit" anyway, never bare
# trouble vocabulary alone -- so this path asks the shared predicate in `strict` mode: an AMBIGUOUS
# noun caught here needs "audit" or a genuinely code-specific term, not "weakness"/"bug"/"repair"/
# etc, which read as ordinary English the instant a qualifier names a non-code subject.
_QUALIFIED_CODE_AUDIT_RE = re.compile(
    r"\b(?:analy[sz]e|review|inspect|examine|scrutinize|assess|evaluate|critique|go\s+through)\s+"
    r"(?:this|the|my|our|your|its|current|an?)\s+"
    r"(?:(?:[a-z0-9_.-]+)\s+){1,3}"
    rf"(?P<noun>{_ANY_SCOPE_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# Longer natural sentences can separate the review verb from the workspace noun (for example,
# "examine the command relay in the active directory and deliver a launch-blocker assessment").
# Match those by bounded semantic co-occurrence instead of requiring one exact word order.  All
# three signals are required, keeping conceptual asks such as "examine directory traversal and
# explain how it works" out of the tool lane. Built from the same shape constant as every other
# noun-bearing pattern in this module, rather than a fourth private copy of the noun list -- when an
# AMBIGUOUS noun is the only scope signal here, `_AUDIT_VERDICT_RE`'s own co-occurrence requirement
# already supplies the same "independent confirmation" the shared predicate asks for elsewhere.
#
# Round C (2026-08-04): this check has NO adjacency requirement between the verb, the noun, and the
# verdict word at all -- they can sit in three unrelated clauses of the same sentence. That is
# exactly why it is the dominant path every one of the 9 newly-found false positives actually took
# ("review my grandmother's recipe folder for weaknesses" has a verb, a scope noun, and a trouble
# word, in that order, with no code referent anywhere). `_AUDIT_SCOPE_RE` now carries a `noun`
# group so `looks_like_code_audit_request` can route each candidate noun through the shared
# predicate in `strict` mode -- see the block comment on `_scope_noun_is_code_related` -- instead of
# treating bare presence of a scope-noun-shaped word as proof of anything.
_AUDIT_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|review|inspect|examine|scrutinize|assess|evaluate|critique|go\s+through)\b",
    re.IGNORECASE,
)
_AUDIT_SCOPE_RE = re.compile(rf"\b(?P<noun>{_ANY_SCOPE_NOUN_PATTERN})\b", re.IGNORECASE)
# 2026-08-07: `read[\s-]+only` used to live in this alternation and was removed. It was added to
# answer the DOMAIN question ("is this scope noun about code or about someone's knitting project?"),
# where it is genuinely decisive — but this pattern is also consulted as evidence of VERDICT intent,
# and for that question it is not merely useless, it is backwards. Measured minimal pair on the
# installed build: "Inspect this project." routed to ordinary investigation and "Inspect this
# project read-only." routed to a stepped audit that opened 189 of 268 files. The operator's safety
# qualifier was the audit trigger. A phrase that constrains HOW we work may never be read as
# evidence about WHAT conclusion to produce; see `core/agent_runtime/investigation_intent.py`.
_AUDIT_VERDICT_RE = re.compile(
    r"\b(?:verdict|production(?:[\s-]+readiness)?|launch[\s-]+blockers?|weakness(?:es)?|"
    r"remed(?:y|ies)|repairs?|robust|sound|failures?|bugs?|vulnerabilit(?:y|ies))\b"
    # Code-quality judgements in plain words (measured 2026-09-07: "check my project, see how many
    # monolith files it has and suggest which ones can be splitted" opened no audit at all). A
    # separate alternation so the original group text stays byte-identical for the mutation test
    # that reintroduces "read-only" into it.
    r"|\b(?:monolith(?:ic|s)?|bloated|oversized|dead\s+(?:code|functions?|methods?|branches?)|unused|"
    r"never\s+called|nobody\s+calls|broken|refactor(?:ing|ed)?|spaghetti)\b",
    re.IGNORECASE,
)
# The narrow half of `_AUDIT_VERDICT_RE`, used (via `strict=True`) wherever an AMBIGUOUS noun's
# match shape leaves room for an intervening qualifier that could name a non-code domain --
# `_QUALIFIED_CODE_AUDIT_RE` and the loose co-occurrence check above. Every term here is genuinely
# code/engineering-specific: nobody describes a knitting project's "production readiness", asks for
# a garden's "launch-blockers", or calls a recipe folder "read-only". This deliberately excludes
# the bare generic forms `_AUDIT_VERDICT_RE` also carries (weakness/failure/bug/remedy/repair/
# robust/sound) -- each of those is equally ordinary English for a non-code "project" or "folder"
# ("my knitting project['s] bugs", "repairs my garage workspace needs"), so on its own none of them
# distinguishes a code audit from an ordinary review of literally anything. `bare` vulnerabilit(y|
# ies) is excluded the same way ("vulnerabilities in the composition" of an art project is real
# English); requiring "security" in front of it is what keeps it decisive.
#
# `read[\s-]+only` is absent here for the same reason it was removed from `_AUDIT_VERDICT_RE`
# above: it is a permission statement, not a requested conclusion, and it was the measured cause of
# ordinary read-only navigation being answered by a repository audit.
_STRICT_AUDIT_VERDICT_RE = re.compile(
    r"\bverdict\b"
    r"|\bproduction[\s-]+readiness\b"
    r"|\blaunch[\s-]+blockers?\b"
    r"|\bsecurity\s+vulnerabilit(?:y|ies)\b"
    r"|\bcode\s+smells?\b"
    # Engineering-specific judgements nobody applies to a garden or a recipe folder.
    r"|\bmonolith(?:ic|s)?\b"
    r"|\bdead\s+(?:code|functions?|methods?)\b"
    r"|\b(?:never\s+called|nobody\s+calls)\b"
    r"|\bbroken\s+(?:logic|code|imports?|tests?)\b"
    r"|\brefactor(?:ing|ed)?\b"
    r"|\bspaghetti\s+code\b",
    re.IGNORECASE,
)

#: Code-STRUCTURE vocabulary: independent evidence that an AMBIGUOUS scope noun ("project",
#: "folder") means code here. A garden project has no modules, functions, imports or entry point;
#: a request naming them beside "my project" is about a codebase whether or not it asks for a
#: verdict ("see how many monolith files it has", "find the core files").
_CODE_STRUCTURE_EVIDENCE_RE = re.compile(
    r"\b(?:source\s+files?|code\s+files?|modules?|functions?|methods?|classes|imports?|"
    r"entry\s+points?|codebase|dependenc(?:y|ies)|unit\s+tests?|test\s+suite|monolith(?:ic|s)?\s+files?|"
    r"\.py|\.ts|\.js|\.rs|\.go|\.java)\b",
    re.IGNORECASE,
)

# The single most natural way an audit is actually phrased: a bare "verb + this/my/our code|
# repo|..." with NO filename and NO verdict word at all -- "roast this code", "anaylse this
# code", "give this repo a once-over". Adversary + QA both reproduced this as still broken
# (2026-08-04, rejected-attempt review): the literal verb-adjacent patterns above never routed
# through `contains_audit_verb`'s fuzzy/synonym vocabulary, and the only OTHER path that does call
# `contains_audit_verb` still additionally requires `_AUDIT_VERDICT_RE` to match -- a word a casual
# "roast this code" never carries. Deliberately a whole-sentence check, not adjacency to the verb:
# `contains_audit_verb` is already narrow enough (Finding A's 4 + the QA round's 7 measured false
# positives all stay excluded -- see `tests/test_workspace_intent_detection.py`) that requiring the
# two signals to merely CO-OCCUR, rather than sit next to each other, does not reopen that class.
#
# The noun here goes through the same shared predicate as every other path: an AMBIGUOUS noun
# ("project"/"workspace"/"folder"/"dir(ectory)") still needs the literal word "audit" or explicit
# verdict language elsewhere in the sentence before it counts -- "roast this workspace" carries
# neither and stays rejected here exactly as it always has, just decided in the one shared place
# instead of by this pattern's own noun list excluding those four words outright.
_AUDIT_SCOPE_DEMONSTRATIVE_RE = re.compile(
    r"\b(?:this|my|our|its|your|current|that)\s+(?:work(?:ing|space)?\s+)?"
    rf"(?P<noun>{_ANY_SCOPE_NOUN_PATTERN})\b",
    re.IGNORECASE,
)

# Common root manifests/config whose contents ground the audit (read-only; misses are skipped).
_MANIFESTS = (
    # The project's own CONTRACT comes first. A competitor audit of the same fixture landed its
    # report by citing `AGENTS.md`'s round-trip guarantee — the document that says what the code
    # PROMISES — while this audit read README and the target and stopped. A defect is a broken
    # promise, so the promise has to be in scope.
    "AGENTS.md", "CONTRIBUTING.md", "CLAUDE.md", "ARCHITECTURE.md",
    "README.md", "README", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "Gemfile",
    "composer.json", "Dockerfile", "docker-compose.yml", "Makefile",
)

_NO_EVIDENCE_STATUSES = {"no_results", "not_found", "missing", "empty"}
# Reading source is the one genuinely expensive thing an audit does, so it gets a budget — but the
# budget belongs on the expensive axis (lines, which are tokens), not on a proxy. A flat 64 files
# fired first on any repository of many small files: 64 modules of 40 lines is 2.5k lines against a
# 50k-line allowance, so the audit stopped at 8% of the budget it was given and called the result a
# whole-project review. `_SOURCE_TOTAL_LINE_LIMIT` is the real governor; the file count is only here
# to stop a pathological repo of ten thousand one-line files from spending the run on `open()`.
_SOURCE_FILE_LIMIT = 400
_SOURCE_LINES_PER_FILE = 20000
_SOURCE_TOTAL_LINE_LIMIT = 50000
_READ_CHUNK_LINES = 400
# Verbatim source of the file the operator named, carried into the evidence the model reads.
# Bounded so a large file cannot crowd out the rest of the report.
_TARGET_EXCERPT_CHARS = 14000


# "audit" used as a verb, so a filename like `audit.py` mentioned in prose does not by itself
# make the sentence an audit request.
# The verb, for a request whose target is a FILE. It is the same verb set every noun branch above
# already accepts (`_VERB_PLUS_NOUN_RE`, `_QUALIFIED_CODE_AUDIT_RE`, `_AUDIT_ACTION_RE`) — the file
# branch used to accept the single word "audit" and nothing else.
#
# That asymmetry had no reason behind it and cost the operator the same turn twice. 2026-08-01:
# "audit <path>" missed the lane and the direct-read fast path PRINTED THE FILE; the fix taught
# this branch the word "audit". 2026-08-03: "pdf_rebuild.py in depth anylyse this for me" missed
# it again and printed the file again, because "analyse" was never added — and neither were
# "review", "examine" or "assess", each of which still routed a code-quality question to a raw
# `cat`. Extending one word at a time is how the same defect ships three times; a filename counts
# as a source-code noun, which is what the noun branches were always saying.
_AUDIT_VERB_RE = re.compile(
    r"\baudit(?:s|ed|ing)?\b"
    r"|\b(?:analy[sz]e[sd]?|analy[sz]ing|review(?:s|ed|ing)?|inspect(?:s|ed|ing)?"
    r"|examine[sd]?|examining|scrutinize[sd]?|assess(?:es|ed|ing)?"
    r"|evaluate[sd]?|evaluating|critique[sd]?)\b",
    re.IGNORECASE,
)


def looks_like_code_audit_request(text: str) -> bool:
    normalized = normalize_current_scope_reference(text)
    from core.tool_demand_signals import is_explicit_code_repair

    # A repair directive owns a mutation-and-verification workflow. The audit
    # lane can only collect read-only evidence and suppresses the coding tool
    # planner, so it must not claim such a directive on an incidental audit verb
    # (including a fuzzy match in a later test-result clause).
    if is_explicit_code_repair(normalized):
        return False
    # READ-ONLY IS NOT AUDIT. Navigation asks what the code IS; an audit asks whether it is WRONG,
    # and only the second is this lane's. Removing "read-only" from the verdict vocabulary above
    # fixed the measured minimal pair on its own, but not the wider class: prompt B of the same
    # 2026-08-07 batch ("Inspect this project read-only. Find the provider circuit-breaker
    # implementation ... which failures degrade health") still opened an audit, because "failures"
    # is legitimately verdict vocabulary and the AMBIGUOUS noun "project" accepted it. No vocabulary
    # tightening reaches that: the sentence really does contain a trouble word, and it really is
    # navigation. What separates them is the requested deliverable, so that is what is asked here —
    # and it is asked FIRST, because a turn that wants a description must not be claimed by the lane
    # that produces judgements no matter which words it happens to contain.
    from core.agent_runtime.investigation_intent import investigation_overrides_audit

    if investigation_overrides_audit(normalized):
        return False
    # Every noun-bearing shape below (`_AUDIT_ON_NOUN_RE`, `_VERB_PLUS_NOUN_RE`,
    # `_CHECK_PLUS_NOUN_RE`) matches the noun TIGHT against the verb -- nothing but an optional
    # demonstrative/possessive and an optional "work(space)" filler can sit between them, so there
    # is no room for a qualifier to name a different domain. These three keep the full, generous
    # `_scope_noun_is_code_related` (the default `strict=False`). `_QUALIFIED_CODE_AUDIT_RE` is
    # different: its own shape REQUIRES 1-3 filler words between the demonstrative and the noun,
    # which is exactly where a qualifier naming a non-code domain lives ("my garden project", "my
    # grandmother's recipe folder") -- so it asks the predicate in `strict` mode. See the block
    # comment on `_scope_noun_is_code_related` for the full reasoning.
    if (
        _AUDIT_DECISIVE_RE.search(normalized)
        or _scope_regex_matches(_AUDIT_ON_NOUN_RE, normalized)
        or _scope_regex_matches(_VERB_PLUS_NOUN_RE, normalized)
        or _scope_regex_matches(_CHECK_PLUS_NOUN_RE, normalized)
        or _scope_regex_matches(_QUALIFIED_CODE_AUDIT_RE, normalized, strict=True)
    ):
        return True
    # `contains_audit_verb` augments the literal `_AUDIT_ACTION_RE` alternation with typo
    # tolerance and the human synonyms measured missing live (Finding A, 2026-08-04: 'look over',
    # 'check', 'rate', 'roast', ...). The SCOPE + VERDICT co-occurrence requirement is unchanged,
    # so this only widens which VERB opens the same already-correct gate -- it cannot by itself
    # turn a conceptual question ("Inspect the current workspace") into an audit.
    #
    # This check has NO adjacency guarantee between the verb, the noun, and the verdict word at
    # all -- by design, for sentences like "examine the command relay in the active directory and
    # deliver a launch-blocker assessment", where the noun sits nowhere near the verb. That same
    # freedom from adjacency is what let all 9 of Round C's false positives through ("review my
    # grandmother's recipe folder for weaknesses" has all three signals, in order, naming no code
    # anywhere). Each scope noun this finds is routed through the shared predicate in `strict`
    # mode -- same reasoning as `_QUALIFIED_CODE_AUDIT_RE` above: with no adjacency to lean on at
    # all, this path can never rule out an intervening qualifier, so it can never safely trust the
    # generic vocabulary either.
    if _AUDIT_ACTION_RE.search(normalized) or contains_audit_verb(normalized):
        if _AUDIT_VERDICT_RE.search(normalized) and _scope_regex_matches(
            _AUDIT_SCOPE_RE, normalized, strict=True
        ):
            return True
    # Bare "verb + this/my/our/its/your/current/that code|project|repo|..." with no filename and
    # no verdict word -- the most natural real-world phrasing, and the specific gap the previous
    # attempt shipped without (adversary + QA, 2026-08-04). `contains_audit_verb` alone already
    # covers every typo'd core verb and every added synonym/idiom (including "once-over"/"once
    # over"); this only adds the co-occurring demonstrative-qualified scope so a bare verb by
    # itself, with nothing pointing at code at all, still does not open the gate. The scope noun
    # itself is validated by the same shared predicate as every other path.
    if contains_audit_verb(normalized) and _scope_regex_matches(_AUDIT_SCOPE_DEMONSTRATIVE_RE, normalized):
        return True
    # "audit <file>" — the most natural audit request of all names a FILE, not a project noun, and
    # every branch above requires the noun. Measured live 2026-08-01: "lets audit
    # api/apache/liquefy_apache_repetition_v1.py - whats the single worst real bug in it" fell past
    # this gate and was claimed by the direct-read fast path, which answered by PRINTING THE FILE.
    # The bare "audit api/apache/liquefy_apache_repetition_v1.py" missed the lane the same way.
    # The verb is searched with path tokens stripped, so a file NAMED `audit.py` in ordinary prose
    # ("the audit.py module needs no changes") is not read as the verb.
    target = audit_target_in(normalized)
    if not target:
        return False
    # Finding A's 4 measured false positives ('rate this restaurant.com review', 'review the
    # contract.pdf for the lease', ...) all come from a verb word incidentally present in ordinary
    # prose next to ANY dotted-looking token. Requiring a recognized code/text extension is what
    # tells a source file apart from a website, a document, or a spreadsheet.
    if not has_code_like_extension(target):
        return False
    without_paths = _AUDIT_TARGET_RE.sub(" ", normalized)
    if not contains_audit_verb(without_paths):
        return False
    extension = target.rsplit(".", 1)[-1].strip().lower()
    if extension in DUAL_USE_TEXT_EXTENSIONS:
        # QA regression (2026-08-04): combining the 5 new exact-match verbs (check/rate/grade/
        # score/roast) — and even the core verb `review` — with a dual-use extension reopened
        # Finding A's false-positive class one level down: 'rate my playlist.json', 'review my
        # notes.md', 'check my resume.md', 'grade my essay.md', 'score the recipe.yaml', 'check
        # the theme.css', 'rate my portfolio.html' all incorrectly fired the heavy audit pipeline.
        # None of these ordinary-document sentences use the one verb whose primary sense IS a
        # formal/code review ("audit"/"auditing" — nobody casually "audits" a resume or a
        # playlist), and none carry explicit bug/verdict language either. Requiring one or the
        # other closes the class without touching the unambiguous source extensions above, where a
        # bare verb + target was always enough (`auditing config.yaml today, anything scary in
        # it?` keeps working because it says "auditing").
        has_audit_word = bool(_LITERAL_AUDIT_WORD_RE.search(without_paths))
        if not has_audit_word and not _AUDIT_VERDICT_RE.search(normalized):
            return False
    return True


def _numbered_excerpt(body: str, *, limit_chars: int = _TARGET_EXCERPT_CHARS) -> tuple[str, int, int]:
    """The target file with its real line numbers attached, for a model asked to cite a range.

    The stored `sources` stay verbatim -- `audit_claim_verifier.line_at` indexes them with
    `splitlines()[n - 1]`, so numbering them at the source would move every coordinate it checks by
    the width of the prefix. Numbering happens here, at render time, for the model only.

    Measured 2026-08-01: asked for the highest-risk bug in a 210-line Apache codec under the rule
    "cite the exact file and line range", the reply cited 147-160 for a loop that lives at 132 and a
    helper at 137. The mechanism it described was real; every coordinate was invented, because the
    excerpt it reasoned over was the `verbatim=True` branch of `_read_file` -- the one with no line
    numbers. A citation nobody can check costs more than no citation: it survives the range check in
    `audit_claim_verifier` (147 < 210) and sends the operator to the wrong block.

    Truncation is by LINE, not by character, so the last number shown belongs to a whole line and the
    caller can state the range it actually handed over.
    """

    lines = str(body or "").splitlines()
    total = len(lines)
    rendered: list[str] = []
    used = 0
    for number, text in enumerate(lines, start=1):
        # Same `N: text` shape `_read_file` uses for a numbered read, so a model that has seen one
        # form has seen both.
        row = f"{number}: {text}"
        if used + len(row) + 1 > limit_chars and rendered:
            break
        rendered.append(row)
        used += len(row) + 1
    shown = len(rendered)
    if shown < total:
        rendered.append(
            f"...lines {shown + 1}-{total} not shown. This is a fragment: it cannot support any "
            "finding about what this file does NOT contain."
        )
    return "\n".join(rendered), shown, total


def _summary(result: Any, *, limit: int = 1400) -> str:
    text = str(getattr(result, "response_text", "") or "").strip()
    return (text[:limit] + "\n...[truncated]") if len(text) > limit else text


def _has_evidence(result: Any) -> bool:
    if result is None or not bool(getattr(result, "ok", False)):
        return False
    status = str(getattr(result, "status", "") or "").strip().lower()
    return status not in _NO_EVIDENCE_STATUSES


def _tree_is_empty(result: Any) -> bool:
    if result is None:
        return False
    status = str(getattr(result, "status", "") or "").strip().lower()
    if status in _NO_EVIDENCE_STATUSES:
        return True
    text = _summary(result).lower()
    return any(
        marker in text
        for marker in (
            "no files or directories matched",
            "no files found",
            "folder is empty",
            "directory is empty",
        )
    )


def _detail_paths(result: Any) -> tuple[str, ...]:
    details = dict(getattr(result, "details", {}) or {})
    return tuple(
        str(item).strip()
        for item in list(details.get("paths") or [])
        if str(item).strip()
    )


def _detail_content(result: Any) -> str:
    details = dict(getattr(result, "details", {}) or {})
    rows = [dict(item) for item in list(details.get("lines") or []) if isinstance(item, dict)]
    return "\n".join(str(item.get("text") or "") for item in rows)


def _finding_text(finding: Any) -> str:
    location = str(getattr(finding, "path", "") or ".")
    line = int(getattr(finding, "line", 0) or 0)
    if line > 0:
        location = f"{location}:{line}"
    evidence = str(getattr(finding, "evidence", "") or "").strip()
    lines = [
        f"### [{getattr(finding, 'severity', 'P3')}] {getattr(finding, 'title', 'Finding')}",
        f"- Evidence: `{location}`" + (f" — `{evidence}`" if evidence else ""),
        f"- Impact: {getattr(finding, 'impact', '')}",
        f"- Fix: {getattr(finding, 'recommendation', '')}",
    ]
    return "\n".join(lines)


def _finding_group_text(findings: list[Any]) -> str:
    first = findings[0]
    evidence_rows: list[str] = []
    for finding in findings[:5]:
        location = str(getattr(finding, "path", "") or ".")
        line = int(getattr(finding, "line", 0) or 0)
        if line > 0:
            location = f"{location}:{line}"
        evidence = str(getattr(finding, "evidence", "") or "").strip()
        evidence_rows.append(f"`{location}`" + (f" — `{evidence}`" if evidence else ""))
    if len(findings) > 5:
        evidence_rows.append(f"{len(findings) - 5} additional occurrence(s)")
    return "\n".join(
        (
            f"### [{getattr(first, 'severity', 'P3')}] {getattr(first, 'title', 'Finding')}",
            f"- Evidence ({len(findings)} occurrence(s)): " + "; ".join(evidence_rows),
            f"- Impact: {getattr(first, 'impact', '')}",
            f"- Fix: {getattr(first, 'recommendation', '')}",
        )
    )


def _group_findings(findings: tuple[Any, ...]) -> list[list[Any]]:
    grouped: dict[tuple[str, str, str], list[Any]] = {}
    for finding in findings:
        key = (
            str(getattr(finding, "severity", "") or ""),
            str(getattr(finding, "path", "") or ""),
            str(getattr(finding, "rule_id", "") or getattr(finding, "title", "") or ""),
        )
        grouped.setdefault(key, []).append(finding)
    return list(grouped.values())


def _validation_for(
    manifests: dict[str, str],
    *,
    all_paths: tuple[str, ...],
) -> tuple[str, str, dict[str, Any]] | None:
    """Return a conservative, non-formatting validation action for a detected project stack."""
    if "Cargo.toml" in manifests:
        return (
            "Rust formatting check",
            "workspace.run_lint",
            {"command": "cargo fmt --all -- --check"},
        )
    package = manifests.get("package.json", "")
    if package and re.search(r'["\']lint["\']\s*:', package, re.IGNORECASE):
        return (
            "Configured package lint",
            "workspace.run_lint",
            {"command": "npm run lint"},
        )
    requirements = manifests.get("requirements.txt", "")
    has_pytest = bool(re.search(r"(?im)^\s*pytest(?:\s|[<>=!~].*)?$", requirements))
    has_python_tests = any(is_test_path(path) and path.lower().endswith(".py") for path in all_paths)
    if has_pytest and has_python_tests:
        return (
            "Configured Python test suite",
            "workspace.run_tests",
            {"command": "python3 -m pytest -q"},
        )
    return None


# A path the user typed inside an audit request: "audit app-landing/index.html", "check
# ./src/app.js", "review `config.py`". Requires a real file extension so ordinary prose and bare
# words cannot be mistaken for a target.
# A DOTTED stem is part of the filename, not a sentence boundary. The stem was `[\w\-]+` - no dots -
# so `tsconfig.extensions.projects.json` was captured as `ts.json`: the regex matched the last two
# segments and threw the rest away.
#
# Measured live 2026-08-03. The operator asked "i need you to run an audit on
# tsconfig.extensions.projects.json tell me if you see any issues or flawS?". `ts.json` resolved to
# nothing on disk, so the audit ran its whole-project sweep instead - 9 collector tools, the named
# file never opened - and ended "Audit blocked ... this turn carried no readable audit evidence".
# The one file the operator asked about was the one file it did not read.
#
# The extension cap moves 5 -> 8 for the same reason: `.markdown`, `.yaml.example` and similar are
# ordinary names, and a cap tuned to the shortest case silently renames the target.
_AUDIT_TARGET_RE = re.compile(
    r"[`'\"]?((?:[\w\-.]+/)*[\w\-]+(?:\.[\w\-]+)*\.[A-Za-z][A-Za-z0-9]{0,8})[`'\"]?"
)


def audit_target_in(text: str) -> str:
    """The file the request names, or "" when it names none.

    Deliberately returns the LAST match: users write "run audit for this - app-landing/index.html",
    where an earlier word may look path-ish. A request naming nothing still audits the workspace,
    which is the pre-existing behaviour and stays correct for "audit my project".
    """

    found = ""
    for match in _AUDIT_TARGET_RE.finditer(str(text or "")):
        candidate = match.group(1)
        # Strip punctuation the operator used as a bullet or separator rather than as part of the
        # path. Measured: "-api/apache/liquefy_apache_repetition_v1.py - audit the code please"
        # extracted `-api/apache/...` with the leading hyphen intact, which then matched no
        # inventory path, resolved to "", and silently fell back to the broad 64-file sweep — so a
        # request naming ONE file audited sixty-four. A leading `-`, `*` or `•` is how people write
        # a list item; it is never how they write a path.
        candidate = candidate.strip("-*•·>–—,;:'\"()[]{}").strip()
        # A dotted token is not a path. This used to be a five-entry BLOCKlist (`e`/`g`/`i`/`etc`/
        # `vs`), so every other dotted identifier was a file to it: "Trace one workspace.write_file
        # request from tool schema through the rollback ledger" was audited with the target
        # `workspace.write` (measured 2026-08-07). The same defect had already been fixed six days
        # earlier in the inspection-claim validator, which parsed `blob.startswith` as a file named
        # `blob.starts` — with its own private allowlist that did not travel. One shared contract
        # now, so the next extractor inherits the answer instead of re-deciding it.
        if not is_real_file_target(candidate):
            continue
        found = candidate
    return found


def _match_target_path(target_path: str, all_paths: tuple[str, ...] | list[str]) -> str:
    """Resolve a named target against the paths the inventory actually found.

    Matched on trailing segments so the spelling the user typed finds the real workspace-relative
    path. Ambiguity resolves to nothing rather than to a guess: auditing the wrong file and saying
    so confidently is the failure this whole change exists to prevent.
    """

    wanted = [p for p in str(target_path or "").strip().strip("`'\"").replace("\\", "/").split("/") if p]
    if not wanted:
        return ""
    matches = []
    for path in all_paths:
        parts = [p for p in str(path).replace("\\", "/").split("/") if p]
        if len(parts) >= len(wanted) and parts[-len(wanted):] == wanted:
            matches.append(str(path))
    return matches[0] if len(matches) == 1 else ""


def _nearby_path_suggestions(target_path: str, all_paths: tuple[str, ...], *, limit: int = 5) -> tuple[str, ...]:
    """Paths that plausibly answer what the operator meant, for a name that is not here.

    "No such file" on its own makes the operator retype the whole path to find out they had a typo
    or the wrong directory. The inventory is already in hand, so the near misses cost nothing:
    same basename anywhere in the tree first (wrong directory), then same stem ignoring extension,
    then a shared prefix (truncated or misremembered name).
    """

    asked = str(target_path or "").strip().replace("\\", "/").lstrip("./")
    if not asked:
        return ()
    base = asked.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0].lower()
    if not stem:
        return ()

    same_name: list[str] = []
    same_stem: list[str] = []
    prefix: list[str] = []
    for candidate in all_paths:
        normalized = str(candidate).replace("\\", "/")
        candidate_base = normalized.rsplit("/", 1)[-1]
        candidate_stem = candidate_base.rsplit(".", 1)[0].lower()
        if candidate_base == base:
            same_name.append(normalized)
        elif candidate_stem == stem:
            same_stem.append(normalized)
        elif len(stem) >= 4 and (candidate_stem.startswith(stem) or stem.startswith(candidate_stem)):
            prefix.append(normalized)

    ordered: list[str] = []
    for group in (same_name, same_stem, prefix):
        for item in group:
            if item not in ordered:
                ordered.append(item)
            if len(ordered) >= limit:
                return tuple(ordered)
    return tuple(ordered)


def _match_target_path_on_disk(target_path: str, workspace_root: str) -> str:
    """Find a named target that the capped inventory never listed.

    `workspace.list_files` is bounded at 200 paths. On any repository larger than that the named
    file can fall outside the listing — and because `_match_target_path` only searches what the
    inventory returned, the audit then found no target at all and silently degraded to a
    whole-project sweep. Measured on a 221-file fixture: asked to audit `api/target.py`, the audit
    reported on `api/filler_000.py` through `api/filler_058.py` and never mentioned the file that
    was named.

    A capped listing is not evidence of absence. This is the same fallback
    `AuditEvidence.has_test_files` already makes for the same reason.
    """

    wanted = [p for p in str(target_path or "").strip().strip("`'\"").replace("\\", "/").split("/") if p]
    root = str(workspace_root or "").strip()
    if not wanted or not root:
        return ""
    from pathlib import Path

    try:
        base = Path(root)
        if not base.is_dir():
            return ""
        matches: list[str] = []
        for candidate in base.rglob(wanted[-1]):
            if not candidate.is_file():
                continue
            relative = str(candidate.relative_to(base)).replace("\\", "/")
            parts = [p for p in relative.split("/") if p]
            if len(parts) >= len(wanted) and parts[-len(wanted):] == wanted:
                matches.append(relative)
                if len(matches) > 1:
                    # Same rule as the inventory match: ambiguity resolves to nothing rather than
                    # to a guess. Auditing the wrong file confidently is the failure being avoided.
                    return ""
        return matches[0] if matches else ""
    except Exception:
        return ""


_TOP_LEVEL_SYMBOL_RE = re.compile(
    r"^(?:class|def|async\s+def)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
)
# How many of the target's own names are worth a search. The distinctive ones are the classes and
# the public functions; searching twenty of them would cost twenty tool calls to find the same
# handful of files.
_SYMBOL_QUERY_LIMIT = 4


_LOCAL_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w.]*))",
    re.MULTILINE,
)


def _imported_local_modules(source: str) -> list[str]:
    """Module stems this file imports, so the audit can read what the target DEPENDS ON.

    The audit already resolves the INBOUND direction — `workspace.search_text` finds who references
    the target. The outbound direction was never resolved, and the asymmetry produced a false
    finding: measured live 2026-08-03 on `api/apache/liquefy_apache_repetition_v1.py`, the model was
    given that file alone, could not see `api/liquefy_primitives.py`, and nominated "decompression
    crash — unpack_varint_buf and zigzag_dec are not defined in the provided source tree". All four
    named helpers ARE defined there. The adversarial checker was handed the same single file, so it
    could not refute the claim either and the turn shipped it as an unproven candidate.

    A model asked to find a bug in a file it cannot fully see will nominate the absence it can see.
    """

    stems: list[str] = []
    for dotted_from, dotted_import in _LOCAL_IMPORT_RE.findall(str(source or "")):
        dotted = dotted_from or dotted_import
        if not dotted or dotted.startswith("."):
            continue
        stem = dotted.split(".")[-1].strip()
        # Standard library and site-packages are not in the workspace, so a stem that matches no
        # project path simply resolves to nothing below. No allowlist to maintain.
        if stem and stem not in stems:
            stems.append(stem)
    # NOT capped like `_target_symbols`. That cap exists because each symbol there costs a
    # `search_text` call; these cost nothing — the caller matches them against paths already
    # listed. Capping here truncated the list at the first four imports, and a module's LOCAL
    # imports come last, after the standard library: on the fixture that exposed this, the cap
    # dropped `liquefy_primitives` — the one file that would have refuted the finding.
    return stems


def _target_symbols(source: str) -> list[str]:
    """The target's own top-level names — how other files actually refer to it.

    A test rarely imports by module path; it names the class, or reaches the engine through a
    registry. Searching the stem alone found nothing on the fixture that produced two false audits.
    """
    names = [
        name
        for name in _TOP_LEVEL_SYMBOL_RE.findall(str(source or ""))
        if not name.startswith("_") and len(name) > 3
    ]
    seen: list[str] = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return seen[:_SYMBOL_QUERY_LIMIT]


def _related_to_target(target: str, all_paths: tuple[str, ...] | list[str], referencing: set[str]) -> list[str]:
    """The files an audit of ``target`` must also read, and no more.

    A named-file audit read the target plus whatever else fitted under the 64-file limit, and scored
    its coverage against EVERY source file in the repository — so a request to audit one file
    reported `PARTIAL` no matter how completely it had read that file, and spent most of its reads
    on code nobody asked about.

    The scope that makes a single-file audit answerable is small and decidable without any static
    analysis:

      * the target itself;
      * files that REFERENCE it — resolved by `workspace.search_text` on the module stem, which the
        runtime already has, rather than by parsing imports;
      * its tests, matched on the conventional names.

    Ordered target-first, then tests, then referrers, so the file that was named is never the one
    dropped by a limit.
    """

    stem = str(target).replace("\\", "/").rsplit("/", 1)[-1]
    base = stem.rsplit(".", 1)[0]
    tests: list[str] = []
    others: list[str] = []
    for path in all_paths:
        normalized = str(path).replace("\\", "/")
        if normalized == target:
            continue
        name = normalized.rsplit("/", 1)[-1]
        if not is_source_path(normalized):
            continue
        if base and (
            name in {f"test_{base}.py", f"{base}_test.py", f"{base}.test.js", f"{base}.spec.ts"}
            or (is_test_path(normalized) and base in name)
            # …or a test that REFERENCES the target by any of its own names. The conventional-name
            # rule assumes a test is named after its subject; `tests/test_engines_run.py` exercises
            # the target through a registry and matched nothing, so the one file that explained why
            # CI misses the real defect stayed out of scope.
            or (is_test_path(normalized) and normalized in referencing)
        ):
            tests.append(normalized)
        elif normalized in referencing:
            others.append(normalized)
    return [target, *sorted(tests), *sorted(others)]


def run_workspace_audit(
    workspace_root: str,
    *,
    source_context: dict[str, Any] | None,
    execute_tool: Callable[..., Any],
    emit: Callable[..., None] | None = None,
    target_path: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Run the read-only audit sequence. Returns (report_text, steps); each step is a real tool
    receipt. `execute_tool(intent, arguments, source_context=...)` and `emit` are injected so this is
    fully testable without the runtime."""
    ctx = dict(source_context or {})
    steps: list[dict[str, Any]] = []

    def _emit(event_type: str, message: str, **details: Any) -> None:
        if emit is None:
            return
        with suppress(Exception):
            emit(ctx, event_type=event_type, message=message, details=details)

    def _run(intent: str, arguments: dict[str, Any], label: str) -> Any:
        args_summary = redact_tool_arguments(arguments)
        _emit("tool_selected", f"{label} ({intent})", tool_name=intent, tool_args=args_summary)
        result = execute_tool(intent, arguments, source_context=ctx)
        ok = _has_evidence(result)
        status = str(getattr(result, "status", "no_result") or "no_result") if result is not None else "no_result"
        steps.append({"intent": intent, "label": label, "ok": ok, "status": status, "summary": _summary(result)})
        event_type = "tool_executed" if bool(getattr(result, "ok", False)) else "tool_failed"
        _emit(event_type, f"{label}: {status}", tool_name=intent, status=status, tool_args=args_summary)
        return result

    _emit("scope_resolved", f"Auditing {workspace_root}",
          workspace_root=workspace_root, project_id=str(ctx.get("project_id") or ""))

    sections: list[str] = [f"# Code audit — {workspace_root}", ""]

    tree = _run("workspace.list_tree", {}, "Repository structure")
    structure_text = _summary(tree) or "(no tree)"
    if _tree_is_empty(tree):
        sections += [
            "## Audit stopped",
            (
                "The selected project folder is empty, so there is no code to audit. "
                "No manifest, Git, lint, or test commands were run."
            ),
            "",
            "## Recommended fix",
            (
                "Rebind this project to the folder that actually contains the repository "
                "(for example, a folder with `Cargo.toml`, `package.json`, `pyproject.toml`, "
                "or `.git`) and run the audit again. No files were changed."
            ),
        ]
        return "\n".join(sections).strip(), steps

    manifest_bits: list[str] = []
    manifests: dict[str, str] = {}
    for name in _MANIFESTS:
        res = execute_tool("workspace.read_file", {"path": name, "max_lines": 60}, source_context=ctx)
        if _has_evidence(res):
            content = _detail_content(res)
            body = _summary(res, limit=800)
            steps.append({"intent": "workspace.read_file", "label": f"read {name}", "ok": True, "status": "executed", "summary": body})
            _emit("tool_executed", f"read {name}", tool_name="workspace.read_file",
                  tool_args=redact_tool_arguments({"path": name, "max_lines": 60}))
            manifest_bits.append(f"### {name}\n{body}")
            manifests[name] = content or body
    manifest_text = "\n\n".join(manifest_bits) or "(no standard manifest at the root)"

    inventory = _run(
        "workspace.list_files",
        # Ask for the whole tree. At 200 this bound fired on any repository worth auditing: on a
        # 682-file project it cut at `plugins/` and hid every file under `tests/`, and the audit
        # then reported "No tests discovered — workspace-wide" as a P1. Enumeration is the cheapest
        # evidence an audit has; starving it is what makes the expensive parts wrong.
        {"path": ".", "limit": 5000},
        "Source inventory",
    )
    all_paths = _detail_paths(inventory)
    inventory_truncated = bool(dict(getattr(inventory, "details", {}) or {}).get("truncated", False))
    source_paths = tuple(path for path in all_paths if is_source_path(path))
    ordered_source_paths = tuple(
        sorted(source_paths, key=lambda path: (is_test_path(path), path.lower()))
    )
    # A file the user NAMED is audited first and is never cut by the file limit. Without this the
    # audit was entirely path-blind: asked to audit `app-landing/index.html` it ran its fixed sweep,
    # read `README.md` and one stray script, and reported on the workspace as though the request had
    # named nothing. The named file ranked below the limit — or, being HTML, was not "source" at all.
    wanted = _match_target_path(target_path, all_paths)
    if not wanted and str(target_path or "").strip():
        # The inventory is capped at 200 paths, so on a larger repository the named file can simply
        # not be in it — and the audit then silently became a whole-project sweep of whatever WAS.
        wanted = _match_target_path_on_disk(target_path, workspace_root)
        if wanted and wanted not in all_paths:
            all_paths = (*all_paths, wanted)
            source_paths = tuple(path for path in all_paths if is_source_path(path))
            ordered_source_paths = tuple(
                sorted(source_paths, key=lambda path: (is_test_path(path), path.lower()))
            )
    # Both resolvers signal failure with "", so `wanted == ""` used to conflate three different
    # requests: nothing was named, the name was ambiguous, or the named file DOES NOT EXIST. The
    # `else` branch below treated all three as "audit the whole project", so asking about a file
    # that is not there produced a full sweep headed "COMPLETE source coverage · validation PASSED"
    # that never mentioned the file the operator asked about. Answering a question nobody asked,
    # and calling it complete, is worse than saying the file is missing.
    if not wanted and str(target_path or "").strip():
        asked = str(target_path).strip()
        near = _nearby_path_suggestions(asked, all_paths)
        sections += [
            f"## No file named `{asked}` in this project",
            (
                f"`{asked}` was not found in `{workspace_root}`, so there was nothing to audit and "
                "no files were read. This is a missing target, not a finding about your code."
            ),
            "",
        ]
        if near:
            sections += [
                "## Did you mean one of these?",
                *(f"- `{candidate}`" for candidate in near),
                "",
                "Name one and I will audit it. I can also search the project for a different name.",
            ]
        else:
            sections += [
                "## What I can do next",
                (
                    "Say the word and I will search the project for that name, or give me a path "
                    "that exists and I will audit it."
                ),
            ]
        return "\n".join(sections).strip(), steps
    # The denominator coverage is scored against. For a whole-project audit that is every source
    # file; for a named-file audit it is the target and what depends on it, so a complete audit of
    # one file can report COMPLETE instead of always PARTIAL.
    audit_scope_paths = ordered_source_paths
    if wanted:
        # Who references this file? `workspace.search_text` already exists, so the answer needs no
        # import parsing — and if the search fails or is unavailable, the scope degrades to the
        # target plus its tests rather than to the whole repository.
        base = wanted.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        referencing: set[str] = set()
        # The MODULE STEM alone is not how a test refers to its subject. Measured live 2026-08-01
        # on `api/apache/liquefy_apache_repetition_v1.py`: searching `liquefy_apache_repetition_v1`
        # returned no_results, so `tests/test_engines_run.py` — which exercises the engine through a
        # registry id and was the file that explained why CI misses the real defect — was never
        # read, and the audit answered from the target plus README. A file is referenced by its
        # NAMES too, so the target's own top-level symbols are searched alongside its stem.
        head = _run(
            "workspace.read_file",
            {"path": wanted, "max_lines": 400, "start_line": 1, "verbatim": True},
            f"symbols of {base}",
        )
        head_text = str(getattr(head, "response_text", "") or "")
        # OUTBOUND: the modules this file imports. Pulled from `head_text`, which is already in
        # hand, so this costs no extra tool call. Without it the model sees the import line and no
        # way to check it, and "the imports are missing" becomes the only defect a single-file view
        # can offer.
        imported_stems = _imported_local_modules(head_text)
        for path in all_paths:
            clean = str(path).replace("\\", "/")
            if clean == wanted or not is_source_path(clean):
                continue
            if clean.rsplit("/", 1)[-1].rsplit(".", 1)[0] in imported_stems:
                referencing.add(clean)
        for query in [base, *_target_symbols(head_text)]:
            if not query:
                continue
            hits = _run(
                "workspace.search_text",
                {"query": query, "limit": 60},
                f"references to {query}",
            )
            for path in _detail_paths(hits):
                referencing.add(str(path).replace("\\", "/"))
        scoped = _related_to_target(wanted, all_paths, referencing)
        audit_scope_paths = tuple(scoped)
        selected_source_paths = tuple(scoped[:_SOURCE_FILE_LIMIT])
    else:
        selected_source_paths = ordered_source_paths[:_SOURCE_FILE_LIMIT]
    source_texts: dict[str, str] = {}
    source_line_counts: dict[str, int] = {}
    incomplete_files: list[str] = []
    total_source_lines = 0

    for path in selected_source_paths:
        chunks: list[str] = []
        file_lines = 0
        complete = False
        for start_line in range(1, _SOURCE_LINES_PER_FILE + 1, _READ_CHUNK_LINES):
            if total_source_lines >= _SOURCE_TOTAL_LINE_LIMIT:
                break
            arguments = {
                "path": path,
                "start_line": start_line,
                "max_lines": min(_READ_CHUNK_LINES, _SOURCE_TOTAL_LINE_LIMIT - total_source_lines),
                "verbatim": True,
            }
            result = execute_tool("workspace.read_file", arguments, source_context=ctx)
            details = dict(getattr(result, "details", {}) or {})
            line_count = int(details.get("line_count") or 0)
            status = str(getattr(result, "status", "no_result") or "no_result")
            body = _detail_content(result)
            tool_ok = bool(getattr(result, "ok", False))
            steps.append(
                {
                    "intent": "workspace.read_file",
                    "label": f"read source {path}:{start_line}",
                    "ok": tool_ok,
                    "status": status,
                    "summary": f"{path}:{start_line} ({line_count} line(s))",
                }
            )
            _emit(
                "tool_executed" if bool(getattr(result, "ok", False)) else "tool_failed",
                f"read source {path}:{start_line}: {status}",
                tool_name="workspace.read_file",
                status=status,
                tool_args=redact_tool_arguments(arguments),
            )
            if not tool_ok:
                break
            if line_count <= 0:
                complete = True
                break
            chunks.append(body)
            file_lines += line_count
            total_source_lines += line_count
            if line_count < int(arguments["max_lines"]):
                complete = True
                break
        if chunks:
            source_texts[path] = "\n".join(chunks)
            source_line_counts[path] = file_lines
        if not complete:
            incomplete_files.append(path)

    # Scored against what this audit was ASKED to cover, not against the repository. With the old
    # denominator a named-file audit that read its target completely still reported PARTIAL, because
    # every other source file in the project counted as undiscovered.
    undiscovered_count = max(0, len(audit_scope_paths) - len(selected_source_paths))
    # A truncated inventory means "there may be source we never listed", which genuinely blocks a
    # WHOLE-PROJECT audit from claiming completeness. It does not block a NAMED-FILE audit: the
    # target was found and read, and the bound only limits how many of its referrers were
    # discovered — a caveat, stated below, not an incompleteness of the thing that was asked for.
    scoped_audit = bool(wanted)
    coverage_complete = bool(source_paths) and bool(source_texts) and not (
        (inventory_truncated and not scoped_audit)
        or undiscovered_count
        or incomplete_files
        or len(source_texts) != len(selected_source_paths)
    )
    if not source_paths or not source_texts:
        audit_status = "BLOCKED"
    elif coverage_complete:
        audit_status = "COMPLETE"
    else:
        audit_status = "PARTIAL"

    coverage_lines = [
        (
            f"**{audit_status}** — inspected {len(source_texts)} of {len(audit_scope_paths)} file(s) "
            f"in scope for `{wanted}`, {sum(source_line_counts.values())} line(s)."
            if scoped_audit
            else f"**{audit_status}** — inspected {len(source_texts)} of {len(source_paths)} "
            f"discovered source file(s), {sum(source_line_counts.values())} line(s)."
        ),
    ]
    if scoped_audit:
        coverage_lines.append(
            f"- Scope: `{wanted}`, its tests, and the files that reference it. "
            "The rest of the project was deliberately not read."
        )
    if source_texts:
        coverage_lines.extend(
            f"- `{path}` — {source_line_counts[path]} line(s)"
            for path in sorted(source_texts)
        )
    if inventory_truncated:
        coverage_lines.append(
            "- File discovery hit its 200-file bound; other files may reference this one."
            if scoped_audit
            else "- File discovery hit its 200-file bound; undiscovered source may exist."
        )
    if undiscovered_count:
        coverage_lines.append(
            f"- {undiscovered_count} in-scope file(s) were outside the audit file budget."
            if scoped_audit
            else f"- {undiscovered_count} discovered source file(s) were outside the audit file budget."
        )
    if incomplete_files:
        coverage_lines.append(f"- Incomplete reads: {', '.join(f'`{path}`' for path in incomplete_files)}.")
    finding_groups: list[list[Any]] = []
    if audit_status == "BLOCKED":
        coverage_lines.append("- The runtime refuses to claim a code audit without readable source evidence.")
    coverage_text = "\n".join(coverage_lines)

    analysis = analyze_sources(
        source_texts,
        auxiliary_files=manifests,
        all_paths=all_paths,
        incomplete_paths=tuple(incomplete_files),
    )
    python_parse_text = (
        f" Parsed {analysis.parsed_files} Python source file(s); "
        f"{analysis.parse_failures} parse failure(s)."
        if analysis.parsed_files or analysis.parse_failures
        else ""
    )
    static_text = (
        f"Ran deterministic source checks across {len(source_texts)} inspected file(s)."
        f"{python_parse_text} Produced {len(analysis.findings)} evidence-backed finding(s)."
    )

    git = _run("workspace.git_summary", {}, "Git status")
    git_text = _summary(git) or "(not a git repo)"

    validation = _validation_for(manifests, all_paths=all_paths)
    lint = None
    if validation is not None:
        label, intent, arguments = validation
        lint = _run(intent, arguments, label)
        validation_text = _summary(lint) or "(no validation output)"
    else:
        validation_text = (
            "Skipped: no safe, project-declared validation command was detected from the root "
            "manifests. Source syntax and deterministic safety checks still ran without executing project code."
        )

    if audit_status == "BLOCKED":
        findings_text = (
            "No code findings are claimed because no readable source file was inspected. "
            "Bind the correct workspace or add recognized source files, then rerun."
        )
    elif analysis.findings:
        finding_groups = _group_findings(analysis.findings)
        rendered = [_finding_group_text(group) for group in finding_groups[:20]]
        if len(finding_groups) > 20:
            rendered.append(f"_Showing 20 of {len(finding_groups)} finding groups._")
        findings_text = "\n\n".join(rendered)
    else:
        findings_text = (
            "No high-confidence defect matched the deterministic checks in the inspected source. "
            "That is not proof of correctness; semantic behavior and domain invariants still require targeted tests."
        )
    if lint is not None and not _has_evidence(lint):
        validation_finding = "\n".join(
            (
                "### [P1] Project validation failed",
                f"- Evidence: `{validation[2].get('command', 'configured validation')}` completed with a failing exit code; "
                "the exact failure is shown in Validation.",
                "- Impact: At least one declared functional expectation is currently broken.",
                "- Fix: Diagnose the first failing test or validation error, repair it, and rerun the full suite.",
            )
        )
        findings_text = f"{validation_finding}\n\n{findings_text}"

    recommendations: list[str] = []
    if audit_status == "PARTIAL":
        recommendations.append("- Expand the audit budget or narrow the workspace until every relevant source file is covered.")
    if not any(is_test_path(path) and is_source_path(path) for path in all_paths):
        recommendations.append("- Add automated tests for happy paths, malformed inputs, boundary sizes, and round trips.")
    if "requirements.txt" in manifests and "pyproject.toml" not in manifests:
        recommendations.append("- Add `pyproject.toml` with supported Python versions, dependency policy, and test/lint commands.")
    if lint is not None and not _has_evidence(lint):
        recommendations.append("- Fix the failing declared validation before relying on the audit result.")
    if not recommendations:
        recommendations.append("- Convert the highest-severity findings into regression tests before changing implementation.")
    recommendation_text = "\n".join(recommendations)
    validation_state = (
        "FAILED"
        if lint is not None and not _has_evidence(lint)
        else ("PASSED" if lint is not None else "NOT RUN")
    )
    verdict = (
        f"**{audit_status} source coverage · validation {validation_state}.** "
        f"Inspected {len(source_texts)} source file(s) / {sum(source_line_counts.values())} line(s); "
        f"reported {len(finding_groups) + int(lint is not None and not _has_evidence(lint))} grounded finding group(s)."
    )
    # When the user named a file, its actual CONTENT goes into the evidence — not just its name.
    # The deterministic analyzers cover the languages they know; they have no rules for inline
    # handlers, inline script blocks or CSP, so on a request like "audit index.html, spot any
    # security issues" the report could truthfully say the file was read and still give the model
    # nothing to reason about. The model writes the answer, so the model needs the source.
    target_section: list[str] = []
    if wanted:
        body = source_texts.get(wanted, "")
        if body:
            excerpt, shown, total = _numbered_excerpt(body)
            range_note = (
                f"Lines 1-{shown} of {total} follow"
                if shown < total
                else f"All {total} line(s) follow"
            )
            target_section = [
                f"## Requested file — {wanted}",
                f"The operator asked specifically about `{wanted}`. {range_note}, each prefixed "
                "with its REAL line number. Judge THIS file against the question that was asked, "
                f"and give every finding a `{wanted}:line` coordinate copied from the prefix that "
                "sits on the code you mean — never estimated, never counted by eye.",
                f"```\n{excerpt}\n```",
                "",
            ]
        else:
            target_section = [
                f"## Requested file — {wanted}",
                f"`{wanted}` was named but could not be read; say so rather than reporting on it.",
                "",
            ]

    sections = [
        f"# Code audit — {workspace_root}",
        "",
        "## Verdict",
        verdict,
        "",
        *target_section,
        "## Findings",
        findings_text,
        "",
        "## Validation",
        validation_text,
        "",
        "## Source coverage",
        coverage_text,
        "",
        "## Recommended next work",
        recommendation_text,
        "",
        "## Evidence appendix",
        "### Static analysis",
        static_text,
        "",
        "### Git",
        git_text,
        "",
        "### Manifests & configuration",
        manifest_text,
        "",
        "### Repository structure",
        structure_text,
    ]
    # Hand the STRUCTURED evidence back, not only the rendered report. A cloud-model audit on
    # 2026-07-29 claimed "the repository has zero tests", "no external dependencies" and described a
    # `decode()` method — all three false, and all three decidable from exactly these variables. The
    # report text alone cannot settle them, so the claim verifier needs the paths and sources that
    # produced it. Stashed on source_context rather than returned, so the (report, steps) signature
    # and its callers are untouched.
    if isinstance(source_context, dict):
        source_context["workspace_audit_evidence"] = {
            "all_paths": tuple(all_paths),
            "inspected_paths": tuple(selected_source_paths),
            "sources": dict(source_texts),
            # Empty for a repository-wide sweep, so no downstream step can mistake "first file in
            # scope" for "the file the operator asked about". Carried in the blob rather than
            # recomputed, because the capsule replays this blob on a continuation turn.
            "scoped_target": str(wanted or ""),
            # So the verifier can tell "no tests found" apart from "the 200-path cap never reached
            # the tests directory" — the distinction that let a false zero-tests claim through.
            "workspace_root": str(workspace_root or ""),
            # Which files were read to the END. Without this the verifier cannot tell
            # "the model cited line 900 and the file has 120 lines" from "the model cited line 900
            # and we only read the first 400" — the first is a fabrication, the second is a gap in
            # our own evidence, and flagging the second would be the verifier lying about the model.
            "incomplete_files": tuple(incomplete_files),
        }
    return "\n".join(sections).strip(), steps


def _resume_audit_from_capsule(
    text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Re-arm the audit lane for a continuation, or return None for an ordinary turn.

    Always returns None as a RESULT — like the audit gate itself, this collects state and lets the
    answering lane own the reply. Its effect is the two flags it stamps, which route the turn to
    `run_stepped_audit` (which resumes the capsule) instead of the generic builder.
    """
    from core.agent_runtime.active_finding import scope_from_context
    from core.agent_runtime.audit_session import audit_follow_up_resumes

    if not isinstance(source_context, dict):
        return None
    scope = scope_from_context(source_context, session_id=session_id)
    capsule = audit_follow_up_resumes(
        text, session_id=session_id, project_id=scope.project_id, chat_id=scope.chat_id
    )
    if capsule is None or not capsule.evidence_blob:
        return None
    # A follow-up that names a DIFFERENT file brought its own subject. "Prove the bug in
    # svc/queue.py" after an audit of another file is a new request about the file it names, and
    # resuming under it would answer about the wrong source while sounding continuous — the same
    # substitution this capsule exists to prevent, pointed the other way. Naming the SAME file is
    # still a continuation, so the operator can be explicit without losing their place.
    named = audit_target_in(text)
    if named and _match_target_path(named, (capsule.target_path,)) != capsule.target_path:
        return None
    source_context["workspace_audit_evidence"] = dict(capsule.evidence_blob)
    source_context["workspace_audit_evidence_collected"] = True
    source_context["workspace_audit_continuation"] = True
    return None


def maybe_handle_workspace_audit_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Collect deterministic audit evidence for the answering model.

    Scope refusals remain deterministic because running against a guessed folder would be unsafe.
    A bound audit is different: the static executor gathers real file/tool evidence, attaches it to
    the same turn, and deliberately returns ``None`` so the selected answer model must interpret
    that evidence and answer the operator's actual question.  The executor is a tool, not a hidden
    replacement for the model selected in the composer.
    """
    if source_surface not in {"channel", "openclaw", "api"}:
        ctx_surface = str((source_context or {}).get("surface") or "").strip()
        if ctx_surface not in {"channel", "openclaw", "api"}:
            return None
    text = " ".join(str(user_input or "").split()).strip()
    if not text:
        return None
    # A turn that PASTES the content it asks about is self-contained (MF-17): "Review this pull
    # request comment: \"...\"" was claimed here on the word "review" and answered "I can run the
    # audit, but this chat is General and is not bound to a project folder" -- while the comment
    # to review sat in the turn. The quoted payload is the review target, not this workspace.
    from core.inline_payload import turn_supplies_its_own_content

    if turn_supplies_its_own_content(user_input):
        return None
    # RESUME BEFORE RE-OPEN. A follow-up that names no path and continues an audit this session
    # actually ran is a continuation, whatever else its wording contains — and "Challenge your own
    # audit. Take every confirmed finding you just reported and try to falsify it." contains the
    # word "audit", so the opener below claimed it first. Measured live 2026-08-07: that turn opened
    # a FRESH untargeted audit, lost `api/apache/liquefy_apache_repetition_v1.py`, re-ran nomination
    # three times against `api/__init__.py` and reported 189 of 268 files. The capsule was sitting
    # right there, unread, because the two gates were consulted in the wrong order.
    #
    # Gated on the sentence naming no target: "audit svc/queue.py" after an earlier audit is a NEW
    # request about a file it names, and resuming under it would be the mirror-image mistake.
    named_target = audit_target_in(text)
    if not named_target:
        _resume_audit_from_capsule(text, session_id=session_id, source_context=source_context)
        if bool((source_context or {}).get("workspace_audit_continuation")):
            return None
        # A continuation phrasing the opener below WOULD claim, with nothing to continue. Opening a
        # fresh audit here is how a pronoun becomes a repository scan of a file nobody named, so the
        # lane says what it does not have instead. This is the recovery Invariant 6 asks for: an
        # early classifier match that resolves to no state releases the turn rather than inventing
        # work for it.
        # The recovery used to require `looks_like_code_audit_request` as well, and that conjunct
        # is what kept it from firing on the shortest form of the very sentence it was written for:
        # "Challenge your own audit." names no scope noun, so the opener says no, and the turn fell
        # through to generic routing (ARGUS). The audit-specific test belongs to the CONTINUATION,
        # not to the opener — asking whether this sentence continues an audit is a different
        # question from whether it starts one, and only the first is being answered here. A bare
        # "continue" in a chat that never audited anything is still not this branch's business.
        from core.agent_runtime.audit_session import audit_specific_continuation

        if audit_specific_continuation(text):
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=(
                    "No previous audit is available to challenge — I don't have one recorded in "
                    "this chat, so there is nothing to carry forward. Name the file you want "
                    "audited and I'll run it, or ask me to inspect the workspace read-only and "
                    "I'll investigate without judging it."
                ),
                confidence=0.95,
                source_context=source_context,
                reason="audit_continuation_without_capsule",
            )
    if not looks_like_code_audit_request(text):
        # "Prove the bug you identified before fixing it." names no path, so the audit gate above
        # rejects it — and the turn then fell into the generic app builder, which is the misroute
        # in AGENT_HANDOVER §1A rule 5. A continuation is recognised by the SESSION CAPSULE, not by
        # the sentence: the verb "prove" alone must never open an audit, but it may resume one that
        # this session actually ran. Stamping the flag hands the turn to the stepped audit lane
        # (turn_reasoning) and, by the same flag, keeps the builder off it.
        rearmed = _resume_audit_from_capsule(
            text, session_id=session_id, source_context=source_context
        )
        if rearmed is not None or bool(
            (source_context or {}).get("workspace_audit_continuation")
        ):
            return rearmed
        # No capsule re-armed this turn, so the stepped lane will never see it — and the sentence
        # may still be a pointer at a finding that does not exist. The builder is what claimed that
        # turn in the incident and generated a test for a defect nobody had named, so the referent
        # is resolved HERE, before any lane gets the chance.
        from core.agent_runtime.active_finding import scope_from_context
        from core.agent_runtime.follow_up_gate import follow_up_blocks_execution

        refusal = follow_up_blocks_execution(
            text, scope_from_context(source_context, session_id=session_id)
        )
        if refusal:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=refusal,
                confidence=0.95,
                source_context=source_context,
                reason="follow_up_referent_missing",
            )
        return None
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if not workspace_root:
        from core.folder_overview import build_folder_overview
        return agent._fast_path_result(
            session_id=session_id, user_input=user_input,
            response=build_folder_overview("", project_bound=False),
            confidence=0.9, source_context=source_context, reason="workspace_audit_unbound",
        )
    binding = str((source_context or {}).get("workspace_binding") or "").strip().lower()
    project_id = str((source_context or {}).get("project_id") or "").strip()
    if binding == "default" and not project_id:
        response = (
            "I can run the audit, but this chat is **General** and is not bound to a project folder. "
            f"The only available scope is VOOL's fallback workspace (`{workspace_root}`), which may not be the code you mean. "
            "I will not guess and audit the wrong folder. Move this chat into the intended project in the sidebar "
            "(or provide an explicit absolute folder path), then ask again."
        )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.99,
            source_context=source_context,
            reason="workspace_audit_default_scope_refused",
        )
    from core.runtime_execution_tools import execute_runtime_tool
    from core.runtime_task_events import emit_runtime_event

    report, steps = run_workspace_audit(
        workspace_root, source_context=source_context,
        execute_tool=execute_runtime_tool, emit=emit_runtime_event,
        target_path=audit_target_in(text),
    )
    if isinstance(source_context, dict):
        observations = [
            dict(item)
            for item in list(source_context.get("runtime_tool_observations") or [])
            if isinstance(item, dict)
        ]
        observation = {
            "schema": "tool_observation_v1",
            "intent": "workspace.audit",
            "tool_surface": "workspace",
            "ok": True,
            "status": "executed",
            "workspace_root": workspace_root,
            "read_only": True,
            "final_answer": False,
            "step_count": len(steps),
            "response_preview": report[:12000],
            "instruction": (
                "This is local deterministic evidence, not the final response. Independently judge "
                "the code and answer the user's wording. Cite evidence, distinguish proven findings "
                "from limits, propose fixes, and do not claim files were changed."
            ),
        }
        if not observations or observations[-1] != observation:
            observations.append(observation)
        source_context["runtime_tool_observations"] = observations[-12:]
        source_context["workspace_audit_evidence_collected"] = True
        source_context["workspace_audit_evidence_step_count"] = len(steps)
    return None
