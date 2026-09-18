from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

# --- Model-output hygiene: foreign tool-call vocabulary + synthesis integrity ------------------
#
# Small local models are trained on OTHER platforms' tool protocols (ChatML / Hermes / Qwen /
# Harmony, OpenAI function-calling, Gemma). Under vool-local's text-catalogue tool contract they
# sometimes emit that FOREIGN vocabulary as literal answer content instead of our JSON tool-intent
# — e.g. `<|tool_call|>call:google_search("btc price")`. The plain-text output contract used to pass
# such text straight through with only `.strip()`, so the fake tool request was shown to the user
# and written to memory (the "Gemma leaked google_search" failure).
#
# This is the single shared model-output guard. It is called at two layers:
#   * the universal output choke point (`model_output_contracts.validate_contract`, plain_text) —
#     `foreign_markers()` / `scrub_foreign_markers()` neutralise leaked syntax on EVERY provider
#     output (local + cloud) before it reaches the user or the memory store;
#   * the terminal synthesis validator (the research/tool loop) — `claims_pending_tool()` /
#     `is_ungrounded()` reject a "final" answer that still asks to run a tool, or that ignores the
#     observations already gathered.
#
# Design rule (inherited from reply_control_sanitizer): only STANDALONE control markup is removed,
# never prose that merely mentions a tool by name. A legitimate answer ABOUT `google_search`, or a
# code line like `print("hi")`, must survive untouched.

# Special-token envelopes: `<|tool_call|>`, `<|im_start|>`, `<|eot_id|>`, `<|python_tag|>`, and the
# malformed-but-common unclosed `<|tool_call>`. These are tokenizer control tokens — never legitimate
# prose — so the token AND the rest of its line (the directive that follows it) are removed.
_SPECIAL_TOKEN_STRIP_RE = re.compile(r"<\|[a-zA-Z0-9_]{1,40}\|?>[^\n]*", re.IGNORECASE)
# Just the control token itself (for detection / marker identity, without eating the line).
_SPECIAL_TOKEN_PROBE_RE = re.compile(r"<\|[a-zA-Z0-9_]{1,40}\|?>", re.IGNORECASE)

# Unambiguous tool-protocol tag names (Qwen/Hermes/Gemma). A bare `<function>` is deliberately NOT
# here — it is too common in ordinary prose/HTML/code — but a `<function=name ...>` INVOCATION is
# caught separately below.
# `function_results`, `tool_response`, `tool_result` and `observation` are the shapes a model uses
# when it writes the RESULT of a tool it never called. Measured 2026-07-31 on laguna-s-2.1:free:
# asked to audit a real file, it emitted `<function_results>File: …</function_results><result>{…}`
# carrying an entirely invented Python module, and that reached the user's screen as evidence.
# Neither `function_call` nor `_FUNCTION_INVOKE_PROBE_RE` (which needs `=` or space after
# `function`) matches `function_results`, so every guard was structurally blind to it.
_TOOL_TAG_NAMES = (
    "tool_call|tool_code|function_call|function_calls"
    "|function_results|function_result|tool_response|tool_result|tool_output"
)
_TOOL_BLOCK_RE = re.compile(
    rf"<({_TOOL_TAG_NAMES})\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
# A lone opening/closing tool tag with no partner (e.g. a bare `<tool_call>`).
_LONE_TOOL_TAG_RE = re.compile(rf"</?(?:{_TOOL_TAG_NAMES})\b[^>]*>", re.IGNORECASE)
# A `<function ...>` that carries a name/attribute (`<function=google_search>`), as opposed to a bare
# `<function>` mentioned in prose. Removes the whole block when closed, else the opening tag.
_FUNCTION_INVOKE_RE = re.compile(
    r"<function[=\s][^>]*>(?:.*?</function>)?", re.IGNORECASE | re.DOTALL
)
_FUNCTION_INVOKE_PROBE_RE = re.compile(r"<function[=\s][^>]*>", re.IGNORECASE)

# `<tool>`, `<tools>`, `<invoke>`, `<tool_use>` — real tool-protocol wrappers that were in NO list,
# so a turn wrapped in one leaked to the user. Measured on the live failure:
#
#   in   <tool>\n{"name": "web.search", "arguments": {"query": "price of sol"}}\n</tool>
#   out  <tool>\n\n</tool>          <- the JSON went, the wrapper stayed
#   in   <tool>{"name": "web.search", ...}</tool>        (all on one line)
#   out  unchanged, markers=[]      <- not detected at all
#
# The one-line form escaped every rule because `_LINE_START_BRACE_RE` requires the `{` to open a
# line, and the multi-line form left `<tool></tool>` behind because the emptiness probe below saw
# the letters of the word `tool` inside the surviving tag and concluded real content remained.
#
# These names are ambiguous enough that stripping them on sight would be wrong — `<tool>` could
# appear in a document about XML — so they are only treated as an envelope when the tag CARRIES a
# tool call: a JSON body, or a `name=`/`=` attribute on the tag itself. That signature is what
# separates a wrapper from a word.
_AMBIGUOUS_TOOL_TAG_NAMES = "tool|tools|invoke|tool_use|toolcall"
_AMBIGUOUS_TOOL_BLOCK_RE = re.compile(
    rf"<({_AMBIGUOUS_TOOL_TAG_NAMES})\b[^>]*>\s*\{{.*?\}}\s*</\1>",
    re.IGNORECASE | re.DOTALL,
)
# `<invoke name="web.search"> ... </invoke>` — the call is in the attribute, so the body need not be
# JSON at all.
_ATTRIBUTED_TOOL_BLOCK_RE = re.compile(
    rf"<({_AMBIGUOUS_TOOL_TAG_NAMES})\s+[a-z_:-]+\s*=\s*[^>]*>(?:.*?</\1>)?",
    re.IGNORECASE | re.DOTALL,
)

# A bare function-call DIRECTIVE on its own line: `call:google_search("btc price")`. The `call:`
# prefix is the foreign signature — requiring it means an ordinary code line such as `print("hi")`
# is never matched.
_CALL_DIRECTIVE_LINE_RE = re.compile(r"(?im)^[ \t>*_`\-]*call:[a-z_][a-z0-9_.]*\s*\([^\n]*$")
_CALL_DIRECTIVE_PROBE_RE = re.compile(r"(?im)^[ \t>*_`\-]*call:[a-z_][a-z0-9_.]*\s*\(")


# A JSON object that OPENS A LINE (optionally indented, optionally inside a ``` fence). Anchoring to
# a line start is what separates "the model emitted a tool call" from "prose quotes {"a": 1} inline",
# and it is why broadening the scan below does not start eating ordinary content.
# The prefix class matches `_CALL_DIRECTIVE_LINE_RE`: a leaked call arrives bulleted, quoted or
# emphasised at least as often as bare, and `^[ \t]*\{` saw none of those — measured, a
# `- {"name": "web.search", "arguments": {...}}` line survived the guard untouched. Widening the
# ANCHOR is safe because the anchor was never the filter: `_tool_call_name` is, and it requires a
# tool-name key plus an arguments dict (or a tool-shaped identifier plus an argument-named
# sibling), so an ordinary data object in a bullet list is still not swept up.
_LINE_START_BRACE_RE = re.compile(r"(?m)^[ \t>*_`\-]*\{")
# Tool-identifier keys, then argument-container keys. A leak needs BOTH — a bare {"name": "myapp"}
# (package.json) or {"summary": ..., "bullets": [...]} (a real structured answer) has no argument
# object and is left alone.
# `type` joined 2026-08-15: nemotron's dialect leaks {"type": "search_web", "query": ...} and the
# guard did not know the key -- measured live, that exact JSON shipped as the final answer twice.
# It stays AMBIGUOUS (never in the unambiguous set): {"type": "user"} is ordinary data, so a
# `type`-named call still needs a dotted/snake_case identifier plus an argument sibling.
_TOOL_NAME_KEYS = ("tool", "name", "function", "tool_name", "recipient", "action", "intent", "command", "type")
# Keys that can only mean a tool call. `name`, `action`, `function` and `command` are ordinary
# English and appear in real data, so they still require a dotted/snake_case identifier; these
# do not.
_UNAMBIGUOUS_TOOL_NAME_KEYS = frozenset({"tool", "tool_name", "intent", "recipient"})
_TOOL_ARG_KEYS = ("args", "arguments", "parameters", "input", "params")
# A FLAT tool call carries its arguments as SIBLING keys instead of a nested object:
#   {"action": "read_file", "path": "RESEARCH_STATUS.md"}
# That shape reached a user verbatim. Detecting it needs care, because {"name": "myapp", "version":
# "1.0.0"} must stay clean -- so it requires BOTH a value that looks like a tool identifier (dotted
# or snake_case: read_file, web.search, workspace.read_file -- a bare word like "myapp" does not
# qualify) AND at least one sibling key that names an argument.
_TOOL_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._][a-z0-9]+)+$", re.IGNORECASE)
_FLAT_TOOL_ARG_KEYS = frozenset(
    # `queries` is the plural of an entry already here, and its absence let a real leak through:
    # measured 2026-08-05 on the aviation prompt, `{"tool":"search","queries":[...]}` was rendered
    # to the user as the answer because this set carried only the singular. The flat form still
    # requires a tool-shaped NAME as well, so an ordinary data object with a `queries` field is not
    # swept up by adding it.
    {"path", "query", "queries", "command", "cmd", "url", "content", "file", "filename", "pattern",
     "text", "prompt", "cwd", "directory", "dir", "target", "search", "expression"}
)
_EMPTY_FENCE_RE = re.compile(r"```[a-z]*\s*```")


def _json_object_end(text: str, start: int) -> int:
    """Index just past the JSON object opening at ``start``, or -1. String- and escape-aware, so a
    brace inside a quoted command (``{"command": "awk '{print}'"}``) does not end the object."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return -1


def _tool_call_name(payload: object) -> str:
    """Return the tool name when ``payload`` is a tool-call envelope in any common provider shape."""
    if not isinstance(payload, dict):
        return ""
    calls = payload.get("tool_calls")
    if isinstance(calls, list) and calls:  # OpenAI/OpenRouter native shape leaked as text
        first = calls[0]
        if isinstance(first, dict):
            function = first.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str) and function["name"].strip():
                return function["name"].strip()[:80]
            if isinstance(first.get("name"), str) and first["name"].strip():
                return first["name"].strip()[:80]
        return "tool_calls"
    name = ""
    name_key = ""
    for key in _TOOL_NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            name, name_key = value.strip(), key
            break
        if isinstance(value, dict) and isinstance(value.get("name"), str) and value["name"].strip():
            name, name_key = value["name"].strip(), key
            break
    if not name:
        return ""
    for key in _TOOL_ARG_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            return name[:80]
        # OpenAI serialises `arguments` as a JSON *string*.
        if key == "arguments" and isinstance(value, str) and value.strip().startswith("{"):
            return name[:80]
    # Flat form: arguments as sibling keys. Requires a tool-shaped identifier AND an argument-named
    # sibling, so ordinary data objects are not swept up.
    #
    # The dotted/snake_case requirement exists because `name` and `action` are ambiguous -- it is
    # what keeps {"name": "myapp", "version": "1.0.0"} clean. An UNAMBIGUOUS key does not need it:
    # nothing writes {"tool": ...} or {"intent": ...} except a tool call, so a bare word qualifies
    # there. Measured 2026-08-05 on the aviation prompt, `{"tool":"search","queries":[...]}` was
    # rendered to the user as the answer precisely because `search` is a bare word and the name had
    # arrived under `tool`. An argument-named sibling is still required either way.
    identifier_ok = bool(_TOOL_IDENTIFIER_RE.match(name)) or name_key in _UNAMBIGUOUS_TOOL_NAME_KEYS
    if identifier_ok and any(
        str(key).strip().lower() in _FLAT_TOOL_ARG_KEYS for key in payload
    ):
        return name[:80]
    return ""


def _iter_tool_call_spans(text: str) -> list[tuple[int, int, str]]:
    """All (start, end, tool_name) spans of leaked tool-call JSON in ``text``.

    Round 1 only recognised a reply that was ENTIRELY one JSON object, so any prose preamble
    defeated it — which is exactly what shipped in the live incident (two sentences, then a raw
    ``{"tool": "bash", ...}``). Scanning every line-anchored object closes that.
    """
    spans: list[tuple[int, int, str]] = []
    for match in _LINE_START_BRACE_RE.finditer(text):
        start = match.end() - 1
        if any(start < end for _, end, _ in spans):  # already inside a captured object
            continue
        end = _json_object_end(text, start)
        if end == -1:
            continue
        try:
            payload = json.loads(text[start:end])
        except (TypeError, ValueError):
            continue
        name = _tool_call_name(payload)
        if name:
            spans.append((start, end, name))
    return spans


def stream_release_split(buffered: str) -> tuple[str, str]:
    """Split accumulated stream text into ``(safe_to_release, must_hold)``.

    Streamed chunks are UNRETRACTABLE once sent, and a leaked tool call spans several of them, so the
    streaming path cannot scrub chunk-by-chunk -- by the time the closing brace arrives the opening
    is already on screen. A tool-call envelope always begins at a line-anchored ``{`` (the same
    anchor `_iter_tool_call_spans` uses), so everything BEFORE the first such brace provably cannot
    be part of one and may stream live; the remainder is held until the turn ends, when the full
    guard runs on it.

    Ordinary prose never contains a line-opening ``{``, so the common case holds back nothing and
    streams exactly as before.
    """
    text = str(buffered or "")
    match = _LINE_START_BRACE_RE.search(text)
    if match is None:
        return text, ""
    start = match.end() - 1
    return text[:start], text[start:]



def foreign_markers(text: str) -> list[str]:
    """Return the distinct foreign tool-call / control markers found in ``text`` (empty when clean).

    Detection only — it does not modify the text. Used both as a boolean signal and to log exactly
    which leaked vocabulary was seen.
    """
    s = str(text or "")
    found: list[str] = []
    for _start, _end, leaked_tool in _iter_tool_call_spans(s):
        marker = f"json_tool_envelope:{leaked_tool}"
        if marker not in found:
            found.append(marker)
    # A `<tool>`/`<invoke>` envelope must register here, not only in the scrubber: `foreign_markers`
    # is the boolean that decides whether the guard runs at all, so a wrapper it cannot see is a
    # wrapper that reaches the user untouched. Reported by tag name rather than by the whole match,
    # which would put the leaked arguments into the log line.
    for rx in (_AMBIGUOUS_TOOL_BLOCK_RE, _ATTRIBUTED_TOOL_BLOCK_RE):
        for match in rx.finditer(s):
            marker = f"<{match.group(1).lower()}>"
            if marker not in found:
                found.append(marker)
    for rx in (
        _SPECIAL_TOKEN_PROBE_RE,
        _LONE_TOOL_TAG_RE,
        _FUNCTION_INVOKE_PROBE_RE,
        _CALL_DIRECTIVE_PROBE_RE,
    ):
        for match in rx.finditer(s):
            token = match.group(0).strip()
            if token and token not in found:
                found.append(token)
    return found[:8]


def scrub_foreign_markers(text: str) -> str:
    """Remove leaked foreign tool-call syntax, returning the surviving real content.

    The result may be empty when the whole reply was just a foreign tool call (no real answer) — the
    caller treats that as a failed turn rather than showing a blank. Prose that merely mentions a
    tool is left intact.
    """
    s = str(text or "")
    if not foreign_markers(s):
        return s.strip()
    # Excise each leaked tool-call object (last-first, so earlier offsets stay valid), then drop any
    # fence left empty behind it. A reply that was ONLY the tool call collapses to "" -- the caller
    # treats that as a failed turn rather than showing a blank.
    # Whole envelopes first, wrapper included. Doing this before the JSON-span pass is what stops
    # `<tool>{...}</tool>` on ONE line from escaping — the span scanner anchors on a line-opening
    # brace, and this form has none.
    s = _AMBIGUOUS_TOOL_BLOCK_RE.sub("", s)
    s = _ATTRIBUTED_TOOL_BLOCK_RE.sub("", s)
    # Then the tag-only rules, BEFORE the emptiness probe rather than after it. The probe asks
    # whether any letter or digit survives; running it while `<tool>` and `</tool>` were still in the
    # string meant the letters of the tag name answered "yes", so a reply that was nothing but a
    # tool call was shown to the user as `<tool></tool>` instead of collapsing to a failed turn.
    s = _TOOL_BLOCK_RE.sub("", s)
    s = _FUNCTION_INVOKE_RE.sub("", s)
    s = _SPECIAL_TOKEN_STRIP_RE.sub("", s)
    s = _LONE_TOOL_TAG_RE.sub("", s)
    s = _CALL_DIRECTIVE_LINE_RE.sub("", s)

    spans = _iter_tool_call_spans(s)
    if spans:
        for start, end, _name in reversed(spans):
            s = s[:start] + s[end:]
        s = _EMPTY_FENCE_RE.sub("", s)
    s = _EMPTY_FENCE_RE.sub("", s)
    if not re.search(r"[A-Za-z0-9]", s):
        return ""
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --- Reasoning-monologue leakage ----------------------------------------------------------------
#
# A thinking model reasons before it answers, and Ollama quarantines that reasoning in
# `message.thinking` only while its parser is ON. `think: false` switches off the PARSER, not the
# reasoning, so with it off the monologue arrives in `message.content` and IS the answer the user is
# shown. Measured live 2026-08-01 on the general chat path: the ENTIRE visible reply to a hard
# question was "Let me look at...", "But wait...", "Actually..." cut off mid-sentence at 7,786
# tokens, with no answer anywhere in it and two explicit instructions never addressed.
#
# A reply that is ONLY reasoning is a FAILED LANE — it escalates to the next ranked model rather
# than being shown or silently emptied (core/memory_first_router.py::_soft_failure_details). That
# makes a false positive expensive (a real answer thrown away and re-generated), so a single
# monologue phrase is never enough. Two independent routes qualify:
#
#   * a `<think>` block that OPENS the reply. Truncated before its closing tag there is no answer by
#     construction, and closed with nothing after it the answer is empty. A `<think>` that appears
#     LATER in the reply is content ABOUT the tags — a file the user asked for that parses them —
#     and is left alone. Same rule as
#     core/agent_runtime/builder/app_builder.py::strip_reasoning_monologue, deliberately.
#   * otherwise the reply must OPEN with a self-directed reasoning lead, AND still be reasoning in
#     its second half, AND either be cut off mid-sentence or carry three distinct monologue phrases.
#
# A real answer that merely contains "but wait, actually ..." in the middle opens with the answer,
# so it never reaches the second test — and an answer that legitimately DISCUSSES reasoning keeps
# its "Final answer:"-style handoff, which exempts it outright.

_THINK_OPEN_RE = re.compile(r"<think(?:ing)?\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think(?:ing)?\s*>", re.IGNORECASE)

# Throat-clearing a monologue opens with before the reasoning proper ("Okay, so the user wants...").
_MONOLOGUE_FILLER = (
    r"(?:ok(?:ay)?|alright|hmm+|huh|uh+|um+|well|so|right|now|then|also|but|and|"
    r"first(?:ly)?|second(?:ly)?|actually|wait|hold on)\b[\s,.:;!?—–-]*"
)
# Self-directed inspection, not teaching. "Let me explain / know / show you / walk you through" are
# ordinary answer phrasings and are deliberately absent.
_MONOLOGUE_OPENER = (
    r"(?:"
    r"the user (?:is |just |also |now )?(?:asking|asks|asked|wants|wanted|needs|said|says|"
    r"mentioned|gave|provided|is|has)\b"
    r"|(?:i|we)(?:'|’)?(?:m| am) (?:being asked|asked|supposed to)\b"
    r"|(?:i|we) (?:need|have) to\b"
    r"|(?:i|we) should\b"
    r"|(?:i|we) (?:don(?:'|’)t|do not) (?:see|have|know)\b"
    r"|let me\s+(?:first\s+|now\s+|also\s+|just\s+|quickly\s+|carefully\s+|actually\s+)?"
    r"(?:check|re-?check|double-?check|inspect|look|think|see|start|begin|re-?read|read|"
    r"reconsider|re-?examine|examine|analy[sz]e|figure|try|recall|consider|parse|trace|verify|"
    r"confirm|make sure|break (?:this|it) down|understand|scan|search|find|count|map|recap|"
    r"go back|work out)\b"
    r"|let(?:'|’)s\s+(?:see|think|check|look|start|figure|break|recap)\b"
    r"|wait[,.!]"
    r"|actually[,.!]"
    r"|hold on[,.!]"
    r"|hmm+[,.!]"
    r"|looking (?:at|back at) (?:the|this|my)\s+"
    r"(?:conversation|context|question|request|prompt|history|instructions?|file|code|task)\b"
    r"|(?:from|based on) the (?:previous|earlier) (?:turn|message|context|conversation)\b"
    r"|(?:my|the) (?:job|task|goal) (?:here )?is to\b"
    r")"
)
_MONOLOGUE_LEAD_RE = re.compile(
    rf"^[ \t>*_`#\-]*(?:{_MONOLOGUE_FILLER})*{_MONOLOGUE_OPENER}",
    re.IGNORECASE,
)
# The same vocabulary, unanchored — counted to measure how much of the reply is still monologue.
_MONOLOGUE_MARKER_RE = re.compile(
    r"(?:"
    r"\bbut wait\b|\bwait[,.!]|\bhold on[,.!]|\bhmm+\b|\bactually[,.!]"
    r"|\blet me (?:check|re-?check|look|think|see|re-?read|read|reconsider|start|begin|first|try|"
    r"verify|confirm|make sure|figure|count|scan|trace|parse|recap|go back)\b"
    r"|\blet(?:'|’)s (?:see|think|check|look|recap)\b"
    r"|\bthe user (?:is asking|asks|asked|wants|wanted|needs|said|mentioned)\b"
    r"|\bi need to (?:check|look|see|find|figure|read|verify|confirm|make sure|understand|recall)\b"
    r"|\bi should (?:check|look|probably|also|first|make sure|verify|re-?read)\b"
    r"|\bthat(?:'|’)s not (?:right|quite right|correct)\b"
    r"|\bno,? wait\b|\bokay,? so\b|\bso the user\b|\bwait,? no\b"
    r"|\bi(?:'|’)?m (?:supposed to|being asked)\b"
    r"|\bmaybe i should\b"
    r")",
    re.IGNORECASE,
)
# A reply that reached a handoff HAS an answer, whatever preceded it. Checked before the monologue
# count so a model that thinks out loud and then answers keeps its answer.
_ANSWER_HANDOFF_RE = re.compile(
    r"(?:^|[.\n])\s*(?:final answer|the answer|answer|final|in summary|to summari[sz]e|"
    r"here(?:'|’)s the answer|short version)\s*[:\-—]\s*\S",
    re.IGNORECASE | re.MULTILINE,
)
# A finished sentence ends on punctuation or a closing delimiter. A generation cut off by the token
# budget ends on a word or a comma — the signature of the live 7,786-token failure.
_SENTENCE_END_RE = re.compile(r"""[.!?…:;"'’”)\]}`*]\s*$""")


def strip_reasoning_block(text: str) -> str:
    """Drop a leading ``<think>`` block, returning the answer that follows it.

    A thinking model's chat template opens the tag itself, so the reasoning usually arrives with
    only a CLOSING tag; the block is one leading run, so it ends at the FIRST ``</think>``. A reply
    that opens the tag SOMEWHERE ELSE is content about the tags (a file the user asked for), not
    reasoning, and is returned whole. A reasoning run truncated before its closing tag carries no
    marker at all and is not recoverable here — ``reasoning_only_markers`` classifies that shape as
    a failed lane instead.
    """
    body = str(text or "")
    close_match = _THINK_CLOSE_RE.search(body)
    if close_match is None:
        return body
    opens_the_reply = _THINK_OPEN_RE.match(body.lstrip()) is not None
    opener_before = _THINK_OPEN_RE.search(body[: close_match.start()]) is not None
    if opener_before and not opens_the_reply:
        return body
    return body[close_match.end() :].strip("\n")


def reasoning_only_markers(text: str) -> list[str]:
    """Why ``text`` reads as private reasoning with no answer in it (empty list when it does not).

    Detection only. The caller decides what a monologue costs — the router escalates to the next
    ranked model, the user-facing shaper refuses to show it.
    """
    body = str(text or "").strip()
    if not body:
        return []

    close_match = _THINK_CLOSE_RE.search(body)
    if _THINK_OPEN_RE.match(body) is not None:
        if close_match is None:
            # Cut off by the token budget before the reasoning ever closed: no answer was written.
            return ["think_block_unterminated"]
        return [] if body[close_match.end() :].strip() else ["think_block_only"]
    if close_match is not None and _THINK_OPEN_RE.search(body[: close_match.start()]) is None:
        return [] if body[close_match.end() :].strip() else ["think_block_only"]

    if _MONOLOGUE_LEAD_RE.match(body) is None:
        return []
    if _ANSWER_HANDOFF_RE.search(body):
        return []
    found = {match.group(0).strip().lower() for match in _MONOLOGUE_MARKER_RE.finditer(body)}
    if not found:
        return []
    # Still reasoning in its second half. A reply that opens by thinking out loud and then pivots to
    # a real answer leaves the tail clean, and that is exactly the reply that must survive.
    if _MONOLOGUE_MARKER_RE.search(body[len(body) // 2 :]) is None:
        return []
    cut_off = _SENTENCE_END_RE.search(body) is None
    if not cut_off and len(found) < 3:
        return []
    markers = sorted(found)[:6]
    if cut_off:
        markers.append("cut_off_mid_sentence")
    return markers


def is_reasoning_only(text: str) -> bool:
    """True when a reply is a thinking model's monologue with no synthesised answer in it."""
    return bool(reasoning_only_markers(text))


# --- Streaming release gate ---------------------------------------------------------------------
#
# `stream_release_split` above holds back tool-call ENVELOPES, but a reasoning monologue carries no
# line-anchored brace, so on the streamed web-chat path a raw "Okay, so the user wants me to..."
# shipped to the screen verbatim — and because chunks had been yielded, the guarded buffered reply
# (the honest degraded message the turn actually produced) was then suppressed in favour of a bare
# usage footer. Measured 2026-08-01: `looks_like_internal_payload` and
# `suppress_internal_reasoning_leak` ran on every BUFFERED reply and on no streamed one.
#
# Chunks are unretractable, so the gate's only power is WHEN to release. Every monologue detector
# is anchored to how the reply OPENS (`_MONOLOGUE_LEAD_RE.match`, the filename-array lead, a
# reply-initial `<think>`), which gives the invariant this gate is built on: once the opening is
# proven innocent, the monologue guards can never fire on the full text, so the rest may stream
# live. The gate therefore holds only the HEAD — until the lead regex matches (hold everything, let
# the turn-end guard judge the whole reply), the head provably diverges from every risky opening
# (release immediately — ordinary prose clears within a word or two), or a probe budget expires
# (release; past this depth the buffered guard would not condemn the reply either).
#
# Suppression is never decided here mid-stream: at flush the FULL accumulated text goes through the
# same `internal_payload_or_monologue` the buffered path uses, so the gate can only ever withhold
# what the buffered path would also have refused — and when it does, zero bytes have shipped, which
# lets the transport fall back to delivering the guarded buffered reply instead of a bare footer.

# How much reply-opening to accumulate before declaring the head clean when it still resembles a
# risky lead ("Let me walk you through it..."). ~60 tokens: a monologue lead that has not matched
# by this depth would not be condemned by the buffered guard either, and on a local model this
# costs about a second of first-paint on exactly the ambiguous openings.
_STREAM_HEAD_PROBE_CHARS = 240

_LEAD_FRAME_STRIP_RE = re.compile(r"^[ \t>*_`#\-]+")
# One throat-clearing token plus its trailing delimiters, consumed repeatedly to find the first
# substantive word of the reply.
_LEAD_FILLER_SKIM_RE = re.compile(rf"^(?:{_MONOLOGUE_FILLER})", re.IGNORECASE)

# Every way a `_MONOLOGUE_LEAD_RE` opener can BEGIN, lowercased, apostrophes normalised. A head
# whose first substantive words diverge from all of these can never grow into a monologue lead, so
# it releases without waiting for the probe budget.
_MONOLOGUE_OPENER_PREFIXES = (
    "the user", "the job", "the task", "the goal",
    "my job", "my task", "my goal",
    "i'm", "i am", "we'm", "we are",
    "i need", "we need", "i have", "we have",
    "i should", "we should",
    "i don", "i do not", "we don", "we do not",
    "let me", "let's", "lets",
    "wait", "actually", "hold on", "hmm",
    "looking at", "looking back",
    "from the", "based on",
)

# Tag onsets that must never stream ahead of the guard: reasoning blocks, fabricated tool results,
# tool-protocol wrappers and tokenizer control tokens. Held from first sight; the flush guard and
# `scrub_foreign_markers` decide what a held tail is actually worth. Ambiguous names (`<tool>`,
# `<invoke>`) cost only latency when innocent — the scrubber releases them at flush untouched.
_STREAM_HOLD_TAG_RE = re.compile(
    r"<\||</?(?:think(?:ing)?|tools?|toolcall|tool_call|tool_code|tool_use|tool_response|"
    r"tool_result|tool_output|function_calls?|function_results?|invoke|observation)\b|<function[=\s]",
    re.IGNORECASE,
)
_HOLD_TAG_LITERALS = (
    "<|", "<think", "<thinking", "<tool", "<tools", "<toolcall", "<tool_call", "<tool_code",
    "<tool_use", "<tool_response", "<tool_result", "<tool_output",
    "<function_call", "<function_calls", "<function_result", "<function_results",
    "<function=", "<function ", "<invoke", "<observation",
    "</think", "</thinking", "</tool_call", "</tool_response", "</tool_result",
)


# The filler vocabulary as words, for the boundary case where the head currently ENDS inside a
# possibly-growing token ("wel" may become "well, so the user…") — an incomplete filler must hold,
# not clear.
_FILLER_WORDS = (
    "ok", "okay", "alright", "huh", "well", "so", "right", "now", "then", "also", "but", "and",
    "first", "firstly", "second", "secondly", "actually", "wait", "hold", "hold on",
)
_ELONGATED_FILLER_RE = re.compile(r"(?:hm+|uh+|um+)", re.IGNORECASE)


def _could_still_be_filler(token: str) -> bool:
    lowered = token.lower()
    if any(word.startswith(lowered) for word in _FILLER_WORDS):
        return True
    return bool(_ELONGATED_FILLER_RE.fullmatch(lowered))


def _could_open_monologue(rem: str) -> bool:
    """True while ``rem`` (the head after skimming fillers) could still grow into a monologue
    opener — either it starts with one, or it is a prefix of one still arriving."""
    normalised = rem.lower().replace("’", "'")
    for prefix in _MONOLOGUE_OPENER_PREFIXES:
        if len(normalised) < len(prefix):
            if prefix.startswith(normalised):
                return True
        elif normalised.startswith(prefix):
            return True
    return False


def _could_grow_into_hold_tag(fragment: str) -> bool:
    """True when ``fragment`` (text from a trailing ``<``) is a strict prefix of a watched tag."""
    lowered = fragment.lower()
    return any(
        len(lowered) < len(literal) and literal.startswith(lowered) for literal in _HOLD_TAG_LITERALS
    )


class StreamReleaseGate:
    """Incremental release decisions for one streamed model reply.

    ``feed(chunk)`` returns the text that is safe to put on the wire now; ``flush()`` returns the
    releasable tail once the turn has ended, or ``""`` when the reply is internal payload — in
    which case ``suppressed_reason`` names why and the caller should deliver the guarded buffered
    reply instead (nothing will have been released, by construction).
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._head_cleared = False
        self._hold_all = False
        self._think_head = False
        self._released_any = False
        self.suppressed_reason = ""

    def feed(self, chunk: str) -> str:
        self._buffer += str(chunk or "")
        if not self._head_cleared:
            self._evaluate_head()
            if not self._head_cleared:
                return ""
        released = self._release_scan()
        if released:
            self._released_any = True
        return released

    def flush(self) -> str:
        if not self._head_cleared:
            self._evaluate_head()
        text, self._buffer = self._buffer, ""
        if not text:
            return ""
        if not self._released_any:
            # Nothing has shipped, so the whole reply is in hand: judge it exactly as the
            # buffered path will, so stream and transcript refuse the same things.
            if self._full_reply_is_internal(text):
                self.suppressed_reason = "internal_payload_or_monologue"
                return ""
            return scrub_foreign_markers(text)
        # A held tail begins at a hold onset (a brace or a watched tag), never at the reply
        # opening, so only the payload shapes apply — a monologue lead cannot live mid-reply.
        if self._tail_is_internal(text):
            self.suppressed_reason = "internal_payload_tail"
            return ""
        return scrub_foreign_markers(text)

    # -- head machine ----------------------------------------------------------------------------

    def _evaluate_head(self) -> None:
        if self._hold_all:
            return
        head = self._buffer.lstrip()
        if not head:
            return
        if self._think_head:
            self._skim_closed_think_block(head)
            return
        lowered = head.lower()
        if head.startswith("<"):
            if _THINK_OPEN_RE.match(head):
                # A reasoning block opening the reply. Wait for its close, then re-evaluate what
                # follows as the actual reply; unclosed at flush, the full guard refuses it.
                self._think_head = True
                self._skim_closed_think_block(head)
                return
            if any(lowered.startswith(literal) for literal in _HOLD_TAG_LITERALS) or _could_grow_into_hold_tag(head):
                # A watched tag opening the reply: hold to flush, where the full guard and the
                # scrubber decide (a fabricated result suppresses; a `<tool>` wrapper scrubs).
                return
            # An ordinary angle bracket (HTML, a code sample): fall through to the word logic.
        if head.startswith("["):
            probe = head[1:].lstrip()
            if not probe or probe.startswith('"'):
                # Could be (or become) the filename-array-welded-to-code shape. Hold to flush;
                # a clean pure-array answer is released there, the welded leak is refused.
                return
        if _MONOLOGUE_LEAD_RE.match(head):
            # The reply opens the way a monologue opens. Not a verdict — the full guard decides at
            # flush — but nothing may ship until it does.
            self._hold_all = True
            return
        if len(head) >= _STREAM_HEAD_PROBE_CHARS:
            self._head_cleared = True
            return
        remainder = _LEAD_FRAME_STRIP_RE.sub("", head)
        while True:
            skim = _LEAD_FILLER_SKIM_RE.match(remainder)
            if skim is None or skim.end() >= len(remainder):
                break
            remainder = remainder[skim.end():]
        if not remainder:
            return
        token_still_growing = re.search(r"[\s,.:;!?—–-]", remainder) is None
        if token_still_growing and _could_still_be_filler(remainder):
            return
        if _could_open_monologue(remainder) or remainder.startswith("<"):
            return
        self._head_cleared = True

    def _skim_closed_think_block(self, head: str) -> None:
        close = _THINK_CLOSE_RE.search(head)
        if close is None:
            return
        # The reasoning block is over; what follows is the reply, and it gets its own head
        # evaluation — a monologue can follow its own think block.
        self._buffer = head[close.end():].lstrip("\n")
        self._think_head = False
        self._evaluate_head()

    # -- release scan ------------------------------------------------------------------------------

    def _release_scan(self) -> str:
        text = self._buffer
        hold = len(text)
        brace = _LINE_START_BRACE_RE.search(text)
        if brace is not None:
            hold = min(hold, brace.end() - 1)
        tag = _STREAM_HOLD_TAG_RE.search(text, 0, hold)
        if tag is not None:
            hold = min(hold, tag.start())
        if hold == len(text):
            partial = self._trailing_partial_tag_index(text)
            if partial != -1:
                hold = partial
        released, self._buffer = text[:hold], text[hold:]
        return released

    @staticmethod
    def _trailing_partial_tag_index(text: str) -> int:
        window_start = max(0, len(text) - 24)
        angle = text.rfind("<", window_start)
        if angle == -1:
            return -1
        if _could_grow_into_hold_tag(text[angle:]):
            return angle
        return -1

    # -- flush guards ------------------------------------------------------------------------------

    @staticmethod
    def _full_reply_is_internal(text: str) -> bool:
        try:
            from core.agent_runtime.response import internal_payload_or_monologue

            return internal_payload_or_monologue(text)
        except Exception:
            return False

    @staticmethod
    def _tail_is_internal(text: str) -> bool:
        try:
            from core.tool_call_dialects import looks_like_internal_payload

            return looks_like_internal_payload(text)
        except Exception:
            return False


# --- Terminal synthesis integrity (used post-tool-loop, where observations already exist) -------

# First-person "I still need to run a tool" phrasing. Scoped to the model announcing its OWN pending
# action ("let me search", "I'll run") — NOT advice to the user ("you can search for X"), which is a
# legitimate answer.
_PENDING_TOOL_RE = re.compile(
    r"(?i)\b(?:let me|let's|i'?ll|i will|i'?m going to|i am going to|i need to|i have to|i should|"
    r"i'?m about to|about to|going to)\s+(?:now\s+|just\s+)?"
    r"(?:search|look up|look it up|run|call|use|fetch|query|execute|browse|google)\b"
)
_PENDING_TOOL_HINT_RE = re.compile(
    r"(?i)\b(?:calling|invoking|running|executing)\s+the\s+[a-z0-9_.\- ]{1,30}?\btool\b"
)

#: The MACHINE form of a pending tool: the model emitted the call itself as prose instead of using
#: the tool channel, so the whole answer is a call expression -- `search({"query": ...})`,
#: `search_web("...")`.  Matched against the entire stripped message so an answer that merely
#: MENTIONS `foo(bar)` inside real prose is untouched; only a message that IS a call is rejected.
#: Deliberately shape-based: it names no tool and no topic, so it cannot rot into an allowlist.
_TOOL_CALL_EXPRESSION_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]{1,48}\s*\((?:[^()]|\([^()]*\))*\)[.;]?$",
    re.DOTALL,
)
#: The same claim narrated without a first-person subject: "searching weather for Vilnius...".
#: `_PENDING_TOOL_RE` needs "I'll"/"let me"; a bare progressive headline slips past it.  Restricted
#: to the PROGRESSIVE form on purpose: "Looking up X" reports work in flight, while the imperative
#: "Look up the manual." is a terse but real answer and must survive.
_PENDING_TOOL_NARRATION_RE = re.compile(
    r"(?i)^\s*(?:ok(?:ay)?[,:]?\s+)?(?:now\s+|just\s+)?"
    r"(?:searching|looking\s*up|fetching|querying|retrieving|browsing|checking)\b[^\n]{0,160}$"
)
#: Evidence that a short line reports a RESULT rather than announcing work: a digit, or a verb that
#: states something.  Kept topic-free so it cannot become weather/finance vocabulary.
_ANSWER_CONTENT_RE = re.compile(
    r"\d|(?i:\bsource\b|\bfound\b|\bshows?\b|\bis\b|\bare\b|\bwas\b|\bwere\b|\bno\b|\bnone\b)"
)
_FENCE_RE = re.compile(r"^```[a-z0-9_+-]*\s*\n?|\n?```$", re.IGNORECASE)


def answer_is_tool_invocation(text: str) -> bool:
    """True when a supposedly-final answer IS a tool call, or announces one instead of answering.

    A tool call is a request the runtime should have executed, never an answer the user should
    read.  Both forms observed in the live 0.5.0 chat lane are covered: the machine form
    (``search_web("Berlin 7-day weather forecast")``) and the subjectless narration
    (``searching weather for Vilnius next week...``).  Unlike :func:`claims_pending_tool`, this is
    meaningful on EVERY lane -- a model can emit tool syntax whether or not a tool loop ran.
    """

    stripped = _FENCE_RE.sub("", str(text or "").strip()).strip()
    if not stripped:
        return False
    if _TOOL_CALL_EXPRESSION_RE.match(stripped):
        return True
    # The JSON envelope form, at any length. This function never consulted _iter_tool_call_spans,
    # so a leaked {"type": "search_web", "query": "..."} of 188 characters sailed past the 180-char
    # "long replies carry content" presumption below and shipped as the final answer -- measured
    # live, twice, 2026-08-15. Length is not content: if removing every tool-call span leaves
    # nothing substantive, the answer IS the call.
    spans = _iter_tool_call_spans(stripped)
    if spans:
        remainder = stripped
        for start, end, _name in sorted(spans, reverse=True):
            remainder = remainder[:start] + remainder[end:]
        if len(remainder.strip()) < 20:
            return True
    if "\n" in stripped or len(stripped) > 180:
        # A multi-line or long reply carries content; a bare announcement does not.
        return False
    # An announcement promises retrieval and delivers none, so it must not end a turn.  A real
    # short answer that happens to start with one of these verbs states something instead.
    return bool(_PENDING_TOOL_NARRATION_RE.match(stripped)) and not _ANSWER_CONTENT_RE.search(
        stripped
    )

_CONTENT_TOKEN_RE = re.compile(r"[a-z0-9]{5,}")


def claims_pending_tool(text: str) -> bool:
    """True when a supposedly-final answer still asserts it needs to run a tool (or carries leaked
    tool syntax). Meaningful only where the observations are already in hand — the tool loop's
    synthesis step — so any pending-tool claim there is wrong by construction."""
    s = str(text or "")
    if foreign_markers(s):
        return True
    return bool(_PENDING_TOOL_RE.search(s) or _PENDING_TOOL_HINT_RE.search(s))


def answer_is_unfulfilled_intent(text: str) -> bool:
    """True when a final answer only PROMISES tool work and delivers nothing.

    Measured live 2026-08-15: "I'll provide the car sales breakdown you requested for your
    article. Let me search for the latest data on vehicle sales, production locations, ..."
    shipped as the complete visible answer -- no search ran, nothing was delivered, and the reply
    reads as work in progress that never happened.

    Deliberately fires only on a reply that is intent-DOMINANT: it must claim a pending tool AND
    carry no marker of delivered substance (a digit, a newline, a path, a quote, a backtick, or a
    list). "Let me search my notes -- found it: the config lives in /etc/foo" carries a path and
    passes; a long structured answer passes on length alone. The safe direction is letting a
    doubtful reply through unchanged -- this guard exists for the reply that is nothing BUT the
    promise."""

    body = str(text or "").strip()
    if not body or len(body) > 400:
        return False
    if not (_PENDING_TOOL_RE.search(body) or _PENDING_TOOL_HINT_RE.search(body)):
        return False
    if any(ch.isdigit() for ch in body):
        return False
    if any(marker in body for marker in ("\n", "/", "`", '"', "- ", "* ", "|")):
        return False
    return body.count(".") + body.count("!") + body.count("?") <= 3


def is_ungrounded(text: str, observations) -> bool:
    """True when a final answer shares NO content token with any supplied observation — i.e. it
    ignored the tool results entirely (the "Gemma ignored the successful web.research" failure).

    Deliberately conservative to avoid rejecting a good answer: it fires only when observations are
    present, the answer is non-trivial (>= 40 chars), and there is ZERO overlap of 5+-character
    alphanumeric tokens. A short acknowledgement or any real overlap is treated as grounded.
    """
    answer = str(text or "").strip()
    obs_list = [str(o or "") for o in (observations or []) if str(o or "").strip()]
    if not obs_list or len(answer) < 40:
        return False
    answer_tokens = set(_CONTENT_TOKEN_RE.findall(answer.lower()))
    if not answer_tokens:
        return False
    obs_tokens = set(_CONTENT_TOKEN_RE.findall("\n".join(obs_list).lower()))
    if not obs_tokens:
        return False
    return answer_tokens.isdisjoint(obs_tokens)


# --- Unobserved live-value claims -------------------------------------------------------------
#
# Measured live 2026-08-15 (operator transcript, 12:45): the prior turn reported weather
# UNAVAILABLE (both lookups failed).  The user's follow-up ("not answered in full wtf?!") was
# classified `unknown` and routed to the ordinary chat lane, where nemotron -- with ZERO tools run
# that turn -- stated with full confidence: "Warsaw: 22 C, partly cloudy; Manchester: 18 C",
# flight prices "Luton: EUR 42; Gatwick: EUR 58", and "last 7 days: BTC +4.8%, BNB +6.2%" -- the
# exact readings the runtime had just failed to obtain.  Confidently wrong live claims wearing a
# correct answer's shape are the worst output class this runtime produces, because nothing about
# the text warns the reader.
#
# The invariant: a reply may not present SPECIFIC time-anchored live values -- a currency amount, a
# temperature, a signed percent change bound to now/current/today/this week -- on a turn that ran
# no observation of the world.  Both halves are required before this fires, and both are
# structural, not topical:
#
#   1. a QUANTIFIED live-value pattern: a number bound to a currency symbol/code, a degree /
#      temperature unit, or a signed-or-directional percent change (the "change" is what separates
#      a market move from a fraction in a math answer);
#   2. a CURRENTNESS anchor near it (same +/-120-character window): now / currently / today /
#      tonight / this week / last N days ...  A number with no claim of nowness is history,
#      arithmetic, or code, and none of those are this module's business.
#
# Hedged prose is exempt by construction: "typically 24-27 C in August" is a climate statement
# drawn from general knowledge, not a reading, so a hedge word inside the same window
# (typically / usually / on average / ...) withdraws that window from consideration.  Fenced and
# inline code is stripped before scanning, so numeric literals in a code answer never match.
#
# Whether the turn observed anything is NOT decided here -- `turn_ran_observations` reads the
# same-turn evidence channels off `source_context` (`runtime_tool_observations` is documented as a
# same-turn channel in `core/prompt_normalizer.py`; retrieval receipts are appended in place by
# `core/retrieval_observability.py` / `core/fresh_data/*`; user-supplied material counts as
# observation the same way `core.unsourced_current_claim.turn_has_current_evidence` counts it).
# The caller (the final seam in `core/agent_runtime/response.py`) combines the two: claims found
# AND nothing observed => the reply is replaced with a notice naming what it declined to invent.

#: A currency amount: symbol-first ($42, €4,200.50), ISO-4217-code-adjacent (EUR 42 / 42 EUR), or
#: an everyday currency word (42 euros).  The code list is drawn from a formal standard
#: (ISO 4217), the same deliberate exception to the no-enumeration rule that
#: `core/unsourced_current_claim.py` documents.
_LIVE_CURRENCY_CODE = r"(?:USD|EUR|GBP|JPY|CHF|SEK|NOK|DKK|PLN|CZK|HUF|CAD|AUD|NZD|CNY|INR)"
_LIVE_PRICE_RE = re.compile(
    r"[$€£¥]\s?\d[\d,.]*"
    r"|\b" + _LIVE_CURRENCY_CODE + r"\s?\d[\d,.]*"
    r"|\b\d[\d,.]*\s?" + _LIVE_CURRENCY_CODE + r"\b"
    r"|\b\d[\d,.]*\s?(?:dollars?|euros?|pounds?|cents?)\b",
    re.IGNORECASE,
)
#: A temperature reading: 22°C / 18 °F / 22C / 22 C / ℃ / ℉ / "18 degrees".  The bare-letter arm
#: is case-SENSITIVE on purpose -- `22 C` is a reading, `22 c` is not a shape this failure takes,
#: and lowercase would drag in prose like "section 3 c".
_LIVE_TEMPERATURE_RE = re.compile(
    r"(?<![\w.])-?\d{1,3}(?:\.\d+)?\s?(?:°\s?[CFcf]?|℃|℉|[CF]\b|[Dd]egrees?\b)"
)
#: A percent CHANGE, not a percentage: signed (+4.8%, -2%), or a movement verb bound to a percent
#: within the same clause ("up 6.2%", "fell 3%").  An unsigned bare percent is how fractions are
#: written in math help and historical statistics, and those must never match.
_LIVE_PERCENT_CHANGE_RE = re.compile(
    r"[+\-−]\s?\d[\d,.]*\s?%"
    r"|\b(?:up|down|gained?|lost|rose|fell|dropped|climbed|jumped|surged|slid)\b[^.\n]{0,24}?\d[\d,.]*\s?%",
    re.IGNORECASE,
)
_LIVE_VALUE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("temperature", _LIVE_TEMPERATURE_RE),
    ("price", _LIVE_PRICE_RE),
    ("percent-change", _LIVE_PERCENT_CHANGE_RE),
)
#: The claim of nowness.  Bounded to explicit anchors -- a value without one is not a live claim.
_CURRENTNESS_ANCHOR_RE = re.compile(
    r"\b(?:right now|now|currently|current|today|tonight|at the moment|as of"
    r"|this (?:morning|afternoon|evening|week|weekend)"
    r"|(?:last|past)\s+(?:\d+\s+)?(?:hours?|days?|week))\b",
    re.IGNORECASE,
)
#: A hedge turns a reading into a generalization: "typically 24-27 C in August" is climate, not
#: weather, and general knowledge is allowed to say it.
_LIVE_HEDGE_RE = re.compile(
    r"\b(?:typically|usually|normally|generally|historically|on average|average[sd]?|in general"
    r"|tends? to)\b",
    re.IGNORECASE,
)
#
# There was an ATTRIBUTION exemption here until eca76ff9: a window containing "Source: wttr.in",
# "observed 05:48 PM", a URL or a host token was withdrawn from consideration, on the reasoning that
# the live-data lane composes its answers in that shape, that its "receipts do not travel on every
# context dict that reaches the final seam", and that a fabricated attribution would be convicted
# instead by `core.unsourced_current_claim`.  All three premises were measured at the real
# `/api/chat` seam and the first two are false, while the third has a hole:
#
#   successful live-data turn ...... seam reached with turn_ran_observations=True, web_receipts=1
#                                    -- so this branch is never entered on the path the exemption
#                                    claimed to protect.  Receipts DO travel.
#   zero-evidence turn, attributed .. turn_ran_observations=False, receipts=0, and
#                                    `Bergen: Cloudy, 41.7 C right now. Source: wttr.in,
#                                    observed 2026-08-07T12:00:00Z.`  SHIPPED.
#   the same text, unattributed ..... convicted, and replaced with the notice.
#
# So the runtime was strictly safer when a model invented a reading and named NO source: adding a
# fabricated source defeated the guard.  The delegation to `core.unsourced_current_claim` does not
# close it either -- that module requires `current_information_required`, which is decided from the
# REQUEST, and the turns that produce this ("Bergen pls", "Osaka thx") do not classify that way, so
# no guard owned them at all.
#
# An attribution is a CLAIM about where a value came from.  On a turn that observed nothing it is an
# unsupported claim wearing the one shape that invites more trust rather than less, which makes it
# the worst thing to exempt and not a reason to stand down.  The exemption is gone; what keeps this
# seam off legitimately grounded answers is `turn_ran_observations`, which is the conjunct that
# actually knows whether the turn observed -- and, since eca76ff9, knows that a FAILED observation
# is not one.
_FENCED_CODE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
#: How far apart (in characters) a value and its anchor may sit and still be one claim.  Wide
#: enough to span a list row plus its heading ("Cheapest flights today: Luton: EUR 42"), narrow
#: enough that an anchor in one paragraph cannot convict a number two paragraphs later.
_LIVE_CLAIM_WINDOW = 120


def unobserved_live_value_claims(text: str) -> tuple[str, ...]:
    """The kinds of time-anchored live values this reply states, in a stable order.

    Purely textual: it knows nothing about what the turn observed.  Returns e.g.
    ``("temperature", "price")`` when the reply binds those value shapes to a currentness anchor
    inside the same +/-120-character window with no hedge word in that window; ``()`` otherwise.

    Naming a source does not withdraw a window from consideration -- see the note above the code
    strippers.  The caller has already established that the turn observed nothing, and a reading
    attributed to an instrument the turn never reached is the failure, not an exemption from it.
    """

    body = _INLINE_CODE_RE.sub(" ", _FENCED_CODE_RE.sub(" ", str(text or "")))
    if not body.strip():
        return ()
    found: list[str] = []
    for kind, pattern in _LIVE_VALUE_KINDS:
        for match in pattern.finditer(body):
            window = body[max(0, match.start() - _LIVE_CLAIM_WINDOW) : match.end() + _LIVE_CLAIM_WINDOW]
            if not _CURRENTNESS_ANCHOR_RE.search(window):
                continue
            if _LIVE_HEDGE_RE.search(window):
                continue
            if kind not in found:
                found.append(kind)
            break
    return tuple(found)


#: A temperature written sloppily: `17 c`, `16c`, `18f`. Consulted ONLY by `live_value_windows`,
#: never by `unobserved_live_value_claims`.
#:
#: The shared `_LIVE_TEMPERATURE_RE` keeps its case-SENSITIVE bare-letter arm on purpose, because
#: on an OPEN question `22 c` is more often `section 3 c` than a reading, and a false conviction
#: there costs a correct answer. Under the refused-slot contract the same shape must clear a second
#: gate the open question has no equivalent of -- the window must NAME a slot the ledger already
#: recorded unanswered, with corroboration -- so the prose collision case-sensitivity exists to
#: avoid cannot get through, and the shapes the audit's own instrument counts as quantities
#: (`tests/red_e_followup_fabrication.py`'s case-insensitive `\bc\b`) become reachable here.
#:
#: The trailing `(?![\w])` is what keeps `12 CHF`, `3 cm` and `5 followed` out: the unit letter has
#: to END the token, not start a longer word.
_LOOSE_TEMPERATURE_RE = re.compile(
    r"(?<![\w.])-?\d{1,3}(?:\.\d+)?\s?(?:°\s?[cf]?|℃|℉|[cf])(?![\w])", re.IGNORECASE
)


def live_value_windows(
    text: str, *, radius: int = _LIVE_CLAIM_WINDOW
) -> tuple[tuple[str, str, str], ...]:
    """Every live-value shape in the reply as ``(kind, value, window)`` — WITHOUT the currentness
    requirement and WITHOUT the hedge exemption.

    Same value vocabulary as `unobserved_live_value_claims`; different question. That function asks
    "is this reply making a claim about NOW", and its two exemptions are right for an open
    question: a value with no currentness anchor is not a claim about now, and "typically 24-27 C
    in August" is climate, which general knowledge may state.

    Neither exemption survives contact with a slot the runtime has ALREADY RECORDED as unanswered.
    Measured live on 2026-08-30 (AUD-20260829-003), all 16 fabricated values for such slots were
    invisible to `unobserved_live_value_claims`, and every one of them for one of exactly these two
    reasons — 14 carried a hedge (`typically`, `usually`, `around`, `varies`, `generally`), and the
    remaining 2 carried no currentness anchor. The shapes were never the problem; the exemptions
    were, on the one class of turn where they do not apply.

    So the shapes live here, once, and the two callers differ only in which exemptions they apply.
    `core.refused_slot_register` owns the question this feeds: is the reply asserting one of these
    FOR a slot the record says was refused.

    The window is what the caller needs to decide what the value is ABOUT, so it is returned rather
    than recomputed against a different notion of proximity.
    """
    body = _INLINE_CODE_RE.sub(" ", _FENCED_CODE_RE.sub(" ", str(text or "")))
    if not body.strip():
        return ()
    span = max(0, int(radius))
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[int, int]] = set()
    for kind, pattern in (*_LIVE_VALUE_KINDS, ("temperature", _LOOSE_TEMPERATURE_RE)):
        for match in pattern.finditer(body):
            if (match.start(), match.end()) in seen:
                continue
            seen.add((match.start(), match.end()))
            window = body[max(0, match.start() - span) : match.end() + span]
            found.append((kind, match.group(0).strip(), window))
    return tuple(found)


#: Same-turn evidence channels, each written by a lane that actually observed something this turn:
#: `runtime_tool_observations` (tool loop / workspace audit / fast paths), retrieval receipts
#: (adaptive research, fresh-data fetchers), and material the user supplied with the request.
_TURN_OBSERVATION_KEYS = (
    "runtime_tool_observations",
    "web_retrieval_receipts",
    "fresh_data_retrieval_receipts",
    "external_evidence",
    "attachments",
    "user_material",
    "supplied_files",
    "media_attachments",
)


def turn_ran_observations(source_context) -> bool:
    """Whether THIS turn observed anything at all, judged from the same-turn evidence channels.

    Loose in the exempting direction, but only as far as SUCCESS: an entry that records a usable
    observation means the turn observed, and the lanes that own observed turns
    (`claims_pending_tool`, `is_ungrounded`, `core.unsourced_current_claim`) keep jurisdiction.
    This predicate exists only to identify the turn that observed NOTHING, which is the only turn
    `unobserved_live_value_claims` may convict.

    A FAILED entry is not an observation. Until eca76ff9 this counted any entry at all, including a
    failed tool run -- so a weather lookup that came back `status='failed' source_count=0` exempted
    the very turn it proved had seen nothing, and an invented reading shipped with a borrowed source
    line. `core.observation_evidence` owns the success question for both this predicate and
    `core.unsourced_current_claim.turn_has_current_evidence`, so the two cannot drift apart on it.
    """

    from core.observation_evidence import channel_has_a_usable_observation

    context = source_context if isinstance(source_context, dict) else {}
    return any(channel_has_a_usable_observation(context.get(key)) for key in _TURN_OBSERVATION_KEYS)


def has_restored_evidence_provenance(source_context) -> bool:
    """Whether this turn's channels hold RESTORED evidence -- history carried forward by a recall,
    a resume or a reconstruction, and marked as such by its writer (`fresh: False` or
    `lifecycle: "restored"`).

    This is NOT a freshness licence -- restored entries never count as observations
    (`turn_ran_observations`) and never ground a current claim (`turn_has_current_evidence`). It
    answers the narrower question the final-output backstop needs: when a turn's rendered text
    states live values, are those values inventions of generation -- or re-renderings of stored
    observations whose provenance entry is right there in the channels? A recall answer's readings
    were really fetched (by the turn the entry's `source_turn_id` names, at the timestamp the
    answer itself discloses), so replacing them with "any specific numbers I gave would be
    invented" would be the false statement. Only an affirmative restored mark counts; an
    unmarked channel answers no.
    """

    context = source_context if isinstance(source_context, dict) else {}

    def _is_restored(entry: Any) -> bool:
        if not isinstance(entry, Mapping):
            return False
        fresh = entry.get("fresh")
        if fresh is not None and not fresh:
            return True
        return str(entry.get("lifecycle") or "").strip().lower() == "restored"

    for key in _TURN_OBSERVATION_KEYS:
        value = context.get(key)
        entries = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
        if any(_is_restored(entry) for entry in entries):
            return True
    return False


def unverified_live_value_notice(
    kinds: tuple[str, ...], request_text: str = "", *, part_of_turn: bool = False
) -> str:
    """What ships instead of invented readings.  Names what was declined; states no value.

    When the turn's own request is a currency conversion, the notice names the exact
    conversion it is refusing — the pair and amount out of the request's own parse, never
    a canned subject. A refusal that does not name the demand it refuses reads as a
    generic outage: measured on the served surface, "Convert 100 US dollars to euros."
    came back as "I can't verify current price figures" with no euro anywhere in the
    reply, so the user could not tell WHICH of their three demands had just been
    declined.
    """

    named = ", ".join(kinds) if kinds else "live"
    subject = ""
    try:
        from core.currency_intent import fx_conversion_intent

        request = fx_conversion_intent(str(request_text or ""))
        if request is not None:

            def _side(ref) -> str:
                written = str(getattr(ref, "written", "") or "").strip()
                code = str(getattr(ref, "code", "") or "").strip()
                if written and code and written.casefold() != code.casefold():
                    return f"{written} ({code})"
                return written or code

            subject = (
                f"the conversion of {request.written_amount or request.amount} "
                f"{_side(request.source)} to {_side(request.target)}"
            )
    except Exception:
        subject = ""
    # A planned SUB-TURN is one part of a request whose siblings may well have fetched live data
    # (FINDINGS F15, 2026-09-10 23:48: "I didn't run any live lookup on this turn" stood beside a
    # Cardano quote the same turn had just retrieved). The notice names the part it is about,
    # so it stays true of exactly what it describes.
    scope = (
        f"I didn't run a live lookup for this part of the request ({' '.join(str(request_text or '').split())[:80]})"
        if part_of_turn and str(request_text or "").strip()
        else ("I didn't run a live lookup for this part of the request" if part_of_turn else "I didn't run any live lookup on this turn")
    )
    if subject:
        return (
            f"{scope}, so I can't verify {subject} — "
            "any specific numbers I gave would be invented, and I'm not going to state them. "
            "Ask again and I'll fetch the real data first."
        )
    return (
        f"{scope}, so I can't verify current {named} figures — "
        "any specific numbers I gave would be invented, and I'm not going to state them. Ask again "
        "and I'll fetch the real data first."
    )


__all__ = [
    "StreamReleaseGate",
    "answer_is_unfulfilled_intent",
    "claims_pending_tool",
    "foreign_markers",
    "has_restored_evidence_provenance",
    "is_reasoning_only",
    "is_ungrounded",
    "live_value_windows",
    "reasoning_only_markers",
    "scrub_foreign_markers",
    "strip_reasoning_block",
    "turn_ran_observations",
    "unobserved_live_value_claims",
    "unverified_live_value_notice",
]
