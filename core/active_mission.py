"""Typed latest-wins active-mission slots (Codex Phase 2 F2/F3).

The dialogue goal is a single free-text field that the latest user message
overwrites, so mission constraints (spend cap, active .null domain, target OS,
wallet prefix, deadline, allowed network, repo) are lost after distraction turns.

This module persists those constraints as TYPED slots with exact values, explicit
latest-wins supersession, and source provenance, so they survive long sessions and
are injected back into context. Values are captured verbatim from the raw user
text (the input normalizer now preserves exact literals — see input_normalizer).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ActiveMissionSlot:
    slot_name: str
    value_raw: str
    confidence: float = 0.8


# ── extraction ──────────────────────────────────────────────────────────────
# Each entry: (slot_name, compiled regex, value group index). Patterns capture the
# EXACT literal from the raw text. Order matters only for readability; every slot is
# scanned independently and the last match of each slot in the text wins.
_SLOT_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("spend_cap", re.compile(r"(?:spend[\s-]*cap|\bcap\b|budget|\bmax\b|spend)\D{0,25}?(\d+(?:\.\d+)?\s*(?:SOL|USDC|sol|usdc))", re.IGNORECASE), 1),
    ("active_domain", re.compile(r"\b([a-z0-9][a-z0-9_.\-]*\.null)\b", re.IGNORECASE), 1),
    ("wallet_prefix", re.compile(r"wallet\s+(?:prefix|address|starting|start)?\s*(?:is\s+|starting\s+)?([1-9A-HJ-NP-Za-km-z]{4,12})\b", re.IGNORECASE), 1),
    ("deadline", re.compile(r"(?:deadline|due|by|window opens)\D{0,20}?(\d{4}-\d{2}-\d{2}(?:\s*(?:at\s*)?\d{1,2}:\d{2})?)", re.IGNORECASE), 1),
    ("target_os", re.compile(r"\b(Windows(?:\s*\d+)?|macOS|Mac\b|Linux|Ubuntu|Debian)\b(?:\s*(?:only|11|10)?)", re.IGNORECASE), 1),
    ("allowed_network", re.compile(r"\b(devnet|mainnet|testnet)\b", re.IGNORECASE), 1),
    ("active_repo", re.compile(r"\brepo(?:sitory)?\s+(?:is\s+)?([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)?)", re.IGNORECASE), 1),
    ("maturity_label", re.compile(r"\b(alpha|beta|dogfood|internal dogfood|controlled public alpha)\b", re.IGNORECASE), 1),
    ("style_preference", re.compile(r"(?:answer|reply|respond|keep it|output|in)\s+(?:me\s+)?(?:in\s+)?(?:a\s+|short\s+)?(concise|brief|terse|telegram|formal|casual|blunt|direct|technical|plain)\b(?:\s+(?:dev\s+)?style)?", re.IGNORECASE), 1),
    # Absolute filesystem path (Windows or POSIX), latest-wins so an updated path supersedes the old one.
    ("active_path", re.compile(r"\bpath\s+(?:to\s+|is\s+)?([A-Za-z]:\\[^\s,;]+|/[A-Za-z0-9._\-/]{2,})", re.IGNORECASE), 1),
    # A copy/terminology list ("use A, B, C, ...") that pins the exact terms to use. Gated on a
    # .null term so it only fires for the ecosystem copy rule, not ordinary "use X, Y" sentences.
    ("required_terms", re.compile(r"\buse\s+([^;\n]*\.null[^;\n]*)", re.IGNORECASE), 1),
    # Auto-spend policy as a TYPED slot (not a free directive) so it renders deterministically and
    # is normalized to a canonical phrase. Scanned before the generic directive loop, which is then
    # guarded so the same phrase does not also produce a "directive:never auto-spend" duplicate.
    ("auto_spend_rule", re.compile(r"\b(never\s+auto[\s-]*spend(?:ing)?|do\s+not\s+auto[\s-]*spend|no\s+auto[\s-]*spend|auto[\s-]*spend\s+disabled|require\s+approval\s+before\s+(?:any\s+)?spend)\b", re.IGNORECASE), 1),
]

# Free-text ACTION directives that must survive a long session (e.g. "never auto-spend",
# "ask before publishing"). Term-avoidance directives ("do not mention Web3", "never say ...")
# are deliberately NOT captured — rendering them would leak the very term they forbid.
_DIRECTIVE_RE = re.compile(
    r"\b(never\s+(?!say\b|mention\b|use\b)[a-z][\w-]*(?:\s+[a-z][\w-]*)?|ask\s+before\s+[a-z]+)",
    re.IGNORECASE,
)
# A value preceded by a negation ("... only, not beta") is the rejected option, not the choice.
_NEGATED_BEFORE_RE = re.compile(r"(?:\bnot\b|\bnever\b|\bno\b)\s*$", re.IGNORECASE)

# A forbidden / avoid-this-term rule ("do not mention Web3"). Captured as a TYPED slot
# "forbidden_term:<lower>" that is NEVER rendered or injected — it exists only so the renderer can
# scrub any clause that would otherwise echo the term. A short stopword set prevents "do not mention
# it/them/anything" from capturing a filler word and dropping a legitimate clause.
_FORBIDDEN_RE = re.compile(
    r"\b(?:do\s+not|don'?t|never|avoid)\s+(?P<verb>mention|say|use|reference|write|cite)\s+"
    r"(?P<lexical>(?:the\s+)?(?:word|term|phrase)\s+)?(?P<quote>[\"'“‘`])?"
    r"(?P<term>[A-Za-z0-9][\w.+/\-]*)",
    re.IGNORECASE,
)
_FORBIDDEN_STOPWORDS = {"it", "the", "that", "this", "them", "they", "word", "term", "phrase", "anything", "any"}


def _normalize_auto_spend_rule(value: str) -> str:
    """Map every negative auto-spend phrasing to the canonical 'never auto-spend'; keep an
    approval-gated rule verbatim so it still reads correctly."""
    if value.lower().lstrip().startswith("require"):
        return " ".join(value.split())
    return "never auto-spend"


def _forbidden_terms(text: str) -> set[str]:
    from core.turn_prohibitions import _strip_quoted_spans

    visible = _strip_quoted_spans(text)
    terms = set()
    for match in _FORBIDDEN_RE.finditer(text):
        # Quoted instructions are data. A quoted term may still be the object of
        # an unquoted instruction: "don't use the word 'tools'".
        if not visible[match.start():match.start() + 2].strip():
            continue
        if match.group('verb').lower() in {'use', 'write'} and not (match.group('lexical') or match.group('quote')):
            continue  # "don't use tools" restricts actions, not the vocabulary of an answer.
        term = match.group('term').strip().rstrip('.,;')
        if term and term.lower() not in _FORBIDDEN_STOPWORDS:
            terms.add(term)
    return terms


def _obsolete_forbidden_slot(name: str, value: str, source: str) -> bool:
    if not name.startswith('forbidden_term:') or not source:
        return False
    # Re-interpret only a rule whose original phrase survives in the stored
    # provenance. Never discard a genuine or provenance-less imported rule.
    matched = any(m.group('term').rstrip('.,;').casefold() == value.casefold()
                  for m in _FORBIDDEN_RE.finditer(source))
    return matched and value.casefold() not in {t.casefold() for t in _forbidden_terms(source)}


def extract_active_mission_slots(text: str) -> list[ActiveMissionSlot]:
    """Extract typed mission slots from raw user text. The LAST occurrence of each
    slot in the text wins (a message that updates a value supersedes an earlier one
    in the same message)."""
    raw = str(text or "")
    slots: dict[str, ActiveMissionSlot] = {}
    for slot_name, pattern, group in _SLOT_PATTERNS:
        last = None
        for match in pattern.finditer(raw):
            # For label-style choices, skip a negated option ("alpha only, not beta" -> alpha).
            if slot_name == "maturity_label" and _NEGATED_BEFORE_RE.search(raw[max(0, match.start() - 6):match.start()]):
                continue
            last = match
        if last is not None:
            value = last.group(group).strip().rstrip(".,;")
            if slot_name == "auto_spend_rule":
                value = _normalize_auto_spend_rule(value)
            if slot_name == "wallet_prefix" and value.lower() in {"prefix", "wallet", "address", "starting", "start"}:
                continue
            if value:
                slots[slot_name] = ActiveMissionSlot(slot_name=slot_name, value_raw=value)
    # Forbidden-term rules ("do not mention Web3") captured as typed, never-rendered slots so the
    # renderer can scrub the term; accumulate so multiple forbidden terms coexist.
    for term in _forbidden_terms(raw):
        slots[f"forbidden_term:{term.lower()}"] = ActiveMissionSlot(
            slot_name=f"forbidden_term:{term.lower()}", value_raw=term
        )
    # Multi-value action directives: each distinct directive is its own slot (slot_name
    # "directive:<text>") so they accumulate instead of superseding one another. Normalized to
    # lower case so a restatement at sentence start ("Ask before publishing") does not churn or
    # change the stored phrase. Skip an auto-spend directive — it is now the typed auto_spend_rule
    # slot above, and capturing it here too would produce a duplicate.
    for match in _DIRECTIVE_RE.finditer(raw):
        directive = " ".join(match.group(1).split()).strip().rstrip(".,;").lower()
        if directive and not directive.startswith("never auto"):
            slots[f"directive:{directive}"] = ActiveMissionSlot(
                slot_name=f"directive:{directive}", value_raw=directive
            )
    return list(slots.values())


# ── storage (latest-wins) ────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS active_mission_slots (
  slot_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  slot_name TEXT NOT NULL,
  slot_value_raw TEXT NOT NULL,
  source_turn_id TEXT,
  source_text TEXT NOT NULL DEFAULT '',
  slot_confidence REAL NOT NULL DEFAULT 0.0,
  scope TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL,
  superseded_at TEXT,
  superseded_by_turn_id TEXT
)
"""
_IDX = "CREATE INDEX IF NOT EXISTS idx_active_mission_slots_current ON active_mission_slots(session_id, slot_name, superseded_at)"


def _ensure(conn: Any) -> None:
    conn.execute(_DDL)
    conn.execute(_IDX)


def upsert_active_mission_slots(
    session_id: str,
    slots: list[ActiveMissionSlot],
    *,
    turn_id: str | None = None,
    source_text: str = "",
) -> int:
    """Persist slots with latest-wins semantics: for each incoming slot, mark any
    existing active slot of the same name superseded, then insert the new value.
    Returns the number of slots written."""
    sid = str(session_id or "").strip()
    if not sid or not slots:
        return 0
    now = _utcnow()
    conn = get_connection()
    try:
        _ensure(conn)
        written = 0
        for slot in slots:
            existing = conn.execute(
                "SELECT slot_value_raw FROM active_mission_slots "
                "WHERE session_id = ? AND slot_name = ? AND superseded_at IS NULL",
                (sid, slot.slot_name),
            ).fetchall()
            # If the value is unchanged, keep the existing active row (no churn).
            if existing and all(str(row[0]) == slot.value_raw for row in existing):
                continue
            conn.execute(
                "UPDATE active_mission_slots SET superseded_at = ?, superseded_by_turn_id = ? "
                "WHERE session_id = ? AND slot_name = ? AND superseded_at IS NULL",
                (now, turn_id, sid, slot.slot_name),
            )
            conn.execute(
                "INSERT INTO active_mission_slots "
                "(slot_id, session_id, slot_name, slot_value_raw, source_turn_id, source_text, slot_confidence, scope, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'private', ?)",
                (uuid.uuid4().hex, sid, slot.slot_name, slot.value_raw, turn_id, source_text[:500], float(slot.confidence), now),
            )
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()


def current_active_mission_slots(session_id: str) -> list[dict[str, Any]]:
    """Return the current (non-superseded) mission slots for a session, newest first."""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    conn = get_connection()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT slot_name, slot_value_raw, slot_confidence, created_at, source_turn_id, source_text "
            "FROM active_mission_slots WHERE session_id = ? AND superseded_at IS NULL "
            "ORDER BY created_at DESC",
            (sid,),
        ).fetchall()
        return [
            {"slot_name": str(r[0]), "value": str(r[1]), "confidence": float(r[2]),
             "created_at": str(r[3]), "source_turn_id": r[4]}
            for r in rows if not _obsolete_forbidden_slot(str(r[0]), str(r[1]), str(r[5] or ""))
        ]
    finally:
        conn.close()


_SLOT_LABELS = {
    "spend_cap": "Spend cap", "active_domain": "Active .null domain", "wallet_prefix": "Wallet prefix",
    "deadline": "Deadline", "target_os": "Target OS", "allowed_network": "Allowed network",
    "active_repo": "Active repo", "maturity_label": "Maturity label", "active_path": "Active path",
    "required_terms": "Required terms", "style_preference": "Style", "auto_spend_rule": "Auto-spend rule",
}


def _slot_label(slot_name: str) -> str:
    if slot_name.startswith("directive:"):
        return "Rule"
    return _SLOT_LABELS.get(slot_name, slot_name)


def render_active_slots(slots: list[dict[str, Any]]) -> str:
    """Render current mission slots as a compact, exact context block."""
    if not slots:
        return ""
    lines = ["Active mission (current, exact values — latest instruction wins):"]
    for slot in slots:
        # forbidden_term slots exist only to drive scrubbing — never echo the forbidden term.
        if str(slot.get("slot_name") or "").startswith("forbidden_term:"):
            continue
        lines.append(f"- {_slot_label(slot['slot_name'])}: {slot['value']}")
    return "\n".join(lines)


# Fixed clause order for a deterministic full-mission answer. forbidden_term/style/maturity slots
# are intentionally excluded from the rendered mission answer.
_MISSION_RENDER_ORDER: list[tuple[str, str]] = [
    ("spend_cap", "spend cap {v}"),
    ("active_domain", "active domain {v}"),
    ("wallet_prefix", "wallet prefix {v}"),
    ("target_os", "target OS {v}"),
    ("allowed_network", "network {v}"),
    ("deadline", "deadline {v}"),
    ("active_repo", "repo {v}"),
    ("active_path", "path {v}"),
    ("required_terms", "required terms {v}"),
    ("auto_spend_rule", "the rule is {v}"),
]

_FOCUS_SLOT: dict[str, tuple[str, str]] = {
    "cap": ("spend_cap", "Your spend cap is {v}."),
    "domain": ("active_domain", "Your active .null domain is {v}."),
    "wallet": ("wallet_prefix", "Your wallet prefix is {v}."),
    "os": ("target_os", "Your target OS is {v}."),
    "auto_spend": ("auto_spend_rule", "The auto-spend rule is {v}."),
}


def _full_mission_has_enough_signal(by_name: dict[str, str], directives: list[str]) -> bool:
    signal_names = [
        name
        for name, _template in _MISSION_RENDER_ORDER
        if name in by_name and str(by_name.get(name) or "").strip()
    ]
    if len(signal_names) + len(directives) >= 2:
        return True
    return "spend_cap" in by_name and any(
        name in by_name for name in ("active_domain", "wallet_prefix", "target_os", "auto_spend_rule")
    )


def render_mission_answer(
    slots: list[dict[str, Any]],
    *,
    honor_forbidden: bool = True,
    focus: str | None = None,
) -> str:
    """Deterministically render a mission answer from the current typed slots — never from
    free-form model recall, so cross-session continuity can never contaminate it. Returns "" when
    there is nothing to render (the caller then falls through to the normal path). Any clause that
    would echo a forbidden term is dropped. `focus` renders a single field; None renders the full
    mission sentence."""
    by_name: dict[str, str] = {}
    directives: list[str] = []
    forbidden: set[str] = set()
    for slot in slots:
        name = str(slot.get("slot_name") or "")
        value = str(slot.get("value") if slot.get("value") is not None else slot.get("value_raw") or "").strip()
        if not value:
            continue
        if name.startswith("forbidden_term:"):
            if honor_forbidden:
                forbidden.add(value.lower())
            continue
        if name.startswith("directive:"):
            directives.append(value)
            continue
        by_name.setdefault(name, value)

    def _clean(text: str) -> str:
        # Drop a clause that would echo a forbidden term (belt-and-suspenders; forbidden terms are
        # already excluded from by_name/directives by construction).
        if honor_forbidden and any(term and term in text.lower() for term in forbidden):
            return ""
        return text

    if focus is not None:
        spec = _FOCUS_SLOT.get(focus)
        if spec is None:
            return ""
        name, template = spec
        value = by_name.get(name)
        if not value:
            return ""
        return _clean(template.format(v=value))

    clauses: list[str] = []
    for name, template in _MISSION_RENDER_ORDER:
        value = by_name.get(name)
        if value:
            clause = _clean(template.format(v=value))
            if clause:
                clauses.append(clause)
    for directive in directives:
        clause = _clean(f"and {directive}")
        if clause:
            clauses.append(clause)
    if not clauses or not _full_mission_has_enough_signal(by_name, directives):
        return ""
    return "Current mission: " + ", ".join(clauses) + "."


def capture_active_mission_slots(session_id: str, user_text: str, *, turn_id: str | None = None) -> int:
    """Convenience: extract from user_text and upsert. Called from the input path."""
    slots = extract_active_mission_slots(user_text)
    if not slots:
        return 0
    return upsert_active_mission_slots(session_id, slots, turn_id=turn_id, source_text=str(user_text or ""))
