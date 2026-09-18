"""Deterministic demand signals for the tool-offer authority.

The offer seam (``capability_graph.model_visible_specs``) needs to know, BEFORE any model
is consulted, which tools the user's own words explicitly asked for and which capability
families those words require. That is the same deterministic-first law the rest of the
front door follows: shapes compute before model generation.

Design constraints, each carrying a census finding (a6c8e3c4):

- EXPLICIT beats RANKED. ``run the test suite`` classified ``debugging`` seats the
  workspace family, whose read-only-first ranking evicted ``workspace.run_tests`` from
  every offer. An intent named by the user's words is seated ahead of family ranking.
- MIXED demands name MULTIPLE families. ``read README.md and search the web`` used to
  collapse to whichever family the classifier picked first, starving the other half.
- WRITE demands seat a mutation tool. The offer is a PROPOSAL: the existing permission
  controller still decides execution. Nothing here widens any gate.

This module never executes anything, never reads policy (availability is decided at
seating time against live candidates), and is deliberately conservative: noun+verb
anchors, no fuzzy matching, no second task classifier. Unknown text yields no signals,
which keeps the previous single-family behaviour untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bounded so a single turn cannot flood its own offer with explicit seats.
MAX_EXPLICIT_INTENTS = 3
MAX_REQUIRED_FAMILIES = 3


@dataclass(frozen=True)
class DemandSignals:
    """What the user's words deterministically asked for.

    ``explicit_intents``    concrete tool intents named by the request
    ``required_families``   capability families the request needs (may be several)
    ``write_demand``        the request asks to change something (proposal only; the
                            permission controller still decides execution)
    """

    explicit_intents: tuple[str, ...] = ()
    required_families: tuple[str, ...] = ()
    write_demand: bool = False


# Each rule: (intent, family, pattern). Patterns are noun+verb anchored phrases, matched
# case-insensitively against the whole message. Order is irrelevant to the result; the
# tuples exist so a single compiled pass covers everything.
#
# C18 FINAL CORRECTION — CONTEXT IS NOT AUTHORITY: a pasted command or test artifact ("python
# -m pytest -q", "test_calc.py") proves code CONTEXT only. The REPO_COMMAND_ANCHOR rule that
# seated `code.task.open` on such artifacts alone was REMOVED: it made "Explain this command:
# python -m pytest -q test_calc.py" and "Do not run this; what does ... mean?" authorize the
# code-task control plane. The control plane is seated by typed action demand (the verb-anchored
# rule below, or the bounded mutation+verification predicate); code context without action
# demand fails closed. No phrase regex replaced it — untranslated execution demands stay
# PARTIAL for the C12 owner rather than being authorized by artifacts.

# A repair directive can name the failing code check without naming its directory.
# Only imperative clauses qualify; advice, negation and authoring retain their gates below.
_CODE_REPAIR_ACTION_RE = re.compile(
    r"(?:^|[.;!?]\s*|\b(?:and|then)\s+)(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:fix|repair|correct|resolve|patch)\b", re.IGNORECASE,
)
_CODE_CHECK_CONTEXT_RE = re.compile(
    r"\b(?:tests?|test\s+suite|unit\s+tests?|compiler|compilation|lint|build|assertion)\b",
    re.IGNORECASE,
)

def is_explicit_code_repair(text: str) -> bool:
    """A repair instruction with a code-check subject, shared by reach and tool seating."""
    from core.instructional_request import asks_for_instructions_not_execution

    return bool(
        _CODE_REPAIR_ACTION_RE.search(text)
        and _CODE_CHECK_CONTEXT_RE.search(text)
        and not asks_for_instructions_not_execution(text)
    )


_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # -- repository operations (RepoOps control plane) ------------------------
    # GitHub-management demands -- pull requests, issues, CI truth, the typed git vertical --
    # seat the RepoOps control plane (core/repoops) the same way a repair demand seats the
    # code-task plane. This puts the family on the table ONLY: every forge WRITE still sits
    # behind its operator authorization whatever the words say, and a PR number in an issue
    # body remains untrusted data. The pattern requires repository vocabulary together with
    # an action or truth noun; plain prose that merely mentions a PR does not seat anything.
    (
        "repo.session.open",
        "repo",
        re.compile(
            r"\b(?:pull\s+request|PR\b|merge\s+request)\b[^.?!]{0,80}?\b(?:open|opened|inspect|review|describe|prepare|check|checks?|CI\b|diff|branch|fail\w*|draft|comment|update|summar\w*|bind|session)\b"
            r"|\b(?:inspect|review|open|check|prepare|describe|summarize|comment\s+on|bind)\b[^.?!]{0,80}?\b(?:pull\s+request|PR\b|merge\s+request|repo(?:sitory)?\s+session)\b"
            r"|\b(?:CI\b|checks?|fail\w*|issue)\b[^.?!]{0,80}?\b(?:pull\s+request|PR\b|merge\s+request)\b"
            r"|\b(?:github|gitlab)\b[^.?!]{0,80}?\b(?:issue|pull\s+request|PR\b|CI\b|checks?|branch|repository|repo)\b"
            r"|\bwhich\s+(?:current\s+)?checks?\b[^.?!]{0,30}?\bfail\w*\b",
            re.I,
        ),
    ),
    # -- coding task control plane -------------------------------------------
    # A repair demand -- find/diagnose/fix WHY something in the tree fails -- seats the code-task
    # control plane (core/code_assistant), whose root-cause workflow the runtime then enforces.
    # The model still chooses; this only puts `code.task.open` on the table.
    (
        "code.task.open",
        "code",
        re.compile(
            r"\b(?:fix|repair|debug|diagnose|find\s+(?:out\s+)?why|figure\s+out\s+why|root[\s-]?cause)\b[^.?!]{0,80}?\b(?:fails?|failing|failure|broken|breaks?|bug|defect|regression|error|exception|traceback)\b",
            re.I,
        ),
    ),
    # -- wallet (propose/read only; approval is never a tool) ----------------
    # a decimal amount ("0.0005 SOL", "0.001 ETH") is not a sentence boundary; the native coins of every pilot row count
    ("wallet.propose", "wallet", re.compile(r"\b(?:pay|send|transfer|prepare|propose)\b(?:[^.?!]|\.(?=\d)){0,60}?\b(?:lamports?|sol|eth|bnb|usdc)\b(?:[^.?!]|\.(?=\d)){0,60}?\bto\b", re.I)),
    # -- contacts (the saved people and services; resolution itself happens inside each consumer) -----------
    # possession or the address-book name, never a bare mention: "contact card"/"Contacts are stored locally"
    # is prose about contacts, not a demand on them (the design-review boundary this base pins)
    ("contacts.search", "contacts", re.compile(r"\b(?:(?:my|our|the)\s+contacts?|address\s+book)\b(?![-\s]*(?:us|form|page|support|card)\b)", re.I)),
    ("contacts.resolve", "contacts", re.compile(r"\b(?:messaging|telegram|signal|whatsapp|slack|discord|matrix|teams)\s+(?:address|handle|id|username|identity|details)\b|\b(?:email\s+address|phone\s+number|wallet\s+address)\s+(?:for|of)\b", re.I)),
    # a possessive entry update ("Update Alex Chen's Solana devnet wallet with the one in the attached invoice",
    # "change Tom's phone number") is a Contacts write/lookup on one saved person, never a settings change
    ("contacts.update", "contacts", re.compile(r"\b(?:update|change|replace|edit)\b[^.?!]{0,60}?\w+'s\s+(?:\w+\s+){0,2}?(?:wallet|email|phone|handle|address)\b", re.I)),
    ("wallet.payment_status", "wallet", re.compile(r"\bpay-[0-9a-f]{20}\b|\b(?:status|state)\b[^.?!]{0,40}?\b(?:payment|proposal)\b", re.I)),
    ("wallet.simulate", "wallet", re.compile(r"\bsimulate\b[^.?!]{0,40}?\b(?:payment|proposal|transaction)\b", re.I)),
    ("x402.propose", "wallet", re.compile(r"\bx402\b[^.?!]{0,80}?\b(?:fetch|get|access|pay|paid|buy|resource|offer)\b|\b(?:fetch|access)\b[^.?!]{0,60}?\bpaid\b", re.I)),
    ("wallet.status", "wallet", re.compile(r"\bwallet\b[^.?!]{0,30}?\b(?:status|balance|limits?)\b|\b(?:my|the)\s+wallet\b", re.I)),
    # -- workspace execution/validation -------------------------------------
    (
        "workspace.run_tests",
        "workspace",
        re.compile(
            r"\b(?:run|running|execute|executing|launch|launching)\b[^.?!]{0,40}?\b(?:unit\s+tests?|tests?\s+suite|test\s+suite|tests?)\b",
            re.I,
        ),
    ),
    (
        "workspace.run_lint",
        "workspace",
        re.compile(
            r"\b(?:run|running|execute|executing)\b[^.?!]{0,30}?\blint\b|\blint\s+(?:the\s+)?(?:code|repo|workspace|project)\b",
            re.I,
        ),
    ),
    (
        "workspace.run_formatter",
        "workspace",
        re.compile(
            r"\b(?:run|running|format|formatting)\b[^.?!]{0,30}?\b(?:formatter|prettier|black|ruff format)\b", re.I
        ),
    ),
    # -- workspace mutation --------------------------------------------------
    (
        "workspace.write_file",
        "workspace",
        re.compile(
            r"\b(?:write|create|save|overwrite|append)\b[^.?!]{0,60}?\b(?:file|\.md|\.txt|\.json|\.py|\.js|\.ts|\.yaml|\.yml|\.toml|\.csv|\.html|\.css)\b",
            re.I,
        ),
    ),
    (
        "workspace.replace_in_file",
        "workspace",
        re.compile(
            r"\b(?:edit|replace|substitute|patch)\b[^.?!]{0,40}?\b(?:in\s+(?:the\s+)?file|inside\s+(?:the\s+)?file|in\s+it)\b",
            re.I,
        ),
    ),
    ("workspace.apply_unified_diff", "workspace", re.compile(r"\bapply\b[^.?!]{0,20}?\b(?:unified\s+)?diff\b", re.I)),
    ("workspace.git_status", "workspace", re.compile(r"\bgit\s+status\b", re.I)),
    ("workspace.git_diff", "workspace", re.compile(r"\bgit\s+diff\b", re.I)),
    (
        "workspace.rollback_last_change",
        "workspace",
        re.compile(
            r"\b(?:rollback|roll\s+back|revert)\b[^.?!]{0,20}?\b(?:last\s+change|last\s+edit|that\s+change)\b", re.I
        ),
    ),
    # -- workspace reads ------------------------------------------------------
    (
        "workspace.read_file",
        "workspace",
        re.compile(r"\b(?:read|open|show|display|print|cat)\b[^.?!]{0,40}?\b[\w./-]+\.\w{1,6}\b", re.I),
    ),
    (
        "workspace.list_files",
        "workspace",
        re.compile(
            r"\b(?:list|show|what)\b[^.?!]{0,30}?\bfiles?\b[^.?!]{0,20}?\b(?:here|workspace|repo|repository|project|directory|folder)\b",
            re.I,
        ),
    ),
    (
        "workspace.search_text",
        "workspace",
        re.compile(
            r"\b(?:search|grep|find)\b[^.?!]{0,40}?\b(?:in\s+(?:the\s+)?(?:workspace|repo|repository|codebase|project)|across\s+(?:the\s+)?(?:workspace|repo))\b",
            re.I,
        ),
    ),
    # -- web -------------------------------------------------------------------
    (
        "web.search",
        "web",
        re.compile(
            r"\b(?:search|google|look\s+up|find)\b[^.?!]{0,60}?\b(?:web|online|internet|google|bing|duckduckgo)\b", re.I
        ),
    ),
    ("web.fetch", "web", re.compile(r"\b(?:fetch|open|load|check)\b[^.?!]{0,40}?https?://", re.I)),
    (
        "web.research",
        "web",
        re.compile(r"\bresearch\b[^.?!]{0,40}?\b(?:online|web|sources?|for)\b|\bdo\s+(?:some\s+)?research\b", re.I),
    ),
    # -- pdf -------------------------------------------------------------------
    (
        "pdf.extract_text",
        "pdf",
        re.compile(
            r"\b(?:extract|get|pull|read)\b[^.?!]{0,30}?\b(?:the\s+)?text\b[^.?!]{0,20}?\b\.?pdf\b|\bpdf\b[^.?!]{0,30}?\b(?:text|extract)",
            re.I,
        ),
    ),
    (
        "pdf.ocr",
        "pdf",
        re.compile(r"\bocr\b[^.?!]{0,30}?\b(?:pdf|scan|scanned)\b|\b(?:pdf|scan|scanned)\b[^.?!]{0,30}?\bocr\b", re.I),
    ),
    # -- machine / filesystem ---------------------------------------------------
    (
        "machine.list_directory",
        "filesystem",
        re.compile(
            r"\b(?:list|show|what'?s|how many)\b[^.?!]{0,40}?\b(?:downloads|desktop|documents|folder|directory|files?)\b",
            re.I,
        ),
    ),
    (
        "machine.disk_usage",
        "filesystem",
        re.compile(r"\b(?:disk|drive)\b[^.?!]{0,20}?\b(?:space|usage|free)\b|\bhow\s+much\s+(?:disk|space)\b", re.I),
    ),
    (
        "machine.list_processes",
        "filesystem",
        re.compile(r"\b(?:running|heaviest|top)\b[^.?!]{0,20}?\bprocesses\b|\blist\s+processes\b", re.I),
    ),
    (
        "machine.inspect_specs",
        "filesystem",
        re.compile(
            r"\b(?:machine|host|hardware)\b[^.?!]{0,20}?\b(?:specs?|specifications?)\b|\bwhat\s+(?:chip|hardware|ram)\b",
            re.I,
        ),
    ),
    # -- machine mutations (safe-local-dir writes; the permission gate still decides) --
    (
        "machine.write_file",
        "filesystem",
        re.compile(r"\b(?:write|save|create)\b[^.?!]{0,40}?\b(?:downloads|desktop|documents)\b", re.I),
    ),
    (
        "machine.ensure_directory",
        "filesystem",
        re.compile(
            r"\b(?:create|make|new)\b[^.?!]{0,20}?\b(?:folder|directory)\b[^.?!]{0,20}?\b(?:in\s+)?(?:downloads|desktop|documents)\b",
            re.I,
        ),
    ),
    (
        "machine.move_path",
        "filesystem",
        re.compile(
            r"\b(?:move|relocate)\b[^.?!]{0,60}?\b(?:to|into)\b[^.?!]{0,30}?\b(?:downloads|desktop|documents)\b", re.I
        ),
    ),
    # -- skill -------------------------------------------------------------------
    ("skill.list", "skill", re.compile(r"\b(?:list|show|what|which|audit)\b[^.?!]{0,30}?\bskills?\b", re.I)),
    (
        # Rolling back a SKILL is an activation-class change on the skill store; the rule
        # requires the skill noun so file/code revert phrasings stay in the workspace lane.
        "skill.rollback",
        "skill",
        re.compile(
            r"\b(?:roll\s?back|rollback|revert|restore|undo|previous\s+version|prior\s+version|"
            r"last\s+version|older\s+version)\b[^.?!]{0,60}?\bskills?\b|\bskills?\b[^.?!]{0,60}?"
            r"\b(?:roll\s?back|rollback|revert|restore|undo|previous\s+version|prior\s+version)\b",
            re.I,
        ),
    ),
    # -- email / media (policy may keep them unavailable; seating filters that) --
    # Neither email intent is a rule here. A request about mail the user received is recognized by what
    # it is about (mailbox_retrieval_intents below), and a request to send or compose a message by
    # outgoing_message_intents below -- both under instruction authority, so a how-question, a quoted
    # sentence, a reported example or a request for a KIND of text (a template) names no email tool.
    (
        "image.generate",
        "media",
        re.compile(r"\b(?:generate|create|make|draw)\b[^.?!]{0,30}?\b(?:an?\s+)?image\b", re.I),
    ),
    # -- operator -----------------------------------------------------------------
    ("operator.inspect_disk_usage", "operator", re.compile(r"\b(?:inspect|check|audit)\b[^.?!]{0,20}?\bdisk\b", re.I)),
    (
        "operator.inspect_processes",
        "operator",
        re.compile(r"\b(?:inspect|check|audit)\b[^.?!]{0,20}?\bprocesses\b", re.I),
    ),
    # -- database work (vool-database): ordinary phrasing selects the ONE database
    # skill. Verb+object anchored like every other rule here, and the family is
    # SELECTION-ONLY -- `database` is not a canonical capability family, so it seats
    # no tool. What arms a database tool is a typed action demand that still crosses
    # the approval, effect-budget, Blackbox-coverage and permission gates.
    #
    # These exist because the skill was unreachable by ANY ordinary phrasing: it
    # declares no task-families (so the 4-point task-class term can never fire) and
    # no rule emitted its capability family, so its score was structurally 0 and only
    # a literal `vool-database.<tool>` token could reach it.
    (
        "database.inspect",
        "database",
        re.compile(
            r"\b(?:show|list|describe|inspect|count|check|look\s+at|what(?:'s|\s+is)\s+in)\b"
            r"[^.?!]{0,60}?\b(?:databases?|db|tables?|schemas?|columns?|rows?|sqlite|postgres|mysql)\b",
            re.I,
        ),
    ),
    (
        "database.query",
        "database",
        re.compile(
            r"\b(?:query|select|search|fetch|pull|read)\b[^.?!]{0,60}?"
            r"\b(?:databases?|db|tables?|sqlite|postgres|mysql|rows?)\b"
            r"|\bfrom\s+(?:the\s+)?(?:\w+\s+)?tables?\b",
            re.I,
        ),
    ),
    (
        "database.mutate",
        "database",
        re.compile(
            r"\b(?:insert|update|delete|drop|alter|truncate|add|remove|rename)\b[^.?!]{0,60}?"
            r"\b(?:databases?|db|tables?|schemas?|columns?|rows?|records?|indexe?s?)\b",
            re.I,
        ),
    ),
    (
        "database.migrate",
        "database",
        re.compile(
            r"\b(?:migrate|migration|back\s?up|restore|dump)\b[^.?!]{0,60}?"
            r"\b(?:databases?|db|tables?|schemas?)\b"
            r"|\b(?:databases?|db|schemas?)\b[^.?!]{0,40}?\b(?:migrations?|back\s?ups?)\b",
            re.I,
        ),
    ),
    # -- X editorial studio (C20): direct X-writing requests select the editorial skill;
    # an ordinary mention of X/Twitter in prose must not. Every rule is verb+noun anchored
    # like the rest of this table, and the family is editorial-only (no tool seating).
    (
        "editorial.draft_article",
        "editorial",
        re.compile(
            r"\b(?:write|draft|create|start)\b[^.?!]{0,40}?\b(?:an?\s+)?(?:x|twitter)\s+articles?\b",
            re.I,
        ),
    ),
    (
        "editorial.draft_long_post",
        "editorial",
        re.compile(
            r"\b(?:write|draft|create|turn|convert|make)\b[^.?!]{0,40}?\b(?:premium\s+)?long[ -]?posts?\b",
            re.I,
        ),
    ),
    (
        "editorial.draft_post",
        "editorial",
        re.compile(
            r"\b(?:write|draft|compose|create|condense|compress|squeeze|shrink)\b"
            r"[^.?!]{0,60}?\b(?:single\s+|one\s+)?(?:tweets?|(?:x|twitter)\s+posts?)\b",
            re.I,
        ),
    ),
    (
        "editorial.to_thread",
        "editorial",
        re.compile(
            r"\b(?:turn|convert|break|split)\b[^.?!]{0,60}?\bthreads?\b"
            r"|\bas\s+a\s+thread\b|\bthread\s+this\b",
            re.I,
        ),
    ),
    (
        "editorial.format_x",
        "editorial",
        re.compile(
            r"\b(?:format|fit|adapt|trim|shape|squeeze|optimize|optimise|clean|write|draft)\b"
            r"[^.?!]{0,50}?\bfor\s+(?:x|twitter)\b",
            re.I,
        ),
    ),
    (
        "editorial.critique",
        "editorial",
        re.compile(
            r"\b(?:edit|critique|review|tighten|improve|punch\s+up)\b[^.?!]{0,40}?\b"
            r"(?:my|this|the)\s+(?:tweets?|threads?|(?:x|twitter)\s+(?:posts?|articles?|drafts?))\b",
            re.I,
        ),
    ),
    (
        "editorial.voice",
        "editorial",
        re.compile(
            r"\b(?:sounds?|reads?)\s+like\s+me\b|\bin\s+my\s+voice\b|\bwith\s+my\s+voice\b"
            r"|\bmake\s+(?:it|this)\b[^.?!]{0,20}?\bsounds?\b",
            re.I,
        ),
    ),
    # -- mcp (family-level: server tools are dynamic) ------------------------------
    # matched separately below via the mcp.<server>. prefix vocabulary
)

_MCP_FAMILY_PATTERN = re.compile(r"\b(?:mcp|model\s+context\s+protocol)\b|\bmcp\.[\w.-]+", re.I)

# Segment separator inside a spoken intent: nothing, a bare dot, or dot+spaces — the shapes
# prose normalization leaves between the words of one dotted token ("pack. tool", "pack.tool").
_SEGMENT_JOIN = r"\.?\s*"


def _spoken_variant_patterns(intent: str, expansions: dict[str, str]) -> re.Pattern[str] | None:
    """A pattern that also matches the intent as the normalizer's shorthand table renamed it.

    `normalize_user_text` rewrites shorthand WORDS (`db` -> `database`), which silently renames
    a dotted intent token inside ordinary prose (`vool-database.db.create` becomes
    `vool-database. database. create`) — the dot-fusion repairs the split, not the rename, and
    measured served: the seat was lost for exactly that shape. The variant is built ONLY from
    that table's own expansions, so the matcher undoes precisely what the normalizer did, over
    the CLOSED set of registered intents — it never generalizes beyond them.
    """

    inverse: dict[str, set[str]] = {}
    for shorthand, full in expansions.items():
        full_word = str(full).strip().lower()
        if re.fullmatch(r"[a-z0-9_-]+", full_word):
            inverse.setdefault(full_word, set()).add(str(shorthand).strip().lower())
    parts: list[str] = []
    for segment in str(intent).split("."):
        segment = segment.lower()
        if not re.fullmatch(r"[a-z0-9_-]+", segment):
            return None
        # Both directions of the rewrite table: what this segment normalizes INTO
        # ("db" -> "database" rewrote the token the user typed), and what normalizes
        # INTO it (an un-normalized shorthand beside a canonical segment).
        alts = {segment}
        forward = str(expansions.get(segment, "")).strip().lower()
        if re.fullmatch(r"[a-z0-9_-]+", forward):
            alts.add(forward)
        alts |= inverse.get(segment, set())
        ordered = sorted(alts, key=len, reverse=True)
        parts.append("(?:" + "|".join(re.escape(alt) for alt in ordered) + ")")
    try:
        return re.compile(r"\b" + _SEGMENT_JOIN.join(parts) + r"\b", re.I)
    except re.error:
        return None


def _plugin_intents(text: str) -> tuple[str, ...]:
    """Explicitly named plugin tool intents (``pack.tool`` tokens in the message).

    Plugin intents are dynamic, so they cannot live in the static rule table: the
    registered plugin contracts are the vocabulary. A plugin tool the user names by its
    intent token is the sharpest explicit request there is.
    """
    try:
        from core.tool_registry import registered_tools

        plugin_intents = [
            str(contract.intent) for contract in registered_tools() if str(contract.source or "").startswith("plugin:")
        ]
    except Exception:
        return ()
    if not plugin_intents:
        return ()
    lowered = text.lower()
    # Prose normalization inserts a sentence space after a period, which breaks a dotted
    # intent token in half (`pack.tool` -> `pack. tool`) before this seam runs -- measured
    # served: the seat was lost and the turn routed to a plain-text lane. Matching also
    # against a whitespace-fused variant is safe because the vocabulary is the CLOSED set
    # of registered plugin intents: an ordinary sentence boundary only fuses when a
    # registered intent is literally that dotted pair.
    fused = re.sub(r"(?<=[a-z0-9_-])\.\s+(?=[a-z0-9_-])", ".", lowered)
    matched = [intent for intent in plugin_intents if intent.lower() in lowered or intent.lower() in fused]
    if len(matched) < MAX_EXPLICIT_INTENTS:
        # The same normalizer also rewrites shorthand WORDS inside the token (`db` ->
        # `database`), which the fusion cannot undo. The spoken-variant patterns cover
        # exactly that rename, still over the closed registered vocabulary.
        try:
            from core.input_normalizer import shorthand_expansions

            expansions: dict[str, str] = shorthand_expansions()
        except Exception:
            expansions = {}
        for intent in plugin_intents:
            if intent in matched or len(matched) >= MAX_EXPLICIT_INTENTS:
                continue
            pattern = _spoken_variant_patterns(intent, expansions)
            if pattern is not None and (pattern.search(fused) or pattern.search(lowered)):
                matched.append(intent)
    return tuple(matched[:MAX_EXPLICIT_INTENTS])


def _browser_lane_intents(text: str) -> tuple[str, ...]:
    """Explicitly named vool-browser intents (the C06 native product browser).

    Same law as plugin intents, and the same prose hazard: the vocabulary is the
    closed set of the lane's registered contracts, and the fused-text match
    undoes the normalizer's sentence-space split of a dotted intent token. A
    user naming `vool-browser.navigate` in prose is the sharpest explicit
    request there is.
    """
    try:
        from core.tool_registry import registered_tools

        lane_intents = [
            str(contract.intent)
            for contract in registered_tools()
            if str(contract.intent or "").startswith("vool-browser.")
        ]
    except Exception:
        return ()
    if not lane_intents:
        return ()
    lowered = text.lower()
    fused = re.sub(r"(?<=[a-z0-9_-])\.\s+(?=[a-z0-9_-])", ".", lowered)
    matched = [
        intent
        for intent in lane_intents
        if intent.lower() in lowered or intent.lower() in fused
    ]
    return tuple(matched[:MAX_EXPLICIT_INTENTS])


#: CONTEXT IS NOT AUTHORITY, applied to the database family.
#:
#: The same law C18 established for pasted commands: talking ABOUT a database, asking
#: what one IS, comparing two of them, quoting a command, or explicitly forbidding an
#: action is CONTEXT. None of it may seat the database skill, because the skill teaches
#: how to do database work and this turn is not database work.
#:
#: A veto rather than a narrower pattern: the verbs above are the right verbs, and a
#: request can legitimately contain both ("delete the cancelled rows" vs "explain how
#: DELETE works"). What separates them is whether the message is asking for the work or
#: asking about it, and that is what this names.
_DATABASE_CONTEXT_ONLY = re.compile(
    r"\b(?:explain|describe\s+what|what\s+(?:is|are|does)|what's\s+the\s+difference"
    r"|difference\s+between|compare|comparison|meaning\s+of|how\s+does\b[^.?!]{0,30}\bwork"
    r"|tell\s+me\s+about|read\s+an?\s+article|article\s+about|blog\s+post\s+about"
    r"|do\s+not\s+(?:touch|change|modify|run|execute|alter|delete|drop)"
    r"|don't\s+(?:touch|change|modify|run|execute|alter|delete|drop)"
    r"|without\s+(?:touching|changing|modifying|running)"
    r"|just\s+(?:describe|explain|summari[sz]e)"
    r"|summari[sz]e\s+(?:it|this)\s+only)\b",
    re.I,
)


# -- mailbox retrieval ------------------------------------------------------------------------------
# Asking for mail the user RECEIVED is decided by what the request is about, never by which verb it
# uses: "pull up the most recent email from the kiln repair shop", "search my mail for the invoice",
# "has the supplier emailed me" and "check my email" ask the same thing. The closed verb list this
# replaces (read/check/list/summarize/review/show/open/look at/go through) missed the first three, and
# those turns stayed in the tools-less chat lane or were planned onto the web (revision 6, Gate A).
#
# The anchors are the closed grammar of received mail, never a sender, subject or phrase:
#   * a mailbox the user owns or shares: "my mail", "my work inbox", "our inbox", "the inbox for ...";
#   * a mailbox state: "new emails", "unread mail";
#   * a message named by who sent it: "the email from <sender>", "the message <sender> sent";
#   * a message addressed to the user: "what <sender> emailed me", "did <sender> email me",
#     "the email <sender> sent me";
#   * a mailbox noun as the object of a bare imperative: "open inbox", "pull up emails".
# Instruction authority decides first, from the modules that already own each question: quoted words
# are someone's text (core.turn_prohibitions), a negated or prohibited clause is removed
# (core.retrieval_constraints), a how-to or authoring request asks for TEXT
# (core.instructional_request), and a hypothetical frame supplies its own premises
# (core.stipulated_frame). Two grammatical readings are refused here: a mailbox reference introduced
# as an example ("for example ..."), and a first-person report of what happened ("I got an email from
# my landlord") that carries no request. The recognizer seats `email.read` and the email family; the
# model chooses what to call, and approvals, policy and provider binding decide everything after it.
_MAIL_ITEM = r"(?:e-?mails?|mail|messages?)"
_MAILBOX = r"(?:e-?mails?|mail|inbox(?:es)?|mailbox(?:es)?)"
#: What a mail noun names when it is not mail content: the account's configuration, or a kind of text.
_NOT_MAIL_CONTENT = (
    r"(?:address(?:es)?|signatures?|passwords?|settings?|clients?|apps?|providers?|servers?|domains?|"
    r"aliases?|templates?|marketing|campaigns?|etiquette|lists?|formats?)"
)
#: Words that end a sender phrase: auxiliaries and the first/second persons (mail WE sent is outgoing).
_NOT_A_SENDER_WORD = r"(?:i|we|you|was|were|is|are|be|been|being|has|have|had|get|gets|got|getting|to|that|which)"
_MAILBOX_ANCHOR_RE = re.compile(
    rf"\b(?:my|our)\s+(?:(?!{_NOT_MAIL_CONTENT}\b)[\w-]+\s+){{0,2}}?{_MAILBOX}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
    r"|\bthe\s+(?:inbox|mailbox)\b(?=\s*(?:$|[.,;:!?]|(?:for|from|in|about|at|since|to|and|or)\b))"
    rf"|\b(?:new|unread)\s+{_MAIL_ITEM}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
    rf"|\b{_MAIL_ITEM}\s+(?:(?:that|which)\s+)?(?:from|sent\s+by)\s+"
    r"(?!(?:going|being|getting|landing|ending|reaching|hitting)\b)[\w@&'.-]"
    rf"|\b{_MAIL_ITEM}\s+(?:(?:that|which)\s+)?(?:(?!{_NOT_A_SENDER_WORD}\b)[\w@&'.-]+\s+){{1,4}}sent\b"
    r"|\be-?mailed\s+(?:(?:it|that|this|them|something|anything)\s+)?(?:(?:over|back)\s+)?(?:to\s+)?(?:me|us)\b"
    r"|\b(?:did|does|do|has|have|had|will|would)\s+(?:[\w@&'.-]+\s+){1,5}?e-?mail\s+(?:me|us)\b"
    rf"|\b{_MAIL_ITEM}\b[^.?!]{{0,60}}?\b(?:sent|forwarded|wrote|replied)\s+(?:(?:it|that|this|them)\s+)?"
    r"(?:(?:over|back)\s+)?(?:to\s+)?(?:me|us)\b"
    rf"|^\s*(?:please\s+)?[a-z]+(?:\s+(?:up|out|through|over))?\s+(?:the\s+)?(?:inbox|mailbox|e-?mails?|mail)\b"
    rf"(?!\s+{_NOT_MAIL_CONTENT}\b)",
    re.IGNORECASE,
)
_EXAMPLE_FRAME_RE = re.compile(
    r"\b(?:for\s+example|for\s+instance|e\.g\.|such\s+as|an?\s+example\s+(?:of|request|prompt|query|sentence))",
    re.IGNORECASE,
)
#: A first-person report ("I got an email from ...", "We received ...") -- unless its verb is one of
#: the closed class of request frames ("I need", "I'd like", "I was wondering").
_FIRST_PERSON_REPORT_RE = re.compile(
    r"^\s*(?:i|we)\s+(?!(?:need|want|would|wanna|must|have\s+to|has\s+to|should|can|could|"
    r"am\s+looking|are\s+looking|was\s+wondering|were\s+wondering|wonder)\b)[a-z]+\b",
    re.IGNORECASE,
)
_REQUEST_FRAME_RE = re.compile(r"\?|\b(?:please|can\s+you|could\s+you|would\s+you|will\s+you)\b", re.IGNORECASE)
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

#: What a request about received mail seats: the read tool. The family fill seats the rest.
MAILBOX_RETRIEVAL_INTENTS: tuple[str, ...] = ("email.read",)


def mailbox_retrieval_intents(text: str) -> tuple[str, ...]:
    """The email read seat for a request about mail the user received, or () when the text is not one.

    Pure function of the text, like every other signal here: no policy reads (a disabled email lane
    is filtered at seating) and no execution.
    """
    try:
        from core.instructional_request import asks_for_instructions_not_execution
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.turn_prohibitions import _strip_quoted_spans
    except Exception:
        return ()
    constraints = analyze_retrieval_constraints(_strip_quoted_spans(str(text or "")))
    if constraints.forbids_all_tools:
        return ()
    eligible = " ".join(constraints.eligible_text.split())
    if not eligible or asks_for_instructions_not_execution(eligible):
        return ()
    try:
        from core.stipulated_frame import has_stipulated_frame

        if has_stipulated_frame(eligible):
            return ()
    except Exception:
        pass
    for sentence in _SENTENCE_BREAK_RE.split(eligible):
        anchor = _MAILBOX_ANCHOR_RE.search(sentence)
        if anchor is None:
            continue
        example = _EXAMPLE_FRAME_RE.search(sentence)
        if example is not None and example.start() < anchor.start():
            continue
        if _FIRST_PERSON_REPORT_RE.match(sentence) and not _REQUEST_FRAME_RE.search(sentence):
            continue
        return MAILBOX_RETRIEVAL_INTENTS
    return ()


# An outgoing message: a verb of sending or composing with a message noun as its object. The same
# instruction authority as the mailbox recognizer decides first; then a message noun that names a KIND
# of text -- a sample, an example, a generic or boilerplate email, an email template or format -- is a
# request for text the user will use themselves, not for this runtime's mail tools. "Draft a reply
# saying Wednesday works" composes THIS mailbox's reply and keeps its seat; "write me a sample email
# template" does not. (The noun classes are shared with the mailbox recognizer's _NOT_MAIL_CONTENT.)
_MESSAGE_NOUN = r"(?:e-?mails?|repl(?:y|ies)|responses?)"
_OUTGOING_MESSAGE_RE = re.compile(
    rf"\b(?:send|write|draft|compose|forward|reply|respond|answer)\b[^.?!]{{0,30}}?\b(?:an?\s+)?{_MESSAGE_NOUN}\b",
    re.IGNORECASE,
)
_TEXT_KIND_BEFORE_RE = re.compile(
    rf"\b(?:sample|example|generic|boilerplate|mock|dummy|practice|reusable|standard|model|typical|stock)\s+"
    rf"(?:[\w-]+\s+){{0,3}}?{_MESSAGE_NOUN}\b",
    re.IGNORECASE,
)
_TEXT_KIND_AFTER_RE = re.compile(rf"\b{_MESSAGE_NOUN}\s+{_NOT_MAIL_CONTENT}\b", re.IGNORECASE)
_TEXT_KIND_OBJECT_RE = re.compile(
    rf"\b(?:templates?|formats?|outlines?|examples?|samples?|boilerplate|wording|phrasing)\s+(?:for|of)\b"
    rf"[^.?!]{{0,40}}?\b{_MESSAGE_NOUN}\b",
    re.IGNORECASE,
)

#: What an outgoing-message request seats: the send tool. The family fill seats the draft tools.
OUTGOING_MESSAGE_INTENTS: tuple[str, ...] = ("email.send",)


def outgoing_message_intents(text: str) -> tuple[str, ...]:
    """The email send seat for a request to send or compose a message, or () when the text is not one.

    Pure function of the text, like mailbox_retrieval_intents: no policy reads, no execution.
    """
    try:
        from core.instructional_request import asks_how_to
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.turn_prohibitions import _strip_quoted_spans
    except Exception:
        return ()
    constraints = analyze_retrieval_constraints(_strip_quoted_spans(str(text or "")))
    if constraints.forbids_all_tools:
        return ()
    eligible = " ".join(constraints.eligible_text.split())
    if not eligible:
        return ()
    for sentence in _SENTENCE_BREAK_RE.split(eligible):
        anchor = _OUTGOING_MESSAGE_RE.search(sentence)
        if anchor is None:
            continue
        if asks_how_to(sentence):
            continue
        example = _EXAMPLE_FRAME_RE.search(sentence)
        if example is not None and example.start() < anchor.start():
            continue
        if (_TEXT_KIND_BEFORE_RE.search(sentence) or _TEXT_KIND_AFTER_RE.search(sentence)
                or _TEXT_KIND_OBJECT_RE.search(sentence)):
            continue
        return OUTGOING_MESSAGE_INTENTS
    return ()


def resolve_demand_signals(text: str) -> DemandSignals:
    """Resolve the user's words into explicit intents and required families.

    Pure function of the text (plus registered plugin intents). No policy reads, no
    availability checks — an explicitly requested but disabled tool surfaces here and
    is then filtered at seating, where its disabled status is honest metadata.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return DemandSignals()

    intents: list[str] = []
    families: list[str] = []
    write_demand = False

    def _note(intent: str, family: str) -> None:
        if intent not in intents and len(intents) < MAX_EXPLICIT_INTENTS:
            intents.append(intent)
        if family not in families and len(families) < MAX_REQUIRED_FAMILIES:
            families.append(family)

    if is_explicit_code_repair(cleaned):
        _note("code.task.open", "code")

    database_is_context_only = bool(_DATABASE_CONTEXT_ONLY.search(cleaned))
    for intent, family, pattern in _RULES:
        if pattern.search(cleaned):
            if family == "database" and database_is_context_only:
                # Discussed, not demanded. Recorded here rather than by narrowing the
                # pattern, so the reason a database turn did NOT seat the skill is a
                # named law instead of a regex that quietly failed to match.
                continue
            _note(intent, family)
            if family == "workspace" and intent.startswith(
                ("workspace.write", "workspace.replace", "workspace.apply", "workspace.rollback")
            ):
                write_demand = True

    # A save/store request whose object is a saved contact ("save Alex Chen as a contact",
    # "store Zoë Ng in my address book") is a Contacts demand even though the word-form rules above
    # recognize only possession/lookup shapes: the save-object shape has exactly one owner
    # (core.contacts.requests — the same reader the memory lane defers to), and reading it here
    # keeps the lane release, the tool offer and the memory yield on the same words instead of a
    # second list that can drift (the contacts.search tightening dropped this form, stranding the
    # save turn in the tools-less lane).
    try:
        from core.contacts.requests import storage_object_is_a_contact

        if storage_object_is_a_contact(cleaned):
            _note("contacts.save", "contacts")
            write_demand = True
    except Exception:
        pass
    for intent in mailbox_retrieval_intents(cleaned):
        _note(intent, "email")

    for intent in outgoing_message_intents(cleaned):
        _note(intent, "email")

    for intent in _plugin_intents(cleaned):
        _note(intent, "plugin")

    for intent in _browser_lane_intents(cleaned):
        _note(intent, "web")

    if _MCP_FAMILY_PATTERN.search(cleaned) and "mcp" not in families and len(families) < MAX_REQUIRED_FAMILIES:
        families.append("mcp")

    return DemandSignals(
        explicit_intents=tuple(intents),
        required_families=tuple(families),
        write_demand=write_demand,
    )


__all__ = [
    "MAILBOX_RETRIEVAL_INTENTS",
    "MAX_EXPLICIT_INTENTS",
    "MAX_REQUIRED_FAMILIES",
    "DemandSignals",
    "mailbox_retrieval_intents",
    "resolve_demand_signals",
]
