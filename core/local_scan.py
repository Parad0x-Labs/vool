"""VOOL local static check for standalone instruction skills.

A bounded, dependency-free text analyser that runs entirely on this machine:
no network egress, no subprocesses, no environment expansion and no execution
of submitted content. It inspects the exact bytes VOOL can import today (a
standalone SKILL.md) and reports versioned findings with severity, confidence
and plain-language limitations. It never proves intent or runtime safety, and
an unsupported or incomplete scan is a named status — never a clean pass.

Findings separate three questions that a single alarming score would blur:

* severity — how much damage the behaviour could cause if it occurred;
* confidence — how strongly this text supports the interpretation;
* stance — whether the text *describes* the capability (a reference-table
  row, a manual entry), *discusses* it (defensive warnings), *illustrates* it
  (a quoted example), *instructs* the agent to do it, hands the agent a
  *command* to execute, or is genuinely *ambiguous*.

Stance comes from a lightweight structural reading of the markdown (headings,
table rows, fences, quotes, list items) plus deterministic grammar signals.
Imperative wording overrides quotation, table position, "example" headings and
claimed authorisation, so those formats never excuse an instruction by
themselves; conversely a reference row is reported as documentation instead of
an accusation. The stance drives the explanation: a documented flag says what
the option does and explicitly does not claim the publisher ordered its use.

The rule taxonomy is VOOL's own; overlapping concepts are mapped to the
upstream Eyebrow native rules for evaluation only (see the mission record).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
import unicodedata

SCANNER_ID = "vool-local"
# v3: context-aware stance classification (describes/discusses/illustrates/
# instructs/commands/ambiguous) with stance-derived explanations, connected-
# action evidence (variable and same-path linkage, download-then-execute,
# bypass beside consequential actions), bounded hex/nested decoding with
# encoding provenance, and widened bypass/concealment phrase coverage.
# v4: context binds to the matched clause (or table cell), every bypass match
# on a line is inspected instead of only the first, and bypass families are
# matched per span — protective wording or an appended safety claim in a
# different sentence can no longer downgrade a separate instruction.
SCANNER_VERSION = 4
RULESET_VERSION = 2

# Bounds. The importer itself refuses bodies above core.plugin_skills limits
# and downloads above 128 KiB, so these ceilings only bound scanner work.
MAX_SCAN_BYTES = 192 * 1024
MAX_LINES = 20000
MAX_FINDINGS = 32
MAX_SNIPPET = 100
DEFAULT_DEADLINE = 5.0
_MAX_DECODED_BLOBS = 8       # first-stage base64 blobs decoded per document
_MAX_DECODED_HEX = 8         # first-stage hex blobs decoded per document
_MAX_DECODE_DEPTH = 2        # hex -> base64 style chains, never deeper
_MAX_TOTAL_DECODE_OPS = 24   # hard cap across every decoding stage
_MAX_BLOB_CHARS = 4096
_MAX_TRACKED_VARS = 8

_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_STANCES = ("describes", "discusses", "illustrates", "instructs", "commands", "ambiguous")
_STANCE_CONFIDENCE = {"instructs": "high", "commands": "high",
                      "ambiguous": "medium", "describes": "low",
                      "discusses": "low", "illustrates": "low"}
# Families whose matched wording is itself an order ("ignore …", "don't tell
# the user", "skip the confirmation"): without contrary framing, these count
# as instructions rather than descriptions.
_SELF_IMPERATIVE = {"prompt-injection", "concealment", "approval-bypass", "unrestricted-mode"}

# --- signal vocabularies: combinations, never isolated keyword bans ----------


def _ci(pattern: str):
    return re.compile(pattern, re.IGNORECASE)


_SENSITIVE_SOURCE = _ci(
    r"(?:~|/|\b)(?:\.ssh/|\.aws/|\.gnupg/|\.config/solana|\.config/google-cloud|\.netrc\b|\.env\b"
    r"|id_rsa|id_ed25519|authorized_keys|keychain|login data|cookies\.db|wallet\.dat|keystore"
    r"|electrum|metamask|seed phrase|recovery phrase|\bmnemonic\b|solana/id\.json|\.azure/|\.kube/|\.docker/config)"
)
_CREDENTIAL_TOKEN = _ci(
    r"\b(?:api[_ -]?key|secret[_ -]?key|access[_ -]?token|private[_ -]?key|password)\b\s*(?:file|from|in|at)\b"
)
_OUTBOUND = _ci(
    r"\b(?:curl|wget|fetch|http\s+(?:post|put)|nc|ncat|socat)\b[^\n]{0,160}?(?:-F\b|-T\b|-d\b|--data\b|--upload-file\b|POST\b|PUT\b)"
    r"|\b(?:upload|send|post|exfiltrate|uploadfile)\b[^\n]{0,80}?\b(?:to|towards)\b[^\n]{0,120}?\b(?:https?://|webhook|pastebin|discord|telegram|email|smtp)\b"
    r"|webhook\.(?:site|app)|pastebin\.com|requestbin|pipedream|ngrok\.(?:io|app)|transfer\.sh|0x0\.st|termbin\.com|discord\.com/api/webhooks"
)
_DOWNLOAD_EXEC = _ci(
    r"\b(?:curl|wget|fetch)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da|k|a|c|fi)?sh\b"
    r"|\bi(?:wr|nvoke-request|nvoke-webrequest|ex)\b[^\n|]{0,120}\|\s*iex\b"
    r"|\bbase64\b[^\n|]{0,40}(?:-d\b|--decode\b|decode\b)[^\n|]{0,120}\|\s*(?:ba|z)?sh\b"
    r"\b|\beval\b[^\n]{0,20}[\"'`]?\s*\$\(\s*(?:curl|wget)"
    r"|\b(?:python|python3|node)\b[^\n]{0,40}-c\b[^\n]{0,120}\b(?:exec|eval|urlopen|requests\.get)\b"
)
_REMOTE_CHANNEL = _ci(
    r"/dev/tcp/|\bnc(?:at)?\b[^\n]{0,80}?\s-e\b|\bbash\s+-i\b|\bsh\s+-i\b[^\n]{0,40}(?:>&|2>)|socat\b[^\n]{0,80}?exec"
    r"|169\.254\.169\.254|metadata\.google\.internal|metadata\.azure\.(?:com|net)"
)

# Bypass phrases, grouped into families so the explanation can say what the
# matched wording is and what following it would mean, instead of one
# accusatory string for every match.
_BYPASS_FAMILIES = [
    ("prompt-injection", _ci(
        r"ignore\s+(?:(?:all|any|the|your|previous|prior|above|these)\s+)*(?:instructions?|rules|guidelines|system prompt|context|approval\s+polic(?:y|ies))"
        r"|disregard\s+(?:all\s+)?(?:previous|prior|above|your|the)\s+(?:instructions?|rules|guidelines|context)"
        r"|forget\s+(?:all\s+)?(?:previous|prior|above|your)\s+(?:instructions?|rules|everything)"
    )),
    ("concealment", _ci(
        r"d(?:o\s+not|on't)\s+(?:tell|inform|notify|alert|mention|reveal)\b[^\n]{0,40}?\bthe\s+user"
        r"|(?:never|not)\s+mention\b[^\n]{0,40}?\bthe\s+user\b"
        r"|without\s+(?:telling|informing|notifying|asking|mentioning|revealing)\s+the\s+user"
        r"|hide\s+(?:this|it|the\s+(?:output|action|command|file))\s+from\s+the\s+user"
        r"|\b(?:clear|wipe|delete|remove|truncate)\b[^.\n]{0,30}?\b(?:history|logs?|audit\s*trail)\b"
        r"|proceed\s+silently\b|run\s+it\s+silently\b"
    )),
    ("approval-bypass", _ci(
        r"bypass\s+(?:the\s+)?(?:approval|confirmation|permission|consent|safety|guard)"
        r"|skip\s+(?:the\s+)?(?:approval|confirmation|permission|prompt|review)"
        r"|no\s+(?:need\s+to\s+)?ask(?:ing)?\s+(?:the\s+user\s+)?(?:for\s+)?(?:permission|approval|confirmation)"
        r"|d(?:o\s+not|on't)\s+ask\s+the\s+user\b"
        r"|no(?:thing|ne)\s+(?:asks|prompts|blocks|will\s+ask|will\s+prompt)\b"
    )),
    ("auto-approve", _ci(
        r"auto[- _]?(?:approve|accept|confirm)"
    )),
    ("claimed-authorization", _ci(
        r"(?:user|owner)\s+(?:has\s+)?already\s+(?:approved|authorized|given\s+permission)"
        r"|act\s+as\s+if\s+(?:the\s+user|it\s+was|approved)"
        r"|as\s+pre[- ]?approved\b"
    )),
    ("unrestricted-mode", _ci(
        r"(?:you\s+(?:are|now)\s+(?:in|operating\s+in))\s+(?:developer|god|admin|unrestricted)\s+mode"
        r"|disable\s+(?:all\s+)?safety|safety\s+(?:checks?\s+)?(?:are\s+)?disabled"
    )),
]
_BYPASS_PHRASES = re.compile("|".join("(?:%s)" % p.pattern for _, p in _BYPASS_FAMILIES), re.IGNORECASE)
_FAMILY_PATTERN = dict(_BYPASS_FAMILIES)

# What each family is and what following it would mean. Kept factual and
# scoped to the skill's own instructions — never a claim about VOOL's
# permission system, which this text cannot reach.
_FAMILY_TEXT = {
    "prompt-injection": (
        "wording that tells the agent to ignore or replace its operating instructions",
        "the agent could follow this skill's directions instead of the user's"),
    "concealment": (
        "wording that keeps actions or output hidden from the user",
        "actions could be taken or cleaned up without the user noticing them"),
    "approval-bypass": (
        "a way to skip approval or confirmation steps",
        "steps could run without the user reviewing them first"),
    "auto-approve": (
        "an option that automatically approves actions",
        "the reviewed tool's own approval prompts would not be shown before it acts"),
    "claimed-authorization": (
        "a claim that approval was already given",
        "the agent could proceed without asking, on this skill's word alone"),
    "unrestricted-mode": (
        "wording that declares an unrestricted or safety-disabled mode",
        "the agent could treat safety checks as already turned off"),
}
_RULE_TEXT = {
    "LS-DOWNLOAD-EXEC": (
        "a download piped straight into a shell or interpreter",
        "whatever the remote host serves at that moment would run on this machine"),
    "LS-REMOTE-CHANNEL": (
        "a reverse shell, an unrestricted remote-control listener, or a cloud metadata endpoint",
        "an outside party could control this machine or harvest instance credentials"),
    "LS-EXFIL-CREDENTIALS": (
        "secret material together with an outbound transfer",
        "keys, seed phrases or session cookies could leave this device"),
}

_NEGATED = _ci(r"\b(?:never|don't|do\s+not|must\s+not|avoid|refrain\s+from)\b[^.\n]{0,60}$")
_QUOTED_SPAN = _ci(r'["\u201c\u2018\'][^"\u201d\u2019\n]{0,160}["\u201d\u2019]')
# Blob patterns stay case-sensitive: mixed case is what distinguishes encoded data from prose.
_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{64,}={0,2}")
_HEX_BLOB = re.compile(r"\b(?:[0-9a-fA-F]{2}){40,}\b")
_SHELLISH_DECODED = _ci(
    r"\b(?:curl|wget|/bin/(?:ba)?sh|nc\s+-e|eval|base64|rm\s+-rf|chmod\s+\+x|\.ssh|id_rsa|webhook|pastebin)\b"
)
# A decode hint in already-decoded text is the justified candidate rule for
# one more decoding stage: the text itself shows a decode of a nested blob.
_DECODE_HINT = _ci(r"base64\s+(?:-d\b|--decode\b)|xxd\s+-r|\|\s*(?:ba|z)?sh\b")
# Descriptive framing: the same words used to warn about an attack, not run it.
# "example" must appear as prose, not inside a hostname such as foo.example; a
# trailing sentence period does not make a warning word part of a hostname.
# Habitual frequency adverbs ("docs often show …") mark description in the
# clause that carries them.
_DOCUMENTING = _ci(
    r"\b(?:never|avoid|beware|dangerous|malicious|hostile|suspicious|harmful|unsafe|risky|attackers?|attacks?|e\.g\.|for instance|such as|suppose|hypothetical|tutorial|defence|defense|mitigat\w+|flagged|detect\w*|indicators?|compromise|don't|do\s+not|untrusted|injected|often|sometimes|usually|typically|commonly|frequently|rarely|occasionally)\b(?![\w.-]*[a-z0-9])|(?<![.\w/])example\b(?![\w.-]*[a-z0-9])"
)
_PLACEHOLDER = _ci(
    r"<your[_ -]?(?:api[_ -]?)?key>|\byour_api_key\b|\bxxx\b|\bplaceholder\b|example\.com|your[_ -]?(?:token|secret|key)\s+here"
    r"|\$(?!\{?(?:HOME|PWD|USER|PATH|SHELL|TMPDIR|TEMP|LANG|LC_[A-Z_]+)\b)\{?[A-Z][A-Z0-9_]{2,}\}?"
    r"|\b(?:user[- ]provided|user[- ]supplied|provided\s+by\s+the\s+user|the\s+user\s+(?:sets|provides)|you\s+set)\b"
)
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_QUOTE = re.compile(r"^\s*>")
_INLINE_CODE = re.compile(r"`[^`\n]{4,}`")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
# An imperative verb opening the segment, or agent-directed wording anywhere
# in it. Gerunds ("using", "running") are deliberately absent: "Using this
# flag is convenient" describes, it does not order.
_IMPERATIVE_LEAD = _ci(
    r"^(?:please\s+|then\s+|now\s+|first\s+|next\s+|finally\s+|also\s+|always\s+|quietly\s+)*"
    r"(?:run|execute|install|use|invoke|call|send|upload|post|read|fetch|download|pipe|write|delete|remove|clear|"
    r"append|add|enable|disable|set|export|source|make|perform|start|open|copy|move|launch|trigger|proceed|continue)\b"
)
# A verb before the match only signals an imperative when function words —
# not a content-noun subject — separate it from the match: "run the bootstrap:
# curl…" is an order; "bad install docs often show `curl…`" is a description.
_IMPERATIVE_BEFORE = _ci(
    r"\b(?:run|execute|pipe|install|append)\b"
    r"(?:\s+(?:the|a|an|this|that|these|those|it|them|all|each|every|your|our|now|then|quietly|silently|locally|with|into|to|from|as|by|of|for|and))*\s*:?\s*$")
_AGENT_DIRECTED = _ci(
    r"\byou\s+(?:should|must|are\s+to|now)\b|\balways\b|\bevery\s+(?:time|call|command|invocation|run)\b|\beach\s+time\b|\bmake\s+sure\b|\bbe\s+sure\s+to\b|\bremember\s+to\b"
)
_COMMAND_SHAPED = _ci(
    r"^\s*(?:\$|>|#)?\s*`?\s*(?:curl|wget|bash|sh|zsh|powershell|pwsh|iwr|irm|python3?|node|npm|pip3?|git|chmod|rm|cat|echo|export|cd|sudo|ssh|nc|openssl|base64|xxd|eval|terminal\(|process\()"
    r"|^[A-Z_][A-Z0-9_]{1,30}="
    r"|terminal\s*\(\s*command\s*=|process\s*\(\s*action\s*="
)
# Sections that primarily document rather than direct. Used for notes and for
# example-fence detection; never as an excuse on its own.
_REFERENCE_SECTION = _ci(r"\b(?:flags?|options?|parameters?|arguments?|commands?|reference|configuration|settings|manual|variables?|keys?|api|glossary|faq|cheat\s?sheet)\b")
_EXAMPLE_SECTION = _ci(r"\b(?:example|examples|sample|demo|illustration)\b|for\s+instance")
_ACTION_SECTION = _ci(r"\b(?:setup|install(?:ation)?|usage|how\s+to|steps?|quick\s?start|getting\s+started|run(?:ning)?|execution|workflow|instructions?|deployment)\b")
# Simple, bounded variable relationships: a secret read captured into a name.
_VAR_FROM_CAT = _ci(r"\b([A-Z_][A-Z0-9_]{1,30})\s*=\s*\$?\(?cat\s+([^\s)&|;]+)")
_VAR_FROM_READ = _ci(r"\bread\s+([^\s,.:;]+)\s+into\s+(?:the\s+)?(?:variable\s+)?([A-Z_][A-Z0-9_]{1,30})\b")
_DL_SAVE = _ci(r"\b(?:curl|wget)\b[^|\n]{0,120}?(?:-o|--output|--output-document)\s+[\"']?([^\s\"';&|]+)")


def _normalize(text: str) -> str:
    """Unicode normalization applied before pattern matching, per line so
    reported line numbers stay truthful for the original bytes.

    What this handles: NFKC maps single-codepoint compatibility forms to their
    canonical equivalents (fullwidth "ｒ" -> "r", superscripts, ligatures), and
    stripping format (Cf) characters removes zero-width joiners/spaces and BOMs
    used to split keywords. What it does NOT handle: confusable letters from
    different scripts that are NFKC-stable (Cyrillic "а" vs Latin "a"), visually
    similar multi-character sequences, or substitution ciphers. Homoglyph
    attacks are mitigated only for the compatibility/zero-width class.
    """
    cleaned = []
    for line in text.split("\n"):
        line = "".join(ch for ch in line if unicodedata.category(ch) not in {"Cf"})
        cleaned.append(unicodedata.normalize("NFKC", line))
    return "\n".join(cleaned)


def _snippet(line: str) -> str:
    text = "".join(ch if ch.isprintable() or ch == " " else " " for ch in line.strip())
    return text[:MAX_SNIPPET] + ("…" if len(text) > MAX_SNIPPET else "")


def _finding(rule: str, severity: str, confidence: str, line_no: int, line: str,
             explanation: str, limitations=(), stance: str = "ambiguous", context=None,
             encoding=None, decoded_excerpt: str = "") -> dict:
    finding = {"ruleId": rule, "severity": severity, "confidence": confidence,
               "stance": stance, "line": line_no, "snippet": _snippet(line),
               "explanation": explanation, "limitations": list(limitations)}
    if context:
        finding["context"] = context
    if encoding:
        finding["encoding"] = list(encoding)
        if decoded_excerpt:
            finding["decodedExcerpt"] = decoded_excerpt[:80]
    return finding


def _documents_attack(text: str, pattern=None) -> bool:
    """Framing that discusses an attack rather than issuing it.

    Concealment phrases such as "do not tell the user" are stripped first: they
    are the attack, not the warning around it.
    """
    stripped = _BYPASS_PHRASES.sub(" ", text)
    if pattern is not None:
        stripped = pattern.sub(" ", stripped)
    return bool(_DOCUMENTING.search(stripped))


_CLAUSE_BOUND = re.compile(r"[.!?;](?=\s|$)")


def _segment_bounds(norm: str, start: int, end: int, table_row: bool) -> tuple:
    """Bounds of the cell (in a table) or clause carrying the match.

    Prose clauses break on sentence and semicolon boundaries, so framing in a
    different sentence — an appended safety claim, protective wording about
    something else — cannot change the classification of this match."""
    if table_row:
        seg_start = norm.rfind("|", 0, start) + 1
        seg_end = norm.find("|", end)
        if seg_end == -1:
            seg_end = len(norm)
        return seg_start, seg_end
    seg_start = 0
    for bound in _CLAUSE_BOUND.finditer(norm):
        if bound.start() < start:
            seg_start = bound.end()
        else:
            break
    seg_end = len(norm)
    for bound in _CLAUSE_BOUND.finditer(norm):
        if bound.start() >= end:
            seg_end = bound.start()
            break
    return seg_start, seg_end


def _is_imperative(segment: str, rel_start: int) -> bool:
    before = segment[:rel_start]
    return bool(_IMPERATIVE_LEAD.match(segment.strip())
                or _LABELED_COMMAND.match(segment.strip())
                or _AGENT_DIRECTED.search(segment)
                or (before.strip() and _IMPERATIVE_BEFORE.search(before)))


def _is_quoted_span(norm: str, start: int, end: int) -> bool:
    if any(q.start() <= start and end <= q.end() for q in _QUOTED_SPAN.finditer(norm)):
        return True
    # An opening double quote before the match with no closing quote between
    # them: the quoted phrase is line-wrapped and continues past this line.
    # Parity decides opener from closer (the closer of a completed pair has an
    # odd number of marks before it). Single quotes are excluded so
    # contractions never count as openers.
    marks = [m.start() for m in re.finditer(r'[\u201c"\u201d]', norm[:start])]
    return bool(marks and len(marks) % 2 == 1 and not re.search(r'[\u201c"\u201d]', norm[marks[-1] + 1:start]))


def _row_documents_command(norm: str, start: int) -> bool:
    """A table row whose first cell names a command or option and whose later
    cell describes its effect is reference documentation of that command —
    provided the match sits in the description, not the subject."""
    first_bar = norm.find("|")
    second_bar = norm.find("|", first_bar + 1)
    if first_bar == -1 or second_bar == -1:
        return False
    subject = norm[first_bar + 1:second_bar].strip()
    return start > second_bar and bool(re.match(r"^(?:`[^`]+`|-{1,2}[\w-]+|/\w+|\$\w+)", subject))


# A list item that opens with a named command or option token and then
# describes it ("- **`--sandbox`** for building — auto-approves changes").
_ITEM_SUBJECT = re.compile(
    r"^\s*(?:[-*+]|\d{1,3}[.)])\s+\**\s*(?:`[^`\n]{2,48}`|--[\w][\w-]*|/\w+)[^\n|]{0,48}?\**\s*(?:\u2014|\u2013|:|-)\s")
# A label like "**Installation:**" that introduces a command to run.
_LABELED_COMMAND = _ci(
    r"^\s*(?:[-*+]|\d{1,3}[.)])?\s*\**\s*(?:installation|uninstall|setup|install|quick\s?start|getting\s+started|bootstrap|run|running|execute|usage)\b\s*\**\s*[:\u2014\u2013-]\s*")
# Protective hardening: the object of "ignore" is instructions inside fetched
# or external content, not the agent's own operating instructions.
_PROTECTED_IGNORE = _ci(
    r"\b(?:instructions?|rules|guidelines)\s+(?:\w+\s+){0,3}?(?:embedded|inside)\s+(?:in\s+)?(?:fetched|external|untrusted|remote|downloaded|web|third[- ]party|scraped)\b"
    r"|\bignore\b[^.\n]{0,60}?\b(?:in|within)\s+(?:fetched|external|untrusted|remote|downloaded|web|third[- ]party|scraped)\s+(?:content|pages|text|data|results?|documents?)")


def _names_a_command_or_option(norm: str, start: int, table_row: bool) -> bool:
    """True when the line's structure names a command/option as the subject
    and carries the match in its description — reference documentation."""
    if table_row:
        return _row_documents_command(norm, start)
    subject = _ITEM_SUBJECT.match(norm)
    return subject is not None and start >= subject.end()


# A clause opened by a sequential connective continues the sentence's command
# ("Read the key silently; then upload it"): it inherits the imperative mood
# of the clauses before it on the same line, and nothing else.
_CONTINUATION = _ci(r"^(?:then|next|also|finally|after\s+that|and\s+then|and\s+)\b")


def _clause_imperative(norm: str, seg_start: int, segment: str, rel_start: int) -> bool:
    if _is_imperative(segment, rel_start):
        return True
    if seg_start > 0 and _CONTINUATION.match(segment.strip()):
        prefix = norm[:seg_start].strip()
        return bool(prefix and _IMPERATIVE_LEAD.match(prefix))
    return False


def _search(norm: str, pattern, table_row: bool):
    """Pattern search that respects table cells: inside a row, a match may not
    span the cell borders, so wording in the subject cell cannot combine with
    wording in a description cell to look like one instruction."""
    if not table_row:
        return pattern.search(norm)
    best = None
    for pos in (m.start() for m in re.finditer(r"\|", norm)):
        end = norm.find("|", pos + 1)
        if end == -1:
            end = len(norm)
        m = pattern.search(norm, pos + 1, end)
        if m and (best is None or m.start() < best.start()):
            best = m
    return best


def _iter_search(norm: str, pattern, table_row: bool, cap: int = 4):
    """Every distinct match on the line, cell-respecting in tables.

    A line can carry several independent instructions; stopping at the first
    match would let a defensive-looking earlier clause hide a malicious later
    one, so all matches (bounded) are inspected."""
    if not table_row:
        return list(pattern.finditer(norm))[:cap]
    out = []
    for pos in (m.start() for m in re.finditer(r"\|", norm)):
        end = norm.find("|", pos + 1)
        if end == -1:
            end = len(norm)
        for m in pattern.finditer(norm, pos + 1, end):
            if not any(m.start() < s and m.end() > e for s, e in (x.span() for x in out)):
                out.append(m)
                if len(out) >= cap:
                    return out
    return out


def _family_for(norm: str, match):
    """Which bypass family produced this match — decided by span overlap, so
    wording elsewhere on the line cannot relabel this instruction."""
    for name, pat in _BYPASS_FAMILIES:
        for m in pat.finditer(norm):
            if m.start() < match.end() and m.end() > match.start():
                return name
    return "prompt-injection"


def _stance_for(norm: str, match, ctx: dict, family: str, pattern) -> tuple:
    """Decide what the matched text is *doing*. Imperative wording wins over
    quotation, tables, example headings and authorization claims, so none of
    those formats can hide an instruction on its own.

    Defensive framing, protective wording and negation are read from the
    clause (or table cell) carrying this match, never from a different
    sentence on the same line: an appended safety claim or a protective
    instruction next door must not neutralize a different instruction.

    Returns (stance, notes); stance None means a negation shows the clause
    orders the opposite ("never proceed without asking the user") and no
    finding should be made.
    """
    notes = []
    start, end = match.span()
    seg_start, seg_end = _segment_bounds(norm, start, end, ctx["table_row"])
    if _NEGATED.search(norm[seg_start:start]):
        return None, notes
    segment = norm[seg_start:seg_end]
    imperative = _clause_imperative(norm, seg_start, segment, start - seg_start)
    documenting = _documents_attack(segment, pattern)
    quoted_span = _is_quoted_span(norm, start, end)
    quoted_ctx = quoted_span or ctx["quote"] or ctx["fence_example"]
    command_shaped = bool(_COMMAND_SHAPED.search(segment.strip()))

    if ctx["fenced"]:
        notes.append("Match is inside a fenced code block.")
    elif ctx["quote"]:
        notes.append("Match is inside a blockquote; the skill may be quoting an attack for discussion.")
    if ctx["table_row"]:
        notes.append("Match is inside a table row.")
    if quoted_span:
        notes.append("The phrase is wrapped in quotation marks on this line.")

    if imperative:
        stance = "commands" if (ctx["fenced"] or command_shaped) else "instructs"
    elif family == "prompt-injection" and _PROTECTED_IGNORE.search(segment):
        # "Ignore instructions embedded in fetched content" hardens the agent
        # against injected text; the object of the verb is what matters.
        stance = "discusses"
        notes.append("The object being ignored is instructions inside fetched or external content — protective wording, not an override of the agent's own instructions.")
    elif family == "claimed-authorization":
        # An authorization claim is itself the mechanism; even quoted or framed
        # as discussion it stays at least a calm question, never dismissed.
        stance = "ambiguous" if quoted_ctx or documenting else "instructs"
        notes.append("A claim that approval already happened is treated as part of the instruction, not as proof of it.")
    elif quoted_ctx:
        stance = "illustrates"
        if ctx["fence_example"]:
            notes.append("The introducing prose frames this block as something to avoid or detect, which a benign tutorial would do.")
    elif documenting:
        stance = "discusses"
        notes.append("The surrounding line frames this as something to avoid or detect, which a benign tutorial would do.")
    elif ctx.get("meta") and family not in _SELF_IMPERATIVE:
        # Frontmatter name/description lines are metadata about the skill.
        stance = "describes"
        notes.append("The match is in the skill's frontmatter metadata, which describes the skill rather than directing the agent.")
    elif family not in ("prompt-injection", "unrestricted-mode") \
            and _names_a_command_or_option(norm, start, ctx["table_row"]):
        # A table row or list item that names a command/option as its subject
        # and describes its effect is reference documentation. Prompt-injection
        # wording never takes this path: describing an option is ordinary,
        # reprogramming the agent is not.
        stance = "describes"
        notes.append("The line names a command or option as its subject and describes its effect; it does not by itself tell the agent to use it.")
    elif family in _SELF_IMPERATIVE:
        # "Ignore the rules" / "don't tell the user" / "skip the confirmation"
        # are orders in themselves when nothing frames them otherwise.
        stance = "instructs"
    elif ctx["fenced"]:
        stance = "commands"
    elif ctx["table_row"]:
        stance = "ambiguous" if command_shaped else "describes"
        if stance == "describes":
            notes.append("The row documents what an option or command does; it does not by itself tell the agent to use it.")
        else:
            notes.append("The cell contains an executable-looking command inside a table; whether to run it is unclear from context alone.")
    elif _COMMAND_SHAPED.match(norm.strip()):
        # A line that is itself a shell command, outside any quoted or
        # documenting frame, is an executable instruction by form.
        stance = "commands"
    else:
        stance = "ambiguous"
    return stance, notes


def _half_stance(norm: str, ctx: dict, stance_of_line, segment: str, seg_start: int) -> str:
    """Stance for a sensitive-read or outbound line that matched no other
    rule: used to decide whether a combination is a live sequence. Grammar
    signals are evaluated on the cell or clause carrying the match."""
    if stance_of_line:
        return stance_of_line
    if _clause_imperative(norm, seg_start, segment, len(segment)):
        return "commands" if (ctx["fenced"] and not ctx["fence_example"]) or _COMMAND_SHAPED.match(segment.strip()) else "instructs"
    if _documents_attack(segment) or ctx["quote"] or ctx["fence_example"]:
        return "discusses"
    if ctx["fenced"] and not ctx["fence_example"]:
        return "commands"
    if ctx["table_row"] or ctx.get("meta"):
        return "ambiguous" if _COMMAND_SHAPED.search(segment) else "describes"
    if _COMMAND_SHAPED.match(segment.strip()):
        return "commands"
    return "ambiguous"


def _explain(rule: str, family: str, stance: str, segment: str, ctx: dict) -> str:
    """What was found, what could happen, and what the stance means — without
    accusing a publisher of intent the text does not show."""
    if rule in _RULE_TEXT:
        capability, consequence = _RULE_TEXT[rule]
    else:
        capability, consequence = _FAMILY_TEXT.get(family, ("dangerous-sounding wording", "the described behaviour could occur"))
    where = " under the heading \u201c%s\u201d" % ctx["heading"] if ctx.get("heading") else ""
    seg = " ".join(segment.strip().strip("|").split())
    evidence = " (\u201c%s\u201d)" % seg[:90] if 0 < len(seg) <= 90 else ""
    if stance == "describes":
        noun = "reference row" if ctx.get("table_row") else "description"
        return ("Documents %s%s%s. If that option or behaviour were enabled, %s. "
                "This %s alone does not establish an instruction to use it." %
                (capability, where, evidence, consequence, noun))
    if stance == "discusses":
        return ("Explains or warns about %s%s%s — defensive discussion, not an instruction to do it. "
                "If an agent nevertheless followed it, %s." % (capability, where, evidence, consequence))
    if stance == "illustrates":
        return ("Shows %s%s%s as an example. An example is not an order, but an agent could mistake it for one; "
                "if followed, %s." % (capability, where, evidence, consequence))
    if stance == "commands":
        return ("Gives the agent a command involving %s%s. If run, %s." % (capability, evidence, consequence))
    if stance == "instructs":
        return ("Instructs the agent to act through %s%s%s. If followed, %s." % (capability, where, evidence, consequence))
    return ("Contains %s%s%s. Whether this text documents the behaviour or tells the agent to perform it is not "
            "clear from the surrounding context; if performed, %s." % (capability, where, evidence, consequence))


def _block_name(ctx: dict) -> str:
    if ctx["fenced"]:
        return "fenced-code"
    if ctx["quote"]:
        return "blockquote"
    if ctx["table_row"]:
        return "table-row"
    return "prose"


def scan_bytes(name: str, content: bytes, *, deadline: float = DEFAULT_DEADLINE) -> dict:
    """Scan the exact standalone-skill bytes; never a substitute for judgement."""
    started = time.monotonic()
    report = {"kind": "local_scan", "scanner": SCANNER_ID, "scanner_version": SCANNER_VERSION,
              "ruleset_version": RULESET_VERSION, "status": "unsupported", "findings": [],
              "source": {"name": str(name)[:80], "sha256": hashlib.sha256(content).hexdigest(),
                         "bytes": len(content)},
              "limitations": [], "duration_ms": 0}
    try:
        if len(content) > MAX_SCAN_BYTES:
            report["reason"] = "input_too_large"
            report["limitations"] = ["This file exceeds the local scanner's size bound."]
            return _finish(report, started)
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        report["reason"] = "not_utf8"
        report["limitations"] = ["The file is not valid UTF-8 text, so it cannot be a standalone instruction skill."]
        return _finish(report, started)
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        report["reason"] = "too_many_lines"
        report["limitations"] = ["The file exceeds the local scanner's line bound."]
        return _finish(report, started)

    normalized = _normalize(text).split("\n")
    findings: list = []
    stop = started + deadline
    fenced = False
    in_frontmatter = False
    doc_recent = 99     # lines since prose that frames an attack as something to avoid
    heading = ""        # most recent section heading text
    section_kind = ""   # reference | example | action | ""
    sensitive_hits: list = []    # (line_no, raw, stance, [var names])
    outbound_hits: list = []     # (line_no, raw, stance, [referenced var names])
    secret_vars: dict = {}       # var name -> (line_no, path) for tracked secret reads
    downloads: dict = {}         # saved artifact name -> line_no of the download
    bypass_instructs: list = []  # (line_no, family) where stance is instructs/commands
    concealment_instructs: list = []

    for index, raw in enumerate(lines, start=1):
        if time.monotonic() > stop:
            report["status"] = "incomplete"
            report["reason"] = "work_deadline_exceeded"
            report["limitations"] = ["The local check exceeded its work deadline; results are partial, not clean."]
            return _finish(report, started)
        if _FENCE.match(raw):
            fenced = not fenced
            continue
        if index == 1 and raw.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if raw.strip() == "---":
                in_frontmatter = False
            # Frontmatter is scanned like any other line: instructions hiding
            # in metadata still match; only the default stance changes.
        heading_match = _HEADING.match(raw)
        if heading_match:
            heading = heading_match.group(1).strip()
            if _REFERENCE_SECTION.search(heading):
                section_kind = "reference"
            elif _EXAMPLE_SECTION.search(heading):
                section_kind = "example"
            elif _ACTION_SECTION.search(heading):
                section_kind = "action"
            else:
                section_kind = ""
        norm = normalized[index - 1]
        table_row = bool(_TABLE_ROW.match(raw)) and raw.count("|") >= 2
        ctx = {"fenced": fenced, "quote": bool(_QUOTE.match(raw)), "table_row": table_row,
               "meta": in_frontmatter, "heading": heading, "fence_example": False}
        if not norm.strip():
            continue
        if not fenced:
            doc_recent = 0 if _documents_attack(norm) else doc_recent + 1
        ctx["fence_example"] = fenced and (doc_recent <= 2 or section_kind == "example")

        stance_of_line = None
        for rule, pattern, severity in (
            ("LS-DOWNLOAD-EXEC", _DOWNLOAD_EXEC, "critical"),
            ("LS-REMOTE-CHANNEL", _REMOTE_CHANNEL, "critical"),
            ("LS-INSTRUCTION-BYPASS", _BYPASS_PHRASES, "high"),
        ):
            # Every bypass phrase on the line is inspected, not just the first:
            # one defensive-looking clause must not hide a later instruction.
            matches = (_iter_search(norm, pattern, table_row)
                       if rule == "LS-INSTRUCTION-BYPASS"
                       else [m for m in [_search(norm, pattern, table_row)] if m])
            for match in matches:
                family = ""
                if rule == "LS-INSTRUCTION-BYPASS":
                    family = _family_for(norm, match)
                stance, notes = _stance_for(norm, match, ctx, family,
                                            _FAMILY_PATTERN.get(family, pattern))
                if stance is None:
                    # A negated bypass phrase ("never proceed without asking
                    # the user") orders the agent to keep asking.
                    continue
                seg_start, seg_end = _segment_bounds(norm, match.start(), match.end(), table_row)
                findings.append(_finding(rule, severity, _STANCE_CONFIDENCE[stance], index, raw,
                                         _explain(rule, family, stance, norm[seg_start:seg_end], ctx), notes, stance,
                                         {"section": heading, "block": _block_name(ctx)}))
                if rule == "LS-INSTRUCTION-BYPASS" and stance in ("instructs", "commands"):
                    bypass_instructs.append((index, family))
                    if family == "concealment":
                        concealment_instructs.append((index, family))
                if stance_of_line is None or stance in ("instructs", "commands"):
                    stance_of_line = stance

        # Track secret reads, captured variables, saved downloads and outbound
        # preps for the connected-action pass, each with the line's stance.
        sensitive_match = _search(norm, _SENSITIVE_SOURCE, table_row) or _search(norm, _CREDENTIAL_TOKEN, table_row)
        outbound_match = _search(norm, _OUTBOUND, table_row)
        sensitive_seg = norm
        sensitive_s = 0
        outbound_seg = norm
        outbound_s = 0
        if sensitive_match is not None:
            s, _e = _segment_bounds(norm, sensitive_match.start(), sensitive_match.end(), table_row)
            sensitive_seg = norm[s:_e]
            sensitive_s = s
        if outbound_match is not None:
            s, _e = _segment_bounds(norm, outbound_match.start(), outbound_match.end(), table_row)
            outbound_seg = norm[s:_e]
            outbound_s = s
        if sensitive_match is not None and not _PLACEHOLDER.search(norm):
            var_names = []
            for var_re, var_group, path_group in ((_VAR_FROM_CAT, 1, 2), (_VAR_FROM_READ, 2, 1)):
                m = var_re.search(norm)
                if m:
                    var, path = m.group(var_group), m.group(path_group)
                    if var and path and len(secret_vars) < _MAX_TRACKED_VARS and \
                            (_SENSITIVE_SOURCE.search(path) or _CREDENTIAL_TOKEN.search(path)):
                        secret_vars.setdefault(var, (index, path))
                        var_names.append(var)
            half_ctx = {"section": heading, "block": _block_name(ctx)}
            sensitive_hits.append((index, raw, _half_stance(norm, ctx, stance_of_line, sensitive_seg, sensitive_s),
                                   var_names, half_ctx))
        if outbound_match is not None:
            referenced = [v for v in secret_vars
                          if re.search(r"\$\{?%s\}?|\b%s\b" % (re.escape(v), re.escape(v)), norm)]
            half_ctx = {"section": heading, "block": _block_name(ctx)}
            outbound_hits.append((index, raw, _half_stance(norm, ctx, stance_of_line, outbound_seg, outbound_s),
                                  referenced, half_ctx))
        dl = _search(norm, _DL_SAVE, table_row)
        if dl and dl.group(1) not in (".", "-"):
            downloads.setdefault(dl.group(1), index)

    findings.extend(_connected_actions(lines, sensitive_hits, outbound_hits, secret_vars,
                                       downloads, bypass_instructs, concealment_instructs))
    encoded_findings, coverage_note = _encoded_payloads(lines, normalized, stop)
    findings.extend(encoded_findings)
    findings.sort(key=lambda f: (-_SEVERITY_ORDER[f["severity"]], f["line"]))
    report["findings"] = findings[:MAX_FINDINGS]
    if len(findings) > MAX_FINDINGS:
        report["truncated_findings"] = len(findings) - MAX_FINDINGS
    if coverage_note:
        # Partial coverage is never a clean pass, but what was examined still counts.
        report["status"] = "incomplete"
        report["reason"] = coverage_note
    else:
        report["status"] = "findings" if report["findings"] else "clean"
    report["limitations"] = [
        "Static analysis cannot prove intent or runtime safety; absence of findings is not a safety guarantee.",
        "Covers the standalone SKILL.md bytes only; executable plugins, MCP servers and multi-file packages are out of scope.",
        "Stance, context and explanations are heuristic: documentation is reported calmly, instructions firmly, unclear text as unclear.",
        "Benign security tutorials can match with reduced confidence; quoted examples are reported, not silently ignored.",
    ]
    return _finish(report, started)


def _connected_actions(lines, sensitive_hits, outbound_hits, secret_vars,
                       downloads, bypass_instructs, concealment_instructs) -> list:
    """Combine halves the text actually connects: reads that feed uploads,
    downloads that are later executed, and approval bypass beside the actions
    it would cover. Documentation halves are reported without claiming flow."""
    out = []

    # Rank for picking the less assertive half when a combination is not live.
    rank = {"describes": 0, "discusses": 1, "illustrates": 2, "ambiguous": 3, "instructs": 4, "commands": 5}

    if sensitive_hits and outbound_hits:
        for line_no, line, stance, var_names, half_ctx in sensitive_hits[:3]:
            partner = None
            linked = None
            for hit in outbound_hits[:3]:
                partner = hit
                if set(var_names) & set(hit[3]):
                    linked = "variable"
                    break
                if any(path and path in hit[1] for _, path in secret_vars.values()):
                    linked = "same-path"
                    break
            if partner is None:
                continue
            o_no, o_line, o_stance, o_vars, _o_ctx = partner
            halves_live = stance in ("instructs", "commands") and o_stance in ("instructs", "commands")
            halves_doc = stance in ("describes", "discusses", "illustrates") or \
                o_stance in ("describes", "discusses", "illustrates")
            confidence = "high" if halves_live else ("low" if halves_doc else "medium")
            combined_stance = stance if halves_live else min(stance, o_stance, key=lambda s: rank[s])
            limitations = ["The other half of this combination is on line %d: %s" % (o_no, _snippet(o_line))]
            if linked == "variable":
                var = sorted(set(var_names) & set(o_vars))[0]
                limitations.append("line %d reads secret material into %s and line %d references it, which supports a real data flow."
                                   % (line_no, var, o_no))
            elif linked == "same-path":
                limitations.append("Both lines name the same secret path, which supports a real data flow.")
            else:
                limitations.append("The two halves appear in different places; a flow between them is plausible but not proven by this text alone.")
            if halves_doc:
                limitations.append("One or both halves sit in quoted, documented or example text, so this may be an illustrative pair rather than a live sequence.")
            out.append(_finding(
                "LS-EXFIL-CREDENTIALS", "critical", confidence, line_no, line,
                _explain("LS-EXFIL-CREDENTIALS", "", combined_stance, line, half_ctx),
                limitations, combined_stance, half_ctx))

    # A download saved to a file that a later line executes.
    for artifact, dl_no in list(downloads.items())[:4]:
        for index, raw in enumerate(lines, start=1):
            if index <= dl_no:
                continue
            if re.search(r"(?:\b(?:sudo\s+)?(?:ba|z|da)?sh\b|python3?\b|node\b|chmod\s+\+x|\.\/)[^\n&|;]{0,40}\b%s\b"
                         % re.escape(artifact), raw) or \
               re.search(r"\b%s\b[^\n&|;]{0,20}\|\s*(?:ba|z)?sh\b" % re.escape(artifact), raw):
                out.append(_finding(
                    "LS-DOWNLOAD-EXEC", "critical", "high", index, raw,
                    "Saves a download to a file and a later step executes that file. If followed, whatever the remote host served would run on this machine.",
                    ["Downloaded on line %d: %s" % (dl_no, _snippet(lines[dl_no - 1]))],
                    "instructs", {"section": "", "block": "sequence"}))
                break

    # Approval bypass or concealment beside the consequential actions it would
    # cover: noted on the finding itself, not inflated into a new verdict.
    consequential = [f for f in out if f["ruleId"] in ("LS-EXFIL-CREDENTIALS", "LS-DOWNLOAD-EXEC")]
    if bypass_instructs and consequential:
        line_no, family = bypass_instructs[0]
        for f in consequential:
            f["limitations"] = [*list(f.get("limitations", [])), "Line %d carries %s wording as an instruction; combined, those actions could run unreviewed." % (line_no, _FAMILY_TEXT[family][0])]
    if concealment_instructs and consequential:
        line_no, _ = concealment_instructs[0]
        for f in consequential:
            f["limitations"] = [*list(f.get("limitations", [])), "Line %d instructs the agent to keep this from the user." % line_no]
    return out


def _decode_candidate(blob: str, enc: str):
    """Decode as data only; never evaluated, executed or imported."""
    if enc == "base64":
        return base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
    return binascii.unhexlify("".join(blob.split()))


def _encoded_payloads(lines: list, normalized: list, stop: float):
    """Flag encoded blobs, decoding a bounded number — decoding is analysis, never execution.

    Budgets bind before every decode in every stage (base64, hex, and one
    nested stage whose justified candidate rule is a decode hint beside
    another blob in the already-decoded text). Stopping early on budget or
    deadline leaves content unexamined; the caller then reports incomplete
    coverage instead of a clean pass. Returns (findings, coverage_note).
    """
    out = []
    seen_b64 = 0
    seen_hex = 0
    ops = 0
    tail_b64 = ""
    tail_hex = ""
    for index, norm in enumerate(normalized, start=1):
        found_here = False
        for match in _BASE64_BLOB.finditer(norm):
            if seen_b64 >= _MAX_DECODED_BLOBS or ops >= _MAX_TOTAL_DECODE_OPS:
                tail_b64 = norm[match.start():]  # this match and any after it went unexamined
                break
            if time.monotonic() > stop:
                return out, "work_deadline_exceeded"
            blob = match.group(0)[:_MAX_BLOB_CHARS]
            try:
                decoded = _decode_candidate(blob, "base64")
            except (ValueError, binascii.Error):
                continue
            finally:
                seen_b64 += 1
                ops += 1
            text = decoded.decode("utf-8", errors="ignore")
            extra_ops, nested_text, nested_enc = _nested_stage(text, stop, lambda current_ops=ops: current_ops < _MAX_TOTAL_DECODE_OPS)
            ops += extra_ops
            if nested_text:
                text = nested_text
            provenance = ["base64", *nested_enc]
            if _SHELLISH_DECODED.search(text) and not found_here:
                # One notice per line is enough; every blob is still examined,
                # because stopping at the first hit would hide the rest.
                found_here = True
                out.append(_finding("LS-ENCODED-PAYLOAD", "high", "medium", index, lines[index - 1],
                                    "Contains an encoded blob whose decoded text includes shell commands or secret paths. Encoding like this is a common way to hide what a skill really does.",
                                    ["Decoded locally for inspection only; nothing was executed.",
                                     "Decoding chain: %s." % " \u2192 ".join(provenance)],
                                    "commands", {"section": "", "block": "encoded-blob"}, encoding=provenance, decoded_excerpt=text))
        if found_here:
            continue
        if time.monotonic() > stop:
            return out, "work_deadline_exceeded"
        hex_match = _HEX_BLOB.search(norm)
        if not hex_match:
            continue
        if seen_hex >= _MAX_DECODED_HEX or ops >= _MAX_TOTAL_DECODE_OPS:
            tail_hex = norm[hex_match.start():]
            continue
        if time.monotonic() > stop:
            return out, "work_deadline_exceeded"
        try:
            decoded = _decode_candidate(hex_match.group(0)[:_MAX_BLOB_CHARS], "hex")
        except (ValueError, binascii.Error):
            decoded = None
        finally:
            seen_hex += 1
            ops += 1
        text = decoded.decode("utf-8", errors="ignore") if decoded is not None else ""
        extra_ops, nested_text, nested_enc = _nested_stage(text, stop, lambda current_ops=ops: current_ops < _MAX_TOTAL_DECODE_OPS)
        ops += extra_ops
        if nested_text:
            text = nested_text
        provenance = ["hex", *nested_enc]
        if decoded is not None and _SHELLISH_DECODED.search(text):
            out.append(_finding("LS-ENCODED-PAYLOAD", "high", "medium", index, lines[index - 1],
                                "Contains a hex-encoded blob whose decoded text includes shell commands or secret paths. Encoding like this is a common way to hide what a skill really does.",
                                ["Decoded locally for inspection only; nothing was executed.",
                                 "Decoding chain: %s." % " \u2192 ".join(provenance)],
                                "commands", {"section": "", "block": "encoded-blob"}, encoding=provenance, decoded_excerpt=text))
        else:
            out.append(_finding("LS-OPAQUE-BLOB", "low", "low", index, lines[index - 1],
                                "Contains a long opaque encoded blob the local check cannot fully interpret. This is informational, not evidence of malicious intent.",
                                (["Decoded locally: ordinary text, no shell commands found; kept informational."]
                                 if decoded is not None else [])))
    if tail_b64 or tail_hex:
        # The decode budget stopped before the end; a pattern-only pass (no decoding)
        # decides whether encoded content remains unexamined on this or later lines.
        remainder = "\n".join([p for p in (tail_b64, tail_hex) if p] + normalized[index:])
        if _BASE64_BLOB.search(remainder) or _HEX_BLOB.search(remainder):
            return out, "encoded_blob_budget_exceeded"
    return out, ""


def _nested_stage(text: str, stop: float, budget_ok) -> tuple:
    """One extra decoding stage, only when the decoded text itself shows a
    decode hint next to another blob (the justified candidate rule). Ordinary
    decoded prose, hashes and identifiers are never re-decoded. Depth is one
    stage on top of the first: this is only called from stage one, so the
    chain can never exceed two decodings.
    """
    if not text or not _DECODE_HINT.search(text):
        return 0, None, []
    inner = _BASE64_BLOB.search(text) or _HEX_BLOB.search(text)
    if inner is None:
        return 0, None, []
    if time.monotonic() > stop or not budget_ok():
        return 0, None, []
    blob = inner.group(0)[:_MAX_BLOB_CHARS]
    enc = "hex" if inner.re is _HEX_BLOB else "base64"
    try:
        decoded = _decode_candidate(blob, enc)
    except (ValueError, binascii.Error):
        return 1, None, []
    return 1, decoded.decode("utf-8", errors="ignore"), [enc]


def _finish(report: dict, started: float) -> dict:
    report["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    return report


def summarize(report: dict) -> dict:
    """Small, safe projection for UI listing without re-serving the whole report."""
    if not isinstance(report, dict) or report.get("kind") != "local_scan":
        raise ValueError("not a local scan report")
    return {"status": report.get("status"), "reason": report.get("reason", ""),
            "finding_count": len(report.get("findings") or []),
            "max_severity": max((f["severity"] for f in report.get("findings") or []),
                                key=lambda s: _SEVERITY_ORDER.get(s, 0), default=""),
            "checked_scanner_version": report.get("scanner_version"),
            "ruleset_version": report.get("ruleset_version")}


def dumps(report: dict) -> str:
    return json.dumps(report, sort_keys=True, ensure_ascii=False)
