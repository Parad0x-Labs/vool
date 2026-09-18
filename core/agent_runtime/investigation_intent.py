"""Look versus judge: does this request ask to UNDERSTAND code, or to pass VERDICT on it?

Three fresh project-bound chats on 2026-08-07 asked for ordinary read-only navigation — "find the
implementation of /api/runtime/version", "find the provider circuit-breaker implementation", "trace
one workspace.write_file request from tool schema through the rollback ledger" — and every one of
them was answered by a stepped code audit that opened 189 of 268 files and reported a target the
operator had never named.

Traced, the cause was one word. `_AUDIT_VERDICT_RE` in `workspace_audit` carried `read[\\s-]+only`
alongside `bugs`, `vulnerabilities` and `launch-blockers`, because that vocabulary had been built to
answer a DIFFERENT question — "is this scope noun about code, or about someone's knitting project?"
— and for that question "read-only" is decisive (nobody calls a recipe folder read-only). The
measured minimal pair:

    "Inspect this project."               -> ordinary
    "Inspect this project read-only."     -> stepped audit

So the operator's safety qualifier was the audit trigger. That is the failure this module exists to
make impossible: a phrase that constrains HOW we work must never be read as evidence about WHAT
conclusion to produce, and the two vocabularies must not share a list.

The split kept here is deliberately about INTENT, not about verbs. "Inspect" appears in both an
investigation ("inspect these three files and tell me what they do") and an audit ("inspect this
release for launch-blockers"), so no verb list can decide it alone. What separates them is the
requested OUTPUT: a description of what the code does, or a judgement about whether it is wrong.

Nothing here reads a filename, a language, or a bug class.
"""
from __future__ import annotations

import re

# --- scope constraints: HOW to work, never WHAT to conclude -------------------------------------
# These describe the permission envelope. They are the phrases a careful operator adds to keep a
# turn safe, and before this module every one of them either did nothing or actively increased
# audit likelihood. They may never do the latter again.
_SCOPE_CONSTRAINT_RE = re.compile(
    r"\bread[\s-]*only\b"
    r"|\bdo\s+not\s+(?:modify|change|edit|write|touch|create)\b"
    r"|\bdon'?t\s+(?:modify|change|edit|write|touch|create)\b"
    r"|\bwithout\s+(?:modifying|changing|editing|writing)\b"
    r"|\bno\s+(?:edits?|changes?|writes?|modifications?)\b"
    r"|\buse\s+only\s+th(?:is|e)\s+(?:project|repo(?:sitory)?|workspace|folder|directory)\b"
    r"|\bdo\s+not\s+use\s+(?:the\s+)?web(?:\s+search)?\b",
    re.IGNORECASE,
)

# --- judge intent: a verdict is being requested -------------------------------------------------
# Decisive audit vocabulary. Each shape names a JUDGEMENT as the deliverable, not a description.
# "review"/"assess"/"evaluate" are absent on their own on purpose: "review this function with me"
# is ordinary. They qualify only when bound to a defect noun, which the second alternation does.
_JUDGE_RE = re.compile(
    r"\baudit(?:s|ed|ing)?\b"
    r"|\b(?:security|correctness|code|quality)\s+review\b"
    r"|\b(?:review|check|scan|assess|examine|analy[sz]e|inspect|evaluate|go\s+through)\b"
    r"(?:[^.!?]|\.(?=\w)){0,80}?\bfor\b(?:[^.!?]|\.(?=\w)){0,40}?"
    r"\b(?:bugs?|defects?|vulnerabilit(?:y|ies)|flaws?|weakness(?:es)?|issues?|errors?|"
    r"correctness|security|regressions?|race\s+conditions?|leaks?|smells?)\b"
    r"|\bfind\b(?:[^.!?]|\.(?=\w)){0,40}?\b(?:bugs?|defects?|vulnerabilit(?:y|ies)|flaws?|security\s+"
    r"(?:issues?|problems?|holes?))\b"
    r"|\bprove\s+or\s+(?:reject|disprove)\b"
    r"|\b(?:launch[\s-]+blockers?|production[\s-]+readiness)\b"
    r"|\bis\s+th(?:is|e)\s+\w+\s+(?:safe|correct|sound|broken)\b"
    # Plain-words code-quality verdicts (measured 2026-09-07: "Check my project, find core files,
    # see if all is sound if there is no dead functions or broken logic" read as pure navigation
    # because "find ... files" is a look signal and none of its judgement words were here).
    r"|\b(?:see|check|tell\s+me)\s+(?:if|whether)\b(?:[^.!?]|\.(?=\w)){0,40}?\b(?:sound|ok|okay|fine|correct|broken|safe|healthy|right)\b"
    r"|\bmonolith(?:ic|s)?\b"
    r"|\bdead\s+(?:code|functions?|methods?|branches?)\b"
    r"|\b(?:never\s+called|nobody\s+calls|nothing\s+calls)\b"
    r"|\bbroken\s+(?:logic|code|imports?|tests?)\b"
    r"|\b(?:bloated|oversized)\b"
    r"|\bunused\s+(?:code|functions?|methods?|imports?|files?|modules?)\b"
    r"|\b(?:worth\s+splitting|split(?:ting)?\s+(?:up|into)|refactor(?:ing|ed)?)\b",
    re.IGNORECASE,
)

# --- look intent: a description is being requested ----------------------------------------------
# Navigation and explanation. The deliverable is "where is it / what does it do / in what order",
# which no audit verdict answers. Bound to a concrete object where the verb alone is too generic,
# so "find out whether this is safe" does not read as navigation.
# Windows read `(?:[^.!?]|\.(?=\w))`, not `[^.!?]`: a dot followed by a word character is part of an
# identifier ("workspace.write_file", "app.py"), not a sentence end. With the bare class the trace
# window of "Trace one workspace.write_file request from ..." stopped at the dot, the look signal was
# lost, and the widened audit vocabulary then claimed the navigation prompt (measured 2026-09-07).
_LOOK_RE = re.compile(
    r"\b(?:where\s+(?:is|are|does|do)|how\s+does|how\s+is|what\s+does)\b"
    r"|\b(?:find|locate|show\s+me|point\s+me\s+to|identify)\b(?:[^.!?]|\.(?=\w)){0,60}?"
    r"\b(?:implementation|definition|function|class|method|module|file|files|code|"
    r"handler|endpoint|route|caller|callers|usage|where)\b"
    r"|\btrace\b(?:[^.!?]|\.(?=\w)){0,60}?\b(?:call|path|flow|request|through|from)\b"
    r"|\bexplain\b(?:[^.!?]|\.(?=\w)){0,60}?\b(?:how|what|from\s+the\s+source|implementation|code)\b"
    r"|\bwalk\s+me\s+through\b"
    r"|\b(?:read|open|inspect|look\s+at|list)\b\s+(?:these|those|the\s+following)\b",
    re.IGNORECASE,
)


def states_scope_constraint(text: str) -> bool:
    """Whether the request constrains HOW to work (read-only, no edits, stay local)."""
    return bool(_SCOPE_CONSTRAINT_RE.search(str(text or "")))


def decisive_audit_intent(text: str) -> bool:
    """Whether the request asks for a VERDICT — a defect judgement — as its deliverable."""
    return bool(_JUDGE_RE.search(str(text or "")))


def decisive_investigation_intent(text: str) -> bool:
    """Whether the request asks for a DESCRIPTION — location, structure, behaviour."""
    return bool(_LOOK_RE.search(str(text or "")))


def investigation_overrides_audit(text: str) -> bool:
    """True when this turn is navigation and must NOT be claimed by the audit lane.

    Both halves are required, and the asymmetry is the whole point. A look signal alone does not
    override — "find the bug in parser.py" is navigation-shaped and genuinely an audit. What
    overrides is a look signal with NO verdict requested anywhere in the sentence, which is exactly
    the shape all three production misroutes had.

    A scope constraint is deliberately NOT part of this decision. The rule it earns is neutrality,
    not veto: "read-only" is removed from the audit VERDICT vocabulary (see `workspace_audit`), so
    it can no longer raise audit likelihood — but letting it LOWER audit likelihood would be the
    same mistake mirrored, and would break real phrasings like "Evaluate this project for fragile
    code, then propose repairs; keep it read-only", whose audit intent lives in "repairs".
    """
    body = str(text or "")
    if not body.strip():
        return False
    if decisive_audit_intent(body):
        return False
    return decisive_investigation_intent(body)


__all__ = [
    "decisive_audit_intent",
    "decisive_investigation_intent",
    "investigation_overrides_audit",
    "states_scope_constraint",
]
