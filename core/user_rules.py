"""User rules: what the operator tells VOOL never (or always) to do, in plain words, per scope.

A user says "add this to global rules: never push the words xxx or ffff to git" or "add to chat
rules: never fff and ggg". This module is the ONE owner of those rules:

* **scopes** -- ``global`` (everywhere), ``project`` (every chat bound to that project, and git
  actions inside that project's folder) and ``chat`` (that chat only). A chat's project is the
  server-owned binding (chat namespace, then persisted session meta), never a client claim;
* **store** -- the runtime SQLite database, table ``user_rules``. Rules survive restarts. Removal
  is a tombstone (``status='removed'`` with the time and turn), so what was removed stays
  answerable and nothing is silently rewritten;
* **commands** -- add, list and remove are read deterministically from the chat turn
  (:func:`handle_rule_command`) and answered from the store, so "saved" is only said after the
  write happened;
* **enforcement** -- rules reach the model as one stable system block (:func:`rules_prompt_block`);
  banned words are masked out of replies (:func:`mask_reply_terms`); and every git door that
  creates or publishes history asks :func:`git_outbound_refusal` first, so a commit, tag or push
  carrying a banned word is refused before git runs.

Bans add up across scopes: a chat rule can never lift a global ban (remove the global rule for
that). For instructions that are not bans, the narrower scope wins (chat, then project, then
global), the same order the Operator Profile resolves.

Narrow-only by construction: a rule can refuse an effect; nothing here grants or widens any
permission, and no caller treats "no rule matched" as approval.
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from storage.db import get_connection

__all__ = [
    "KIND_EXCEPTION",
    "KIND_RULE",
    "RULE_NOTE_RE",
    "SCOPES",
    "TARGET_GIT",
    "TARGET_REPLY",
    "GitRuleVerdict",
    "Lift",
    "ReplyReview",
    "Resolution",
    "RuleRefusal",
    "UserRule",
    "active_rules",
    "add_exception",
    "add_rule",
    "chat_project_id",
    "extract_terms",
    "find_banned_terms",
    "git_outbound_check",
    "git_outbound_refusal",
    "handle_rule_command",
    "list_rules_text",
    "mask_reply_terms",
    "observe_rules_turn",
    "override_note",
    "parse_rule_command",
    "project_ids_for_path",
    "remove_rules",
    "resolve_rules",
    "review_reply",
    "rules_command_owns_forget",
    "rules_prompt_block",
    "sandbox_git_refusal",
    "scope_keys_for",
    "turn_answered_rules_command",
]

SCOPES = ("global", "project", "chat")
#: Narrower scopes rank higher. An exception lifts only rules from a strictly broader scope.
_SCOPE_RANK = {"global": 0, "project": 1, "chat": 2}
KIND_RULE = "rule"
KIND_EXCEPTION = "exception"
TARGET_REPLY = "reply"
TARGET_GIT = "git"
ALL_TARGETS = (TARGET_REPLY, TARGET_GIT)

#: Bounds. A rule is an instruction, not a document; a ban list is a handful of words.
MAX_RULE_CHARS = 500
MAX_TERMS_PER_RULE = 20
MAX_TERM_CHARS = 60
#: The most git output read to check one commit or push. Beyond it the check cannot be complete,
#: and an incomplete check refuses rather than guesses.
MAX_GIT_SCAN_BYTES = 20 * 1024 * 1024
GIT_SCAN_TIMEOUT_SECONDS = 30

#: What replaces a banned word in a reply.
REPLY_MASK = "[blocked word]"


# ── records ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UserRule:
    rule_no: int
    rule_id: str
    scope: str
    scope_ref: str
    text: str
    terms: tuple[str, ...] = ()
    targets: tuple[str, ...] = ALL_TARGETS
    status: str = "active"
    created_at: str = ""
    removed_at: str = ""
    #: ``rule`` (a ban or instruction) or ``exception`` (a narrower scope lifting a broader rule).
    kind: str = KIND_RULE
    #: For an exception: the rule number it lifts whole, or 0 when it lifts only ``terms``.
    overrides_rule_no: int = 0

    def applies_to(self, target: str) -> bool:
        return target in self.targets

    @property
    def is_exception(self) -> bool:
        return self.kind == KIND_EXCEPTION

    def scope_label(self) -> str:
        if self.scope == "global":
            return "global"
        if self.scope == "project":
            return "project"
        return "this chat"


@dataclass(frozen=True)
class Violation:
    term: str
    rule: UserRule
    where: str = ""


@dataclass(frozen=True)
class RuleRefusal:
    """A git effect a rule refused. ``reason`` is what the user reads."""

    operation: str
    reason: str
    violations: tuple[Violation, ...] = ()
    incomplete: bool = False
    rule_nos: tuple[int, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "reason": self.reason,
            "incomplete": self.incomplete,
            "rules": list(self.rule_nos),
            "terms": sorted({v.term for v in self.violations}),
        }


# ── storage ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS user_rules (
  rule_no INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id TEXT NOT NULL UNIQUE,
  scope TEXT NOT NULL,
  scope_ref TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  terms_json TEXT NOT NULL DEFAULT '[]',
  targets_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  created_turn TEXT NOT NULL DEFAULT '',
  removed_at TEXT NOT NULL DEFAULT '',
  removed_turn TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'rule',
  overrides_rule_no INTEGER NOT NULL DEFAULT 0
)
"""
#: Columns added after the table's first shape; a store created before them gains them in place.
_LATER_COLUMNS = (
    ("kind", "TEXT NOT NULL DEFAULT 'rule'"),
    ("overrides_rule_no", "INTEGER NOT NULL DEFAULT 0"),
)
_IDX = "CREATE INDEX IF NOT EXISTS idx_user_rules_active ON user_rules(status, scope, scope_ref)"


def _utcnow() -> str:
    from core.memory.files import utcnow

    return utcnow()


def _ensure(conn: Any) -> None:
    conn.execute(_DDL)
    conn.execute(_IDX)
    present = {str(row[1]) for row in conn.execute("PRAGMA table_info(user_rules)").fetchall()}
    for name, ddl in _LATER_COLUMNS:
        if name not in present:
            conn.execute(f"ALTER TABLE user_rules ADD COLUMN {name} {ddl}")


def _row_to_rule(row: Any) -> UserRule:
    try:
        terms = tuple(str(t) for t in json.loads(row["terms_json"] or "[]") if str(t).strip())
    except (TypeError, ValueError):
        terms = ()
    try:
        targets = tuple(str(t) for t in json.loads(row["targets_json"] or "[]") if str(t) in ALL_TARGETS)
    except (TypeError, ValueError):
        targets = ()
    return UserRule(
        rule_no=int(row["rule_no"]),
        rule_id=str(row["rule_id"]),
        scope=str(row["scope"]),
        scope_ref=str(row["scope_ref"] or ""),
        text=str(row["text"]),
        terms=terms,
        # A stored row with no readable targets binds everywhere: an unreadable scope of
        # enforcement must not quietly become "nowhere".
        targets=targets or ALL_TARGETS,
        status=str(row["status"]),
        created_at=str(row["created_at"] or ""),
        removed_at=str(row["removed_at"] or ""),
        kind=str(row.get("kind") or KIND_RULE),
        overrides_rule_no=int(row.get("overrides_rule_no") or 0),
    )


def _query(sql: str, params: tuple = ()) -> list[UserRule]:
    conn = get_connection()
    try:
        _ensure(conn)
        conn.row_factory = _dict_row
        return [_row_to_rule(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _dict_row(cursor: Any, row: tuple) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


# ── scope resolution ─────────────────────────────────────────────────────────


def chat_project_id(chat_id: str) -> str:
    """The project a chat is bound to, from the server-owned binding, or "".

    Same authority order as ``core.context_namespace.authoritative_chat_workspace``: the chat
    namespace row, then the persisted session meta.
    """
    clean = str(chat_id or "").strip()
    if not clean:
        return ""
    try:
        from core.context_namespace import load_chat_namespace

        namespace = load_chat_namespace(clean)
        if namespace is not None and str(namespace.project_id or "").strip():
            return str(namespace.project_id).strip()
    except Exception:
        pass
    try:
        from core.persistent_memory import load_session_meta

        return str((load_session_meta().get(clean) or {}).get("project_id") or "").strip()
    except Exception:
        return ""


def _session_id_from(source_context: Mapping[str, Any] | None) -> str:
    context = source_context if isinstance(source_context, Mapping) else {}
    # The server-bound canonical chat id (ContextAccessPolicy.chat_id) wins over a raw session id.
    canonical = str(context.get("chat_id") or "").strip()
    if canonical:
        return canonical
    try:
        from core.turn_contract import TURN_REQUEST_KEY

        request = context.get(TURN_REQUEST_KEY)
        session = str(getattr(request, "session_id", "") or "") if request is not None else ""
        if session:
            return session
    except Exception:
        pass
    return str(context.get("session_id") or context.get("runtime_session_id") or "").strip()


def scope_keys_for(
    source_context: Mapping[str, Any] | None = None,
    *,
    chat_id: str = "",
    project_id: str = "",
) -> dict[str, str]:
    """``{"chat": <chat key>, "project": <project key>}`` for a turn.

    Explicit arguments win; otherwise the chat is the turn's session id and the project is the
    chat's server-owned binding. Blank means "no such scope for this turn".
    """
    chat = str(chat_id or "").strip() or _session_id_from(source_context)
    project = str(project_id or "").strip()
    if not project:
        context = source_context if isinstance(source_context, Mapping) else {}
        policy = context.get("_context_access_policy")
        project = str(getattr(policy, "project_id", "") or "").strip()
    if not project and chat:
        project = chat_project_id(chat)
    return {"chat": chat, "project": project}


def _ledger_scope_keys() -> dict[str, str]:
    """Scope keys of the turn whose effect ledger is open, for doors that get no context."""
    try:
        from core.effect_gateway import current_effect_ledger

        ledger = current_effect_ledger()
        context = getattr(ledger, "source_context", None) if ledger is not None else None
        if isinstance(context, Mapping):
            return scope_keys_for(context)
    except Exception:
        pass
    try:
        from core.operator_profile_turn import current_turn_scope

        _principal, session, project = current_turn_scope()
        if session or project:
            return scope_keys_for(None, chat_id=session, project_id=project)
    except Exception:
        pass
    return {"chat": "", "project": ""}


def project_ids_for_path(path: str | Path) -> list[str]:
    """Projects whose registered folder contains ``path`` (a repo inside a project folder)."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return []
    try:
        from core import project_store

        projects = project_store.list_projects()
    except Exception:
        return []
    found: list[str] = []
    for project in projects:
        root = str(project.get("root") or "").strip()
        pid = str(project.get("id") or project.get("project_id") or "").strip()
        if not root or not pid:
            continue
        try:
            root_path = Path(root).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if target == root_path or root_path in target.parents:
            found.append(pid)
    return found


# ── reading rules ────────────────────────────────────────────────────────────


def active_rules(
    *,
    chat_id: str = "",
    project_id: str = "",
    project_ids: Iterable[str] = (),
    include_exceptions: bool = False,
) -> list[UserRule]:
    """Every active rule stored for a turn in ``chat_id`` (and its project), broadest scope first.

    Global rules always; project rules for ``project_id`` and any ``project_ids``; chat rules for
    ``chat_id`` only. These are the rules as written; :func:`resolve_rules` applies exceptions.
    """
    projects = {p for p in [str(project_id or "").strip(), *(str(p).strip() for p in project_ids)] if p}
    chat = str(chat_id or "").strip()
    clauses = ["scope = 'global'"]
    params: list[str] = []
    if projects:
        clauses.append(f"(scope = 'project' AND scope_ref IN ({','.join('?' * len(projects))}))")
        params.extend(sorted(projects))
    if chat:
        clauses.append("(scope = 'chat' AND scope_ref = ?)")
        params.append(chat)
    sql = (
        "SELECT * FROM user_rules WHERE status = 'active' AND (" + " OR ".join(clauses) + ") "
        "ORDER BY CASE scope WHEN 'global' THEN 0 WHEN 'project' THEN 1 ELSE 2 END, rule_no"
    )
    rows = _query(sql, tuple(params))
    return rows if include_exceptions else [row for row in rows if not row.is_exception]


@dataclass(frozen=True)
class Lift:
    """One exception lifting a broader rule here: whole (``terms == ()``) or for some words."""

    rule: UserRule
    exception: UserRule
    terms: tuple[str, ...] = ()

    def describe(self) -> str:
        # No parentheses: the reply guard wraps its note in them and must find its own note again
        # (a second pass over the sealed reply has to leave it byte-identical).
        what = "" if not self.terms else " for " + ", ".join(self.terms)
        where = "this project" if self.exception.scope == "project" else "this chat"
        return (f"exception {self.exception.rule_no} for {where} lifts "
                f"{self.rule.scope_label()} rule {self.rule.rule_no}{what}")


@dataclass(frozen=True)
class Resolution:
    """The rules in force for one chat after its exceptions are applied."""

    rules: tuple[UserRule, ...] = ()
    lifts: tuple[Lift, ...] = ()
    exceptions: tuple[UserRule, ...] = ()
    #: The rules as written (before exceptions), so a check can say what an exception allowed.
    written: tuple[UserRule, ...] = ()

    def lift_for(self, rule_no: int, term: str) -> Lift | None:
        key = str(term or "").lower()
        for lift in self.lifts:
            if lift.rule.rule_no != rule_no:
                continue
            if not lift.terms or key in {t.lower() for t in lift.terms}:
                return lift
        return None

    def lifts_for_rule(self, rule_no: int) -> list[Lift]:
        return [lift for lift in self.lifts if lift.rule.rule_no == rule_no]


def resolve_rules(
    *,
    chat_id: str = "",
    project_id: str = "",
    project_ids: Iterable[str] = (),
) -> Resolution:
    """Apply exceptions: a chat exception lifts project and global rules, a project exception lifts
    global rules, never the other way round. A rule lifted whole leaves the result; a rule lifted
    for some words keeps its other words. Exceptions only remove restrictions a rule imposed."""
    rows = active_rules(chat_id=chat_id, project_id=project_id, project_ids=project_ids, include_exceptions=True)
    written = [row for row in rows if not row.is_exception]
    exceptions = [row for row in rows if row.is_exception]
    effective: list[UserRule] = []
    lifts: list[Lift] = []
    for rule in written:
        narrower = [e for e in exceptions if _SCOPE_RANK.get(e.scope, 0) > _SCOPE_RANK.get(rule.scope, 0)]
        whole = next((e for e in narrower if e.overrides_rule_no == rule.rule_no), None)
        if whole is not None:
            lifts.append(Lift(rule=rule, exception=whole))
            continue
        kept: list[str] = []
        for term in rule.terms:
            exc = next(
                (e for e in narrower if e.overrides_rule_no == 0 and term.lower() in {t.lower() for t in e.terms}),
                None,
            )
            if exc is None:
                kept.append(term)
            else:
                lifts.append(Lift(rule=rule, exception=exc, terms=(term,)))
        effective.append(rule if len(kept) == len(rule.terms) else replace(rule, terms=tuple(kept)))
    return Resolution(rules=tuple(effective), lifts=tuple(lifts), exceptions=tuple(exceptions), written=tuple(written))


def _rules_for_scope(scope: str, scope_ref: str) -> list[UserRule]:
    return _query(
        "SELECT * FROM user_rules WHERE status = 'active' AND scope = ? AND scope_ref = ? ORDER BY rule_no",
        (scope, scope_ref),
    )


# ── reading a rule's banned words ────────────────────────────────────────────

_QUOTED_RE = re.compile(r"[\"“”'‘’`]([^\"“”'‘’`\n]{1,60})[\"“”'‘’`]")
_WORDS_LEAD_RE = re.compile(
    r"\b(?:the\s+|these\s+|any\s+of\s+the\s+)?(?:words?|terms?|phrases?|strings?)"
    r"(?:\s+like|\s+such\s+as|\s+including|\s*:)?\s+(?P<list>.+)$",
    re.IGNORECASE,
)
#: "never say X", "don't write X or Y", "never commit X": a speech/output verb followed by a word list.
_VERB_LIST_RE = re.compile(
    r"^\s*(?:never|don'?t|do\s+not|no)\s+(?:ever\s+)?"
    r"(?:say|mention|write|type|print|output|use\s+the\s+words?)\s+"
    r"(?P<list>.+)$",
    re.IGNORECASE,
)
#: "Never fff and ggg": a prohibition whose whole object is a list of single words.
_BARE_LIST_RE = re.compile(r"^\s*(?:never|don'?t|do\s+not|no)\s+(?:ever\s+)?(?P<list>.+)$", re.IGNORECASE)
#: Where a word list stops: the surface or purpose the rule names ("... to git", "... in replies").
_LIST_END_RE = re.compile(
    r"\s+(?:to|into|in|on|onto|from|for|when|while|anywhere|ever|again|at\s+all|inside|within|via)\b.*$",
    re.IGNORECASE,
)
_LIST_SPLIT_RE = re.compile(r"\s*(?:,|;|/|\bor\b|\band\b|\bnor\b|&)\s*", re.IGNORECASE)
#: First words that make a "never X" clause a behaviour, not a word list.
_BEHAVIOUR_VERBS = frozenset(
    [
        "use", "say", "write", "mention", "push", "commit", "include", "reply", "respond",
        "answer", "be", "do", "make", "send", "post", "share", "run", "delete", "remove",
        "touch", "edit", "change", "call", "add", "put", "type", "give", "show", "ask", "talk",
        "speak", "start", "end", "sign", "apologize", "apologise", "forget", "go", "open",
        "close", "install", "buy", "sell", "pay", "spend", "email", "message", "text", "tell",
        "explain", "repeat", "capitalize", "capitalise", "format", "translate", "summarize",
        "summarise", "guess", "assume", "skip", "ignore", "leave", "let", "create", "merge",
        "rebase", "tag", "force", "deploy", "publish", "upload", "download", "read", "print",
        "output", "stop"
    ]
)
_GIT_CUE_RE = re.compile(
    r"\b(?:git|github|gitlab|push(?:ed|es|ing)?|commit(?:s|ted|ting)?|repo(?:sitory|sitories|s)?|branch(?:es)?|"
    r"tags?|pull\s+requests?|prs?)\b",
    re.IGNORECASE,
)
_REPLY_CUE_RE = re.compile(
    r"\b(?:say|said|reply|replies|respond|responses?|answers?|chat|messages?|tell|talk|speak|write\s+to\s+me|"
    r"mention)\b",
    re.IGNORECASE,
)


def _clean_term(raw: str) -> str:
    term = " ".join(str(raw or "").split()).strip(" .,:;!?\"'“”‘’`()[]{}")
    for article in ("the ", "a ", "an "):
        if term.lower().startswith(article):
            term = term[len(article):]
    return term[:MAX_TERM_CHARS].strip()


def _split_list(raw: str) -> list[str]:
    head = _LIST_END_RE.sub("", str(raw or "").strip())
    head = head.rstrip(" .!?")
    return [_clean_term(part) for part in _LIST_SPLIT_RE.split(head) if _clean_term(part)]


def extract_terms(text: str) -> tuple[str, ...]:
    """The exact words a rule bans, read deterministically, or () for a behaviour-only rule.

    Quoted words always count. Otherwise a word list after "the words ..." or after a speech
    verb ("never say ..."), or a prohibition whose whole object is a list of single words
    ("Never fff and ggg"). A phrase that reads as a behaviour ("never use emojis", "always answer
    in English") yields no words: it is still a rule, but there is nothing to match mechanically.
    """
    body = " ".join(str(text or "").split())
    found: list[str] = []
    for match in _QUOTED_RE.finditer(body):
        term = _clean_term(match.group(1))
        if term:
            found.append(term)
    if not found:
        lead = _WORDS_LEAD_RE.search(body)
        verb = _VERB_LIST_RE.match(body)
        bare = _BARE_LIST_RE.match(body)
        candidates: list[str] = []
        if lead:
            candidates = _split_list(lead.group("list"))
        elif verb:
            items = _split_list(verb.group("list"))
            if items and all(" " not in item for item in items):
                candidates = items
        elif bare:
            items = _split_list(bare.group("list"))
            if items and all(" " not in item for item in items) and items[0].lower() not in _BEHAVIOUR_VERBS:
                candidates = items
        found.extend(candidates)
    unique: list[str] = []
    seen: set[str] = set()
    for term in found:
        key = term.lower()
        if key and key not in seen and len(unique) < MAX_TERMS_PER_RULE:
            seen.add(key)
            unique.append(term)
    return tuple(unique)


def _targets_for(text: str) -> tuple[str, ...]:
    """Where a rule's words are banned: only git when the rule names git and nothing else,
    otherwise everywhere (replies and git)."""
    body = str(text or "")
    if _GIT_CUE_RE.search(body) and not _REPLY_CUE_RE.search(body):
        return (TARGET_GIT,)
    return ALL_TARGETS


def _term_pattern(term: str) -> re.Pattern[str]:
    """A banned word matches as a whole word: not inside a longer word ("xxxl", "fooxxx"), but
    still inside identifiers where digits or underscores separate it ("add_xxx", "xxx2")."""
    return re.compile(rf"(?<![^\W\d_]){re.escape(term)}(?![^\W\d_])", re.IGNORECASE)


def find_banned_terms(text: str, rules: Iterable[UserRule], *, target: str, where: str = "") -> list[Violation]:
    """Every (word, rule) pair from ``rules`` binding ``target`` that occurs in ``text`` as a whole word."""
    body = str(text or "")
    if not body:
        return []
    hits: list[Violation] = []
    for rule in rules:
        if not rule.applies_to(target):
            continue
        for term in rule.terms:
            if _term_pattern(term).search(body):
                hits.append(Violation(term=term, rule=rule, where=where))
    return hits


# ── writing rules ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RuleChange:
    kind: str  # saved | duplicate | removed | refused | none
    report: str
    rules: tuple[UserRule, ...] = ()


def _scope_ref(scope: str, *, chat_id: str, project_id: str) -> str:
    if scope == "global":
        return ""
    if scope == "project":
        return str(project_id or "").strip()
    return str(chat_id or "").strip()


def add_rule(
    scope: str,
    text: str,
    *,
    chat_id: str = "",
    project_id: str = "",
    turn_id: str = "",
) -> RuleChange:
    """Save one rule. Refuses (and saves nothing) when the scope cannot be bound, the text is
    empty, too long, or carries a secret."""
    clean_scope = str(scope or "").strip().lower()
    if clean_scope not in SCOPES:
        return RuleChange("refused", f"`{scope}` is not a rule scope. Use global, project or chat. Nothing was saved.")
    body = " ".join(str(text or "").split()).strip().rstrip(".")
    if not body:
        return RuleChange("refused", "That rule is empty. Nothing was saved.")
    if len(body) > MAX_RULE_CHARS:
        return RuleChange(
            "refused",
            f"That rule is {len(body)} characters; a rule can be at most {MAX_RULE_CHARS}. Nothing was saved.",
        )
    try:
        from core.secret_redaction import contains_secret

        if contains_secret(body):
            return RuleChange("refused", "That looks like it contains a secret (a key, token or password). I don't save secrets in rules. Nothing was saved.")
    except Exception:
        return RuleChange("refused", "I couldn't check that rule for secrets, so I didn't save it.")
    ref = _scope_ref(clean_scope, chat_id=chat_id, project_id=project_id)
    if clean_scope == "project" and not ref:
        return RuleChange("refused", "This chat isn't in a project, so there's no project to attach the rule to. Nothing was saved.")
    if clean_scope == "chat" and not ref:
        return RuleChange("refused", "I can't tell which chat this is, so I didn't save a chat rule.")
    for existing in _rules_for_scope(clean_scope, ref):
        if not existing.is_exception and existing.text.lower() == body.lower():
            return RuleChange("duplicate", f"That's already rule {existing.rule_no} ({existing.scope_label()}). Nothing changed.", (existing,))
    terms = extract_terms(body)
    targets = _targets_for(body)
    now = _utcnow()
    rule_id = "rule-" + uuid.uuid4().hex[:12]
    conn = get_connection()
    try:
        _ensure(conn)
        cursor = conn.execute(
            "INSERT INTO user_rules (rule_id, scope, scope_ref, text, terms_json, targets_json, status, created_at, created_turn) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (rule_id, clean_scope, ref, body, json.dumps(list(terms)), json.dumps(list(targets)), now, str(turn_id or "")),
        )
        conn.commit()
        rule_no = int(cursor.lastrowid)
    finally:
        conn.close()
    saved = UserRule(rule_no=rule_no, rule_id=rule_id, scope=clean_scope, scope_ref=ref, text=body,
                     terms=terms, targets=targets, created_at=now)
    return RuleChange("saved", _saved_report(saved), (saved,))


def _enforcement_line(rule: UserRule) -> str:
    if not rule.terms:
        return "I'll follow it in replies. It names no exact words, so nothing is blocked automatically."
    words = ", ".join(rule.terms)
    if rule.targets == (TARGET_GIT,):
        return f"Blocked in git (commit messages, committed changes, branch and tag names, pushes): {words}."
    return f"Blocked in replies and in git (commits, branch and tag names, pushes): {words}."


def _saved_report(rule: UserRule) -> str:
    where = {
        "global": "a global rule (every chat and project)",
        "project": "a project rule (every chat in this project)",
        "chat": "a chat rule (this chat only)",
    }[rule.scope]
    return f"Saved rule {rule.rule_no} as {where}: \"{rule.text}\". {_enforcement_line(rule)}"


def remove_rules(rules: Iterable[UserRule], *, turn_id: str = "") -> list[UserRule]:
    targets = [rule for rule in rules if rule.status == "active"]
    if not targets:
        return []
    now = _utcnow()
    conn = get_connection()
    try:
        _ensure(conn)
        for rule in targets:
            conn.execute(
                "UPDATE user_rules SET status = 'removed', removed_at = ?, removed_turn = ? WHERE rule_id = ? AND status = 'active'",
                (now, str(turn_id or ""), rule.rule_id),
            )
        conn.commit()
    finally:
        conn.close()
    return targets


def _insert_row(*, scope: str, ref: str, text: str, terms: tuple[str, ...], targets: tuple[str, ...],
                kind: str, overrides_rule_no: int, turn_id: str) -> UserRule:
    now = _utcnow()
    rule_id = ("exc-" if kind == KIND_EXCEPTION else "rule-") + uuid.uuid4().hex[:12]
    conn = get_connection()
    try:
        _ensure(conn)
        cursor = conn.execute(
            "INSERT INTO user_rules (rule_id, scope, scope_ref, text, terms_json, targets_json, status, created_at, "
            "created_turn, kind, overrides_rule_no) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (rule_id, scope, ref, text, json.dumps(list(terms)), json.dumps(list(targets)), now, str(turn_id or ""),
             kind, int(overrides_rule_no)),
        )
        conn.commit()
        rule_no = int(cursor.lastrowid)
    finally:
        conn.close()
    return UserRule(rule_no=rule_no, rule_id=rule_id, scope=scope, scope_ref=ref, text=text, terms=terms,
                    targets=targets, created_at=now, kind=kind, overrides_rule_no=int(overrides_rule_no))


def add_exception(
    scope: str,
    *,
    chat_id: str = "",
    project_id: str = "",
    rule_no: int = 0,
    words: Iterable[str] = (),
    turn_id: str = "",
) -> RuleChange:
    """Save an exception: this chat or project lifts a BROADER rule, whole (``rule_no``) or for
    ``words``. A chat exception lifts project and global rules; a project exception lifts global
    rules. It never lifts a rule of its own scope (remove that rule instead) and never adds a ban."""
    clean_scope = "project" if str(scope or "").strip().lower() == "project" else "chat"
    ref = _scope_ref(clean_scope, chat_id=chat_id, project_id=project_id)
    if not ref:
        where = "a project" if clean_scope == "project" else "a chat I can identify"
        return RuleChange("refused", f"This isn't {where}, so there's nowhere to attach the exception. Nothing was saved.")
    rank = _SCOPE_RANK[clean_scope]
    resolution = resolve_rules(chat_id=chat_id, project_id=project_id)
    written = {rule.rule_no: rule for rule in resolution.written}
    existing = [e for e in resolution.exceptions if e.scope == clean_scope and e.scope_ref == ref]
    here = "this chat" if clean_scope == "chat" else "this project"
    if rule_no:
        rule = written.get(int(rule_no))
        if rule is None:
            return RuleChange("refused", f"There's no active rule {rule_no} here. Nothing was saved. "
                              "Say \"what are my rules\" to see the numbers.")
        if _SCOPE_RANK[rule.scope] >= rank:
            return RuleChange("refused", f"Rule {rule.rule_no} is a {rule.scope_label()} rule, not a broader one, so an "
                              f"exception for {here} can't lift it. To drop it, say \"remove rule {rule.rule_no}\". "
                              "Nothing was saved.")
        for exc in existing:
            if exc.overrides_rule_no == rule.rule_no:
                return RuleChange("duplicate", f"That's already exception {exc.rule_no}. Nothing changed.", (exc,))
        saved = _insert_row(scope=clean_scope, ref=ref, text=f"lift rule {rule.rule_no} here", terms=(), targets=ALL_TARGETS,
                            kind=KIND_EXCEPTION, overrides_rule_no=rule.rule_no, turn_id=turn_id)
        return RuleChange("saved", (f"Saved exception {saved.rule_no} for {here}: {rule.scope_label()} rule {rule.rule_no} "
                                    f"(\"{rule.text}\") no longer applies here. Other chats are unchanged. "
                                    f"To undo it, say \"remove rule {saved.rule_no}\"."), (saved,))
    wanted = [w for w in dict.fromkeys(_clean_term(w) for w in words) if w]
    broader = [rule for rule in written.values() if _SCOPE_RANK[rule.scope] < rank]
    lifted: list[str] = []
    lifted_rules: list[UserRule] = []
    for word in wanted:
        hit = [rule for rule in broader if word.lower() in {t.lower() for t in rule.terms}]
        if hit:
            lifted.append(word)
            lifted_rules.extend(r for r in hit if r not in lifted_rules)
    if not lifted:
        own = [rule for rule in written.values() if _SCOPE_RANK[rule.scope] >= rank
               and {w.lower() for w in wanted} & {t.lower() for t in rule.terms}]
        if own:
            rule = own[0]
            return RuleChange("refused", f"{', '.join(wanted)} is banned by {rule.scope_label()} rule {rule.rule_no}, which "
                              f"is not broader than {here}, so an exception can't lift it. To drop it, say "
                              f"\"remove rule {rule.rule_no}\". Nothing was saved.")
        return RuleChange("none", f"No broader rule bans {', '.join(wanted) or 'that'} here, so there's nothing to "
                          "allow. Nothing was saved.")
    for exc in existing:
        if exc.overrides_rule_no == 0 and {t.lower() for t in lifted} <= {t.lower() for t in exc.terms}:
            return RuleChange("duplicate", f"That's already exception {exc.rule_no}. Nothing changed.", (exc,))
    saved = _insert_row(scope=clean_scope, ref=ref, text="allow " + ", ".join(lifted) + " here", terms=tuple(lifted),
                        targets=ALL_TARGETS, kind=KIND_EXCEPTION, overrides_rule_no=0, turn_id=turn_id)
    rules_txt = ", ".join(f"{r.scope_label()} rule {r.rule_no}" for r in sorted(lifted_rules, key=lambda r: r.rule_no))
    ignored = [w for w in wanted if w not in lifted]
    tail = f" No broader rule bans {', '.join(ignored)}, so that needed no exception." if ignored else ""
    return RuleChange("saved", (f"Saved exception {saved.rule_no} for {here}: {', '.join(lifted)} allowed here despite "
                                f"{rules_txt}. Other chats are unchanged.{tail} To undo it, say "
                                f"\"remove rule {saved.rule_no}\"."), (saved,))


# ── chat commands ────────────────────────────────────────────────────────────

_SCOPE_WORD = r"(?P<scope>global|chat|local|project|this\s+chat(?:'s)?|this\s+project(?:'s)?)"
_LEAD = r"^\s*(?:(?:hey|ok|okay)\s+vool[,\s]+)?(?:please\s+)?"
_ADD_PATTERNS = (
    # "add this to global rules: <body>", "add to my chat rules - <body>", "save to project rules: <body>"
    re.compile(
        _LEAD + r"(?:add|save|put|store|set)\s+(?:this|that|it|the\s+following)?\s*(?:rule\s+)?(?:to|into|in|as|under)\s+"
        r"(?:my\s+|the\s+|our\s+)?" + _SCOPE_WORD + r"?\s*rules?(?:\s+list)?\s*(?P<sep>[:\-–—,])?\s*(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    # "add to the rules for this chat: <body>"
    re.compile(
        _LEAD + r"(?:add|save|put|store)\s+(?:this|that|it)?\s*(?:to|into|in)\s+(?:my\s+|the\s+)?rules\s+(?:for|of)\s+"
        r"(?:this\s+)?(?P<scope>chat|project|everything|all\s+chats)\s*(?P<sep>[:\-–—,])?\s*(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    # "add a global rule: <body>", "new chat rule: <body>", "create a project rule - <body>"
    re.compile(
        _LEAD + r"(?:add|save|create|make|new|set)\s+(?:a\s+|an\s+|new\s+)*" + _SCOPE_WORD + r"\s+rule\s*(?P<sep>[:\-–—,])?\s*(?P<body>.+)$",
        re.IGNORECASE | re.DOTALL,
    ),
    # "global rule: <body>"
    re.compile(_LEAD + _SCOPE_WORD + r"\s+rule\s*(?P<sep>[:\-–—])\s*(?P<body>.+)$", re.IGNORECASE | re.DOTALL),
)
#: "<body> -- add this to global rules" (the instruction after the rule).
_ADD_TRAILING_RE = re.compile(
    r"^(?P<body>.+?)[\s,.;:–—-]+(?:please\s+)?(?:add|save|put|make)\s+(?:this|that|it)\s+(?:to|into|as|in)\s+"
    r"(?:a\s+|my\s+|the\s+)?" + _SCOPE_WORD + r"?\s*rules?\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_LIST_RE = re.compile(
    _LEAD + r"(?:what\s+(?:are|is)|show|list|tell\s+me|which\s+are|give\s+me|display|see)\s+(?:me\s+)?(?:all\s+)?"
    r"(?:of\s+)?(?:my|the|your|our|current|active|all)?\s*(?:current\s+|active\s+)?" + _SCOPE_WORD + r"?\s*rules"
    r"(?:\s+(?:for|in|of)\s+this\s+(?P<scope2>chat|project))?\s*(?:do\s+you\s+have|you\s+have|now)?\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_LIST_WHAT_RE = re.compile(
    _LEAD + r"what\s+(?:" + _SCOPE_WORD + r"\s+)?rules\s+(?:do\s+(?:i|you|we)\s+have|are\s+(?:there|active|set|on))"
    r"(?:\s+(?:for|in|of)\s+this\s+(?P<scope2>chat|project))?(?:\s+(?:now|here|saved))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_LIST_SHORT_RE = re.compile(r"^\s*(?:my\s+)?" + _SCOPE_WORD + r"?\s*rules\s*\??\s*$", re.IGNORECASE)
_REMOVE_VERB = r"(?:remove|delete|drop|forget|clear|cancel|erase|scrap)"
_REMOVE_NUM_RE = re.compile(
    _LEAD + _REMOVE_VERB + r"\s+(?:the\s+)?(?:" + _SCOPE_WORD + r"\s+)?(?:rules?|exceptions?)\s*(?:#|number|no\.?|nr\.?)?\s*"
    r"(?P<nums>\d+(?:\s*(?:,|and|&)\s*#?\d+)*)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_REMOVE_ALL_RE = re.compile(
    _LEAD + _REMOVE_VERB + r"\s+(?:all\s+(?:of\s+)?)?(?:my\s+|the\s+)?(?:" + _SCOPE_WORD + r"\s+)?rules"
    r"(?:\s+(?:for|in|of)\s+this\s+(?P<scope2>chat|project))?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_REMOVE_ABOUT_RE = re.compile(
    _LEAD + _REMOVE_VERB + r"\s+(?:the\s+)?(?:" + _SCOPE_WORD + r"\s+)?rules?\s+(?:about|with|containing|on|for|mentioning|"
    r"that\s+(?:says|mentions|bans|blocks))\s+(?P<needle>.+?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


_EXC_VERB = r"(?:ignore|override|overrule|lift|suspend|skip|disable|waive|pause|turn\s+off|switch\s+off)"
_EXC_SCOPE_TAIL = r"(?:\s+(?:in|for)\s+(?:this\s+)?(?P<scope>chat|project)|\s+(?P<here>here|for\s+now))?\s*[.!]?\s*$"
_EXC_SCOPE_LEAD = r"(?:in|for)\s+this\s+(?P<scope>chat|project)\s*[,:]?\s+"
_EXC_RULE_RES = (
    re.compile(_LEAD + _EXC_VERB + r"\s+(?:the\s+)?(?:(?:global|project)\s+)?rule\s*(?:#|number|no\.?)?\s*(?P<num>\d+)"
               + _EXC_SCOPE_TAIL, re.IGNORECASE),
    re.compile(_LEAD + _EXC_SCOPE_LEAD + _EXC_VERB + r"\s+(?:the\s+)?(?:(?:global|project)\s+)?rule\s*(?:#|number|no\.?)?"
               r"\s*(?P<num>\d+)\s*[.!]?\s*$", re.IGNORECASE),
)
_ALLOW_VERB = (r"(?:(?:you\s+(?:can|may)\s+(?:now\s+)?(?:use|say|write))|allow|permit|unban|unblock|whitelist|"
               r"accept|let\s+me\s+(?:use|say))")
_EXC_WORD_RES = (
    re.compile(_LEAD + _ALLOW_VERB + r"\s+(?:the\s+)?(?:words?\s+|terms?\s+)?(?P<list>.+?)" + _EXC_SCOPE_TAIL, re.IGNORECASE),
    re.compile(_LEAD + _EXC_SCOPE_LEAD + _ALLOW_VERB + r"\s+(?:the\s+)?(?:words?\s+|terms?\s+)?(?P<list>.+?)\s*[.!]?\s*$",
               re.IGNORECASE),
)


def _allow_words(raw: str) -> tuple[str, ...]:
    """The single words an allow command names (quoted words, or a plain list of single words)."""
    quoted = [_clean_term(m.group(1)) for m in _QUOTED_RE.finditer(raw)]
    words = [w for w in quoted if w] or _split_list(raw)
    if not words or any(" " in w for w in words) or len(words) > MAX_TERMS_PER_RULE:
        return ()
    return tuple(words)


_RULE_BODY_LEAD_RE = re.compile(r"^(?:that|which|saying|says|to\s+say)\s+", re.IGNORECASE)
_DIRECTIVE_START_RE = re.compile(
    r"^(?:never|always|don'?t|do\s+not|no|avoid|only|stop|keep|use|reply|answer|respond|write|speak|"
    r"call|address|be|make|ask|refuse|block|ban)\b",
    re.IGNORECASE,
)


#: What the persistence layer's redaction leaves where a secret was ("[redacted-api-key]", ...).
_REDACTION_MARK_RE = re.compile(r"\[redacted(?:-[a-z-]+)?\]", re.IGNORECASE)


@dataclass(frozen=True)
class RuleCommand:
    action: str  # add | list | remove_numbers | remove_all | remove_about | except_rule | except_words
    scope: str = ""  # "", global, project, chat
    body: str = ""
    numbers: tuple[int, ...] = ()
    needle: str = ""
    words: tuple[str, ...] = ()
    #: The message itself says "rule"; an allow command without it is only a command when a rule
    #: actually bans the word here (otherwise "allow cookies in this chat" is ordinary chat).
    names_rules: bool = True


def _norm_scope(raw: str | None) -> str:
    value = " ".join(str(raw or "").lower().split()).replace("'s", "")
    if not value:
        return ""
    if value in {"global", "everything", "all chats"}:
        return "global"
    if value in {"chat", "local", "this chat"}:
        return "chat"
    if value in {"project", "this project"}:
        return "project"
    return ""


def parse_rule_command(text: str) -> RuleCommand | None:
    """Read an add / list / remove rules command, or None when the message is not one.

    Only messages that name rules are commands, and a message about skills is never one (the
    skill lane owns "create a skill for this chat ...").
    """
    raw = " ".join(str(text or "").split()).strip()
    if not raw or len(raw) > MAX_RULE_CHARS + 200:
        return None
    lowered = raw.lower()
    if re.search(r"\bskills?\b", lowered):
        return None
    for pattern in _EXC_RULE_RES:
        match = pattern.match(raw)
        if match:
            scope = "project" if str(match.group("scope") or "").lower() == "project" else "chat"
            return RuleCommand("except_rule", scope=scope, numbers=(int(match.group("num")),))
    for pattern in _EXC_WORD_RES:
        match = pattern.match(raw)
        if match:
            words = _allow_words(match.group("list"))
            if words:
                scope = "project" if str(match.group("scope") or "").lower() == "project" else "chat"
                return RuleCommand("except_words", scope=scope, words=words, names_rules="rule" in lowered)
    if "rule" not in lowered:
        return None
    match = _REMOVE_NUM_RE.match(raw)
    if match:
        nums = tuple(int(n) for n in re.findall(r"\d+", match.group("nums")))
        return RuleCommand("remove_numbers", scope=_norm_scope(match.group("scope")), numbers=nums)
    match = _REMOVE_ABOUT_RE.match(raw)
    if match:
        needle = _clean_term(match.group("needle"))
        if needle:
            return RuleCommand("remove_about", scope=_norm_scope(match.group("scope")), needle=needle)
    match = _REMOVE_ALL_RE.match(raw)
    if match:
        return RuleCommand("remove_all", scope=_norm_scope(match.group("scope") or match.group("scope2")))
    match = _LIST_RE.match(raw) or _LIST_WHAT_RE.match(raw) or _LIST_SHORT_RE.match(raw)
    if match:
        scope2 = match.groupdict().get("scope2")
        return RuleCommand("list", scope=_norm_scope(match.group("scope") or scope2))
    for pattern in _ADD_PATTERNS:
        match = pattern.match(raw)
        if match:
            body = _RULE_BODY_LEAD_RE.sub("", match.group("body").strip())
            # Without an explicit ":" / "-" the body must read as an instruction, so "put it in the
            # rules folder" is not a rule.
            if body and (match.group("sep") or _DIRECTIVE_START_RE.match(body)):
                return RuleCommand("add", scope=_norm_scope(match.group("scope")) or "global", body=body)
    match = _ADD_TRAILING_RE.match(raw)
    if match and match.group("body").strip():
        return RuleCommand("add", scope=_norm_scope(match.group("scope")) or "global", body=match.group("body").strip())
    return None


def _visible_rules(chat_id: str, project_id: str, scope: str = "") -> list[UserRule]:
    rules = active_rules(chat_id=chat_id, project_id=project_id, include_exceptions=True)
    if scope:
        rules = [rule for rule in rules if rule.scope == scope]
    return rules


def list_rules_text(chat_id: str, project_id: str, scope: str = "") -> str:
    rules = _visible_rules(chat_id, project_id, scope)
    label = {"global": "global ", "project": "project ", "chat": "chat "}.get(scope, "")
    if not rules:
        if scope == "project" and not project_id:
            return "This chat isn't in a project, so there are no project rules here."
        return f"You have no active {label}rules{'' if scope else ' for this chat'}."
    resolution = resolve_rules(chat_id=chat_id, project_id=project_id)
    written_nos = {rule.rule_no for rule in resolution.written}
    lines = [f"Active {label}rules{'' if scope else ' for this chat'}:"]
    for rule in rules:
        if rule.is_exception:
            target = (f"lifts rule {rule.overrides_rule_no}" if rule.overrides_rule_no
                      else "allows " + ", ".join(rule.terms))
            gone = " (that rule is no longer active)" if rule.overrides_rule_no and rule.overrides_rule_no not in written_nos else ""
            lines.append(f"- Exception {rule.rule_no} ({rule.scope_label()}): {target} here{gone}")
            continue
        lines.append(f"- Rule {rule.rule_no} ({rule.scope_label()}): {rule.text}")
        lines.append(f"  {_enforcement_line(rule)}")
        for lift in resolution.lifts_for_rule(rule.rule_no):
            lines.append(f"  Lifted here{' for ' + ', '.join(lift.terms) if lift.terms else ''} by exception "
                         f"{lift.exception.rule_no} ({lift.exception.scope_label()}).")
    if not scope and len({rule.scope for rule in rules}) > 1:
        lines.append("Bans from every scope apply together, except where this chat or its project made an exception "
                     "(say \"in this chat, allow <word>\" or \"ignore rule <number> in this chat\").")
    lines.append("To remove one, say \"remove rule <number>\".")
    return "\n".join(lines)


def handle_rule_command(
    text: str,
    *,
    chat_id: str,
    project_id: str = "",
    owner_local: bool = True,
    turn_id: str = "",
    secret_carved: bool = False,
) -> tuple[str, list[UserRule], str] | None:
    """Run a rules command. Returns ``(reply, changed_rules, kind)`` or None when ``text`` is not
    a rules command. ``kind`` is saved / removed / duplicate / refused / listed / none."""
    command = parse_rule_command(text)
    if command is None:
        return None
    chat = str(chat_id or "").strip()
    project = str(project_id or "").strip() or chat_project_id(chat)
    if command.action == "list":
        return list_rules_text(chat, project, command.scope), [], "listed"
    if command.action in {"except_rule", "except_words"}:
        if command.action == "except_words" and not command.names_rules:
            # "allow X" with no mention of rules is a rules command only when a broader rule here
            # actually bans X; otherwise it is ordinary conversation and not this lane's to answer.
            resolution = resolve_rules(chat_id=chat, project_id=project)
            banned = {t.lower() for rule in resolution.written for t in rule.terms}
            if not any(word.lower() in banned for word in command.words):
                return None
        if not owner_local:
            return ("Rules can only be changed from this computer's own VOOL. Nothing changed.", [], "refused")
        change = add_exception(command.scope, chat_id=chat, project_id=project,
                               rule_no=command.numbers[0] if command.numbers else 0,
                               words=command.words, turn_id=turn_id)
        return change.report, list(change.rules), change.kind
    if command.action == "add":
        if secret_carved or _REDACTION_MARK_RE.search(command.body):
            return ("Part of that message looked like a key or other secret, so I didn't save it as a rule: "
                    "the rule would not say what you typed. Nothing was saved.", [], "refused")
        if not owner_local:
            return ("Rules can only be added or removed from this computer's own VOOL. Nothing changed.", [], "refused")
        change = add_rule(command.scope, command.body, chat_id=chat, project_id=project, turn_id=turn_id)
        return change.report, list(change.rules), change.kind
    if not owner_local:
        return ("Rules can only be added or removed from this computer's own VOOL. Nothing changed.", [], "refused")
    visible = _visible_rules(chat, project, command.scope)
    if command.action == "remove_numbers":
        by_no = {rule.rule_no: rule for rule in visible}
        missing = [n for n in command.numbers if n not in by_no]
        if missing:
            shown = ", ".join(str(n) for n in missing)
            return (f"There's no active rule {shown} {'here' if not command.scope else 'in that scope'}. Nothing was removed. "
                    "Say \"what are my rules\" to see the numbers.", [], "refused")
        removed = remove_rules([by_no[n] for n in command.numbers], turn_id=turn_id)
        return _removed_report(removed), removed, "removed"
    if command.action == "remove_all":
        if not visible:
            return ("There are no active rules to remove here. Nothing changed.", [], "none")
        removed = remove_rules(visible, turn_id=turn_id)
        return _removed_report(removed), removed, "removed"
    needle = command.needle.lower()
    matches = [rule for rule in visible
               if needle in rule.text.lower() or any(needle == term.lower() for term in rule.terms)]
    if not matches:
        return (f"No active rule here mentions \"{command.needle}\". Nothing was removed.", [], "none")
    if len(matches) > 1:
        listed = "; ".join(f"rule {rule.rule_no}: {rule.text}" for rule in matches)
        return (f"More than one rule mentions \"{command.needle}\" ({listed}). Nothing was removed. "
                "Say \"remove rule <number>\" for the one you mean.", [], "refused")
    removed = remove_rules(matches, turn_id=turn_id)
    return _removed_report(removed), removed, "removed"


def _removed_report(removed: list[UserRule]) -> str:
    if not removed:
        return "Nothing was removed."
    parts = [f"{'exception' if rule.is_exception else 'rule'} {rule.rule_no} ({rule.scope_label()}): \"{rule.text}\""
             for rule in removed]
    return "Removed " + "; ".join(parts) + ". It no longer applies." if len(parts) == 1 else (
        "Removed " + "; ".join(parts) + ". They no longer apply."
    )


# ── enforcement: prompt ──────────────────────────────────────────────────────


def _sorted_words(words: Iterable[str]) -> list[str]:
    # Total order (case-folded, then exact) so an unchanged rule set renders byte-identical text.
    return sorted(set(words), key=lambda t: (t.lower(), t))


def rules_prompt_block(*, chat_id: str = "", project_id: str = "") -> str:
    """The rules that bind this chat, as one system-prompt block, or "" when there are none.

    Stable for a chat until a rule or exception is added or removed, so it belongs in the cached
    prefix: rules in scope order then number, words in a total order, no times or counts.
    """
    project = str(project_id or "").strip() or chat_project_id(chat_id)
    resolution = resolve_rules(chat_id=chat_id, project_id=project)
    if not resolution.written:
        return ""
    lines = ["User rules. The user set these. Follow every one in every reply and every action:"]
    for scope, heading in (("global", "Global"), ("project", "This project"), ("chat", "This chat")):
        scoped = [rule for rule in resolution.written if rule.scope == scope]
        if not scoped:
            continue
        lines.append(f"{heading}:")
        for rule in scoped:
            lifts = resolution.lifts_for_rule(rule.rule_no)
            if any(not lift.terms for lift in lifts):
                lines.append(f"- {rule.text} [lifted here by the user's exception: it does not apply in this chat]")
            elif lifts:
                allowed = _sorted_words(t for lift in lifts for t in lift.terms)
                lines.append(f"- {rule.text} [the user's exception allows here: {', '.join(allowed)}]")
            else:
                lines.append(f"- {rule.text}")
    reply_words = _sorted_words(t for rule in resolution.rules if rule.applies_to(TARGET_REPLY) for t in rule.terms)
    git_words = _sorted_words(t for rule in resolution.rules if rule.applies_to(TARGET_GIT) for t in rule.terms)
    if reply_words:
        lines.append("Never write these words in a reply: " + ", ".join(reply_words) + ".")
    if git_words:
        lines.append("Never put these words in a commit message, committed change, branch name, tag or push: "
                     + ", ".join(git_words) + ". VOOL refuses such git actions.")
    git_only = [t for t in git_words if t not in reply_words]
    if git_only:
        lines.append("These are banned from git only and are fine in replies: " + ", ".join(git_only) + ".")
    if resolution.lifts:
        lines.append("Where the user lifted a broader rule here, do what they ask and say in one short line "
                     "which rule their exception lifted. Do not refuse because of a lifted rule.")
    lines.append("Apply each rule exactly as written: a ban covers only the exact words and places it names. "
                 "Bans from all scopes add up unless the user lifted one for this chat or project; a chat "
                 "or project exception overrides a broader rule. When a chat or project rule conflicts "
                 "with a global rule on anything else, the narrower scope wins.")
    return "\n".join(lines)


# ── enforcement: replies ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplyReview:
    text: str
    hits: tuple[Violation, ...] = ()
    #: Exceptions that let a word through which a broader rule would have masked.
    overrides: tuple[Lift, ...] = ()


def review_reply(text: str, *, chat_id: str = "", project_id: str = "") -> ReplyReview:
    """Mask every word the rules in force here ban from replies; report words an exception let through."""
    body = str(text or "")
    if not body:
        return ReplyReview(body)
    project = str(project_id or "").strip() or chat_project_id(chat_id)
    resolution = resolve_rules(chat_id=chat_id, project_id=project)
    hits = find_banned_terms(body, [rule for rule in resolution.rules if rule.terms], target=TARGET_REPLY, where="reply")
    overrides: list[Lift] = []
    for hit in find_banned_terms(body, [rule for rule in resolution.written if rule.terms], target=TARGET_REPLY, where="reply"):
        lift = resolution.lift_for(hit.rule.rule_no, hit.term)
        if lift is not None and lift not in overrides:
            overrides.append(lift)
    masked = body
    for term in sorted({hit.term for hit in hits}, key=len, reverse=True):
        masked = _term_pattern(term).sub(REPLY_MASK, masked)
    return ReplyReview(text=masked, hits=tuple(hits), overrides=tuple(overrides))


def mask_reply_terms(text: str, *, chat_id: str = "", project_id: str = "") -> tuple[str, list[Violation]]:
    """Replace every banned word (rules in force for replies in this chat) with :data:`REPLY_MASK`."""
    review = review_reply(text, chat_id=chat_id, project_id=project_id)
    return review.text, list(review.hits)


#: The note the reply guard appends, so a second pass over an already-guarded reply finds it.
RULE_NOTE_RE = re.compile(r"\n\n\((?:Your rules? |Allowed by your exception: )[^()]*\)\s*$")


def override_note(overrides: Iterable[Lift]) -> str:
    """One line naming the rules an exception lifted, or ""."""
    unique: list[str] = []
    for lift in overrides:
        line = lift.describe()
        if line not in unique:
            unique.append(line)
    if not unique:
        return ""
    return "Allowed by your exception: " + "; ".join(unique) + "."


# ── enforcement: git ─────────────────────────────────────────────────────────


class _ScanIncomplete(RuntimeError):
    pass


def _git_text(root: Path, *argv: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *argv],
            capture_output=True,
            timeout=GIT_SCAN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise _ScanIncomplete(f"git {' '.join(argv[:2])} timed out") from exc
    except FileNotFoundError as exc:
        raise _ScanIncomplete("git is not installed") from exc
    if completed.returncode != 0:
        raise _ScanIncomplete(f"git {' '.join(argv[:2])} failed: {completed.stderr.decode('utf-8', 'replace')[:200]}")
    if len(completed.stdout) > MAX_GIT_SCAN_BYTES:
        raise _ScanIncomplete("the change is too large to check completely")
    return completed.stdout.decode("utf-8", "replace")


def _added_lines(diff_text: str) -> str:
    return "\n".join(line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))


@dataclass(frozen=True)
class GitRuleVerdict:
    """A git effect's rules verdict: a refusal, or None plus any exceptions that let words through."""

    refusal: RuleRefusal | None = None
    overrides: tuple[Lift, ...] = ()

    @property
    def note(self) -> str:
        return override_note(self.overrides)


def _git_resolution(root: Path | None, chat_id: str, project_id: str) -> Resolution:
    keys = scope_keys_for(None, chat_id=chat_id, project_id=project_id) if (chat_id or project_id) else _ledger_scope_keys()
    folder_projects = project_ids_for_path(root) if root is not None else []
    return resolve_rules(chat_id=keys["chat"], project_id=keys["project"], project_ids=folder_projects)


def _git_rules(rules: Iterable[UserRule]) -> list[UserRule]:
    return [rule for rule in rules if rule.terms and rule.applies_to(TARGET_GIT)]


def _refusal(operation: str, hits: list[Violation]) -> RuleRefusal:
    terms = sorted({hit.term for hit in hits}, key=str.lower)
    rule_nos = tuple(sorted({hit.rule.rule_no for hit in hits}))
    places = sorted({hit.where for hit in hits if hit.where})
    rules_txt = ", ".join(f"rule {n}" for n in rule_nos)
    where_txt = f" in the {', '.join(places)}" if places else ""
    reason = (f"Refused: this git {operation} carries {', '.join(repr(t) for t in terms)}{where_txt}, which "
              f"your {rules_txt} bans from git. Nothing was {'committed' if operation == 'commit' else 'run'}. "
              "To allow it here, say for example \"in this chat, allow "
              f"{terms[0] if terms else 'that word'}\" or remove the rule.")
    return RuleRefusal(operation=operation, reason=reason, violations=tuple(hits), rule_nos=rule_nos)


def _classify(operation: str, hits: list[Violation], resolution: Resolution) -> GitRuleVerdict:
    """Split matches against the rules AS WRITTEN into what an exception lifted and what still binds."""
    binding: list[Violation] = []
    overrides: list[Lift] = []
    for hit in hits:
        lift = resolution.lift_for(hit.rule.rule_no, hit.term)
        if lift is None:
            binding.append(hit)
        elif lift not in overrides:
            overrides.append(lift)
    return GitRuleVerdict(refusal=_refusal(operation, binding) if binding else None, overrides=tuple(overrides))


def git_outbound_check(
    repo_root: str | Path,
    operation: str,
    *,
    message: str = "",
    names: Iterable[str] = (),
    staged: bool = False,
    tracked_changes: bool = False,
    rev_range: tuple[str, str] | None = None,
    unpublished_head: str = "",
    unpublished_revs: tuple[str, ...] = (),
    chat_id: str = "",
    project_id: str = "",
) -> GitRuleVerdict:
    """The rules verdict for a git effect's outbound content.

    Content checked (only what this effect ADDS): ``message``; ``names`` (branch, tag, ref);
    the staged diff when ``staged``; tracked working-tree changes when ``tracked_changes``
    (``commit -a``); for a push, commit messages, added lines and file names of
    ``rev_range`` (base, head), or of every commit reachable from ``unpublished_head`` that no
    remote has yet. A word a rule bans refuses the effect unless this chat's or project's
    exception lifted that rule here; then the verdict names the exception instead. A check that
    cannot read the content completely refuses while any ban is in force: an unread commit is
    not a clean one.
    """
    root = Path(repo_root)
    resolution = _git_resolution(root, chat_id, project_id)
    written = _git_rules(resolution.written)
    if not written:
        return GitRuleVerdict()
    hits: list[Violation] = []
    hits += find_banned_terms(message, written, target=TARGET_GIT, where="message")
    for name in names:
        hits += find_banned_terms(str(name or ""), written, target=TARGET_GIT, where="branch or tag name")
    try:
        if staged:
            hits += find_banned_terms(_added_lines(_git_text(root, "diff", "--cached", "--no-color", "--no-ext-diff", "-U0")),
                                      written, target=TARGET_GIT, where="committed changes")
            hits += find_banned_terms(_git_text(root, "diff", "--cached", "--name-only", "--diff-filter=ACR"),
                                      written, target=TARGET_GIT, where="file names")
        if tracked_changes:
            hits += find_banned_terms(_added_lines(_git_text(root, "diff", "--no-color", "--no-ext-diff", "-U0")),
                                      written, target=TARGET_GIT, where="committed changes")
        if rev_range is not None or unpublished_head or unpublished_revs:
            if rev_range is not None:
                base, head = (str(rev_range[0] or "").strip(), str(rev_range[1] or "").strip())
                log_args = (f"{base}..{head}",) if base else (head, "--not", "--remotes")
            else:
                # Everything these sources would send that no remote has yet.
                log_args = (*([unpublished_head] if unpublished_head else []), *unpublished_revs, "--not", "--remotes")
            messages = _git_text(root, "log", "--no-color", "--format=%B%x00", *log_args)
            hits += find_banned_terms(messages, written, target=TARGET_GIT, where="commit messages")
            patches = _git_text(root, "log", "--no-color", "--no-ext-diff", "--format=", "-p", "-U0", *log_args)
            hits += find_banned_terms(_added_lines(patches), written, target=TARGET_GIT, where="pushed changes")
            files = _git_text(root, "log", "--no-color", "--format=", "--name-only", "--diff-filter=ACR", *log_args)
            hits += find_banned_terms(files, written, target=TARGET_GIT, where="file names")
    except _ScanIncomplete as exc:
        binding = _git_rules(resolution.rules)
        if not binding:
            return GitRuleVerdict()  # every ban is lifted here; there is nothing left to enforce
        return GitRuleVerdict(refusal=RuleRefusal(
            operation=operation,
            reason=(f"Refused: your rules ban words from git, and I couldn't check this {operation} completely "
                    f"({exc}). Nothing was {'committed' if operation == 'commit' else 'run'}."),
            incomplete=True,
            rule_nos=tuple(rule.rule_no for rule in binding),
        ))
    return _classify(operation, hits, resolution)


def git_outbound_refusal(repo_root: str | Path, operation: str, **content: Any) -> RuleRefusal | None:
    """:func:`git_outbound_check`, refusal only."""
    return git_outbound_check(repo_root, operation, **content).refusal


# ── enforcement: shell git (sandbox.run_command) ─────────────────────────────

_GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
_GIT_OUTBOUND = {"commit", "tag", "push", "merge", "branch", "switch", "checkout", "notes", "am", "cherry-pick", "revert"}


def _git_subcommand(argv: list[str]) -> tuple[str, list[str], str]:
    """``(subcommand, rest, -C path)`` of a git argv, skipping git's global options."""
    index = 1
    repo = ""
    while index < len(argv):
        token = argv[index]
        if token in _GIT_GLOBAL_WITH_VALUE:
            if token == "-C" and index + 1 < len(argv):
                repo = argv[index + 1]
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token, argv[index + 1:], repo
    return "", [], repo


def _option_values(rest: list[str], short: str, long: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in {short, long} and index + 1 < len(rest):
            values.append(rest[index + 1])
            index += 2
            continue
        if token.startswith(long + "="):
            values.append(token.split("=", 1)[1])
        elif (short and len(short) == 2 and token.startswith("-") and not token.startswith("--")
              and len(token) > 2 and token.endswith(short[1]) and index + 1 < len(rest)):
            # a short-flag cluster ending in the option letter ("-am <message>")
            values.append(rest[index + 1])
            index += 2
            continue
        elif short and token.startswith(short) and len(token) > len(short) and not token.startswith("--"):
            values.append(token[len(short):])
        index += 1
    return values


#: Options that take a separate value, per subcommand, so a value is never read as a ref or a name.
_GIT_VALUE_OPTIONS = {
    "commit": {"-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message", "-t",
               "--template", "--author", "--date", "--fixup", "--squash", "--trailer", "--cleanup",
               "--pathspec-from-file"},
    "tag": {"-m", "--message", "-F", "--file", "-u", "--local-user", "--cleanup", "--sort", "--format",
            "--contains", "--no-contains", "--points-at", "--merged", "--no-merged"},
    "push": {"-o", "--push-option", "--repo", "--receive-pack", "--exec"},
    "merge": {"-m", "--message", "-F", "--file", "-s", "--strategy", "-X", "--strategy-option", "--cleanup"},
    "notes": {"-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message", "--ref"},
}
#: Largest message file read for a check; a larger one cannot be checked completely.
MAX_MESSAGE_FILE_BYTES = 1024 * 1024


class _Unreadable(RuntimeError):
    """Part of what the command would send could not be read, so the check is incomplete."""


def _positional(sub: str, rest: list[str]) -> list[str]:
    takes_value = _GIT_VALUE_OPTIONS.get(sub, set())
    out: list[str] = []
    skip = False
    for token in rest:
        if skip:
            skip = False
            continue
        if token == "--":
            continue
        if token.startswith("-"):
            if token in takes_value:
                skip = True
            continue
        out.append(token)
    return out


def _message_sources(sub: str, rest: list[str], root: Path | None) -> str:
    """Every message this command would record: -m, -F/--file (a file, never stdin), a message
    reused from another commit (-C/-c, --fixup/--squash), a template, and the HEAD message an
    --amend keeps. Raises :class:`_Unreadable` when one of them cannot be read."""
    parts = list(_option_values(rest, "-m", "--message"))
    for path_text in _option_values(rest, "-F", "--file"):
        if path_text == "-":
            raise _Unreadable("the message comes from standard input")
        parts.append(_read_message_file(path_text, root))
    if sub == "commit":
        for path_text in _option_values(rest, "-t", "--template"):
            parts.append(_read_message_file(path_text, root))
        reused = [*_option_values(rest, "-C", "--reuse-message"), *_option_values(rest, "-c", "--reedit-message")]
        for spec in (*_option_values(rest, "", "--fixup"), *_option_values(rest, "", "--squash")):
            reused.append(spec.split(":", 1)[1] if spec.startswith(("amend:", "reword:")) else spec)
        if "--amend" in rest and not parts and not reused:
            reused.append("HEAD")
        for rev in reused:
            if root is None:
                raise _Unreadable("the reused message's repository is unknown")
            try:
                parts.append(_git_text(root, "log", "-1", "--no-color", "--format=%B", rev))
            except _ScanIncomplete as exc:
                raise _Unreadable(f"the reused message {rev!r} could not be read") from exc
    return "\n".join(parts)


def _read_message_file(path_text: str, root: Path | None) -> str:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        if root is None:
            raise _Unreadable(f"the message file {path_text!r} has no known directory")
        path = root / path
    try:
        size = path.stat().st_size
        if size > MAX_MESSAGE_FILE_BYTES:
            raise _Unreadable(f"the message file {path_text!r} is too large to check")
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise _Unreadable(f"the message file {path_text!r} could not be read") from exc


def _push_refspecs(rest: list[str]) -> list[str]:
    """The refspecs of a ``git push``. The first positional names the remote unless ``--repo``
    already did, in which case every positional is a refspec."""
    positional = _positional("push", rest)
    if any(token == "--repo" or token.startswith("--repo=") for token in rest):
        return positional
    return positional[1:]


def _push_sources(rest: list[str], root: Path) -> tuple[list[str], list[str], list[str]]:
    """What a ``git push`` would send: ``(commit sources, extra log revs, ref names)``.

    Each refspec's SOURCE is resolved to the commit it names (never HEAD by default), so a clean
    checkout cannot carry another branch's banned commit out. ``--all``/``--branches``,
    ``--mirror`` and ``--tags`` send every matching local ref, and so does the matching refspec
    ``:``. With no refspec git first follows any configured ``remote.<name>.push`` refspecs, which
    are checked as every branch and tag; otherwise ``push.default``: ``matching`` sends every
    branch, anything else the current branch. A refspec that cannot be resolved, or a wildcard,
    cannot be checked and raises :class:`_Unreadable`.
    """
    refspecs = _push_refspecs(rest)
    sources: list[str] = []
    revs: list[str] = []
    names: list[str] = []
    if "--mirror" in rest:
        revs.append("--all")
        names += _git_text(root, "for-each-ref", "--format=%(refname:short)").splitlines()
    if {"--all", "--branches"} & set(rest):
        revs.append("--branches")
        names += _git_text(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    if "--tags" in rest:
        revs.append("--tags")
        names += _git_text(root, "for-each-ref", "--format=%(refname:short)", "refs/tags").splitlines()
    index = 0
    while index < len(refspecs):
        spec = refspecs[index].lstrip("+")
        if spec == "tag" and index + 1 < len(refspecs):
            spec = f"refs/tags/{refspecs[index + 1]}"
            index += 1
        index += 1
        if "*" in spec:
            raise _Unreadable(f"the wildcard refspec {spec!r} cannot be checked")
        src, _, dst = spec.partition(":")
        if not src and not dst:
            # ":" is the matching refspec: every branch that also exists on the remote is sent
            revs.append("--branches")
            names += _git_text(root, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
            continue
        names += [part for part in (src, dst) if part]
        if not src:
            continue  # deleting a remote ref sends no content
        try:
            sources.append(_git_text(root, "rev-parse", "--verify", "--quiet", f"{src}^{{commit}}").strip())
        except _ScanIncomplete as exc:
            raise _Unreadable(f"the push source {src!r} could not be resolved") from exc
    if not refspecs and not revs:
        try:
            configured = _git_text(root, "config", "--get-regexp", r"^remote\..*\.push$").strip()
        except _ScanIncomplete:
            configured = ""  # none configured
        if configured:
            # a configured push refspec decides what is sent; check every branch and tag it could name
            revs += ["--branches", "--tags"]
            names += _git_text(root, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/tags").splitlines()
            return sources, revs, names
        try:
            mode = _git_text(root, "config", "--get", "push.default").strip().lower()
        except _ScanIncomplete:
            mode = ""  # unset: git's default sends the current branch
        if mode == "matching":
            revs.append("--branches")
        else:
            sources.append("HEAD")
    return sources, revs, names


def sandbox_git_check(argv: list[str], *, cwd: str = "") -> GitRuleVerdict:
    """The rules verdict for a git command run through the shell tool, read from its argv.

    Covers every message the command would record (-m, -F/--file, reused and amended messages),
    branch and tag names, and push refs; and, when the repository is known, what a commit would
    add and what each pushed ref would send. Used by the command gate before any approval is
    asked, so nobody is asked to approve a command a rule will refuse. Anything that cannot be
    read completely refuses while a ban is in force.
    """
    if not argv:
        return GitRuleVerdict()
    sub, rest, repo_opt = _git_subcommand(argv)
    if sub not in _GIT_OUTBOUND:
        return GitRuleVerdict()
    root: Path | None = None
    if repo_opt:
        root = Path(repo_opt).expanduser()
        if not root.is_absolute() and cwd:
            root = Path(cwd) / root  # `git -C` is relative to where git starts
    elif cwd:
        root = Path(cwd)
    keys = _ledger_scope_keys()
    resolution = _git_resolution(root, keys["chat"], keys["project"])
    written = _git_rules(resolution.written)
    if not written:
        return GitRuleVerdict()
    in_repo = root is not None and _is_inside_repo(root)

    def _incomplete(reason: str) -> GitRuleVerdict:
        binding = _git_rules(resolution.rules)
        if not binding:
            return GitRuleVerdict()  # every ban is lifted here
        return GitRuleVerdict(refusal=RuleRefusal(
            operation=sub,
            reason=(f"Refused: your rules ban words from git, and I couldn't check this git {sub} completely "
                    f"({reason}). Nothing was {'committed' if sub == 'commit' else 'run'}."),
            incomplete=True,
            rule_nos=tuple(rule.rule_no for rule in binding),
        ))

    try:
        message = _message_sources(sub, rest, root if in_repo else (root or None))
    except _Unreadable as exc:
        return _incomplete(str(exc))
    positional = _positional(sub, rest)
    names: list[str] = []
    if sub in {"tag", "branch", "switch", "checkout"}:
        names = list(positional)
    hits: list[Violation] = []
    hits += find_banned_terms(message, written, target=TARGET_GIT, where="message")
    for name in names:
        hits += find_banned_terms(name, written, target=TARGET_GIT, where="branch or tag name")
    if sub == "push":
        for token in _push_refspecs(rest):
            hits += find_banned_terms(token.lstrip("+"), written, target=TARGET_GIT, where="push ref")
    verdict = _classify(sub, hits, resolution)
    if verdict.refusal is not None or not in_repo:
        return verdict
    content = GitRuleVerdict()
    if sub == "commit":
        content = git_outbound_check(root, "commit", staged=True, tracked_changes=_commit_all(rest),
                                     chat_id=keys["chat"], project_id=keys["project"])
    elif sub == "push":
        try:
            sources, revs, ref_names = _push_sources(rest, root)
        except (_Unreadable, _ScanIncomplete) as exc:
            return _incomplete(str(exc))
        content = git_outbound_check(root, "push", names=ref_names, unpublished_revs=(*sources, *revs),
                                     chat_id=keys["chat"], project_id=keys["project"])
    overrides = tuple(dict.fromkeys((*verdict.overrides, *content.overrides)))
    return GitRuleVerdict(refusal=content.refusal, overrides=overrides)


def sandbox_git_refusal(argv: list[str], *, cwd: str = "") -> RuleRefusal | None:
    """:func:`sandbox_git_check`, refusal only."""
    return sandbox_git_check(argv, cwd=cwd).refusal


def _commit_all(rest: list[str]) -> bool:
    """``git commit -a`` / ``--all`` / a cluster like ``-am``: tracked changes are committed too."""
    messages = set(_option_values(rest, "-m", "--message"))
    for token in rest:
        if token in messages:
            continue
        if token in {"-a", "--all"}:
            return True
        if token.startswith("-") and not token.startswith("--") and len(token) > 2:
            cluster = token[1:]
            if "a" in cluster and ("m" not in cluster or cluster.index("a") < cluster.index("m")):
                return True
    return False


def _is_inside_repo(path: Path) -> bool:
    try:
        completed = subprocess.run(["git", "-C", str(path), "rev-parse", "--git-dir"], capture_output=True,
                                   timeout=GIT_SCAN_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    return completed.returncode == 0


# ── the chat turn seat ───────────────────────────────────────────────────────

#: Set on a turn's source_context when this lane answered it; the reply guard leaves that reply
#: alone (it quotes the user's own rules back to them).
RULES_TURN_MARKER = "_user_rules_command"
#: The turn facts the rules lane files: a write ("rules.add" / "rules.remove") or any other answer it gave.
RULES_TURN_TOOLS = frozenset({"rules.add", "rules.remove", "rules.answer"})


def _record_turn_fact(tool: str, *, session_id: str, source_context: Mapping[str, Any] | None,
                      detail: dict[str, Any]) -> None:
    """File the rules command as a turn-scoped execution fact (core.execution_truth).

    The agent answers on a copy of the turn context, so the receipt and the marker it writes there
    never reach the served boundary that re-checks the sealed reply (K-08). The execution ledger is
    keyed by the turn itself and is what that re-check reads, so "Saved rule N" stays backed and the
    rules answer stays exempt from word masking on both passes.
    """
    try:
        from core.execution_truth import KIND_TOOL, record_execution, resolve_turn_key

        context = dict(source_context or {})
        # The agent's copy carries its own turn id; the served boundary resolves the same turn by its
        # request id. File the fact under both so either side finds it.
        keys = {resolve_turn_key(context, None), str(context.get("request_id") or "").strip()} - {""}
        for turn_key in sorted(keys):
            record_execution(session_id=str(session_id or ""), turn_key=turn_key, kind=KIND_TOOL, name=tool,
                             ok=True, status="executed", detail=detail,
                             dedupe=f"{tool}:{json.dumps(detail, sort_keys=True, default=str)}")
    except Exception:
        pass


def rules_command_owns_forget(text: str) -> bool:
    """Whether a "forget/remove the rule ..." turn is wholly a rules removal (no second forget in it)."""
    command = parse_rule_command(text)
    return (command is not None and command.action in {"remove_about", "remove_numbers", "remove_all"}
            and not re.search(r"\b(?:forget|erase)\b", command.needle or "", re.IGNORECASE))


def turn_answered_rules_command(session_id: str | None, source_context: Mapping[str, Any] | None) -> bool:
    """Whether the rules lane itself answered this turn (marker on this context, or its turn fact)."""
    if isinstance(source_context, Mapping) and source_context.get(RULES_TURN_MARKER):
        return True
    try:
        from core.execution_truth import facts_for_turn, resolve_turn_key

        turn_key = resolve_turn_key(dict(source_context or {}), None)
        if not turn_key:
            return False
        return any(fact.name in RULES_TURN_TOOLS and fact.ok
                   for fact in facts_for_turn(turn_key, session_id=str(session_id or "").strip()))
    except Exception:
        return False


def _record_receipt(kind: str, rules: list[UserRule], *, session_id: str, source_context: dict[str, Any] | None) -> None:
    """A rule write is real work, so it carries an executed receipt the final-action honesty gate
    can check "Saved rule N" against, in the turn context and in the durable receipt store."""
    tool = "rules.add" if kind == "saved" else "rules.remove"
    execution = {
        "executed": True,
        "ok": True,
        "status": "executed",
        "tool": tool,
        "rule_ids": [rule.rule_id for rule in rules],
        "rule_nos": [rule.rule_no for rule in rules],
        "kind": kind,
    }
    receipt = {"tool_name": tool, "intent": tool, "execution": execution, "status": "executed"}
    if isinstance(source_context, dict):
        receipts = source_context.get("tool_receipts")
        if not isinstance(receipts, list):
            receipts = []
            source_context["tool_receipts"] = receipts
        receipts.append(receipt)
    _record_turn_fact(tool, session_id=session_id, source_context=source_context,
                      detail={"rule_ids": execution["rule_ids"], "kind": kind})
    try:
        from core.runtime_continuity import build_tool_receipt_key, store_tool_receipt

        checkpoint = str((source_context or {}).get("runtime_checkpoint_id") or "")
        key = build_tool_receipt_key(
            checkpoint_id=checkpoint or session_id,
            step_index=max((rule.rule_no for rule in rules), default=0),
            intent=tool,
            arguments={"rule_ids": execution["rule_ids"], "kind": kind},
        )
        store_tool_receipt(
            receipt_key=key,
            session_id=session_id,
            checkpoint_id=checkpoint,
            tool_name=tool,
            idempotency_key=f"{','.join(execution['rule_ids'])}:{kind}",
            arguments={"scopes": sorted({rule.scope for rule in rules})},
            execution=execution,
        )
    except Exception:
        pass


def _take_back_standing(text: str, *, session_id: str, source_context: Mapping[str, Any] | None,
                        project_id: str) -> list[str]:
    """"Forget the rule about X" with no rule here about X: take back the owner's standing instructions
    about X (core.standing_instructions), the rules the owner gave in plain words ("from now on ...").

    Same guards as that store's own take-back: the owner's own words only, in the turn's workspace.
    """
    command = parse_rule_command(text)
    if command is None or command.action != "remove_about":
        return []
    try:
        from core import operator_profile
        from core import standing_instructions as standing
        from core.request_trust import request_is_owner_local, turn_is_owner_authored

        context = dict(source_context or {})
        if not (request_is_owner_local(context) and turn_is_owner_authored(context)):
            return []
        principal = operator_profile.principal_for_request(context)
        if not principal:
            return []
        project = str(project_id or context.get("_trusted_project_id") or "")
        workspace = standing.workspace_key(context, project)
        words = standing._topic(command.needle)
        removed = standing._remove_matching(principal, workspace, words)
    except Exception:
        return []
    if removed:
        _record_turn_fact("rules.remove", session_id=session_id, source_context=source_context,
                          detail={"standing_instructions": removed, "kind": "taken_back"})
    return removed


def _taken_back_report(removed: list[str]) -> str:
    quoted = "; ".join(f"\"{text}\"" for text in removed)
    noun = "instruction" if len(removed) == 1 else "instructions"
    return f"Took back your standing {noun}: {quoted}. {'It no longer applies' if len(removed) == 1 else 'They no longer apply'}."


def observe_rules_turn(
    agent: Any,
    raw_user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    access_policy: Any | None = None,
) -> dict[str, Any] | None:
    """Answer a rules command (add / list / remove) deterministically, or return None.

    Claims the whole turn: the reply states exactly what the store now holds, so "saved" is never
    said for a rule that was not written.
    """
    if parse_rule_command(raw_user_input) is None:
        return None
    from core.request_trust import request_is_owner_local

    keys = scope_keys_for(
        source_context,
        chat_id=str(getattr(access_policy, "chat_id", "") or "") or session_id,
        project_id=str(getattr(access_policy, "project_id", "") or ""),
    )
    turn_id = str((source_context or {}).get("cancel_turn_id") or (source_context or {}).get("_canonical_user_turn_id") or "")
    try:
        handled = handle_rule_command(
            raw_user_input,
            chat_id=keys["chat"],
            project_id=keys["project"],
            owner_local=request_is_owner_local(source_context),
            turn_id=turn_id,
            secret_carved=bool((source_context or {}).get("_secret_intake_consumed")),
        )
    except Exception as exc:
        handled = (f"I couldn't read or write your rules ({type(exc).__name__}), so nothing changed.", [], "refused")
    if handled is None:
        return None
    reply, changed, kind = handled
    if kind == "none":
        taken_back = _take_back_standing(raw_user_input, session_id=session_id, source_context=source_context,
                                         project_id=str(getattr(access_policy, "project_id", "") or ""))
        if taken_back:
            reply = _taken_back_report(taken_back)
            kind = "taken_back"
    if isinstance(source_context, dict):
        source_context[RULES_TURN_MARKER] = kind
    if kind in {"saved", "removed"} and changed:
        _record_receipt(kind, changed, session_id=session_id, source_context=source_context)
    else:
        # A listing or a refusal quotes the user's rules back; it writes nothing, but the served
        # re-check must still know this answer came from the rules lane.
        _record_turn_fact("rules.answer", session_id=session_id, source_context=source_context,
                          detail={"kind": kind})
    return {
        "result": agent._fast_path_result(
            session_id=session_id,
            user_input=raw_user_input,
            response=reply,
            confidence=0.97,
            source_context=source_context,
            reason="user_rules_command",
        )
    }
