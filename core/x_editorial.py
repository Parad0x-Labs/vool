"""The X editorial engine: the typed XDraft contract, deterministic validation, and exact
copy-ready rendering for the x-editorial-studio native skill.

Division of labor (the C20 law): the MODEL proposes editorial content; THIS ENGINE owns the
judgment that can be made deterministic — platform truth, claim grounding, AI-sludge
detection, privacy, and the exact copy-ready text. The model's word is never taken for any
engine-owned field: ``platform_policy_version``, ``claim_ledger``,
``validation_results`` and ``copy_ready_text`` are always computed here.

The contract: every drafting turn the skill guides MUST carry its answer in the envelope

    ===XDRAFT===
    {"mode": "post" | "long_post" | "thread" | "article", ...}
    ===XDRAFT-END===

Model-owned keys: mode, audience, purpose, thesis, title, hook, posts, sections,
media_notes, links, cta, opinions. Engine-owned keys (model values ignored):
voice_profile_version, platform_policy_version, claim_ledger, validation_results,
copy_ready_text.

A clean draft is served as EXACTLY its copy-ready text — no commentary around it. Any
failure finding withholds the copy-ready rendering and serves the draft with a typed,
machine-readable findings block instead: a hostile provider can introduce unsupported
numbers, quotations or URLs, but never UNNOTICED. A response with no envelope passes
through untouched (critique and conversation are prose, not paste-ready copy).

No function here writes files, grants tools, or touches the network. Drafting is
permission-free and effect-free; publishing does not exist in this lane.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from dataclasses import replace as dataclasses_replace
from typing import Any

from core import x_platform_policy as policy
from core.x_platform_policy import weighted_length

X_EDITORIAL_SKILL_ID = "x-editorial-studio"

ENVELOPE_BEGIN = "===XDRAFT==="
ENVELOPE_END = "===XDRAFT-END==="
_WITHHELD_HEADER = "[x-editorial] copy-ready withheld — validation findings:"


# --- typed records --------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimEntry:
    text: str
    kind: str          # url | number | quote | opinion
    status: str        # grounded | ungrounded | opinion
    where: str = ""


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    message: str
    severity: str = "failure"   # failure | warning | unknown
    where: str = ""


@dataclass(frozen=True)
class XDrafT:
    """The typed X editorial draft contract."""
    mode: str
    audience: str = ""
    purpose: str = ""
    thesis: str = ""
    voice_profile_version: int | None = None
    title: str = ""
    hook: str = ""
    posts: tuple[str, ...] = ()       # thread items; the body for post / long_post
    sections: tuple[str, ...] = ()    # article body sections
    media_notes: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    claim_ledger: tuple[ClaimEntry, ...] = ()
    cta: str = ""
    platform_policy_version: str = ""
    validation_results: tuple[ValidationFinding, ...] = ()
    copy_ready_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "audience": self.audience,
            "purpose": self.purpose,
            "thesis": self.thesis,
            "voice_profile_version": self.voice_profile_version,
            "title": self.title,
            "hook": self.hook,
            "posts": list(self.posts),
            "sections": list(self.sections),
            "media_notes": list(self.media_notes),
            "links": list(self.links),
            "claim_ledger": [
                {"text": c.text, "kind": c.kind, "status": c.status, "where": c.where}
                for c in self.claim_ledger
            ],
            "cta": self.cta,
            "platform_policy_version": self.platform_policy_version,
            "validation_results": [
                {"code": f.code, "message": f.message, "severity": f.severity,
                 "where": f.where}
                for f in self.validation_results
            ],
            "copy_ready_text": self.copy_ready_text,
        }


@dataclass(frozen=True)
class XEditorialApplication:
    """The response-edge result: what to serve, and the typed receipt for the ledger."""
    text: str
    changed: bool
    compliant: bool
    record: dict[str, Any] = field(default_factory=dict)


# --- envelope --------------------------------------------------------------------------


def envelope_for(draft: dict[str, Any]) -> str:
    return f"{ENVELOPE_BEGIN}\n{json.dumps(draft)}\n{ENVELOPE_END}"


def parse_envelope(text: str) -> tuple[dict[str, Any] | None, str]:
    """(draft dict | None, status) where status is "" | "absent" | "malformed"."""
    raw = str(text or "")
    begin = raw.find(ENVELOPE_BEGIN)
    if begin < 0:
        return None, "absent"
    end = raw.find(ENVELOPE_END, begin)
    if end < 0:
        return None, "malformed"
    payload = raw[begin + len(ENVELOPE_BEGIN):end].strip()
    try:
        draft = json.loads(payload)
    except Exception:
        return None, "malformed"
    if not isinstance(draft, dict):
        return None, "malformed"
    return draft, ""


# --- deterministic editorial parsing ---------------------------------------------------

_ARTICLE_MODE = re.compile(r"\b(?:x|twitter)\s+articles?\b", re.IGNORECASE)
_LONG_POST_MODE = re.compile(r"\b(?:premium\s+)?long[ -]?posts?\b", re.IGNORECASE)
_THREAD_MODE = re.compile(
    r"\b(?:into|as)\s+(?:a\s+)?threads?\b|\bthreads?\s+this\b", re.IGNORECASE
)
_POST_MODE = re.compile(
    r"\b(?:write|draft|compose|create|condense|compress|squeeze|shrink|fit)\b"
    r"[^.?!]{0,60}?\b(?:single\s+|one\s+)?(?:tweets?|(?:x|twitter)\s+posts?)\b",
    re.IGNORECASE,
)


def requested_mode(user_text: str) -> str:
    """The output mode the OPERATOR's own words request, or "" when they name none.

    Deterministic parse of the user text only — the model's choice never overrides it.
    """
    text = str(user_text or "")
    if _ARTICLE_MODE.search(text):
        return "article"
    if _LONG_POST_MODE.search(text):
        return "long_post"
    if _THREAD_MODE.search(text):
        return "thread"
    if _POST_MODE.search(text):
        return "post"
    return ""


_URL_TOKEN = "\ue000"  # sentinel replacing URLs during number/quote extraction

_NUMBER_PATTERN = re.compile(
    r"(?<![\w/])(?:\d+(?:[.,]\d+)*%|\d+(?:[.,]\d+)*(?:s|m|h|k|x)?\b)(?!\s*[.)]\s*[A-Za-z])",
    re.IGNORECASE,
)
# An enumeration prefix ("1. First point") is layout, not a claim.
_ENUMERATION_PATTERN = re.compile(r"^\d{1,2}\.\s")
_QUOTE_PATTERNS = (
    re.compile(r"\"([^\"]{4,240}?)\""),
    re.compile("\u201c([^\u201d]{4,240}?)\u201d"),
    re.compile("\u2018([^\u2019]{4,240}?)\u2019"),
)


def _normalize(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def extract_claims(text: str) -> list[tuple[str, str]]:
    """(kind, text) claims found in one piece of editorial text: urls, numbers, quotes."""
    raw = str(text or "")
    urls = policy.find_urls(raw)
    scrubbed = raw
    for url in urls:
        scrubbed = scrubbed.replace(url, _URL_TOKEN)
    claims: list[tuple[str, str]] = [("url", url) for url in urls]
    for match in _NUMBER_PATTERN.finditer(scrubbed):
        if _ENUMERATION_PATTERN.match(scrubbed[match.start():]):
            continue
        claims.append(("number", match.group(0)))
    for pattern in _QUOTE_PATTERNS:
        for match in pattern.finditer(scrubbed):
            claims.append(("quote", match.group(1)))
    return claims


def _grounded(kind: str, claim: str, corpus: str) -> bool:
    """A claim is grounded when it appears in the operator's own supplied material."""
    normalized_corpus = _normalize(corpus)
    if not normalized_corpus:
        return False
    claim_normalized = _normalize(claim)
    if kind == "url":
        bare = claim_normalized.rstrip("/")
        return bare in normalized_corpus
    if kind == "number":
        variants = {
            claim_normalized,
            claim_normalized.replace(",", ""),
            claim_normalized.rstrip("%smhkx"),
        }
        corpus_tokens = set(normalized_corpus.replace(",", " ").split())
        for variant in variants:
            if not variant:
                continue
            if variant in corpus_tokens:
                return True
            # unit-bearing forms ground on their bare number ("94" inside "94s to 12s")
            if re.search(rf"(?<![\w.]) {re.escape(variant)}", f" {normalized_corpus} "):
                return True
            compact = normalized_corpus.replace(",", "")
            if variant.replace(",", "") in compact and len(variant.rstrip("%smhkx")) >= 1:
                bare_variant = variant.replace(",", "").rstrip("%smhkx")
                if bare_variant and re.search(
                    rf"(?<![\d.]){re.escape(bare_variant)}(?![\d.])", compact
                ):
                    return True
        return False
    if kind == "quote":
        return claim_normalized in normalized_corpus
    return False


# --- AI sludge -------------------------------------------------------------------------

# The named offenders the editorial law calls out. A phrase the OPERATOR supplied verbatim
# in their own material is deliberate, not sludge.
_SLUDGE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("in today's fast-paced world",
     re.compile(r"in today'?s (?:fast[- ]paced|busy|modern|digital) world", re.I)),
    ("game changer", re.compile(r"game[- ]?chang(?:er|ing)", re.I)),
    ("delve", re.compile(r"\bdelv(?:e|es|ed|ing)\b", re.I)),
    ("unlock", re.compile(r"\bunlock(?:s|ed|ing)?\b", re.I)),
    ("revolutionary", re.compile(r"\brevolutionar(?:y|ily)\b", re.I)),
    ("empty superlative",
     re.compile(r"\b(?:greatest|best|most amazing)\s+(?:ever|of all time|in the world)\b", re.I)),
    ("repetitive conclusion",
     re.compile(r"\bin conclusion\b|\bto sum(?:marize|mary) up\b|\bwrap(?:ping)? it up\b", re.I)),
    ("it's not X, it's Y",
     re.compile(r"\bit'?s not (?:just|only|about)\b[^.?!]{0,80}\b(?:it'?s|but rather)\b", re.I)),
    ("let's dive in", re.compile(r"\blet'?s dive\b|\bbuckle up\b", re.I)),
    ("hype announcement",
     re.compile(r"\b(?:thrilled|excited|proud)\s+to\s+announce\b|\bwe'?re (?:so |really )?excited\b", re.I)),
    ("elevate your",
     re.compile(r"\belevate\s+your\b|\btake\s+it\s+to\s+the\s+next\s+level\b", re.I)),
    ("in the realm of",
     re.compile(r"\bin the (?:realm|landscape|world) of\b", re.I)),
    ("tapestry", re.compile(r"\btapestry\b", re.I)),
    ("testament to", re.compile(r"\ba testament to\b", re.I)),
)


def sludge_hits(text: str, corpus: str = "") -> list[str]:
    """The sludge labels found in ``text``. A phrase present verbatim in the operator's own
    material is deliberate and not reported."""
    raw = str(text or "")
    corpus_normalized = _normalize(corpus)
    hits: list[str] = []
    for label, pattern in _SLUDGE_RULES:
        for match in pattern.finditer(raw):
            phrase = _normalize(match.group(0))
            if phrase and corpus_normalized and phrase in corpus_normalized:
                continue  # the operator chose these words
            hits.append(label)
            break
    return hits


# --- privacy ----------------------------------------------------------------------------

_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:/Users?/[\w.\-]+/|/home/[\w.\-]+/|[A-Za-z]:\\Users\\[\w.\-]+\\)"
)


def _privacy_hits(text: str) -> list[str]:
    from core.privacy_guard import text_privacy_risks
    from core.secret_redaction import contains_secret

    hits: list[str] = []
    if _ABSOLUTE_PATH_PATTERN.search(str(text or "")):
        hits.append("local_path")
    try:
        if contains_secret(str(text or "")):
            hits.append("secret_material")
    except Exception:
        hits.append("secret_material")
    try:
        if text_privacy_risks(str(text or "")):
            hits.append("privacy_risk")
    except Exception:
        hits.append("privacy_risk")
    return hits


# --- the contract builder ---------------------------------------------------------------


def _clean_list(values: Any) -> tuple[str, ...]:
    return tuple(str(v).strip() for v in (values or []) if str(v or "").strip())


def _scan_targets(draft: dict[str, Any]) -> list[tuple[str, str]]:
    """Every editorial string that will be rendered or shown, for claim/sludge/privacy scans."""
    targets: list[tuple[str, str]] = []
    hook = str(draft.get("hook") or "").strip()
    if hook:
        targets.append(("hook", hook))
    for index, post in enumerate(_clean_list(draft.get("posts")), start=1):
        targets.append((f"post {index}", post))
    for index, section in enumerate(_clean_list(draft.get("sections")), start=1):
        targets.append((f"section {index}", section))
    for index, note in enumerate(_clean_list(draft.get("media_notes")), start=1):
        targets.append((f"media {index}", note))
    for index, link in enumerate(_clean_list(draft.get("links")), start=1):
        targets.append((f"link {index}", link))
    cta = str(draft.get("cta") or "").strip()
    if cta:
        targets.append(("cta", cta))
    return targets


def _item_label(mode: str, index: int) -> str:
    return f"post {index}" if mode in ("post", "long_post", "thread") else f"section {index}"


def compose_xdraft(
    draft: dict[str, Any],
    *,
    user_text: str,
    source_context: dict[str, Any] | None = None,
    voice_profile_version: int | None = None,
) -> XDrafT:
    """Build the full typed contract from a model-proposed draft. Engine-owned fields are
    always computed here; anything the model supplied for them is ignored."""
    corpus = str(user_text or "")
    findings: list[ValidationFinding] = []
    ledger: list[ClaimEntry] = []
    weighted_by_item: dict[str, int] = {}

    mode = str(draft.get("mode") or "").strip()
    if mode not in policy.MODES:
        findings.append(
            ValidationFinding(
                "invalid_mode",
                f"draft mode {mode!r} is not one of {list(policy.MODES)}",
            )
        )
        mode = mode or "post"
    requested = requested_mode(corpus)
    if requested and requested != mode:
        findings.append(
            ValidationFinding(
                "requested_mode_mismatch",
                f"the operator requested a {policy.MODE_LABELS.get(requested, requested)} "
                f"but the draft is a {policy.MODE_LABELS.get(mode, mode)}; the operator's "
                "requested format wins",
                where="mode",
            )
        )

    posts = _clean_list(draft.get("posts"))
    sections = _clean_list(draft.get("sections"))
    if mode in ("post", "long_post") and not posts:
        posts = sections
    if mode == "article" and not sections:
        sections = posts
    links = _clean_list(draft.get("links"))

    if not posts and not sections and not str(draft.get("hook") or "").strip():
        findings.append(
            ValidationFinding("empty_draft", "the draft carries no editorial content")
        )

    # --- claims and grounding -----------------------------------------------------------
    ledger.extend(
        ClaimEntry(text=opinion, kind="opinion", status="opinion")
        for opinion in _clean_list(draft.get("opinions"))
    )
    for where, text in _scan_targets(draft):
        for kind, claim in extract_claims(text):
            status = "grounded" if _grounded(kind, claim, corpus) else "ungrounded"
            ledger.append(ClaimEntry(text=claim, kind=kind, status=status, where=where))
            if status == "ungrounded":
                findings.append(
                    ValidationFinding(
                        f"ungrounded_{kind}",
                        f"unsupported {kind} in {where}: {claim!r} does not appear in the "
                        "operator's supplied material — remove it or source it",
                        where=where,
                    )
                )

    # --- platform truth -------------------------------------------------------------------
    limit, certainty = policy.length_limit_with_certainty(mode)
    if mode in ("post", "long_post"):
        if not posts:
            findings.append(
                ValidationFinding("empty_draft", f"a {mode} needs body text", where="post 1")
            )
        for index, post in enumerate(posts, start=1):
            weighted = weighted_length(post)
            weighted_by_item[f"post {index}"] = weighted
            if certainty == "official" and limit is not None and weighted > limit:
                findings.append(
                    ValidationFinding(
                        "over_limit",
                        f"post {index} is {weighted} weighted characters; "
                        f"the {policy.MODE_LABELS[mode]} limit is {limit}",
                        where=f"post {index}",
                    )
                )
            elif (
                mode == "long_post"
                and limit is not None
                and policy.STANDARD_POST_LIMIT < weighted <= limit
            ):
                findings.append(
                    ValidationFinding(
                        "premium_eligibility_unverified",
                        policy.PREMIUM_LONG_POST_NOTE,
                        severity="warning",
                        where=f"post {index}",
                    )
                )
    elif mode == "thread":
        if not posts:
            findings.append(
                ValidationFinding("thread_empty", "a thread needs at least one post")
            )
        for index, post in enumerate(posts, start=1):
            weighted = weighted_length(post)
            weighted_by_item[f"post {index}"] = weighted
            if limit is not None and weighted > limit:
                findings.append(
                    ValidationFinding(
                        "over_limit",
                        f"post {index} is {weighted} weighted characters; each thread item "
                        f"is a post limited to {limit}",
                        where=f"post {index}",
                    )
                )
    elif mode == "article":
        if not str(draft.get("title") or "").strip():
            findings.append(
                ValidationFinding(
                    "article_title_missing", "an X Article needs a title", where="title"
                )
            )
        findings.append(
            ValidationFinding(
                "validation_unknown",
                policy.ARTICLE_UNKNOWN_NOTE,
                severity="unknown",
                where="article length",
            )
        )
        findings.append(
            ValidationFinding(
                "premium_eligibility_unverified",
                policy.ARTICLE_ELIGIBILITY_NOTE,
                severity="warning",
                where="article eligibility",
            )
        )
        for index, section in enumerate(sections, start=1):
            weighted_by_item[f"section {index}"] = weighted_length(section)

    # --- sludge and privacy -----------------------------------------------------------------
    for where, text in _scan_targets(draft):
        for label in sludge_hits(text, corpus):
            findings.append(
                ValidationFinding(
                    "ai_sludge",
                    f"AI sludge in {where}: {label!r} — rewrite in concrete language, or "
                    "supply the phrase yourself to keep it deliberately",
                    where=where,
                )
            )
    rendered_preview = "\n".join(text for _where, text in _scan_targets(draft))
    for hit in _privacy_hits(rendered_preview):
        findings.append(
            ValidationFinding(
                "privacy_leak",
                f"privacy risk in the draft ({hit}): local paths, credentials and machine "
                "identity must never appear in copy",
            )
        )

    xdraft = XDrafT(
        mode=mode,
        audience=str(draft.get("audience") or ""),
        purpose=str(draft.get("purpose") or ""),
        thesis=str(draft.get("thesis") or ""),
        voice_profile_version=voice_profile_version,
        title=str(draft.get("title") or ""),
        hook=str(draft.get("hook") or ""),
        posts=posts,
        sections=sections,
        media_notes=_clean_list(draft.get("media_notes")),
        links=links,
        claim_ledger=tuple(ledger),
        cta=str(draft.get("cta") or ""),
        platform_policy_version=policy.PLATFORM_POLICY_VERSION,
        validation_results=tuple(findings),
    )
    return dataclasses_replace(xdraft, copy_ready_text=_render(xdraft))


def _render(xdraft: XDrafT) -> str:
    """The exact copy/paste text. Clean, deliberate, no commentary."""
    if xdraft.mode == "article":
        parts = [xdraft.title] if xdraft.title else []
        if xdraft.hook:
            parts.append(xdraft.hook)
        parts.extend(xdraft.sections)
        return "\n\n".join(part for part in parts if part)
    if xdraft.mode == "thread":
        return "\n\n".join(f"{index}/ {post}" for index, post in enumerate(xdraft.posts, start=1))
    return "\n\n".join(xdraft.posts)


def copy_ready_text(xdraft: XDrafT) -> str:
    return xdraft.copy_ready_text


def findings_of(xdraft: XDrafT) -> tuple[ValidationFinding, ...]:
    return xdraft.validation_results


def is_clean(xdraft: XDrafT) -> bool:
    return not any(f.severity == "failure" for f in xdraft.validation_results)


# --- response-edge entry point ------------------------------------------------------------


def _findings_block(findings: list[ValidationFinding]) -> str:
    lines = [_WITHHELD_HEADER]
    for finding in findings:
        marker = {"failure": "-", "warning": "~", "unknown": "?"}.get(finding.severity, "-")
        lines.append(
            f"{marker} {finding.code}: {finding.message}"
            + (f" [{finding.where}]" if finding.where else "")
        )
    return "\n".join(lines)


def apply_x_editorial_output(
    provider_text: str,
    *,
    user_text: str,
    state: dict[str, Any] | None,
) -> XEditorialApplication:
    """The single response-edge entry: parse, validate, render — or withhold with typed
    findings. ``state`` is the turn metadata stamped at prompt build (scope, voice)."""
    state = dict(state or {})
    draft, status = parse_envelope(provider_text)
    if status == "absent":
        return XEditorialApplication(
            text=provider_text,
            changed=False,
            compliant=True,
            record={"envelope_found": False, "skill": X_EDITORIAL_SKILL_ID},
        )

    if status == "malformed":
        finding = ValidationFinding(
            "envelope_malformed",
            "the draft envelope is present but malformed; the answer cannot be validated "
            "into exact copy",
        )
        return XEditorialApplication(
            text=f"{provider_text}\n\n{_findings_block([finding])}",
            changed=True,
            compliant=False,
            record={
                "envelope_found": True,
                "clean": False,
                "copy_ready_served": False,
                "findings": [{"code": finding.code, "severity": "failure",
                              "message": finding.message, "where": ""}],
                "skill": X_EDITORIAL_SKILL_ID,
                "policy_version": policy.PLATFORM_POLICY_VERSION,
            },
        )

    voice_version = state.get("voice_profile_version")
    xdraft = compose_xdraft(
        draft,
        user_text=user_text,
        source_context=state.get("source_context") or {},
        voice_profile_version=int(voice_version) if voice_version else None,
    )
    clean = is_clean(xdraft)
    record = {
        "envelope_found": True,
        "skill": X_EDITORIAL_SKILL_ID,
        "policy_version": xdraft.platform_policy_version,
        "mode": xdraft.mode,
        "requested_mode": requested_mode(user_text),
        "clean": clean,
        "copy_ready_served": clean,
        "voice_profile_version": xdraft.voice_profile_version,
        "scope": str(state.get("scope") or "global"),
        "findings": [
            {"code": f.code, "severity": f.severity, "message": f.message, "where": f.where}
            for f in xdraft.validation_results
        ],
        "claims": [
            {"kind": c.kind, "status": c.status, "where": c.where, "text": c.text[:80]}
            for c in xdraft.claim_ledger
        ],
    }
    if clean:
        return XEditorialApplication(
            text=xdraft.copy_ready_text,
            changed=True,
            compliant=True,
            record=record,
        )
    return XEditorialApplication(
        text=f"{provider_text}\n\n{_findings_block(list(xdraft.validation_results))}",
        changed=True,
        compliant=False,
        record=record,
    )


# --- prompt-build state ---------------------------------------------------------------------


def x_editorial_turn_state(
    user_text: str, *, source_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The turn metadata stamped into the request when the skill is selected: what the
    response edge needs (scope, voice, requested format), nothing more."""
    from core import x_voice_profile
    from core.tool_demand_signals import resolve_demand_signals

    scope = x_voice_profile.turn_scope(source_context)
    voice_requested = (
        x_voice_profile.VOICE_INTENT
        in resolve_demand_signals(str(user_text or "")).explicit_intents
    )
    version = x_voice_profile.voice_profile_version(scope) if voice_requested else None
    return {
        "skill": X_EDITORIAL_SKILL_ID,
        "policy_version": policy.PLATFORM_POLICY_VERSION,
        "requested_mode": requested_mode(user_text),
        "voice_requested": bool(voice_requested),
        "voice_profile_version": version,
        "scope": scope,
    }


def skill_selected(skill_rows: Any) -> bool:
    """Whether the canonical selection rows name this skill (provenance-driven, never text)."""
    return any(
        str((row or {}).get("name") or "") == X_EDITORIAL_SKILL_ID for row in skill_rows or ()
    )


__all__ = [
    "ENVELOPE_BEGIN",
    "ENVELOPE_END",
    "X_EDITORIAL_SKILL_ID",
    "XDrafT",
    "XEditorialApplication",
    "apply_x_editorial_output",
    "compose_xdraft",
    "copy_ready_text",
    "envelope_for",
    "extract_claims",
    "findings_of",
    "is_clean",
    "parse_envelope",
    "requested_mode",
    "skill_selected",
    "sludge_hits",
    "x_editorial_turn_state",
]
