from __future__ import annotations

import bisect
import json
import re
import functools
import hashlib
from dataclasses import dataclass
from datetime import date
from collections.abc import Iterable, Iterator, Mapping, Sequence
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


# --- Linear tool-markup scans -------------------------------------------------------------------
#
# The block/tag patterns above pair a greedy `[^>]*` with a lazy `.*?` (or an optional close), so a
# reply made of many unclosed openings made each regex scan the whole remaining tail per opening —
# measured quadratic through `validate_contract` on main cfae90f (×4 per doubling: e.g. 300 ms at
# 64k chars for the space-run form). The scanners below compute the SAME match set as the pinned
# regexes in a single left-to-right pass: one precomputed index of close-tag and `>` positions, and
# per opening a bisect instead of a rescan. The regexes stay compiled above as the differential
# spec (scratch harness proves span equality); they must not be reinstated on the scrub path.

_AMBIGUOUS_NAMES = ("tool", "tools", "invoke", "tool_use", "toolcall")
_TOOL_BLOCK_NAMES = (
    "tool_call", "tool_code", "function_call", "function_calls",
    "function_results", "function_result", "tool_response", "tool_result", "tool_output",
)
# Every literal whose close tag (`</name>`) terminates one of the block scanners. Order matters
# only for lookup; matching is exclusive because a full-text match plus the mandatory `>` can hold
# at most one name at a given position (the `call`/`calls`, `result`/`results` pairs differ in the
# char the shorter name would need to be `>`).
_CLOSE_TAG_NAMES = frozenset({*_AMBIGUOUS_NAMES, *_TOOL_BLOCK_NAMES, "function"})


_CI_LITERAL_CACHE: dict[str, re.Pattern[str]] = {}


def _ci_starts_with(text: str, pos: int, literal: str) -> bool:
    """Case-insensitive prefix test at ``pos`` with the engine's own IGNORECASE folding.

    ``re.I`` folds İ/ı/ſ/Kelvin-K into ASCII in ways neither ``str.lower`` nor ``str.casefold``
    reproduces, so the test goes through a cached one-shot literal regex — exactly the pinned
    patterns' semantics at O(literal) cost. Every literal is a module constant, so the cache is
    bounded to a handful of entries.
    """
    rx = _CI_LITERAL_CACHE.get(literal)
    if rx is None:
        rx = re.compile(re.escape(literal), re.IGNORECASE)
        _CI_LITERAL_CACHE[literal] = rx
    return rx.match(text, pos) is not None


# `[a-z_:-]+` under IGNORECASE — a single character class, so the run match is linear.
_ATTR_RUN_RE = re.compile(r"[a-z_:-]+", re.IGNORECASE)


def _is_word_char(char: str) -> bool:
    return char == "_" or char.isalnum()


def _skip_ws(text: str, pos: int) -> int:
    n = len(text)
    while pos < n and text[pos].isspace():
        pos += 1
    return pos


class _TagScanIndex:
    """Close-tag, `}`-close and `>` positions for one string, built in a single pass.

    ``close_spans`` maps each folded tag name to the sorted ``(start, end)`` spans of its exact
    ``</name>`` occurrences; ``brace_closes`` maps a name to ``}`` positions whose following
    whitespace run leads into that name's close tag (the ``\\s*</\\1>`` tail of the ambiguous
    envelope); ``gt_positions`` is every ``>`` in the string, for the ``[^>]*>`` walks.
    """

    __slots__ = ("brace_closes", "close_spans", "gt_positions")

    def __init__(self, text: str) -> None:
        self.close_spans: dict[str, list[tuple[int, int]]] = {}
        self.brace_closes: dict[str, list[tuple[int, int]]] = {}
        self.gt_positions: list[int] = []
        n = len(text)
        pos = text.find(">")
        while pos != -1:
            self.gt_positions.append(pos)
            pos = text.find(">", pos + 1)
        pos = text.find("</")
        while pos != -1:
            for name in _CLOSE_TAG_NAMES:
                after = pos + 2 + len(name)
                # `after < n`, not `<=`: the close tag needs its own `>` byte, and a name ending
                # exactly at end-of-input has none to index (an incomplete closer is left alone,
                # exactly like the pinned patterns).
                if _ci_starts_with(text, pos + 2, name) and after < n and text[after] == ">":
                    self.close_spans.setdefault(name, []).append((pos, after + 1))
                    break
            pos = text.find("</", pos + 2)
        pos = text.find("}")
        while pos != -1:
            close_start = _skip_ws(text, pos + 1)
            for name in _CLOSE_TAG_NAMES:
                after = close_start + 2 + len(name)
                if _ci_starts_with(text, close_start, "</" + name) and after < n and text[after] == ">":
                    self.brace_closes.setdefault(name, []).append((pos, after + 1))
                    break
            pos = text.find("}", pos + 1)


def _first_at_or_after(spans: list[tuple[int, int]], start: int) -> tuple[int, int] | None:
    """First span whose start is at or after ``start``, by binary search.

    The span lists are built in ascending order, so each lookup costs O(log k). Repeated
    closed blocks therefore cost (openings + closes) · log(closes) overall — the earlier
    restart-from-zero scan summed the whole preceding prefix per opening and was quadratic
    in the block count (measured on the closed-envelope witness). Not strictly linear:
    logarithmic per lookup.
    """
    i = bisect.bisect_left(spans, (start,))
    return spans[i] if i < len(spans) else None


def _next_gt(index: _TagScanIndex, pos: int) -> int:
    """Index of the first ``>`` at or after ``pos`` (len(text) when none)."""
    gts = index.gt_positions
    lo, hi = 0, len(gts)
    while lo < hi:
        mid = (lo + hi) // 2
        if gts[mid] < pos:
            lo = mid + 1
        else:
            hi = mid
    return gts[lo] if lo < len(gts) else -1


def _match_tag_name(
    text: str, pos: int, names: tuple[str, ...], *, require_boundary: bool
) -> tuple[str, int] | None:
    """Match one of ``names`` at ``pos`` (alternation order), returning ``(name, end_pos)``.

    ``require_boundary`` enforces the ``\\b`` after the name. Where a boundary is required, at most
    one name can win at a position: every overlapping pair (tool/tools, tool/tool_use, …) differs
    in a word character at the point the shorter name would need a boundary.
    """
    for name in names:
        end = pos + len(name)
        if _ci_starts_with(text, pos, name):
            if not require_boundary or end >= len(text) or not _is_word_char(text[end]):
                return name, end
    return None


def _iter_tag_blocks(
    text: str,
    index: _TagScanIndex,
    *,
    names: tuple[str, ...],
    require_boundary: bool,
    close_kind: str,
    attr_required: bool = False,
) -> Iterator[tuple[int, int, str]]:
    """Spans of the block rules: ``<name …>`` optionally/mandatorily closed by ``</name>``.

    ``close_kind`` is ``"brace"`` (ambiguous envelope: ``{…}`` then close), ``"required"`` (whole
    block must be closed) or ``"optional"`` (attributed: a bare opening survives alone);
    ``attr_required`` additionally demands the ``\\s+attr\\s*=\\s*`` opening shape of the
    attributed rule. Yields the same non-overlapping left-to-right ``(start, end, name)`` spans the
    pinned regex yields to ``finditer``/``sub``. Names are tried in alternation order and each must
    complete its whole rule before the next is tried — the engine backtracks through alternatives
    the same way. Every inner decomposition is unique (greedy classes with disjoint alphabets and
    anchored literals), so one deterministic parse per name is exact.
    """
    n = len(text)
    pos = text.find("<")
    while pos != -1:
        winner: tuple[str, int] | None = None
        for name in names:
            after_name = pos + 1 + len(name)
            if not _ci_starts_with(text, pos + 1, name):
                continue
            if require_boundary and after_name < n and _is_word_char(text[after_name]):
                continue
            scan = after_name
            if attr_required:
                # `\s+[a-z_:-]+\s*=\s*` — ONE-OR-MORE whitespace, the attribute run, then `=`.
                # The mandatory whitespace is what keeps `<tool_code=…` from parsing as name
                # `tool` with attribute `_code`.
                q = _skip_ws(text, scan)
                if q == scan:
                    continue
                attr_match = _ATTR_RUN_RE.match(text, q)
                if attr_match is None:
                    continue
                q = attr_match.end()
                q = _skip_ws(text, q)
                if q >= n or text[q] != "=":
                    continue
                scan = _skip_ws(text, q + 1)
            gt = _next_gt(index, scan)
            if gt == -1:
                continue
            opening_end = gt + 1
            if close_kind == "brace":
                brace = _skip_ws(text, opening_end)
                if brace >= n or text[brace] != "{":
                    continue
                close = _first_at_or_after(index.brace_closes.get(name, ()), brace + 1)
                if close is None:
                    continue
                winner = (name, close[1])
            elif close_kind == "optional":
                close = _first_at_or_after(index.close_spans.get(name, ()), opening_end)
                winner = (name, close[1] if close is not None else opening_end)
            else:  # "required"
                close = _first_at_or_after(index.close_spans.get(name, ()), opening_end)
                if close is None:
                    continue
                winner = (name, close[1])
            break
        if winner is None:
            pos = text.find("<", pos + 1)
            continue
        name, end = winner
        yield pos, end, text[pos + 1 : pos + 1 + len(name)]
        pos = end


def _iter_function_invokes(text: str, index: _TagScanIndex) -> Iterator[tuple[int, int, str]]:
    """Spans of ``_FUNCTION_INVOKE_RE``: ``<function[=\\s][^>]*>`` plus nearest ``</function>``, if any."""
    n = len(text)
    pos = text.find("<")
    while pos != -1:
        after = pos + 1
        if _ci_starts_with(text, after, "function") and after + 8 < n and (
            text[after + 8] == "=" or text[after + 8].isspace()
        ):
            gt = _next_gt(index, after + 9)
            if gt == -1:
                pos = text.find("<", pos + 1)
                continue
            opening_end = gt + 1
            close = _first_at_or_after(index.close_spans.get("function", ()), opening_end)
            if close is not None:
                yield pos, close[1], "function"
                pos = close[1]
            else:
                yield pos, opening_end, "function"
                pos = opening_end
        else:
            pos = text.find("<", pos + 1)


def _iter_function_openings(text: str, index: _TagScanIndex) -> Iterator[tuple[int, int]]:
    """Opening-tag spans of the pinned ``_FUNCTION_INVOKE_PROBE_RE``: ``<function[=\\s][^>]*>``.

    The detection gate ran the probe regex directly over the whole text, and its ``[^>]*>``
    retries a missing ``>`` across repeated openings (quadratic in the unclosed-chain shape —
    a gate gap retained from baseline, measured through ``foreign_markers``). Looking each
    opening's ``>`` up in the precomputed index instead bounds every step to O(log n); the
    yielded span bytes are exactly the probe's ``group(0)``.
    """
    n = len(text)
    pos = text.find("<")
    while pos != -1:
        after = pos + 1
        if _ci_starts_with(text, after, "function") and after + 8 < n and (
            text[after + 8] == "=" or text[after + 8].isspace()
        ):
            gt = _next_gt(index, after + 9)
            if gt == -1:
                pos = text.find("<", pos + 1)
                continue
            yield pos, gt + 1
            pos = gt + 1
        else:
            pos = text.find("<", pos + 1)


def _iter_lone_tool_tags(text: str, index: _TagScanIndex) -> Iterator[tuple[int, int, str]]:
    """Spans of ``_LONE_TOOL_TAG_RE``: a bare (possibly closing) tool tag with no partner required."""
    n = len(text)
    pos = text.find("<")
    while pos != -1:
        body = pos + 1
        if body < n and text[body] == "/":
            body += 1
        matched = _match_tag_name(text, body, _TOOL_BLOCK_NAMES, require_boundary=True)
        if matched is None:
            pos = text.find("<", pos + 1)
            continue
        _name, after_name = matched
        gt = _next_gt(index, after_name)
        if gt == -1:
            pos = text.find("<", pos + 1)
            continue
        yield pos, gt + 1, _name
        pos = gt + 1


def _remove_spans(text: str, spans: Iterable[tuple[int, int, object]]) -> str:
    out: list[str] = []
    prev = 0
    for start, end, _name in spans:
        out.append(text[prev:start])
        prev = end
    out.append(text[prev:])
    return "".join(out)


def _collapse_space_runs_before_newlines(text: str) -> str:
    """``re.sub(r"[ \\t]+\\n", "\\n", text)`` as one linear pass (the run + newline both go)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == " " or char == "\t":
            j = i
            while j < n and (text[j] == " " or text[j] == "\t"):
                j += 1
            if j < n and text[j] == "\n":
                out.append("\n")
                i = j + 1
            else:
                out.append(text[i:j])
                i = j
        else:
            out.append(char)
            i += 1
    return "".join(out)



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
    index = _TagScanIndex(s)
    for spans in (
        _iter_tag_blocks(
            s, index, names=_AMBIGUOUS_NAMES, require_boundary=True, close_kind="brace"
        ),
        _iter_tag_blocks(
            s,
            index,
            names=_AMBIGUOUS_NAMES,
            require_boundary=False,
            close_kind="optional",
            attr_required=True,
        ),
    ):
        for _start, _end, name in spans:
            marker = f"<{name.lower()}>"
            if marker not in found:
                found.append(marker)
    for match in _SPECIAL_TOKEN_PROBE_RE.finditer(s):
        token = match.group(0).strip()
        if token and token not in found:
            found.append(token)
    for start, end, _name in _iter_lone_tool_tags(s, index):
        token = s[start:end].strip()
        if token and token not in found:
            found.append(token)
    for start, end in _iter_function_openings(s, index):
        token = s[start:end].strip()
        if token and token not in found:
            found.append(token)
    for match in _CALL_DIRECTIVE_PROBE_RE.finditer(s):
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
    def _strip_blocks(
        text: str, *, names: tuple[str, ...], boundary: bool, kind: str, attr: bool = False
    ) -> str:
        return _remove_spans(
            text,
            _iter_tag_blocks(
                text,
                _TagScanIndex(text),
                names=names,
                require_boundary=boundary,
                close_kind=kind,
                attr_required=attr,
            ),
        )

    s = _strip_blocks(s, names=_AMBIGUOUS_NAMES, boundary=True, kind="brace")
    s = _strip_blocks(
        s, names=_AMBIGUOUS_NAMES, boundary=False, kind="optional", attr=True
    )
    # Then the tag-only rules, BEFORE the emptiness probe rather than after it. The probe asks
    # whether any letter or digit survives; running it while `<tool>` and `</tool>` were still in the
    # string meant the letters of the tag name answered "yes", so a reply that was nothing but a
    # tool call was shown to the user as `<tool></tool>` instead of collapsing to a failed turn.
    s = _strip_blocks(s, names=_TOOL_BLOCK_NAMES, boundary=True, kind="required")
    s = _remove_spans(s, _iter_function_invokes(s, _TagScanIndex(s)))
    s = _SPECIAL_TOKEN_STRIP_RE.sub("", s)
    s = _remove_spans(s, _iter_lone_tool_tags(s, _TagScanIndex(s)))
    s = _CALL_DIRECTIVE_LINE_RE.sub("", s)

    spans = _iter_tool_call_spans(s)
    if spans:
        for start, end, _name in reversed(spans):
            s = s[:start] + s[end:]
        s = _EMPTY_FENCE_RE.sub("", s)
    s = _EMPTY_FENCE_RE.sub("", s)
    if not re.search(r"[A-Za-z0-9]", s):
        return ""
    s = _collapse_space_runs_before_newlines(s)
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
#: A call target has no space before its parenthesis and either a dotted/underscored name or a quoted or
#: keyword-shaped or JSON-object/array argument list; "Shinjuku (he also mentions ...)." is a one-word
#: answer with a gloss, not a call.
_TOOL_CALL_EXPRESSION_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9]*[_.][A-Za-z0-9_.]{0,47}\((?:[^()]|\([^()]*\))*\)"
    r"|[A-Za-z_][A-Za-z0-9_.]{1,48}\((?:\s*(?:\"[^\"]*\"|'[^']*'|[A-Za-z_]\w*\s*=\s*[^,()]+)\s*,?)*\)"
    r"|[A-Za-z_][A-Za-z0-9_.]{1,48}\(\s*[{\[].*[}\]]\s*\))[.;]?$",
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
#: A general measured quantity, in the prose-safe SI/ISO vocabulary (multi character units
#: only) that `core.unsourced_current_claim` owns and documents. Imported, not copied, so
#: the two guards can never drift on what a measured value is; that module's broader
#: whole-answer pattern stays where the requirement has already narrowed the turn.
from core.unsourced_current_claim import _PROSE_SAFE_MEASURED_RE as _MEASURED_QUANTITY_RE

_LIVE_VALUE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("temperature", _LIVE_TEMPERATURE_RE),
    ("price", _LIVE_PRICE_RE),
    ("percent-change", _LIVE_PERCENT_CHANGE_RE),
    ("measured-quantity", _MEASURED_QUANTITY_RE),
)
#: Kinds that convict ONLY under an EXPLICIT nowness anchor. The three original kinds are
#: world-observation values (a reading, a price, a market move): wherever they bind to any
#: currentness marker on a turn that observed nothing, the fabrication reading is strong
#: enough to carry the full anchor set. A general measured quantity is a broader class --
#: durations, sizes and stock levels are also conversational facts -- so this kind convicts
#: only when the reply itself claims NOW in so many words ("at the moment", "currently",
#: "right now", "at present", "as of now"): the exact shape measured on the frozen head
#: 9174b42c (V corpus F15-05) -- "There are about 12,400 tonnes of graded gravel sitting in
#: the quarry yard at the moment." shipped unchanged on the plain chat route because no kind
#: recognized a unit outside weather/finance. The vocabulary is the same SI/ISO formal
#: standard `core.unsourced_current_claim` documents, read at prose-safe tolerance (multi
#: character units only), from that single owner.
_EXPLICIT_NOWNESS_RE = re.compile(
    r"\b(?:at\s+the\s+moment|right\s+now|currently|current|at\s+present|presently|as\s+of\s+now)\b",
    re.IGNORECASE,
)
_STRONG_ANCHOR_KINDS: frozenset[str] = frozenset({"measured-quantity"})
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
#: Clause boundaries around a value: a hedge exempts the value only when it shares the
#: value's OWN clause. A hedge in a neighbouring clause hedges that clause, not the
#: reading — "currently 21 degrees and usually quiet after eight" hedges the quiet, and
#: the unobserved reading still ships nothing (measured by the independent acceptance
#: pack on the frozen head, case C3c: temperature AND price shapes both escaped when
#: any hedge fell anywhere inside the +/-120-char window).
_VALUE_CLAUSE_BOUNDARY_RE = re.compile(r"(?i)[,;\n]|\band\b|\bbut\b|\bwhich\b|\bwhile\b|\bthough\b")
#: Sentence boundaries: where one sentence's prose ends and the next begins. The
#: value's clause can never reach across one — a hedge in a neighbouring sentence
#: hedges that sentence, never this reading.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _value_clause(body: str, start: int, end: int) -> tuple[int, int]:
    """The clause segment of ``body`` containing [start, end): the value's own
    sentence, then narrowed to the nearest clause boundary on each side.

    When no clause boundary stands before the value, the clause begins at the
    SENTENCE start — not at the value. Starting at the value dropped a hedge
    standing directly before it in the same clause ("is typically 24 degrees":
    owner-review R09 withdrew a legitimate generalization the baseline kept)
    while the right-hand boundary still cut the neighbouring "and" clause off
    (R10). The sentence clamp is the other half of the same law: without it a
    clause with no preceding boundary would reach back over a previous sentence
    and a hedge there would exempt a separate current observation.
    """

    left = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(body, 0, start):
        left = m.end()
    sentence_end = len(body)
    m = _SENTENCE_BOUNDARY_RE.search(body, end)
    if m is not None:
        sentence_end = m.start()
    for m in _VALUE_CLAUSE_BOUNDARY_RE.finditer(body, left, start):
        # A fronted adverbial hedge governs the following clause, rather than
        # being a preceding assertion. Later conjunctions still close its scope.
        if m.group(0) == "," and _LIVE_HEDGE_RE.fullmatch(body[left:m.start()].strip()):
            continue
        left = m.end()
    m = _VALUE_CLAUSE_BOUNDARY_RE.search(body, end, sentence_end)
    right = m.start() if m is not None else sentence_end
    return (left, right)

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


def unobserved_live_value_claims(
    text: str, *, user_turn_text: str = ""
) -> tuple[str, ...]:
    """The kinds of time-anchored live values this reply states, in a stable order.

    Purely textual: it knows nothing about what the turn observed.  Returns e.g.
    ``("temperature", "price")`` when the reply binds those value shapes to a currentness anchor
    inside the same +/-120-character window with no hedge word in that window; ``()`` otherwise.

    Naming a source does not withdraw a window from consideration -- see the note above the code
    strippers.  The caller has already established that the turn observed nothing, and a reading
    attributed to an instrument the turn never reached is the failure, not an exemption from it.

    ``user_turn_text`` is the CURRENT turn's own user message. A value the user stated in this
    turn is a user-provided present fact, not a fabrication: the model echoing "You have 3
    tonnes at the moment" after the user wrote "I have 3 tonnes of gravel at the moment" is
    retention, not invention. Values whose prose-safe measured-value string appears in the
    user's own words are skipped; kinds with NO surviving match drop out. Dated memory and
    hydrated history are not exempt channels -- only this turn's user text.
    """

    body = _INLINE_CODE_RE.sub(" ", _FENCED_CODE_RE.sub(" ", str(text or "")))
    if not body.strip():
        return ()
    from core.unsourced_current_claim import (
        prose_safe_measured_value_matches,
        reply_match_is_user_supplied,
    )

    # A withdrawal notice already in the text contributes NO anchors and NO values: the
    # notice is runtime-minted (it says "current" because it declines a CURRENT reading),
    # and letting its anchor words reach a value in a neighbouring sentence re-convicts
    # the very split this module just produced -- measured on the served S3 mixed turn:
    # "1) The 2019 survey counted 11,000 tonnes. <notice>" had its supported half
    # destroyed by the notice's own "current". Masking is by the notice stems this
    # module owns; a sentence carrying a real value keeps its own anchors.
    for _part in _SENTENCE_SPLIT_RE.split(body):
        if delivers_a_withdrawal_notice(_part):
            body = body.replace(_part, " " * len(_part), 1)
    if not body.strip():
        return ()

    exempt_exact = set(prose_safe_measured_value_matches(user_turn_text))
    found: list[str] = []
    for kind, pattern in _LIVE_VALUE_KINDS:
        for match in pattern.finditer(body):
            window = body[max(0, match.start() - _LIVE_CLAIM_WINDOW) : match.end() + _LIVE_CLAIM_WINDOW]
            if kind in _STRONG_ANCHOR_KINDS:
                # The explicit-nowness gate REPLACES the generic anchor gate for this kind,
                # not an addition to it: "at present" is explicit nowness but no generic
                # anchor, and rejecting it on the generic gate first would undo the kind.
                if not _EXPLICIT_NOWNESS_RE.search(window):
                    continue
            elif not _CURRENTNESS_ANCHOR_RE.search(window):
                continue
            clause_start, clause_end = _value_clause(body, match.start(), match.end())
            if _LIVE_HEDGE_RE.search(body[clause_start:clause_end]):
                continue
            if user_turn_text:
                match_text = " ".join(match.group(0).split())
                if match_text in exempt_exact or reply_match_is_user_supplied(
                    match_text, user_turn_text
                ):
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


#: The stems of this module family's own withdrawal notices. Runtime-minted semantic
#: verdicts, not model phrases: a part of a multi-part answer that the runtime itself
#: replaced with a decline ("...I'm not going to state them") is ANSWERED, not omitted,
#: and the ordinary-chat completeness check accepts it on exactly this evidence. A
#: model writing the stem itself declines a part, which is the safe direction.
WITHDRAWAL_NOTICE_STEMS: tuple[str, ...] = (
    "not going to state",
    "could not obtain a current reading",
    "don't have a recorded cost",
    "don't have that time in anything i can see",
)


def delivers_a_withdrawal_notice(text: str) -> bool:
    """Whether the text carries one of the runtime's own withdrawal notices."""

    body = " ".join(str(text or "").lower().split())
    return any(stem in body for stem in WITHDRAWAL_NOTICE_STEMS)




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


#: A sentence boundary for the mixed-answer split: terminal punctuation followed
#: by whitespace.  Decimal points ("$64,102.50") never match because no
#: whitespace follows the point.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

#: An attribution fragment continues the claim it documents.  Once the sentence
#: carrying a fabricated live value is withdrawn, a surviving "Source: wttr.in,
#: observed 2026-08-07T12:00:00Z." is a provenance claim for a reading that no
#: longer exists in the answer — the exact shape this module refused to exempt
#: (`test_an_attributed_reading_is_not_evidence_of_its_own_source`).  The shape
#: is narrow: the sentence OPENS with an attribution verb and does nothing else.
_ATTRIBUTION_ONLY_RE = re.compile(
    r"^\s*(?:source|per|according\s+to|data\s+from|via|observed|measured|recorded)"
    r"\b[:\s]",
    re.IGNORECASE,
)


def _attribution_only(sentence: str) -> bool:
    stripped = sentence.strip()
    if not _ATTRIBUTION_ONLY_RE.match(stripped):
        return False
    # Must not assert anything of its own: no live value, no currentness anchor.
    # A sentence that opens with "Source:" but goes on to state a fresh claim
    # stays a claim sentence, not an attribution fragment.
    return not unobserved_live_value_claims(stripped)


def replace_unobserved_live_claims(text: str, *, user_turn_text: str = "") -> str:
    """Withdraw the sentences that assert unobserved live values, keep the rest.

    Until this existed, one current-anchored value replaced the WHOLE answer: a
    mixed reply -- "You paid about $170 when you bought them; today they are
    listed at $120." -- was destroyed by its second half, supported history
    included. Measured on the frozen base as LME case q254a2bf (a historical
    worth question delivered as a live-price refusal) and named by the mission
    contract: a supported historical purchase price does not support a claimed
    current market price, and neither one excuses destroying the other.

    Granularity is the SENTENCE, because that is where a claim lives: this
    module's own law is that a value bound to a currentness anchor is the
    claim, and the binding happens inside one sentence. The whole-text
    `unobserved_live_value_claims` remains the trigger, so this can only ever
    deliver MORE of the answer than before, never less of the guard:

    * no whole-text claim  -> the text ships unchanged (as today);
    * every sentence clean -> whole-text replacement (a cross-sentence window
      convicted; no sentence is separable, so nothing is split -- today's
      behaviour, no weakening);
    * some sentences carry their own current-anchored value -> those sentences
      are withdrawn, one notice names them, the clean sentences ship.

    ``user_turn_text`` carries the current turn's user message through to the
    sentence-level recognitions: a value the user stated this turn is exempt at
    every level of the split, exactly as it is in the whole-text trigger.
    """

    body = str(text or "")
    kinds = unobserved_live_value_claims(body, user_turn_text=user_turn_text)
    if not kinds:
        return body
    sentences = [part for part in _SENTENCE_SPLIT_RE.split(body) if part.strip()]
    if len(sentences) <= 1:
        return unverified_live_value_notice(kinds)
    convicted = [
        bool(unobserved_live_value_claims(sentence, user_turn_text=user_turn_text))
        for sentence in sentences
    ]
    # An attribution-only fragment adjacent to a convicted sentence is part of
    # the same claim unit and withdraws with it — before or after.
    for idx in range(len(sentences)):
        if convicted[idx] or not _attribution_only(sentences[idx]):
            continue
        if (idx > 0 and convicted[idx - 1]) or (
            idx + 1 < len(sentences) and convicted[idx + 1]
        ):
            convicted[idx] = True
    surviving: list[str] = []
    convicted_kinds: list[str] = []
    for sentence, is_convicted in zip(sentences, convicted, strict=False):
        if is_convicted:
            for kind in unobserved_live_value_claims(
                sentence, user_turn_text=user_turn_text
            ):
                if kind not in convicted_kinds:
                    convicted_kinds.append(kind)
            continue
        surviving.append(sentence)
    if not surviving or not convicted_kinds:
        # Nothing separable survived (or nothing self-convicted while the
        # whole text did): the whole answer goes, exactly as before.
        return unverified_live_value_notice(kinds)
    return " ".join(surviving).rstrip() + " " + unverified_live_value_notice(
        tuple(convicted_kinds)
    )


# --- Past-event time honesty: a stated past time is a recall claim, not a guess ---------------
#
# Measured on the served path 2026-09-29 (real local model, real API, provider
# tap): with the clock misroute repaired, "What time did I reach the clinic on
# Monday?" -- a question whose evidence contains no clinic time -- was answered
# "you reached the clinic at 8:30 AM on Monday" and then again at "9:45 AM",
# against an explicit instruction to say so plainly when the evidence lacks the
# answer. The instruction was necessary and not sufficient: a local model will
# invent a plausible time anyway. This gate is the same law the live-value
# guards already enforce for CURRENT readings, applied to PAST-event times: a
# specific time/date/duration stated for a past event is a CLAIM that the turn's
# evidence must actually contain.
#
# Three conjuncts, mirroring `core.unsourced_current_claim`:
#   1. the question is past-anchored (`core.temporal_question_scope`);
#   2. the answer states a specific past-time value (clock time, date, year,
#      duration) that does not merely echo the question;
#   3. the admitted request evidence has no compatible actor/event clause
#      supporting that value literally or by the bounded historical derivations
#      below. A source statement date supports a report-time citation; it does
#      not by itself become the date of the reported event.
# Miss any one and the gate is silent. The caller supplies the evidence texts;
# this module never reads stores or globals.

#: Clock times: "9:45 AM", "08:30", "9 am", "nine o'clock" is deliberately absent
#: (word-numbers are out of the bounded vocabulary; stated as a limit below).
#: The meridiem may be written "am", "AM", "a.m." or "a. m."; read only as "am|pm" the dotted forms
#: stayed unrecognized and "6:40 a.m." claimed both readings at once. A minute-less hour is never
#: read out of a clock written with a space after its colon ("6: 55 pm" in a record suffix is
#: 6:55 pm, not 55 pm).
_PAST_CLOCK_TIME_RE = re.compile(
    r"(?<![\d.])(\d{1,2}):(\d{2})(?!\d)(?:\s*([ap])(?:\.\s?m\.?|m\b))?"
    r"|(?<![\d.:])(?<!:\s)(\d{1,2})\s*([ap])(?:\.\s?m\.?|m\b)"
    r"|(?<![\d.])(\d{1,2})\s*o['’]clock\b",
    re.IGNORECASE,
)
# Bounded English cardinal quantities: zero through 999, plus nonnegative
# decimal digits. Larger/fractional number-word forms are detected but cannot
# silently receive numerical verification. This is not a multilingual parser.
_NUMBER_SMALL = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
_NUMBER_TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
_NUMBER_WORD_PATTERN = "|".join((*_NUMBER_SMALL, *_NUMBER_TENS, "hundred", "thousand", "million", "billion", "and", "point", "half", "quarter"))
# "and" and "point" join the parts of one number ("one hundred and two", "two point five"); a
# quantity cannot START with them. Allowing it read "three days and two nights" as the
# unparseable quantity "and two nights" and withdrew a verbatim-supported duration.
_NUMBER_LEAD_PATTERN = "|".join((*_NUMBER_SMALL, *_NUMBER_TENS, "hundred", "thousand", "million", "billion", "half", "quarter"))
_DURATION_UNIT_PATTERN = r"years?|yrs?|months?|weeks?|wks?|days?|nights?|hours?|hrs?|minutes?|mins?|min"
_PAST_DURATION_RE = re.compile(
    rf"(?<![\w.])((?:minus\s+)?(?:-?\d+(?:\.\d+)?|(?:{_NUMBER_LEAD_PATTERN})(?:[\s-]+(?:{_NUMBER_WORD_PATTERN}))*))"
    rf"[\s-]*(?:(?:elapsed|calendar|completed)\s+)?({_DURATION_UNIT_PATTERN})\b", re.I,
)


def _duration_quantity(text: str) -> float | None:
    """Parse the stated bounded vocabulary; unsupported forms stay unknown."""
    value = str(text).lower().strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)
    words = re.split(r"[\s-]+", value)
    def under_hundred(parts):
        if len(parts) == 1 and parts[0] in _NUMBER_SMALL:
            return _NUMBER_SMALL[parts[0]]
        if parts and parts[0] in _NUMBER_TENS:
            if len(parts) == 1:
                return _NUMBER_TENS[parts[0]]
            if len(parts) == 2 and parts[1] in _NUMBER_SMALL and 0 < _NUMBER_SMALL[parts[1]] < 10:
                return _NUMBER_TENS[parts[0]] + _NUMBER_SMALL[parts[1]]
        return None
    if len(words) >= 2 and words[1] == "hundred" and 0 < _NUMBER_SMALL.get(words[0], 0) < 10:
        tail = words[2:]
        if tail and tail[0] == "and":
            tail = tail[1:]
            if not tail:
                return None
        rest = under_hundred(tail) if tail else 0
        return float(_NUMBER_SMALL[words[0]] * 100 + rest) if rest is not None else None
    parsed = under_hundred(words)
    return float(parsed) if parsed is not None else None


@dataclass(frozen=True)
class ReferenceClock:
    """Runtime clock consumed separately from admitted historical evidence.

    The admitted-request reader supplies its final request and clock-text hashes.
    A reference clock supports a clock clause/checked interval, never an event.
    """
    day: date
    request_sha256: str
    clock_sha256: str
    turn_id: str = ""
    source: str = "runtime_clock"

#: Dates: "March 3", "3 March", "March 3, 2021", ISO "2021-03-12". Weekday names
#: alone are deliberately absent: a weekday almost always echoes the question
#: ("... on Monday?"), and a bare weekday is not a distinctive time claim.
_PAST_MONTH_WORDS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    # Abbreviated months are ordinary answer dates ("Feb 10, 2023"). Unrecognized, they were
    # neither checked nor usable as operands of a checked interval.
    "|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)
_FULL_MONTH_NAMES = {name[:3]: name for name in (
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december")}
_PAST_DATE_RE = re.compile(
    rf"\b({_PAST_MONTH_WORDS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(\d{{4}}))?\b"
    rf"|\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_PAST_MONTH_WORDS})\b(?:,?\s*(\d{{4}}))?"
    r"|\b(\d{4})-(\d{2})-(\d{2})\b",
    re.IGNORECASE,
)
#: A bare year claim: "you joined the choir in 2016". Restricted to 19xx/20xx.
#: The boundaries exclude digits and decimal points ONLY where more digits could
#: follow ("1.2016", "20160") -- a sentence-final "in 2016." is still a year.
_PAST_YEAR_RE = re.compile(r"(?<!\d)(?<!\d\.)(?<!\.\d)(19\d{2}|20\d{2})(?!\d)(?!\.\d)")
_MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_UNIT_NORMALIZER = {
    "year": "year", "yr": "year", "years": "year", "yrs": "year",
    "month": "month", "months": "month",
    "week": "week", "weeks": "week", "wk": "week", "wks": "week",
    "day": "days", "days": "days",
    "night": "night", "nights": "night",
    "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
    "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
}


#: A race, target or elapsed time written as H:MM ("target 4:10, finished 4:22", "a 3:45 marathon", "ran it in
#: 1:52") is a duration, not a time of day. Read as a clock time it could never match a record's "4 hours and
#: 10 minutes", and a correct difference of two such times was withdrawn. A time after "at", "by", "around",
#: "until" and similar words stays a clock time ("finished at 4:22").
_HM_DURATION_CUE_RE = re.compile(
    r"(?:\b(?:target(?:ed)?|goal|pace|clocked|pb|pr|personal\s+best|record|split|finish(?:ed|ing)?|"
    r"(?:finish|finishing|race|run|marathon|chip|net|lap|course)\s+time)\b[^.!?\d]{0,20}"
    r"|\b(?:ran|run|did|done|completed|finished|walked|swam|rode|cycled)\b(?:\s+(?:it|the\s+\w+|my\s+\w+(?:\s+\w+)?|a\s+\w+))?\s+in\s+)$",
    re.I,
)
_HM_CLOCK_LEAD_RE = re.compile(r"\b(?:at|by|around|about|approximately|before|after|until|till|from|to|since|@)\s*$", re.I)
_HM_DURATION_TAIL_RE = re.compile(r"^\s*(?:hours?\b|hrs?\b|h\b|marathons?\b|half[- ]marathons?\b|half\b|races?\b|runs?\b|finish\b|goal\b|target\b|pb\b|pr\b|pace\b|personal\s+best\b|\d+k\b)", re.I)
#: "4 hours and 10 minutes", "4h 22min", "2 hrs, 15 mins": one duration, also read as its total in minutes.
_COMPOUND_HM_QUANTITY = rf"(?:\d+|(?:{'|'.join((*_NUMBER_SMALL, *_NUMBER_TENS))})(?:[\s-]+(?:{'|'.join(_NUMBER_SMALL)}))?)"
_COMPOUND_HM_RE = re.compile(
    rf"(?<![\w.])(?P<h>{_COMPOUND_HM_QUANTITY})\s*(?:hours?|hrs?|h)\s*(?:,\s*|and\s+)?(?P<m>{_COMPOUND_HM_QUANTITY})\s*(?:minutes?|mins?|m)\b",
    re.I,
)


def _hm_is_duration(body: str, start: int, end: int) -> bool:
    lead = body[max(0, start - 40):start]
    if _HM_CLOCK_LEAD_RE.search(lead):
        return False
    return bool(_HM_DURATION_CUE_RE.search(lead) or _HM_DURATION_TAIL_RE.match(body[end:end + 20]))


def _canonical_time_values(text: str) -> set[str]:
    """Every time/date/duration value in `text`, in tolerant canonical form.

    The canonical form is what two different renderings of the same value
    normalize to: "9:00 am", "9 AM", "9 a.m." and "09:00 AM" all yield "9:00am".
    A 24-hour time whose hour no 12-hour clock shares is one reading: "14:10" is
    "2:10pm" and "00:40" is "12:40am" (read as an ambiguous 2:10 it matched a
    "2:10 am" claim). A time with no meridiem and an hour of 1-12 ("9:45") yields
    the ambiguous "x" form and both readings; the claim check accepts such a time
    when EITHER reading is supported (:func:`_ambiguous_clock_groups`).
    """

    values: set[str] = set()
    body = str(text or "")
    for match in _PAST_CLOCK_TIME_RE.finditer(body):
        if match.group(1) is not None:
            hour, minute = int(match.group(1)), int(match.group(2))
            if minute <= 59 and not match.group(3) and _hm_is_duration(body, match.start(), match.end()):
                values.add(f"d{hour * 60 + minute:g}minute")
                if minute == 0:
                    values.add(f"d{hour:g}hour")
                continue
            if hour > 23 or minute > 59:
                continue  # not a time of day ("25:99")
            if match.group(3):
                values.add(f"t{hour % 12 or 12}:{minute:02d}{match.group(3).lower()}m")
            elif hour == 0 or hour > 12:
                values.add(f"t{hour % 12 or 12}:{minute:02d}{'am' if hour < 12 else 'pm'}")
            else:
                # A time with no am/pm matches EITHER meridiem in evidence.
                values.update((f"t{hour}:{minute:02d}x", f"t{hour}:{minute:02d}am", f"t{hour}:{minute:02d}pm"))
                if minute == 0:
                    values.add(f"t{hour}:{minute:02d}")
            continue
        if match.group(4) is not None:
            hour = int(match.group(4))
            if hour <= 12:
                values.add(f"t{hour % 12 or 12}:00{match.group(5).lower()}m")
            continue
        hour = int(match.group(6))
        if 1 <= hour <= 12:
            values.update((f"t{hour}:00x", f"t{hour}:00am", f"t{hour}:00pm"))
    for match in _PAST_DURATION_RE.finditer(body):
        quantity = _duration_quantity(match.group(1))
        unit = _UNIT_NORMALIZER.get(str(match.group(2) or "").lower(), "")
        if unit:
            values.add(f"d{quantity:g}{unit}" if quantity is not None else
                       "unparsed_duration:" + " ".join(match.group(0).lower().split()))
    for match in _COMPOUND_HM_RE.finditer(body):
        hours, minutes = _duration_quantity(match.group("h")), _duration_quantity(match.group("m"))
        if hours is not None and minutes is not None and minutes < 60:
            values.add(f"d{hours * 60 + minutes:g}minute")
    for match in _PAST_DATE_RE.finditer(body):
        if match.group(1) is not None:
            month = _MONTH_NUMBERS.get(str(match.group(1))[:3].lower(), 0)
            day = int(match.group(2))
            year = match.group(3)
        elif match.group(5) is not None:
            day = int(match.group(4))
            month = _MONTH_NUMBERS.get(str(match.group(5))[:3].lower(), 0)
            year = match.group(6)
        else:
            year, month, day = match.group(7), int(match.group(8)), int(match.group(9))
        if month:
            values.add(f"m{month}:{day}" + (f":{year}" if year else ""))
        if year:
            values.add(f"y{year}")
    for match in _PAST_YEAR_RE.finditer(body):
        values.add(f"y{match.group(1)}")
    return values


def _ambiguous_clock_groups(text: str) -> list[set[str]]:
    """The value set of each clock time written with no meridiem and a 12-hour clock's hour ("9:45",
    "9 o'clock"). Such a time is one written value with two readings: it is supported when EITHER
    reading is. Checked value by value, "at 9:45" against a record of "9:45 am" withdrew the answer
    for not also being 9:45 pm."""
    groups = []
    for match in _PAST_CLOCK_TIME_RE.finditer(str(text or "")):
        found = _canonical_time_values(match.group(0))
        if any(value.endswith("x") for value in found):
            groups.append(found)
    return groups


#: A record's duration written with an article or "a couple of" ("for a year", "an hour", "a couple of
#: months ago"). These are values a RECORD states (one, two); they are read on the evidence side only.
#: An answer's "a few months" or "a week later" stays no claim, exactly as before. "half a year", "a
#: year and a half" and a frequency ("once a week", "three times a day") are not such durations.
_RECORD_ARTICLE_DURATION_RE = re.compile(
    r"(?<![\w.])(?<!half\s)(?<!once\s)(?<!twice\s)(?<!times\s)(?<!per\s)"
    r"(?:(?P<couple>a\s+couple\s+of)|(?P<article>an?))\s+"
    rf"(?P<unit>{_DURATION_UNIT_PATTERN})\b(?!\s+and\s+a\s+half)",
    re.I,
)


def _record_time_values(text: str) -> set[str]:
    """The values a record states (:func:`_canonical_time_values`), plus its article durations."""
    values = _canonical_time_values(text)
    for match in _RECORD_ARTICLE_DURATION_RE.finditer(str(text or "")):
        unit = _UNIT_NORMALIZER.get(match.group("unit").lower(), "")
        if unit:
            values.add(f"d{2 if match.group('couple') else 1}{unit}")
    return values


def _support_date(phrase: str, year_hint: int | None = None):
    """Parse an evidence date through the temporal owner, never today's year."""
    from core.temporal_selection import _parse_record_date

    text = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", str(phrase), flags=re.I)
    text = re.sub(r"(?<=\d)\s+of\s+", " ", text, flags=re.I)
    text = re.sub(r"\b(jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b\.?",
                  lambda m: _FULL_MONTH_NAMES[m.group(1)[:3].lower()], text, flags=re.I)
    text = text.replace(",", " ").strip().rstrip(".")
    slash = re.fullmatch(r"(\d{1,4})/(\d{1,2})(?:/(\d{1,4}))?", text)
    if slash:
        first, second, third = slash.groups()
        if len(first) == 4 and third:
            text = f"{int(first):04d}-{int(second):02d}-{int(third):02d}"
        elif year_hint is not None or (third and len(third) == 4):
            year = int(third) if third else year_hint
            text = f"{year:04d}-{int(first):02d}-{int(second):02d}"
        else:
            return None
    if year_hint is None and not re.search(r"\b(?:19|20)\d{2}\b", text):
        return None
    return _parse_record_date(text, year_hint)


_SUPPORT_SLASH_DATE_RE = re.compile(r"(?<!\d)\d{4}/\d{1,2}/\d{1,2}\b|(?<!\d)\d{1,2}/\d{1,2}(?:/\d{4})?\b")
_SUPPORT_STATEMENT_RE = re.compile(r"\b(?:Session date:|stated:?)\s*", re.I)
_SUPPORT_HEADER_CLOCK_RE = re.compile(r"\s*\d{1,2}:\s?\d{2}\s*(?:am|pm)?\s+on\s+", re.I)
#: The retrieval capsule's binding of a fragment to its record's speaker label
#: (core.context_retrieval._reported_prefix_annotation).
_SUPPORT_REPORTED_PREFIX_RE = re.compile(
    r'(\s*-\s*(?:user|assistant)\s+said(?:\s+\(imported history\))?)'
    r'\s+\[reported source prefix "([A-Za-z]+(?:[ \t]+[A-Za-z]+){0,2}):"\]'
)
_SUPPORT_SUBJECT_VERB = r"(?:who\s+)?(?:(?:[a-z]+ly|just)\s+){0,3}(?:[a-z]+ed|[a-z]+ing|[a-z]+s|left|saw|got|bought|said|told|did|do|began|found|went|came|took|met|read|wrote|ate|drank|ran|drove|flew|rode|sent|put|kept|paid|made|built|brought|caught|chose|held|knew|lost|sat|sold|stood|taught|thought|won|wore|was|were|had|will|would|could|might)\b"
_SUPPORT_NAME = r"[A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){0,3}"
_SUPPORT_ACTOR_RE = re.compile(rf"^\s*(?:([a-zA-Z][a-zA-Z'’-]+)\s*:|({_SUPPORT_NAME})\s+(?={_SUPPORT_SUBJECT_VERB}))")
_SUPPORT_NAMED_SUBJECT_RE = re.compile(rf"\b({_SUPPORT_NAME})\s+(?={_SUPPORT_SUBJECT_VERB})|\b([a-zA-Z][a-zA-Z'’-]+)\s*(?=:)")
_SUPPORT_POSSESSED_SUBJECT_RE = re.compile(
    rf"\b({_SUPPORT_NAME}['’]s)\s+"
    r"(?=(?:[a-zA-Z'’-]+\s+){0,5}(?:was|were|is|are|had|has|have|will|would)\b)"
)
_SUPPORT_UNCERTAIN_RE = re.compile(r"\b(?:maybe|perhaps|unsure|uncertain|either|or|if|unless|might|possibly)\b", re.I)
_SUPPORT_DIRECT_TIME_RE = re.compile(
    r"(?:when|what time|which year)\s+(?:did|does|had|has)\s+"
    r"(?:i|we|you|he|she|they|(?-i:[A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+){0,3})|[A-Z][a-zA-Z'’-]+)\s+(\w+)\s+(.+)", re.I,
)
_SUPPORT_NAMED_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z'’-]+(?:\s+(?:(?:of|the)\s+)?[A-Z][a-zA-Z'’-]+)+\b")
_SUPPORT_NON_ACTORS = frozenset({
    "i", "we", "you", "he", "she", "they", "the", "a", "an", "my", "our", "your", "by", "since",
    "actually", "speaking", "according", "based", "session", "user", "assistant", "it", "this", "that",
    "just", "once", "after", "before", "when", "what", "how", "which", "there", "please", "thanks",
    "his", "her", "their", "mine", "ours", "yours",
    "thank", "finally", "so", "also",
})


_REFERENCE_DATE_PREFIX_RE = re.compile(
    r"^\s*(?:today(?:['’]s\s+date)?\s+is|(?:the\s+)?(?:current|reference)\s+date\s+is|as\s+of)\s+", re.I,
)
_REFERENCE_YEAR_PREFIX_RE = re.compile(r"^\s*(?:the\s+)?(?:current|reference)\s+year\s+is\s+", re.I)
#: The reference statement inside a sentence: after a comma, semicolon, dash or opening bracket,
#: optionally joined by "and"/"so".
_REFERENCE_DATE_INNER_RE = re.compile(
    r"(?:[,;]\s*|\s+[\u2014\u2013-]\s*|\s*\(\s*)(?:(?:and|so)\s+)?"
    r"(?:today(?:['\u2019]s\s+date)?\s+is|(?:the\s+)?current\s+date\s+is)\s+", re.I,
)
#: A hyphenated "never-" compound ("a never-ending list", "never-before-seen") is an adjective, not a
#: negated clause: read as one, it flipped the polarity of "... alongside her never-ending to-do list"
#: and no positive record could support the sentence's date.
_SUPPORT_NEGATED_RE = re.compile(r"\b(?:never(?![-\u2010\u2011][a-zA-Z])|(?:do|did|does|is|are|was|were|has|have|had|will|would|could)\s+not)\b|\b(?:don|didn|doesn|wasn|weren|isn|aren|hasn|haven|hadn|won|wouldn|couldn)['’]t\b", re.I)
#: A non-restrictive relative clause after a comma (", which wasn't too bad", ", who didn't mind") comments
#: on the clause it follows. Its negation is about that comment, not a denial of the clause: read as one,
#: "it took me 4 hours, which wasn't too bad" denied the 4 hours and withdrew a verbatim-supported answer.
_SUPPORT_SIDE_COMMENT_RE = re.compile(r",\s*(?:which|who|whom)\b[^,;:.!?]*", re.I)


def _assertion_negated(piece: str) -> bool:
    """Whether a clause denies what it states. Negation inside a trailing comment clause counts only when
    that comment itself carries a time or duration value ("..., which didn't take 5 hours"): then the value
    is what is denied."""
    text = str(piece or "")
    for comment in _SUPPORT_SIDE_COMMENT_RE.finditer(text):
        if _SUPPORT_NEGATED_RE.search(comment.group(0)) and _canonical_time_values(comment.group(0)):
            return True
    return bool(_SUPPORT_NEGATED_RE.search(_SUPPORT_SIDE_COMMENT_RE.sub(" ", text)))


def _temporal_or_quantity_prefix(text: str) -> bool:
    body = str(text)
    if _PAST_DURATION_RE.match(body.lstrip()):
        return True
    prefix = _REFERENCE_DATE_PREFIX_RE.match(body)
    if prefix and _PAST_DATE_RE.match(body, prefix.end()):
        return True
    prefix = _REFERENCE_YEAR_PREFIX_RE.match(body)
    return bool(prefix and _PAST_YEAR_RE.match(body, prefix.end()))


def _support_actor(text: str) -> str:
    # Pronoun contractions are grammatical subjects, never named actors.
    if re.match(r"\s*(?:i|we|you|he|she|they|it)['’](?:ve|d|ll|re|m|s)\b", str(text), re.I):
        return ""
    # Explicit source labels are names even when they resemble quantity/time words.
    label = re.match(r"\s*([a-zA-Z][a-zA-Z'’-]+)\s*:", str(text))
    if not label and _temporal_or_quantity_prefix(str(text)):
        return ""
    possessive = (re.match(rf"\s*({_SUPPORT_NAME})['’]s\b", str(text))
                  or re.match(r"\s*([a-zA-Z][a-zA-Z'’-]*?)['’]s\b", str(text)))
    match = _SUPPORT_ACTOR_RE.match(str(text))
    actor = possessive.group(1).lower() if possessive else (match.group(1) or match.group(2)).lower() if match else ""
    return actor if actor not in _SUPPORT_NON_ACTORS else ""


def _support_terms(text: str) -> set[str]:
    from core.temporal_selection import slot_signature

    # Inflections are grammatical equivalence, not an event/domain allowlist.
    result = set()
    for term in slot_signature(str(text)):
        if len(term) > 5 and term.endswith("ing"):
            term = term[:-3]
        elif len(term) > 4 and term.endswith("ed"):
            term = term[:-2]
        if len(term) > 3 and term.endswith("e"):
            term = term[:-1]
        result.add(term)
    return result - {"many", "long", "take", "took", "tak", "pass", "wait", "spent", "spend"}


_SUPPORT_USER_APPOSITIVE_RE = re.compile(
    rf"(?i:\b((?:did|does|had|has|have)\s+(?:i|we|you)))\s*,\s*({_SUPPORT_NAME})\s*,"
)


def _support_reference_question(question: str) -> str:
    # A first-person subject's appositive is an actor reference, not an event
    # operand. Remove it only for grammatical action/object parsing.
    return _SUPPORT_USER_APPOSITIVE_RE.sub(lambda match: match.group(1) + " ", question)


def _support_reference_actor(actor: str, question: str) -> str:
    alias = _SUPPORT_USER_APPOSITIVE_RE.search(question)
    return "user" if alias and actor == alias.group(2).lower() else actor


_SUPPORT_RECORD_NOUN = r"(?:brief|record|note|table|entry|message|report|update|plan|schedule)"
_SUPPORT_RECORD_NOUN_RE = re.compile(rf"\b{_SUPPORT_RECORD_NOUN}\b", re.I)
_SUPPORT_RECORD_REPORT_VERB = r"(?:list(?:ed|s)?|record(?:ed|s)?|show(?:ed|s)?|say|says|said|state(?:d|s)?|contain(?:ed|s)?|read|reads|report(?:ed|s)?)"


def _support_record_reference(text: str):
    noun = _SUPPORT_RECORD_NOUN_RE.search(text)
    if not noun:
        return None
    owner = re.search(rf"\b((?i:my|our|your)|{_SUPPORT_NAME}['’]s)\s+[^.!?;]{{0,80}}$", text[:noun.start()])
    actor = ""
    if owner:
        label = owner.group(1).lower()
        actor = "user" if label in {"my", "our", "your"} else label[:-2]
    prefix = text[owner.end(1):noun.start()] if owner else ""
    prefix = _PAST_DATE_RE.sub("", prefix)
    entities = [m.group(1).lower() for m in re.finditer(rf"\b({_SUPPORT_NAME})['’]s\b", text)
                if not owner or m.start() != owner.start(1)]
    return dict(actor=actor, noun=noun.group(0).lower(), qualifiers=_support_terms(prefix) - {"full", "whol", "whole", "complet", "complete", "all"},
                dates=_canonical_time_values(text[:noun.end()]), entities=entities)


def _support_record_request(question: str):
    # Query-frame prepositions are grammar, not part of a possessed name.
    # Apply this only to the request; a source owner named In stays intact.
    record_question = re.sub(r"^\s*(?:for|from|in|using|according to)\s+", "", question, flags=re.I)
    reference = _support_record_reference(record_question)
    if not reference or not re.search(r"\b(?:what|which)\b", question, re.I):
        return None
    # A question about an actual action keeps ordinary actor/event authority,
    # even when it mentions a record as background.
    action = re.search(rf"(?i:\b(?:did|does|had|has))\s+(?:(?i:i|we|you)\b|{_SUPPORT_NAME})\s+(\w+)", question)
    if action and not re.fullmatch(_SUPPORT_RECORD_REPORT_VERB, action.group(1), re.I):
        return None
    return reference


def _support_record_field_name(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def _support_record_answer(answer: str) -> bool:
    body = str(answer).strip()
    # An actual occurrence is not a property of the reported record.
    if re.search(r"\b(?:attend(?:ed)?|happen(?:ed)?|occur(?:red)?|took\s+place|was\s+held|finished|arrived)\b", body, re.I):
        return False
    if re.search(rf"\b{_SUPPORT_RECORD_NOUN}\b[^.!?]*\b{_SUPPORT_RECORD_REPORT_VERB}\b", body, re.I):
        return True
    if body.startswith("{"):
        end = _json_object_end(body, 0)
        try:
            return end == len(body.rstrip(" .")) and isinstance(json.loads(body[:end]), dict)
        except (ValueError, TypeError):
            return False
    if re.match(r"\s*[a-zA-Z][a-zA-Z _-]*\s*:", body):
        return True
    # A direct field/value reply makes no independent actor/event assertion.
    return bool(_canonical_time_values(body)) and not re.search(r"[a-zA-Z]", _PAST_DATE_RE.sub("", _PAST_CLOCK_TIME_RE.sub("", body)))


def _support_record_spans(body: str, *, statement, unit: int):
    """Keep an asserted JSON record and its intro in one source-owned span.

    Field strings are data, not speakers or quoted event assertions. They may
    support a reported-property request, never an ordinary action claim.
    """
    result, removals = [], []
    cursor = 0
    while (start := body.find("{", cursor)) >= 0:
        end = _json_object_end(body, start)
        if end < 0:
            break
        cursor = end
        intro_start = max((m.end() for m in re.finditer(r"(?<=[.!?;])\s+|\n+", body[:start])), default=0)
        intro = body[intro_start:start].strip()
        reference = _support_record_reference(intro)
        if not reference:
            continue
        try:
            fields = json.loads(body[start:end])
        except (ValueError, TypeError):
            continue
        if not isinstance(fields, dict):
            continue
        # Remove this typed object from the prose parser even when its intro
        # is speculative: JSON keys can never become grammatical speakers.
        removals.append((intro_start, end))
        if (not re.search(r"\b(?:is|was|reads|lists|contains|recorded|saved)\s*:?\s*$", intro, re.I)
                or _SUPPORT_UNCERTAIN_RE.search(intro)
                or _SUPPORT_NEGATED_RE.search(intro)
                or re.search(r"\b(?:example|sample|hypothetical|hypothetically|suppose|quoted|quotes|would|could)\b", intro, re.I)
                or len(re.findall(r'(?<!\\)"', body[:start])) % 2):
            continue
        speaker = re.match(r"\s*([a-zA-Z][a-zA-Z'’-]+)\s*:", intro)
        actor = reference['actor'] or (speaker.group(1).lower() if speaker else 'user')
        if reference['actor'] == 'user' and speaker:
            actor = speaker.group(1).lower()
        result.append(dict(body=intro + body[start:end], context=intro, actor=actor, statement=statement,
                           dates=set(), values=set(), unit=unit, polarity='positive',
                           record_reference=reference, record_fields=fields))
    for start, end in reversed(removals):
        body = body[:start] + body[end:]
    return result, body


def _record_span_values(span, question: str, answer: str):
    request = _support_record_request(question)
    reference = span['record_reference']
    if (not request or not _support_record_answer(answer)
            or (request['actor'] and request['actor'] != span['actor'])
            or (request['noun'] != 'record' and request['noun'] != reference['noun'])
            or not request['qualifiers'] <= reference['qualifiers']):
        return None
    if not request['dates'] <= reference['dates']:
        return None
    labels = [m.group(1).lower() for m in re.finditer(rf"(?i:\b(?:for|about|of))\s+({_SUPPORT_NAME})\b", span['context'])]
    scalar_labels = {str(value).lower() for value in span['record_fields'].values() if isinstance(value, str)}
    if any(entity not in (set(labels) if labels else scalar_labels) for entity in request['entities']):
        return None
    field_label = re.match(r"\s*([a-zA-Z][a-zA-Z _-]*)\s*:", answer)
    if field_label and _support_record_field_name(field_label.group(1)) not in {_support_record_field_name(key) for key in span["record_fields"]}:
        return None
    normalized_question = _support_record_field_name(question)
    values = _canonical_time_values(span['context'])
    all_fields = bool(re.search(r"\b(?:whole|full|complete|all)\s+(?:record|brief|note|fields|properties)|\b(?:fields|properties|details)\b", question, re.I))
    for key, scalar in span['record_fields'].items():
        field = _support_record_field_name(key)
        if not field or (not all_fields and not re.search(r"(?<!\w)" + re.escape(field) + r"(?!\w)", normalized_question)):
            continue
        if not isinstance(scalar, str):
            continue
        # A standalone temporal scalar is data; an instruction containing a
        # date is still just an instruction. Nested objects donate no values.
        scalar = scalar.strip()
        if not (_PAST_DATE_RE.fullmatch(scalar) or _PAST_CLOCK_TIME_RE.fullmatch(scalar) or _PAST_DURATION_RE.fullmatch(scalar)):
            continue
        values.update(_canonical_time_values(scalar))
    return values



_SUPPORT_QUESTION_ACTION = r"(?!(?:since|after|before|between|when|where|that|then|the|a|an|of|by|for|with|and|or|to|in|on)\b)[a-zA-Z]+\b"
#: A compound grammatical subject of the asked action: "did <Name> and her partner try ...",
#: "were <Name> and <Name> ...". Each named conjunct is an asked subject; an unnamed conjunct
#: ("her partner", "a friend") names nobody whose record could be bound.
_SUPPORT_COMPOUND_SUBJECT_RE = re.compile(
    rf"(?i:\b(?:did|does|had|has|have|was|were|is|are))\s+({_SUPPORT_NAME})\s+(?i:and)\s+"
    rf"(?:({_SUPPORT_NAME})|(?i:her|his|their|its|my|our|your|a|an|the)(?:\s+[a-z][a-z'’-]*){{1,2}}?)"
    rf"\s+(?={_SUPPORT_QUESTION_ACTION})"
)


def _support_question_actors(question: str) -> tuple[str, ...]:
    """Every asked subject: the single actor, or each named conjunct of a compound subject.

    "When did <Name> and her partner try ..." asks about <Name>; read as one actor the compound
    resolved to nobody, and every speaker's record became admissible while the asked subject's own
    record could not be bound. The first entry is the primary actor (:func:`_support_question_actor`).
    """
    record = _support_record_request(question)
    if not (record and record["actor"]):
        compound = _SUPPORT_COMPOUND_SUBJECT_RE.search(_support_reference_question(question))
        if compound:
            names = [name.lower() for name in compound.groups() if name]
            names = [name for name in dict.fromkeys(names) if name not in _SUPPORT_NON_ACTORS]
            if names:
                return tuple(names)
    actor = _support_question_actor(question)
    return (actor,) if actor else ()


def _support_subject_phrase_terms(question: str) -> set[str]:
    """Terms of a compound subject's conjuncts ("her partner"): who acts, never the asked event."""
    compound = _SUPPORT_COMPOUND_SUBJECT_RE.search(_support_reference_question(question))
    if not compound:
        return set()
    return _support_terms(compound.group(0)[compound.start(1) - compound.start():])


def _support_question_actor(question: str) -> str:
    record = _support_record_request(question)
    if record and record["actor"]:
        return record["actor"]
    compound = _SUPPORT_COMPOUND_SUBJECT_RE.search(_support_reference_question(question))
    if compound and compound.group(1).lower() not in _SUPPORT_NON_ACTORS:
        return compound.group(1).lower()
    question_action = _SUPPORT_QUESTION_ACTION
    grammatical = _support_reference_question(question)
    match = re.search(
        rf"(?i:\b(?:did|does|had|has))\s+((?i:i|we|you)\b|{_SUPPORT_NAME}|[a-zA-Z][a-zA-Z'’-]+)\s+(?={question_action})",
        grammatical,
    )
    if match is None:
        # In a between frame, a named subject precedes an action. Bare
        # "between filing and collecting" has no subject to extract.
        match = re.search(rf"(?i:\bbetween)\s+({_SUPPORT_NAME})\s+(?={_SUPPORT_SUBJECT_VERB})", grammatical)
        if match is None:
            match = re.search(rf"\bbetween\s+([a-zA-Z][a-zA-Z'’-]+)\s+(?={_SUPPORT_SUBJECT_VERB})", grammatical, re.I)
    if match:
        actor = match.group(1).lower()
        if actor in {"i", "we", "you"}:
            # The action's grammatical subject takes precedence over an
            # excluded person's possessive in a later instruction.
            return "user"
        if actor not in _SUPPORT_NON_ACTORS:
            return actor
    possessive = (re.search(rf"\b({_SUPPORT_NAME})['’]s\b", grammatical)
                  or re.search(r"\b([a-zA-Z][a-zA-Z'’-]*?)['’]s\b", grammatical))
    if possessive and possessive.group(1).lower() not in _SUPPORT_NON_ACTORS:
        return possessive.group(1).lower()
    if re.search(r"\b(?:i|me|my)\b", grammatical, re.I):
        return "user"
    return ""


#: The text before a capitalized word ends inside a noun phrase: a determiner, possessive,
#: preposition or attributive word directly precedes it.
_SUPPORT_NOUN_PHRASE_TAIL_RE = re.compile(
    r"\b(?:my|our|your|his|her|their|its|the|a|an|this|that|these|those|some|any|each|every|"
    r"of|at|on|in|into|to|from|with|for|by|about|new|old|own|first|last|favorite|favourite)\s+$",
    re.I,
)


def _support_possessed_subject_terms(text: str) -> set[str]:
    match = re.search(
        r"\b(?:my|our|your|her|his|their|[a-zA-Z][a-zA-Z'’-]*?['’]s)\s+(.+?)"
        r"(?=\s+(?:at|in|on|from|to|for|with|back|take|last|was|were|been|did|does|had|has|when|while|after|before|since|because)\b|[?.!]|$)",
        text, re.I,
    )
    return _support_terms(match.group(1)) if match else set()


def _support_possessed_subjects(text: str) -> list[set[str]]:
    """Every possessed subject phrase in ``text`` ("my Adidas running shoes", "my old Converse sneakers")."""
    subjects = []
    for match in re.finditer(
        r"\b(?:my|our|your|her|his|their|[a-zA-Z][a-zA-Z'’-]*?['’]s)\s+(.+?)"
        r"(?=\s+(?:at|in|on|from|to|for|with|back|take|last|was|were|been|did|does|had|has|when|and)\b|[?.!,]|$)",
        text, re.I,
    ):
        terms = _support_terms(match.group(1))
        if terms:
            subjects.append(terms)
    return subjects


def _support_interval_subject_terms(question: str) -> set[str]:
    """An explicit possessed subject distinguishes parallel interval episodes."""
    if not re.search(r"\b(?:how long|how many (?:elapsed |calendar )?(?:days|weeks|nights))\b", question, re.I):
        return set()
    return _support_possessed_subject_terms(question)


def _support_interval_subjects(question: str) -> list[set[str]]:
    """Each event of an interval question may name its own possessed subject: "since I bought my
    Adidas running shoes when I realized ... my old Converse sneakers had broken" has two."""
    first = _support_interval_subject_terms(question)
    if not first:
        return []
    return [first, *(subject for subject in _support_possessed_subjects(question) if subject != first)]


def _support_event_text(question: str) -> str:
    # In a direct time ask the object identifies the event; a shared verb
    # cannot make collecting a permit support collecting a parcel.
    match = _SUPPORT_DIRECT_TIME_RE.match(_support_reference_question(question))
    object_text = match.group(2) if match else question
    if match:
        before_location = re.split(r"\s+(?:at|in|near|from)\s+", object_text, maxsplit=1, flags=re.I)[0]
        if _support_terms(before_location):
            object_text = before_location
    return object_text


def _support_event_terms(question: str) -> set[str]:
    return _support_terms(_support_event_text(question))


def _support_requests_time_value(question: str) -> bool:
    return bool(re.search(
        r"\b(?:when|what time|(?:what|which) (?:day|date|year)|how long|"
        r"how many (?:(?:elapsed|calendar)\s+)?(?:days|weeks|months|years|nights|hours|minutes))\b",
        question, re.I,
    ))


def _shared_named_entity(source: str, answer: str) -> bool:
    # A count/location answer may name the actual venue where the source
    # records its date, even when the question uses a broad category word.
    # This literal binding never relaxes a direct actor/event time request.
    def entities(text):
        return {
            " ".join(word.lower() for word in re.findall(r"[a-zA-Z'’-]+", match.group(0)) if word.lower() not in {"the", "of"})
            for match in _SUPPORT_NAMED_ENTITY_RE.finditer(text)
        }

    return bool(entities(source) & entities(answer))


def _support_action_matches(action: str, body: str) -> bool:
    requested = _support_terms(action)
    observed = _support_terms(body)
    if not requested or requested & observed:
        return True
    # These are ordinary action paraphrases, not domain or benchmark names.
    # Paying for the same ticket supports buying it; leaving the same place
    # does not support reaching it. Object matching remains a separate gate.
    for family in ({"buy", "bought", "paid", "purchas"}, {"reach", "arriv"}):
        if requested & family and observed & family:
            return True
    return bool(requested & {"reach", "arriv"} and re.search(r"\bgot\s+to\b", body, re.I))


def _time_adverbial_only(text: str) -> bool:
    """A piece that only dates something ("Last month", "By the way, two weeks ago")."""
    rest = _AGO_PHRASE_RE.sub(" ", _PAST_DATE_RE.sub(" ", str(text or "")))
    rest = re.sub(r"\b(?:today|yesterday|tomorrow|recently|earlier|lately|(?:this|last|next)\s+(?:week|month|year|weekend))\b", " ", rest, flags=re.I)
    rest = re.sub(r"\b(?:by\s+the\s+way|so|and|well|actually|oh|also|then|on|in)\b|[,;:\s]", " ", rest, flags=re.I)
    return bool(str(text or "").strip()) and not rest.strip()


def _statement_time_markers(text: str) -> tuple[set, list[tuple[int, int]]]:
    """Statement times a text labels ("Session date: ...", "(stated: ...; stated: YYYY-MM-DD)") and
    the character ranges of those labels."""
    statement_dates = set()
    removals = []
    for marker in _SUPPORT_STATEMENT_RE.finditer(text):
        at = marker.end()
        # A statement time may carry a clock time before its date, in the session header
        # ("Session date: 6:55 pm on 20 October, 2023") and in the record suffix
        # ("stated: 6: 55 pm on 20 October, 2023"). Unparsed, the header stayed in front of
        # the speaker label and every first-person clause of that turn fell to the generic
        # user instead of its named speaker.
        clock_prefix = _SUPPORT_HEADER_CLOCK_RE.match(text, at)
        at = clock_prefix.end() if clock_prefix else at
        date_match = _PAST_DATE_RE.match(text, at) or _SUPPORT_SLASH_DATE_RE.match(text, at)
        if date_match:
            parsed = _support_date(date_match.group(0))
            if parsed is not None:
                statement_dates.add(parsed)
            removals.append((marker.start(), date_match.end()))
    return statement_dates, removals


def _evidence_statement_times(evidence_texts) -> list:
    """Every statement time labelled anywhere in the evidence the reader saw, oldest first."""
    times: set = set()
    for text in list(evidence_texts or []):
        times |= _statement_time_markers(str(text or ""))[0]
    return sorted(times)


def _statement_time_clocks(text: str) -> dict:
    """The clock times a text's statement-time labels carry, by statement date.

    A session header "Session date: <h:mm am|pm> on <day month, year>" and a record suffix "(stated:
    <h: mm am|pm> on <day month, year>)" both say WHEN the record was made, to the minute. The label is
    removed from the record's body before its values are read (:func:`_statement_time_markers`), so the
    clock time was not a value of anything, and an answer citing "the <clock> conversation" was
    withdrawn for a time the record states. A label without a clock carries none.
    """
    clocks: dict = {}
    body = str(text or "")
    for marker in _SUPPORT_STATEMENT_RE.finditer(body):
        clock = _SUPPORT_HEADER_CLOCK_RE.match(body, marker.end())
        if clock is None:
            continue
        date_match = _PAST_DATE_RE.match(body, clock.end()) or _SUPPORT_SLASH_DATE_RE.match(body, clock.end())
        parsed = _support_date(date_match.group(0)) if date_match else None
        if parsed is None:
            continue
        written = re.sub(r"\s+on\s+$", "", re.sub(r":\s+", ":", clock.group(0)), flags=re.I)
        found = {value for value in _canonical_time_values(written) if value.startswith("t")}
        if found:
            clocks.setdefault(parsed, set()).update(found)
    return clocks


def _evidence_statement_clocks(evidence_texts) -> dict:
    """Every statement-time clock labelled anywhere in the evidence the reader saw, by statement date."""
    clocks: dict = {}
    for text in list(evidence_texts or []):
        for day, found in _statement_time_clocks(str(text or "")).items():
            clocks.setdefault(day, set()).update(found)
    return clocks


def _past_support_spans(evidence_texts, *, question: str = ""):
    """Separate actor-bearing source clauses before reading their values.

    A statement date belongs to its own source unit. A speaker prefix owns
    first-person clauses, while an explicitly named later subject replaces
    that speaker for its own clause. No date is donated to another unit.
    """
    result = []
    for text in list(evidence_texts or []):
        chunks = re.split(r"(?m)(?=^\s*-\s+(?:user|assistant)\s+(?:said|source)\b)", str(text or ""))
        for chunk in chunks:
            if not chunk.strip() or chunk.strip().startswith(("<retrieved_context>", "Distilled local facts.")):
                continue
            # The role envelope names who is speaking. "assistant said: I visited the exhibition"
            # is the assistant's first person; reading it as the user's let an assistant line
            # support "You visited the exhibition on May 6".
            role = "assistant" if re.match(r"\s*-?\s*assistant\b", chunk, re.I) else "user"
            # The retrieval capsule binds a fragment to its record's own speaker label in the
            # envelope ('- user said [reported source prefix "Name:"] (...): <fragment>'). Left in
            # place, the annotation kept the envelope from being stripped, the label was never read
            # and every first-person clause of that record fell to the generic user.
            annotated_speaker = ""
            annotation = _SUPPORT_REPORTED_PREFIX_RE.match(chunk)
            if annotation:
                annotated_speaker = " ".join(annotation.group(2).split())
                chunk = annotation.group(1) + chunk[annotation.end():]
            statement_dates, removals = _statement_time_markers(chunk)
            body = chunk
            for start, end in reversed(removals):
                body = body[:start] + body[end:]
            body = re.sub(r"\((?:\s*stated|\s*recorded)[^)]*\)", "", body, flags=re.I)
            # Every statement time of a suffix was read above; its emptied parenthesis is no text.
            body = re.sub(r"\s*\(\s*(?:[;,]\s*)*\)", "", body)
            body = re.sub(r"</?retrieved_context>|Distilled local facts[^\n]*", "", body)
            body = re.sub(r"^\s*-?\s*(?:user|assistant)\s*(?:said|source)?\s*\([^)]*\)\s*:\s*", "", body, flags=re.I)
            body = re.sub(r"^\s*-?\s*(?:user|assistant)\s*(?:said|source)?\s*:\s*", "", body, flags=re.I)
            body = body.strip(" \n.;")
            statement = next(iter(statement_dates)) if len(statement_dates) == 1 else None
            # The clock time of the record's own statement label, kept beside (never inside) its values.
            statement_clocks = frozenset(_statement_time_clocks(chunk).get(statement, ())) if statement else frozenset()
            unit_id = len(result)
            records, body = _support_record_spans(body, statement=statement, unit=unit_id)
            for record in records:
                record["statement_clocks"] = statement_clocks
            result.extend(records)
            # A quoted value is evidence of the quote's content, not the
            # narrator's event. A question explicitly asking what was quoted
            # can still receive that exact quoted value.
            if not re.search(r"\b(?:did|does|had|has)\s+[^?.!]*\bquote\b", question, re.I):
                def quote_support(match):
                    inner = match.group(0)[1:-1]
                    named_assertion = bool(_support_actor(inner))
                    if named_assertion and re.match(r"\s*\S+['’]s\b", inner):
                        # Possessed names can be labels. A finite predicate,
                        # rather than possession alone, makes one a claim.
                        named_assertion = bool(re.search(r"\b(?:was|were|is|are|had|has|have|will|would)\b", inner, re.I))
                    event_claim = bool(
                        _canonical_time_values(inner)
                        or _SUPPORT_SLASH_DATE_RE.search(inner)
                        or re.search(r"\b(?:today|yesterday|tomorrow)\b", inner, re.I)
                        or re.match(rf"\s*(?:i|we|you|he|she|they)(?:['’](?:ve|d|ll|re|m|s))?\s+{_SUPPORT_SUBJECT_VERB}", inner, re.I)
                        or named_assertion
                        or re.match(rf"\s*{_SUPPORT_SUBJECT_VERB}\s+(?:it|them|this|that)\b", inner, re.I)
                    )
                    return "" if event_claim else inner

                body = re.sub(r'"[^"\n]*"|“[^”\n]*”|(?<!\w)\'[^\'\n]+\'(?!\w)|‘[^’\n]*’', quote_support, body)
            actor = _support_actor(body)
            speaker = actor if re.match(r"^\s*[a-zA-Z][a-zA-Z'’-]+\s*:", body) else ""
            if annotated_speaker:
                # The bound label names who said the fragment, whether or not the fragment
                # repeats it; a repeated label is the same speaker, not a new clause.
                repeated = re.match(rf"\s*{re.escape(annotated_speaker)}\s*:", body, re.I)
                if repeated:
                    body = body[repeated.end():].strip()
                speaker = annotated_speaker.lower()
            elif speaker:
                body = body.split(":", 1)[1].strip()
            previous = None
            clauses: list[str] = []
            for part in re.split(r"(?<=[.!?;])\s+|\n+|\s+but\s+|,\s+(?=(?:I|we|you|he|she|they)\b)", body, flags=re.I):
                # "Last month, I finally attended an open mic night": the fronted time adverbial
                # belongs to the clause it dates; split off, it dated nothing.
                if clauses and _time_adverbial_only(clauses[-1]):
                    clauses[-1] = clauses[-1].rstrip(" ,") + ", " + part
                else:
                    clauses.append(part)
            for sentence in clauses:
                # Split a compound only when both halves carry dates/relative
                # days. An ordinary compound subject stays intact.
                pieces = re.split(r"\s+and\s+", sentence, flags=re.I)
                if not (len(pieces) == 2 and all(_PAST_DATE_RE.search(p) or _SUPPORT_SLASH_DATE_RE.search(p) or re.search(r"\b(?:today|yesterday|tomorrow)\b", p, re.I) for p in pieces)):
                    pieces = [sentence]
                actor_pieces = []
                for piece in pieces:
                    boundaries = []
                    named_subjects = [*_SUPPORT_NAMED_SUBJECT_RE.finditer(piece), *_SUPPORT_POSSESSED_SUBJECT_RE.finditer(piece)]
                    for match in sorted(named_subjects, key=lambda value: value.start()):
                        name = next(value for value in match.groups() if value).lower()
                        if match.start() <= 0 or name in _SUPPORT_NON_ACTORS or _temporal_or_quantity_prefix(piece[match.start():]):
                            continue
                        if _SUPPORT_NOUN_PHRASE_TAIL_RE.search(piece[:match.start()]):
                            # A capitalized word inside a noun phrase ("bought my new Adidas running
                            # shoes", "on my old Converse sneakers") names a thing, not a new
                            # grammatical subject; splitting there gave the brand the actor role and
                            # cut the action away from its date.
                            continue
                        if name.endswith(("'s", "’s")) and not re.match(
                            r"(?:[a-zA-Z'’-]+\s+){0,5}(?:was|were|is|are|had|has|have|will|would)\b",
                            piece[match.end():], re.I,
                        ):
                            # A possessed label embedded in an object is not
                            # a new grammatical subject and cannot split its
                            # surrounding action away from the action's date.
                            continue
                        if match.start() not in boundaries:
                            boundaries.append(match.start())
                    starts = [0, *boundaries]
                    actor_pieces.extend((piece[start:end], bool(_SUPPORT_UNCERTAIN_RE.search(piece))) for start, end in zip(starts, [*boundaries, len(piece)], strict=True))
                pieces = actor_pieces
                for piece, inherited_uncertainty in pieces:
                    if not piece.strip():
                        continue
                    explicit_actor = _support_actor(piece)
                    first_person = bool(re.match(r"\s*(?:I|we|you)\b", piece, re.I))
                    continued_subject = bool(re.match(
                        rf"\s*(?:(?:he|she|they|it)\b|{_SUPPORT_SUBJECT_VERB})", piece, re.I,
                    ))
                    if explicit_actor:
                        local_actor = explicit_actor
                    elif first_person:
                        local_actor = speaker or (
                            "assistant" if role == "assistant" and re.match(r"\s*(?:I|we)\b", piece, re.I) else "user"
                        )
                    elif previous and continued_subject:
                        local_actor = previous["actor"]
                    else:
                        local_actor = speaker or "user"
                    dates = set()
                    uncertain = inherited_uncertainty or bool(_SUPPORT_UNCERTAIN_RE.search(piece))
                    if not uncertain:
                        for match in list(_PAST_DATE_RE.finditer(piece)) + list(_SUPPORT_SLASH_DATE_RE.finditer(piece)):
                            parsed = _support_date(match.group(0), statement.year if statement else None)
                            if parsed is not None:
                                dates.add(parsed)
                        relative = re.findall(r"\b(today|yesterday|tomorrow)\b", piece, re.I)
                        if statement is not None and len(relative) == 1:
                            from datetime import timedelta

                            dates.add(statement + timedelta(days={"today": 0, "yesterday": -1, "tomorrow": 1}[relative[0].lower()]))
                        elif statement is not None and not relative and not dates and _RECENT_PAST_RE.search(piece):
                            # "I just launched my website" reports a completed event at its own
                            # statement time, exactly like "today".
                            dates.add(statement)
                    values = set() if uncertain else _record_time_values(piece)
                    for day in dates:
                        values.update((f"m{day.month}:{day.day}:{day.year}", f"y{day.year}"))
                    # A completion clause of the same explicitly started task
                    # inherits that task, never a different actor's clause.
                    context = piece
                    # Only a grammatical continuation in this source unit may
                    # inherit the preceding event. An explicit changed actor or
                    # a new object never receives the preceding event's dates.
                    anaphoric_object = bool(re.search(
                        rf"\b{_SUPPORT_SUBJECT_VERB}\s+(?:it|them|that|this)\b|^\s*(?:it|this|that)\b",
                        piece, re.I,
                    ))
                    if previous and previous["actor"] == local_actor and anaphoric_object:
                        context = previous["context"] + " " + piece
                    elif previous and previous["actor"] == local_actor and (_support_terms(piece) - {local_actor}) <= {"finish", "end", "complet"} and re.search(r"\b(?:start(?:ed)?|began|begun)\b", previous["body"], re.I):
                        context = previous["body"] + " " + piece
                    span = dict(body=piece, context=context, actor=local_actor, statement=statement, dates=dates, values=values, unit=unit_id, role=role,
                                speaker=speaker, anaphoric=anaphoric_object, source_text=body, uncertain=uncertain,
                                statement_clocks=statement_clocks,
                                windows=[] if uncertain else _relative_time_windows(piece, statement),
                                polarity="negative" if _assertion_negated(piece) else "positive")
                    result.append(span)
                    previous = span
    return result


def _shared_alphabetic_compound(source: str, request: str) -> bool:
    # Keep the complete phrase: "river-survey" cannot be reduced to the
    # shared word "survey" and authorize a different survey's date.
    compound = r"(?<![\w\-\u2010\u2011])[a-zA-Z]+(?:[-\u2010\u2011][a-zA-Z]+)+(?![\w\-\u2010\u2011])"
    forms = re.findall(compound, source + " " + request)
    def spelling(text):
        return re.sub(r"(?<=[a-zA-Z])[-\u2010\u2011](?=[a-zA-Z])", " ", text).lower()
    source_form, request_form = spelling(source), spelling(request)
    return any(re.search(r"(?<![\w\-\u2010\u2011])" + re.escape(spelling(form)) + r"(?![\w\-\u2010\u2011])", source_form)
               and re.search(r"(?<![\w\-\u2010\u2011])" + re.escape(spelling(form)) + r"(?![\w\-\u2010\u2011])", request_form)
               for form in forms)


def _span_supports_request(span, question: str, answer: str = "", *, complete_subject: bool = False) -> bool:
    if "record_fields" in span:
        return _record_span_values(span, question, answer) is not None
    actors = _support_question_actors(question)
    actor = actors[0] if actors else ""
    if actors and _support_reference_actor(span["actor"], question) not in actors:
        return False
    direct = _SUPPORT_DIRECT_TIME_RE.match(_support_reference_question(question))
    if direct and not _support_action_matches(direct.group(1), span["body"]):
        return False
    context_terms = _support_terms(span["context"])
    subject_options = _support_interval_subjects(question)
    if subject_options:
        # A source about a parallel episode names a different possessed subject. With several
        # subjects named by the question, a source matching any one of them is about one of the
        # asked events; a source naming several possessions is matched on any of them.
        if complete_subject and not any(subject <= context_terms for subject in subject_options):
            return False
        source_subjects = _support_possessed_subjects(span["context"])
        if source_subjects and not any(subject <= source or source <= subject
                                       for subject in subject_options for source in source_subjects):
            return False
    terms = _support_event_terms(question) - {*actors, "night"} - _support_subject_phrase_terms(question)
    if not terms or terms & context_terms:
        return True
    event_request = direct.group(2) if direct else _support_reference_question(question)
    if _shared_alphabetic_compound(span["context"], event_request):
        return True
    return bool(answer and not _support_requests_time_value(question) and _shared_named_entity(span["body"], answer))


#: Verbs that report when somebody SPOKE or wrote ("what you noted on March 15th"); the date they
#: carry is the report time, not the reported event's date.
_REPORT_VERB = (r"(?:mentioned|said|stated|told|remembered|talked|spoke|chatted|discussed|noted|wrote|"
                r"shared|logged|recorded|reported|posted|messaged)")


def _named_reporter_matches(name: str, speaker: str, question: str) -> bool:
    """A reporter the answer names is the record's own speaker (no named reporter: no binding)."""
    words = [word.lower() for word in str(name or "").split()]
    words = [word for word in words if word not in _SUPPORT_NON_ACTORS]
    if not words or not speaker:
        return True
    forms = {" ".join(words), words[0], words[-1]}
    return speaker in {_support_reference_actor(form, question) for form in forms} | forms


def _statement_is_answer_provenance(answer: str, day, *, speaker: str = "", question: str = "", record: str = "") -> bool:
    """A date saying when somebody spoke is not the event's date.

    When the answer names who spoke ("On May 2, Dana said ...", "Dana mentioned on May 2 ..."), that
    person must be the record's own ``speaker``: another person's report is not this record's act.
    A ``record`` that itself reports an act of the answer's reporting verb ("I told my landlord
    yesterday") does not date that act at its statement time: the told-on date is the reported event's.
    """
    occurrences = []
    for match in _PAST_DATE_RE.finditer(answer):
        date_values = _canonical_time_values(match.group(0))
        if f"m{day.month}:{day.day}:{day.year}" in date_values or f"m{day.month}:{day.day}" in date_values:
            prefix = answer[max(0, match.start()-100):match.start()]
            suffix = answer[match.end():match.end()+70]
            source_nouns = r"(?:note|session|conversation|chat|record|table|entry|message|report|update)"
            reported_before = re.search(rf"\b{_REPORT_VERB}\b(?:\s+(?:this|that|it|me|us|you|about|back|again|also|down)){{0,6}}\s*(?:\(\s*)?(?:on|around)\s*$", prefix, re.I)
            reported_after = re.match(rf"[, ]*(you|i|[A-Z][a-zA-Z'’-]+)\s+{_REPORT_VERB}\b", suffix)
            reporter = ""
            if reported_before:
                named = re.search(rf"(?-i:({_SUPPORT_NAME}))\s+(?:(?:[a-z]+ly|just|also)\s+){{0,2}}$", prefix[:reported_before.start()])
                reporter = named.group(1) if named and not named.group(1).endswith(("'s", "’s")) else ""
            elif reported_after:
                reporter = reported_after.group(1)
            # A named reporter ("Dana shared ...") binds the date to the record holding what Dana
            # shared; "you said" / "we talked about it" over the user's own records keeps its reading.
            named_reporter = bool(reporter) and reporter.lower() not in _SUPPORT_NON_ACTORS
            verb = re.search(_REPORT_VERB, (reported_before or reported_after).group(0)).group(0).lower() if (reported_before or reported_after) else ""
            by_verb = (bool(reported_before or reported_after) and not _record_reports_act(record, verb)
                       and (not record or not named_reporter
                            or _clause_object_in_record(answer, question, record, f"{reporter} {verb}")))
            occurrences.append(bool(
                by_verb
                or re.search(rf"\b{source_nouns}\b[^.!?]{{0,35}}\b(?:on|from|of|dated)\s*$", prefix, re.I)
                or re.search(r"\bas\s+of\s*$", prefix, re.I)
                or re.match(rf"\s*{source_nouns}\b", suffix, re.I)
            ) and _named_reporter_matches(reporter, speaker, question))
    return bool(occurrences) and all(occurrences)


#: Communicative acts. A record IS its speaker's act of saying, sharing, telling or asking something
#: in the conversation the record is from, so the act happened at the record's statement time.
_COMMUNICATIVE_ACT_FAMILIES = {
    "said": "say", "shared": "share", "mentioned": "mention", "told": "tell", "posted": "post",
    "showed": "show", "sent": "send", "wrote": "write", "asked": "ask",
}
#: Every written form of each family, for the record-side check that the record does not REPORT such
#: an act of its own ("I told my boss yesterday", "my sister sent me ...").
_COMMUNICATIVE_FAMILY_FORMS = {
    "say": "say|says|said|saying", "share": "share|shares|shared|sharing",
    "mention": "mention|mentions|mentioned|mentioning", "tell": "tell|tells|told|telling",
    "post": "post|posts|posted|posting", "show": "show|shows|showed|shown|showing",
    "send": "send|sends|sent|sending", "write": "write|writes|wrote|written|writing",
    "ask": "ask|asks|asked|asking",
}
#: The other reporting verbs (:data:`_REPORT_VERB`), in every written form.
_REPORT_VERB_FORMS = {
    **{verb: _COMMUNICATIVE_FAMILY_FORMS[family] for verb, family in _COMMUNICATIVE_ACT_FAMILIES.items()},
    "stated": "state|states|stated|stating", "remembered": "remember|remembers|remembered|remembering",
    "talked": "talk|talks|talked|talking", "spoke": "speak|speaks|spoke|spoken|speaking",
    "chatted": "chat|chats|chatted|chatting", "discussed": "discuss|discusses|discussed|discussing",
    "noted": "note|notes|noted|noting", "logged": "log|logs|logged|logging",
    "recorded": "record|records|recorded|recording", "reported": "report|reports|reported|reporting",
    "messaged": "message|messages|messaged|messaging",
}


def _record_reports_act(record: str, verb: str) -> bool:
    """The record itself reports an act of this verb's kind ("I told my boss yesterday", "my sister
    sent me ..."): that act may lie at any time before the record, so the record's statement time
    does not date it. A bracketed attachment note ("[shared photo: ...]") reports nothing."""
    forms = _REPORT_VERB_FORMS.get(str(verb or "").lower())
    if not forms:
        return False
    return bool(re.search(rf"\b(?:{forms})\b", re.sub(r"\[[^\]]*\]", " ", str(record or "")), re.I))


_COMMUNICATIVE_SUBJECT = rf"(?-i:(?P<name>{_SUPPORT_NAME})|(?P<pronoun>[Yy]ou|[Hh]e|[Ss]he|[Tt]hey))"
_COMMUNICATIVE_ACT_RE = re.compile(
    rf"(?<![\w'’-]){_COMMUNICATIVE_SUBJECT}\s+(?:(?:[a-z]+ly|just|also|first|then|once)\s+){{0,2}}"
    rf"(?P<verb>{'|'.join(_COMMUNICATIVE_ACT_FAMILIES)})\b"
)
_COMMUNICATIVE_TIME_PREPOSITION = r"(?:on|in|around|during|back\s+in|as\s+of)"
#: What may stand between a communicative verb and its trailing date: a plain object ("a photo of a
#: desk", "Dana"). An embedded clause or another verb makes the date the reported event's instead
#: ("said she went to the concert on May 2", "told Dana about the concert on May 2").
_COMMUNICATIVE_CLAUSE_WORD_RE = re.compile(
    r"\b(?:that|which|who|whom|whose|what|how|when|where|why|whether|if|because|since|after|before|"
    r"while|until|about|regarding|during|then|later|so|i|he|she|they|we|is|are|was|were|be|been|being|"
    r"has|have|had|do|does|did|will|would|could|can|might|should|left|saw|got|bought|said|told|began|"
    r"found|went|came|took|met|wrote|ate|drank|ran|drove|flew|rode|sent|put|kept|paid|made|built|"
    r"brought|caught|chose|held|knew|lost|sat|sold|stood|taught|thought|won|wore|[a-z]{3,}ed)\b"
    r"|[.!?;:\"“”]",
    re.I,
)
#: A gerund in the object reports an event ("sent a message saying ..."), unless a determiner makes it
#: a noun ("a painting", "her drawing").
_COMMUNICATIVE_GERUND_RE = re.compile(r"(?:^|(?<=\s))(?P<before>[a-z'’]+\s+)?[a-z]{3,}ing\b", re.I)
_COMMUNICATIVE_DETERMINERS = frozenset({"a", "an", "the", "my", "your", "his", "her", "its", "our", "their", "this", "that"})


def _clause_object_in_record(clause: str, question: str, record: str, act_text: str, speaker: str = "") -> bool:
    """At least half of what the clause says beyond the question, the act and its date is in the record.

    A record from another day of the same speaker about the same subject is not the record of
    THIS act: "On <its date>, Dana shared a photo of her study corner" is not supported by Dana's
    record of that date about a beach towel.
    """
    month_terms = _support_terms(" ".join(_FULL_MONTH_NAMES.values()))
    content = (_support_terms(clause) - _support_terms(question) - _support_terms(act_text)
               - month_terms - {speaker, "user"})
    return not content or 2 * len(content & _support_terms(record)) >= len(content)


def _span_speaker(span) -> str:
    """Who spoke a source unit: its speaker label, else the role envelope ("user"/"assistant")."""
    return span.get("speaker") or span.get("role") or ""


def _communicative_act_values(clause: str, span, question: str, answer_actor: str) -> tuple[set[str], list[dict]]:
    """Values a record supports for the date of its own speaker's communicative act.

    "On 2 March 2023, Dana shared a photo of her desk" claims when Dana SHARED it. When the source unit
    is Dana's own record about the asked subject, the sharing is that record, and its statement time S
    is the act's date: the claim is supported at its own granularity (a day equal to S; a month and
    year equal to S's; a year equal to S's), in strict and licensed modes. The claim must date the act
    itself -- a fronted adverbial of the act's clause ("On S, Dana said ..."), or a date following the
    act's verb or its plain object ("Dana shared a photo of her desk on S"); a date after an embedded
    clause ("Dana said she went to the concert on S") dates the reported event, which statement time
    alone never supports. The act's subject must be the record's own speaker (another person's act is
    not this record), the record must not itself report an act of the same kind ("I told my boss
    yesterday": the telling it reports may lie at any earlier time), and at least half of what the
    clause says about the act's object must be in the record.
    """
    statement = span.get("statement")
    speaker = _span_speaker(span)
    if statement is None or "record_fields" in span or not speaker or speaker == "assistant":
        return set(), []
    source = str(span.get("source_text") or span.get("body") or "")
    # The record is about the asked subject: spoken by an asked person, naming the asked event
    # (the question's act verb aside -- the record is the act, it does not narrate it).
    actors = _support_question_actors(question)
    if actors and _support_reference_actor(speaker, question) not in actors:
        return set(), []
    act_terms = _support_terms(" ".join(f.replace("|", " ") for f in _COMMUNICATIVE_FAMILY_FORMS.values()))
    asked = _support_event_terms(question) - set(actors) - act_terms - {"night"}
    if asked and not asked & _support_terms(source):
        return set(), []
    acts = list(_COMMUNICATIVE_ACT_RE.finditer(clause))
    supported: set[str] = set()
    receipt: list[dict] = []
    for claim in _calendar_claim_spans(clause):
        start, end = claim["span"]
        prefix = clause[:start]
        act = None
        position = ""
        fronted = re.search(rf"(?:^|[,;:\u2014\u2013]|\b(?:and|but|so|then)\b)\s*(?:{_COMMUNICATIVE_TIME_PREPOSITION}\s+)?$", prefix, re.I)
        if fronted:
            after = re.match(r"\s*,?\s*", clause[end:])
            act = next((found for found in acts if found.start() == end + after.end()), None)
            position = "fronted"
        if act is None:
            preposition = re.search(rf"(?:^|(?<=[\s,]))({_COMMUNICATIVE_TIME_PREPOSITION})\s*$", prefix, re.I)
            before = [found for found in acts if found.end() <= (preposition.start() if preposition else -1)]
            if before:
                act = before[-1]
                between = clause[act.end():preposition.start()].strip(" ,")
                if (len(between.split()) > 20 or _COMMUNICATIVE_CLAUSE_WORD_RE.search(between)
                        or any(str(gerund.group("before") or "").strip().lower() not in _COMMUNICATIVE_DETERMINERS
                               for gerund in _COMMUNICATIVE_GERUND_RE.finditer(between))):
                    act = None
                position = "after_act"
        if act is None:
            continue
        if act.group("name"):
            name = act.group("name")
            if name.endswith(("'s", "’s")) or not [w for w in name.lower().split() if w not in _SUPPORT_NON_ACTORS]:
                continue
            if not _named_reporter_matches(name, speaker, question):
                continue
        else:
            pronoun = act.group("pronoun").lower()
            subject = "user" if pronoun == "you" else answer_actor
            if subject != speaker:
                continue
        if _record_reports_act(source, act.group("verb")):
            continue
        if not _clause_object_in_record(clause, question, source, act.group(0), speaker):
            continue
        if claim["granularity"] == "day":
            matches = (claim["month"], claim["day"]) == (statement.month, statement.day) and claim["year"] in (None, statement.year)
        elif claim["granularity"] == "month":
            matches = (claim["month"], claim["year"]) == (statement.month, statement.year)
        else:
            matches = claim["year"] == statement.year
        if not matches:
            continue
        values = {f"y{statement.year}"}
        if claim["granularity"] == "day":
            values.add(f"m{statement.month}:{statement.day}:{statement.year}")
        supported |= values
        receipt.append({"source_unit": span["unit"], "rule": "communicative_act_statement_time",
                        "statement": statement.isoformat(), "speaker": speaker, "act": act.group("verb"),
                        "position": position, "granularity": claim["granularity"], "values": sorted(values)})
    return supported, receipt


def _terms_with_joined_compounds(text: str) -> set[str]:
    """Support terms, plus each adjacent word pair written closed ("field trip" -> "fieldtrip")."""

    words = re.findall(r"[A-Za-z]+", str(text or ""))
    terms = _support_terms(text)
    for first, second in zip(words, words[1:]):
        # Two content words only: a function word ("after the") joined to anything is no compound.
        if _support_terms(first) and _support_terms(second):
            terms |= _support_terms(first + second)
    return terms


def _anaphoric_subject_record_values(question: str, clause: str, spans) -> set[str]:
    """Dates an admitted record about the asked subject carries for an event it names by pronoun.

    The existing contract asks a record clause to share the question's event words. A clause whose
    object is a pronoun ("we finally did it yesterday") takes its event from the discourse, so it
    never can, and the guard withdrew the subject's own dated record. Measured on the original
    comparison: the reply equal to the gold date was replaced by the notice.

    The narrowed law: such a date is supported only when every one of these holds --
      * the question names a person (not the user) and the clause is spoken by that person under
        an explicit speaker label;
      * the clause's object is a pronoun, so its event is the one the turn talks about;
      * the clause resolves to that exact date (written, or today/yesterday from its own statement);
      * the same source turn names a term of the asked event (closed compounds match their spaced
        spelling);
      * the clause is positive and certain.
    The record's statement date counts only where the answer presents it as when the subject spoke.
    A fabricated date, another speaker's date, a non-anaphoric clause about a different action and a
    turn that never names the asked event supply nothing.
    """

    actors = _support_question_actors(question)
    if not actors or "user" in actors:
        return set()
    event_terms = _terms_with_joined_compounds(_support_event_text(question)) - {*actors, "night"}
    if not event_terms:
        return set()
    values: set[str] = set()
    for span in spans:
        if ("record_fields" in span or not span.get("anaphoric") or span.get("speaker") not in actors
                or _support_reference_actor(span["actor"], question) != span.get("speaker")
                or span.get("polarity", "positive") != "positive" or not span["dates"]):
            continue
        if not event_terms & _terms_with_joined_compounds(span.get("source_text", "")):
            continue
        for day in span["dates"]:
            values.update((f"m{day.month}:{day.day}:{day.year}", f"y{day.year}"))
        statement = span["statement"]
        if statement and _statement_is_answer_provenance(clause, statement, speaker=_span_speaker(span), question=question,
                                                         record=span.get("source_text", "")):
            values.update((f"m{statement.month}:{statement.day}:{statement.year}", f"y{statement.year}"))
    return values


def _past_interval_action_kind(body: str) -> str:
    """An endpoint date belongs to one action role, never both by word union."""
    starts = bool(re.search(
        r"\b(?:start(?:ed)?|began|begun|hand(?:ed)?\b[^.!?]*?\b(?:in|to)|"
        r"drop(?:ped)?\b[^.!?]*?\boff|check(?:ed)?\s+in|submit(?:ted)?|deposit(?:ed)?)\b",
        body, re.I,
    ))
    ends = bool(re.search(
        r"\b(?:finish(?:ed)?|end(?:ed)?|complet(?:e|ed)|return(?:ed)?|collect(?:ed)?|"
        r"pick(?:ed)?\b[^.!?]*?\bup|check(?:ed)?\s+out|retriev(?:e|ed)|(?:got|came)\s+back)\b",
        body, re.I,
    ))
    return "start" if starts and not ends else "end" if ends and not starts else ""


def _derived_past_values(question: str, answer: str, spans) -> set[str]:
    """Only one actor/event-bound, unambiguous day interval can authorize math."""
    if not re.search(r"\b(?:how (?:many (?:(?:elapsed|calendar)\s+)?(?:days|weeks|nights)|long)|days? .*between|duration)\b", question, re.I):
        return set()
    related = [span for span in spans if _span_supports_request(span, question, complete_subject=True) and span["dates"] and span.get("polarity", "positive") == "positive"]
    if len({_support_reference_actor(span["actor"], question) for span in related}) != 1:
        return set()
    dates = sorted({day for span in related for day in span["dates"]})
    if not dates or len(dates) > 2 or any(len(span["dates"]) != 1 for span in related):
        return set()
    between = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+)", question, re.I)
    after = re.search(r"\b(?:after|before)\s+(.+)", question, re.I)
    frames = (between.group(1), between.group(2)) if between else (question[:after.start()], after.group(1)) if after else None
    if frames:
        actor = _support_question_actor(question)
        frame_terms = []
        for frame in frames:
            terms = _support_terms(frame) - {actor}
            if between:
                # The leading event identifies each operand: filing a permit
                # and collecting that permit are distinct endpoints.
                for word in re.findall(r"[a-zA-Z'’-]+", frame):
                    head = _support_terms(word) - {actor}
                    if head:
                        terms = head
                        break
            frame_terms.append(terms)
        matches = [{day for span in related if terms & _support_terms(span["body"]) for day in span["dates"]} for terms in frame_terms]
        if any(len(match) != 1 for match in matches):
            return set()
        first, second = (next(iter(match)) for match in matches)
        if between and first > second:
            return set()
        if after and ((after.group(0).lower().startswith("after") and second > first) or (after.group(0).lower().startswith("before") and first > second)):
            return set()
    else:
        starts = {day for span in related if _past_interval_action_kind(span["body"]) == "start" for day in span["dates"]}
        ends = {day for span in related if _past_interval_action_kind(span["body"]) == "end" for day in span["dates"]}
        if len(starts) != 1 or len(ends) != 1 or next(iter(starts)) > next(iter(ends)):
            return set()
    delta = (dates[-1] - dates[0]).days
    values = {f"d{delta:g}days", f"d{delta/7:g}week"}
    trip_span = bool(re.search(r"\b(?:trip|stay|vacation|camping)\b", question, re.I))
    if trip_span or re.search(r"\bhow many nights\b", question, re.I):
        values.add(f"d{delta:g}night")
    if trip_span:
        # Counting days spent on a trip/stay can count both calendar endpoints.
        # An explicit elapsed interval, including an elapsed label in the
        # answer, must never gain the extra calendar day.
        inclusive = re.search(r"\bhow many (?:calendar\s+)?days\b", question, re.I) and not re.search(
            r"\b(?:elapsed|between|after|before|duration|took|take|how long)\b", question + " " + answer, re.I,
        )
        if inclusive:
            values.add(f"d{delta+1:g}days")
    # The explained endpoints belong to this checked interval, not a union
    # of arbitrary dates elsewhere in the prompt.
    for day in dates:
        values.update((f"m{day.month}:{day.day}", f"m{day.month}:{day.day}:{day.year}", f"y{day.year}"))
    return values


def _completed_calendar_months(start: date, end: date) -> int | None:
    """Completed calendar anniversaries; an absent month-end anniversary is unknown.

    Full dates are required by callers. There is no days/30 approximation, no
    inferred year and no choice between clipping versus rolling a month end.
    """
    import calendar
    if start > end or start.day > calendar.monthrange(end.year, end.month)[1]:
        return None
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


def _derived_reference_values(question: str, spans, clock: ReferenceClock | None):
    if not isinstance(clock, ReferenceClock) or clock.source != "runtime_clock":
        return set(), {"reason": "missing_bound_reference_clock"}
    match = re.search(r"\bhow many (?:(?:completed|calendar)\s+){0,2}(days|weeks|months|years) ago\b", question, re.I)
    if not match:
        return set(), {"reason": "not_calendar_ago_request"}
    event_object = re.search(r"\bago\s+(?:did|had|have|has)\s+\S+\s+\w+\s+(.+?)[?.!]*$", question, re.I)
    object_terms = _support_terms(event_object.group(1)) if event_object else set()
    related = [span for span in spans if _span_supports_request(span, question, complete_subject=True)
               and span["dates"] and span.get("polarity", "positive") == "positive"
               and (not object_terms or object_terms <= _support_terms(span["context"]))]
    days = {day for span in related for day in span["dates"]}
    if len(days) != 1 or len({_support_reference_actor(span["actor"], question) for span in related}) != 1:
        return set(), {"reason": "ambiguous_or_missing_event_operand"}
    day = next(iter(days))
    if match.group(1).lower() in ("days", "weeks"):
        # Days and weeks carry no month-end convention: the days elapsed from the one recorded event
        # day to the bound clock day, and the whole weeks they make (rounded down or to the nearest
        # week, the two ways a week count is spoken). "How many days ago" was never derived at all,
        # so a correct "17 days ago (on 12 February)" was withdrawn as unsupported (measured
        # 2026-10-06 on the memory benchmark); a wrong count is still withdrawn.
        elapsed = (clock.day - day).days
        receipt = {"method": "elapsed_days", "event_date": day.isoformat(),
                   "reference_date": clock.day.isoformat(), "source_units": sorted({span["unit"] for span in related})}
        if elapsed < 0:
            return set(), {**receipt, "reason": "event_after_reference_clock"}
        if match.group(1).lower() == "days":
            return {f"d{elapsed}days"}, {**receipt, "quantity": elapsed, "unit": "days", "reason": "derived"}
        weeks = sorted({elapsed // 7, round(elapsed / 7)})
        return {f"d{count}week" for count in weeks}, {**receipt, "quantity": weeks, "unit": "week", "reason": "derived"}
    months = _completed_calendar_months(day, clock.day)
    receipt = {"method": "completed_calendar_anniversaries", "event_date": day.isoformat(),
               "reference_date": clock.day.isoformat(), "source_units": sorted({span["unit"] for span in related})}
    if months is None:
        return set(), {**receipt, "reason": "reversed_or_ambiguous_month_end"}
    explicit_calendar = bool(re.search(r"\b(?:completed|calendar)\s+(?:months|years)\b", question, re.I))
    aligned = day.day == clock.day.day and (match.group(1).lower() != "years" or day.month == clock.day.month)
    if not aligned and not explicit_calendar:
        return set(), {**receipt, "reason": "calendar_convention_required"}
    if match.group(1).lower() == "years":
        return {f"d{months // 12}year"}, {**receipt, "quantity": months // 12, "unit": "year", "reason": "derived"}
    return {f"d{months}month"}, {**receipt, "quantity": months, "unit": "month", "reason": "derived"}


def _without_verified_reference_clause(body: str, clock: ReferenceClock | None) -> tuple[str, bool]:
    if not isinstance(clock, ReferenceClock) or clock.source != "runtime_clock":
        return body, False
    prefix = _REFERENCE_DATE_PREFIX_RE.match(body)
    value = _PAST_DATE_RE.match(body, prefix.end()) if prefix else None
    if value and _support_date(value.group(0)) == clock.day:
        tail = body[value.end():].lstrip(" ,;")
        return re.sub(r"^(?:so|and)\s+", "", tail, flags=re.I), True
    prefix = _REFERENCE_YEAR_PREFIX_RE.match(body)
    value = _PAST_YEAR_RE.match(body, prefix.end()) if prefix else None
    if value and int(value.group(1)) == clock.day.year:
        return body[value.end():].lstrip(" ,;"), True
    # The same reference statement later in the sentence ("About 4 weeks ago: on 1 April, and today
    # is 1 May 2023.", "... (today is 1 May 2023)") is the clock too, when its date is the bound clock
    # day. Read as an event date it withdrew a checked weeks-ago answer (measured 2026-10-06).
    # A yearless statement of the clock day ("today is 20 June") reads in the clock's own year: only
    # the bound clock day itself can match, so no event date is ever donated.
    for lead in _REFERENCE_DATE_INNER_RE.finditer(body):
        value = _PAST_DATE_RE.match(body, lead.end())
        if value and _support_date(value.group(0), clock.day.year) == clock.day:
            end = value.end()
            if lead.group(0).lstrip().startswith("(") and body[end:end + 1] == ")":
                end += 1
            return (body[:lead.start()] + body[end:]).rstrip(" ,;"), True
    return body, False


def _question_echo_or_decline(body: str, question: str) -> bool:
    # An attributed question or explicit uncertainty does not affirm its premise.
    # A later independent sentence is checked independently by the caller.
    if re.match(r"\s*(?:you asked|your question asks|the question (?:was|is)|the request was)\b", body, re.I):
        return bool(_canonical_time_values(body) <= _canonical_time_values(question)
                    and not re.search(r"[,;]\s*(?:and|but|so)\b|\band\s+(?:it|you|I|we|they|he|she)\b", body, re.I))
    decline = bool(re.match(r"\s*I\s+(?:don['’]t know|do not know|can['’]t confirm|cannot confirm|have no evidence|don['’]t have|do not have)\b", body, re.I))
    return decline and not re.search(r"[;,]\s*[a-zA-Z]|\b(?:but|yet|however|and|so)\b", body, re.I)


_ANSWER_ABBREVIATION_RE = re.compile(
    r"(?:\b(?:St|Mt|Ft|Mr|Mrs|Ms|Dr|Prof|Sr|Jr|Gen|Col|Capt|Lt|Sgt|No|vs|etc|approx|Ave|Rd|Blvd|Inc|Ltd|Co)"
    r"|(?<![A-Za-z])[A-Z]|\be\.g|\bi\.e)\.$"
)
#: An abbreviated month written with a period ("Dec. 2023", "Sept. 14"). The period belongs to the
#: date, not the sentence, when a day or year follows: splitting there checked "2023" as a bare
#: year in a clause of its own and lost the month the answer claimed.
_ANSWER_MONTH_ABBREVIATION_RE = re.compile(r"\b(?:jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\.$", re.I)
#: A dotted meridiem ("6:55 p.m.") ends no claim when its clause goes on in lower case or with a
#: number ("at 6:55 p.m. on 3 May"): split there, the time lost the date it was stated with.
_ANSWER_MERIDIEM_ABBREVIATION_RE = re.compile(r"\d\s*[ap]\.\s?m\.$", re.I)


def _answer_sentences(text: str) -> list[tuple[str, str]]:
    """Claim units of an answer -- sentences, and each list line on its own -- with separators.

    A period after an abbreviation ("St. Mary's", "Dr. Lee") does not end a claim: splitting there
    withdrew half a name and shipped "Mary's Church with your family" as the answer's opening.
    Joining unit + separator pairs reproduces the text (leading whitespace aside).
    """
    body = str(text or "")
    units: list[tuple[str, str]] = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])[ \t]+|[ \t]*\n+[ \t]*", body):
        if "\n" not in match.group(0) and _ANSWER_ABBREVIATION_RE.search(body[start:match.start()]):
            continue
        if ("\n" not in match.group(0) and _ANSWER_MONTH_ABBREVIATION_RE.search(body[start:match.start()])
                and body[match.end():match.end() + 1].isdigit()):
            continue
        if ("\n" not in match.group(0) and _ANSWER_MERIDIEM_ABBREVIATION_RE.search(body[start:match.start()])
                and re.match(r"[a-z0-9(]", body[match.end():match.end() + 1])):
            continue
        piece = body[start:match.start()]
        if piece.strip():
            units.append((piece, match.group(0)))
        elif units:
            units[-1] = (units[-1][0], units[-1][1] + piece + match.group(0))
        start = match.end()
    if body[start:].strip():
        units.append((body[start:], ""))
    return units


#: Grammar a removed adjunct leaves behind: emptied emphasis, empty parentheses, or a copula or
#: preposition stranded before punctuation ("would have been, and while ...").
_EDIT_FRAGMENT_RE = re.compile(
    r"(\*\*|__)\s*\1|\(\s*\)|\b(?:been|was|were|is|are|be|on|in|by|from|since|until|of|at|to)\s*(?:[,.;:!?)]|$)",
    re.I,
)

_OPERAND_HEDGE_RE = re.compile(
    r"(?:about|around|roughly|approximately|approx\.?|nearly|almost|close\s+to|just\s+(?:over|under)|over|under|~)"
    r"\s*[*_]{0,2}\s*$",
    re.I,
)
#: "I just launched my website" reports the event at its statement time.
_RECENT_PAST_RE = re.compile(
    r"\bjust\s+(?:[a-z]+[^e\W]ed|got|bought|made|had|took|went|came|did|began|won|met|ran|sent|saw|left|wrote|built|paid|sold|found)\b",
    re.I,
)
_AGO_PHRASE_RE = re.compile(
    rf"\b(?P<article>a|an|one)\s+(?P<aunit>week|month|year)\s+ago\b"
    rf"|(?<![\w.])(?P<num>-?\d+(?:\.\d+)?|(?:{_NUMBER_LEAD_PATTERN})(?:[\s-]+(?:{_NUMBER_WORD_PATTERN}))*)"
    rf"\s+(?P<unit>weeks?|months?|years?)\s+ago\b"
    rf"|\blast\s+(?P<lunit>week|month|year)\b",
    re.I,
)
#: Clause edges inside one claim unit. A comma before a year belongs to its date ("May 3, 2023").
_OPERAND_CLAUSE_BREAK_RE = re.compile(r",(?!\s*\d{4}\b)|;|:|\s[\u2014\u2013-]\s|\s+(?:and|but|while|whereas|then|so)\s+", re.I)
_OPERAND_NON_EVENT_TERMS = frozenset({"user", "ago", "last", "month", "week", "year", "day", "days", "date", "about", "around", "exactly"})


def _operand_clause_terms(text: str, start: int, end: int) -> set[str]:
    """Event terms of the answer clause that cites the operand at text[start:end].

    "you bought the shoes on January 10th and noticed the broken shoelace on January 24th" gives
    each date its own clause. A date fronting its clause ("On March 2nd, you reserved ...") takes the
    clause that follows it.
    """
    left = 0
    for edge in _OPERAND_CLAUSE_BREAK_RE.finditer(text, 0, start):
        left = edge.end()
    edge = _OPERAND_CLAUSE_BREAK_RE.search(text, end)
    right = edge.start() if edge else len(text)

    def terms_of(fragment):
        return _support_terms(_AGO_PHRASE_RE.sub(" ", _PAST_DATE_RE.sub(" ", fragment))) - _OPERAND_NON_EVENT_TERMS

    terms = terms_of(text[left:right])
    if not terms and edge:
        following = _OPERAND_CLAUSE_BREAK_RE.search(text, edge.end())
        terms = terms_of(text[left:following.start() if following else len(text)])
    return terms


def _operand_span_is_the_clause_event(clause_terms: set[str], span, request_spans, same) -> bool:
    """The span dating an operand must describe the answer clause's own event, best among sources.

    Request support alone accepts a source sharing any question word: "I returned the kayak on March
    9th" supports a date for "paddling" through "kayak". As an arithmetic operand the date must come
    from a source sharing the citing clause's event terms, and no request source with a different
    date may match that clause better ("the red survey" cannot take the blue survey's date).
    """
    own = len(clause_terms & _support_terms(span["context"]))
    if own < 1:
        return False
    rival = max((len(clause_terms & _support_terms(other["context"])) for other in request_spans
                 if other["dates"] and not any(same(d) for d in other["dates"])), default=0)
    return own >= rival


def _ago_points(text: str, statement) -> list[tuple[str, float, float]]:
    """Relative points ("3 months ago", "last month") as (unit, quantity, position on that unit's scale).

    The position is anchored on the source unit's own statement date, so two relative points stated
    in different sessions stay comparable. Without a statement date the position is NaN (unanchored).
    """
    points: list[tuple[str, float, float]] = []
    for match in _AGO_PHRASE_RE.finditer(str(text or "")):
        if match.group("lunit") or match.group("aunit"):
            unit, quantity = (match.group("lunit") or match.group("aunit")).lower(), 1.0
        else:
            quantity = _duration_quantity(match.group("num"))
            unit = match.group("unit").lower().rstrip("s")
            if quantity is None:
                continue
        if statement is None:
            points.append((unit, quantity, float("nan")))
            continue
        scale = {"month": statement.year * 12 + statement.month, "year": statement.year,
                 "week": statement.toordinal() / 7}[unit]
        points.append((unit, quantity, scale - quantity))
    return points


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_PATTERN = "|".join(_WEEKDAYS)
#: The quantity of a record-stated offset: digits, number words, an article (one) or "a couple of"
#: (two, its dictionary value). "a few" has no single value and is deliberately absent.
_OFFSET_QUANTITY_PATTERN = (rf"(?:\d+|a\s+couple\s+of|an?|one|(?:{_NUMBER_LEAD_PATTERN})"
                            rf"(?:[\s-]+(?:{_NUMBER_WORD_PATTERN}))*)")
#: Relative time expressions a source states against its own statement time. Each form has one
#: calendar reading; ambiguous forms ("the other day", "recently", "a few days ago", "in 3 days",
#: which is also a duration) are deliberately absent and resolve to nothing.
_RELATIVE_TIME_RE = re.compile(
    rf"\b(?P<rel_day>today|yesterday|tomorrow)\b"
    rf"|\b(?P<wd_dir>last|this\s+past|next|this\s+coming)\s+(?P<weekday>{_WEEKDAY_PATTERN})\b"
    r"|\b(?P<we_dir>last|this\s+past|next|this|this\s+coming)\s+weekend\b"
    r"|\b(?P<earlier>earlier\s+)?(?P<per_dir>last|next|this)\s+(?P<period>week|month|year)\b"
    rf"|(?<![\w.])(?P<ago_num>{_OFFSET_QUANTITY_PATTERN})"
    r"\s+(?P<ago_unit>days?|weeks?|months?|years?)\s+ago\b",
    re.I,
)
_OFFSET_HEDGE_PATTERN = r"(?:(?:about|around|almost|nearly|roughly|over|more\s+than|just\s+over|the\s+(?:past|last))\s+)?"
#: Atelic verbs: their present perfect with "for <N units>" reports a state or an activity that has
#: lasted up to the statement ("lived", "worked", "played", "known", "had"). A telic act's "for <N
#: units>" is the length of what the act arranged -- "booked the cabin for two weeks", "signed a lease
#: for two years", "enrolled in a class for ten weeks", "reserved the hall for three days", "committed
#: to the choir for three years" -- and says nothing about how long ago it happened; read as a state it
#: dated the booking two weeks before the statement. A verb outside this list reads no state.
_DURATIVE_PERFECT_PARTICIPLE = (
    r"(?:been|had|known|kept|owned|lived|resided|worked|run|used|done|dated|played|taught|studied|practi[cs]ed|"
    r"trained|volunteered|coached|mentored|tutored|managed|led|served|stayed|remained|waited|belonged|loved|"
    r"liked|wanted|needed|missed|enjoyed|followed|supported|collected|painted|danced|sung|cooked|baked|"
    r"gardened|written|read|drawn|knitted|sewn|surfed|skated|climbed|fished|hiked|swum|sailed|driven|ridden|"
    r"traveled|travelled|toured|competed|performed|attended|struggled|suffered|battled|fought|worried|"
    r"believed|felt|dreamed|dreamt|hoped|wished|tried|saved|cared|looked|grown|raised|continued)"
)
#: A participle after "been" that still names a state ("been married", "been engaged", "been based in").
_STATIVE_BEEN_PARTICIPLE = (
    r"(?:married|engaged|divorced|separated|widowed|retired|employed|unemployed|based|stationed|involved|"
    r"interested|obsessed|addicted|hooked|stuck|injured|tired|worried|excited|bored|blessed|settled|attached|"
    r"devoted|dedicated)"
)
#: ... while any other passive after "been" reports an act done to the subject ("been offered a contract
#: for two years", "been invited to judge for two days", "been given the keys for a week").
_PASSIVE_BEEN_RE = (
    rf"been\s+(?!{_STATIVE_BEEN_PARTICIPLE}\b)(?:[a-z]+ed|given|taken|told|shown|sent|chosen|written|sold|"
    r"bought|paid|made|held|kept|put|set|got|gotten|lent|brought|taught|caught|left|lost|won|built|found|seen|"
    r"heard|met|known|grown|drawn|thrown|flown|driven|ridden|spoken|broken|stolen|hidden|forgiven|forgotten)\b"
)
#: A state the source reports as lasting up to its statement time: "we've been married for four
#: years", "I've had the van for two years now", "it's been six months since I moved". Its start lies
#: that offset before the statement, exactly as "four years ago" does. Read only for a present
#: perfect of an atelic verb (:data:`_DURATIVE_PERFECT_PARTICIPLE`): a past perfect ("had been ...
#: for") is anchored at another past point, a modal perfect ("will have been") is not a report, an
#: experiential "have been to <place> for <N>" reports a past stay of that length, a passive ("been
#: offered ... for two years") reports an act done, and a negated perfect ("haven't seen her for two
#: years") is no such state.
_PERFECT_STATE_OFFSET_RE = re.compile(
    rf"(?:\b(?:have|has)|['’]ve|\b(?:it|he|she|that|this|there|who)['’]s)\s+(?:(?:just|only|already|now|all|both|also)\s+)*"
    rf"(?!been\s+to\b)(?!{_PASSIVE_BEEN_RE}){_DURATIVE_PERFECT_PARTICIPLE}\b"
    r"(?P<between>[^.!?;]{0,60}?)"
    rf"(?:\bfor\s+{_OFFSET_HEDGE_PATTERN}(?P<num>{_OFFSET_QUANTITY_PATTERN})\s+(?P<unit>days?|weeks?|months?|years?)\b"
    r"(?!\s+and\s+a\s+half)"
    rf"|(?<![\w.])(?P<now_num>{_OFFSET_QUANTITY_PATTERN})\s+(?P<now_unit>days?|weeks?|months?|years?)\s+now\b)"
    rf"|\b(?:it|that)(?:['’]s|\s+has)\s+been\s+{_OFFSET_HEDGE_PATTERN}(?P<since_num>{_OFFSET_QUANTITY_PATTERN})"
    r"\s+(?P<since_unit>days?|weeks?|months?|years?)\s+since\b",
    re.I,
)
#: A clause boundary inside the perfect's predicate: "I've visited it twice and stayed for three days"
#: dates a past stay, not a state reaching the statement.
_PERFECT_STATE_BREAK_RE = re.compile(r"\b(?:and|but|or|then|when|after|before|because|while|so|until|since|where)\b", re.I)
#: A length followed by when it starts or runs ("for two weeks from now", "for a month, starting next
#: week", "for three days in August", "for two days next month") is a planned length, not a state that
#: has lasted up to the statement.
_PERFECT_STATE_FUTURE_TAIL_RE = re.compile(
    rf"\s*,?\s*(?:(?:from|starting|beginning|commencing|until|till|through)\b|(?:next|this\s+coming|coming|upcoming)\s+\w"
    rf"|in\s+(?:{_PAST_MONTH_WORDS})\b|(?:later\s+)?this\s+(?:summer|winter|spring|autumn|fall)\b)",
    re.I,
)
#: A verb form reporting a planned or upcoming event rather than an act completed at the statement.
_FUTURE_FORM_RE = re.compile(
    r"\b(?:will|shall|gonna|going\s+to|about\s+to|plan(?:s|ning)?\s+to|hop(?:e|es|ing)\s+to)\b|['’]ll\b"
    r"|\b(?:am|is|are)\s+(?:\w+ly\s+)?\w+ing\b|['’](?:m|re|s)\s+(?:\w+ly\s+)?\w+ing\b",
    re.I,
)
#: A completed act reported by its subject ("I booked", "we've reserved", "she paid").
_COMPLETED_ACT_RE = re.compile(
    r"\b(?:i|we|he|she|they|you)(?:['’](?:ve|d))?\s+(?:(?:just|finally|already|\w+ly)\s+)?(?:(?:have|has|had)\s+)?"
    r"(?:[a-z]+ed|left|saw|got|bought|did|began|found|went|came|took|met|wrote|sent|put|paid|made|built|"
    r"brought|caught|chose|held|sold|won)\b",
    re.I,
)
#: Past markers with no calendar reading; they still place the act before the statement time.
_VAGUE_PAST_RE = re.compile(
    r"\b(?:recently|lately|the\s+other\s+day|a\s+while\s+(?:ago|back)|some\s+time\s+ago|previously|earlier|"
    r"before|once|used\s+to|back\s+in|ago|since)\b",
    re.I,
)
#: A window of at most this many days may support a day-granular claim; a month or year window
#: supports only a claim at its own (coarser) granularity.
_DAY_GRANULAR_WINDOW_DAYS = 7


def _month_window(year: int, month: int) -> tuple[date, date]:
    import calendar
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _offset_count(raw: str) -> int | None:
    """A record-stated offset's quantity as a positive whole number, else None."""
    text = " ".join(str(raw or "").lower().split())
    quantity = 2.0 if text == "a couple of" else 1.0 if text in {"a", "an", "one"} else _duration_quantity(text)
    if quantity is None or quantity != int(quantity) or quantity <= 0:
        return None
    return int(quantity)


def _offset_window(statement, count: int, unit: str) -> tuple[date, date]:
    """The window a point ``count`` ``unit``s before ``statement`` names, at the offset's granularity:
    a day is that day, a week the seven days around it, a month that calendar month, a year that year."""
    from datetime import timedelta

    if unit == "day":
        start = end = statement - timedelta(days=count)
    elif unit == "week":
        center = statement - timedelta(days=7 * count)
        start, end = center - timedelta(days=3), center + timedelta(days=3)
    elif unit == "month":
        start, end = _month_window(statement.year, statement.month - count)
    else:
        start, end = date(statement.year - count, 1, 1), date(statement.year - count, 12, 31)
    return start, end


def _relative_time_windows(text: str, statement) -> list[dict]:
    """Calendar windows that a source's relative time expressions name, read against its statement time.

    "We tried it last Friday" stated on a Sunday names that Friday; "a trip for next month" names the
    following calendar month; "last week" names the previous Monday-to-Sunday week. Each window keeps
    its direction: a past expression dates its event before the statement, a future one after it.

    A stated offset back from the statement -- "four years ago", or a present-perfect state that has
    lasted "for four years" up to the statement (:data:`_PERFECT_STATE_OFFSET_RE`) -- names the window
    at the offset's granularity (:func:`_offset_window`), and the window carries that ``offset``
    (quantity and unit) so an answer citing the record's own offset can be checked against it.
    """
    from datetime import timedelta

    if statement is None:
        return []
    windows: list[dict] = []
    monday = statement - timedelta(days=statement.weekday())
    for match in _RELATIVE_TIME_RE.finditer(str(text or "")):
        expression = " ".join(match.group(0).lower().split())
        if match.group("rel_day"):
            offset = {"today": 0, "yesterday": -1, "tomorrow": 1}[match.group("rel_day").lower()]
            start = end = statement + timedelta(days=offset)
            direction = "present" if offset == 0 else "past" if offset < 0 else "future"
        elif match.group("weekday"):
            target = _WEEKDAYS.index(match.group("weekday").lower())
            if match.group("wd_dir").lower().split()[0] in {"last", "this"} and "coming" not in match.group("wd_dir").lower():
                back = (statement.weekday() - target) % 7 or 7
                start = end = statement - timedelta(days=back)
                direction = "past"
            else:
                ahead = (target - statement.weekday()) % 7 or 7
                start = end = statement + timedelta(days=ahead)
                direction = "future"
        elif match.group("we_dir"):
            word = " ".join(match.group("we_dir").lower().split())
            if word in {"last", "this past"}:
                sunday = statement - timedelta(days=(statement.weekday() - 6) % 7 or 7)
                start, end, direction = sunday - timedelta(days=1), sunday, "past"
            elif word == "this":
                start, end, direction = monday + timedelta(days=5), monday + timedelta(days=6), "present"
            else:
                saturday = statement + timedelta(days=(5 - statement.weekday()) % 7 or 7)
                start, end, direction = saturday, saturday + timedelta(days=1), "future"
        elif match.group("period"):
            period, word = match.group("period").lower(), match.group("per_dir").lower()
            step = {"last": -1, "this": 0, "next": 1}[word]
            if period == "week":
                start = monday + timedelta(days=7 * step)
                end = start + timedelta(days=6)
            elif period == "month":
                start, end = _month_window(statement.year, statement.month + step)
            else:
                start, end = date(statement.year + step, 1, 1), date(statement.year + step, 12, 31)
            direction = "past" if step < 0 else "future" if step > 0 else "present"
            if match.group("earlier"):
                if step != 0:
                    continue
                end, direction = statement, "past"
        else:
            count = _offset_count(match.group("ago_num"))
            if count is None:
                continue
            unit = match.group("ago_unit").lower().rstrip("s")
            start, end = _offset_window(statement, count, unit)
            windows.append({"expression": expression, "start": start, "end": end, "direction": "past",
                            "offset": {"quantity": count, "unit": unit}})
            continue
        windows.append({"expression": expression, "start": start, "end": end, "direction": direction})
    body = str(text or "")
    for match in _PERFECT_STATE_OFFSET_RE.finditer(body):
        if (re.search(r"\b(?:will|would|shall|should|could|might|must|may)\s+$", body[:match.start()], re.I)
                or _PERFECT_STATE_BREAK_RE.search(match.group("between") or "")
                or _PERFECT_STATE_FUTURE_TAIL_RE.match(body, match.end())):
            continue
        count = _offset_count(match.group("num") or match.group("now_num") or match.group("since_num"))
        if count is None:
            continue
        unit = (match.group("unit") or match.group("now_unit") or match.group("since_unit")).lower().rstrip("s")
        start, end = _offset_window(statement, count, unit)
        windows.append({"expression": " ".join(match.group(0).lower().split()), "start": start, "end": end,
                        "direction": "past", "offset": {"quantity": count, "unit": unit}, "form": "perfect_state"})
    return windows


def _window_values(window: dict) -> set[str]:
    """Claim values a window contains at the claim's granularity."""
    from datetime import timedelta

    start, end = window["start"], window["end"]
    values = {f"y{year}" for year in range(start.year, end.year + 1)}
    if (end - start).days < _DAY_GRANULAR_WINDOW_DAYS:
        day = start
        while day <= end:
            values.add(f"m{day.month}:{day.day}:{day.year}")
            day += timedelta(days=1)
    return values


#: A relative time anchored to a written date, read off the text that ends where the date starts:
#: "the weekend before", "the day before", "the Wednesday before", "two weeks before", "the week
#: after", "the night before". The anchor date is the reference point, not the event's date.
_ANCHORED_RELATIVE_PREFIX_RE = re.compile(
    rf"\b(?:(?:the|that)\s+(?P<unit>day|night|evening|morning|afternoon|weekend|week|month|year|{_WEEKDAY_PATTERN})"
    rf"|(?P<num>\d+|a|an|one|(?:{_NUMBER_LEAD_PATTERN})(?:[\s-]+(?:{_NUMBER_WORD_PATTERN}))*)"
    r"\s+(?P<nunit>days?|weeks?|months?|years?))"
    r"\s+(?P<dir>before|after|prior\s+to|preceding|following)\s+(?:(?:on|of)\s+)?$",
    re.I,
)


def _anchored_relative_expression(prefix: str) -> str:
    """The source-grammar relative expression an anchored phrase restates, or "".

    "the weekend before <D>" is "last weekend" said on D; "the day after <D>" is "tomorrow" said on
    D; "two weeks before <D>" is "two weeks ago" said on D. A numeric "after" has no single reading
    in the source grammar and restates nothing.
    """
    match = _ANCHORED_RELATIVE_PREFIX_RE.search(prefix)
    if match is None:
        return ""
    before = match.group("dir").lower().split()[0] in {"before", "prior", "preceding"}
    unit = (match.group("unit") or "").lower()
    if unit in {"day", "night", "evening", "morning", "afternoon"}:
        return "yesterday" if before else "tomorrow"
    if unit:
        return f"{'last' if before else 'next'} {unit}"
    if not before:
        return ""
    return f"{match.group('num')} {match.group('nunit')} ago"


def _relative_anchor_values(clause: str, statement, windows) -> tuple[set[str], list[dict]]:
    """A date the clause uses as the anchor of a relative time the source states against it.

    A same-subject source stated on S says "last weekend"; the answer "the weekend before S" keeps
    that relative time and names S as its anchor, not as the event's date. S is supported in that
    clause when the anchored phrase, read against S, names a window that meets one of the source's
    own relative windows in the same direction. A date that is not S, or an anchored phrase whose
    window misses the source's ("two months before S" for "last weekend"), is supported by nothing
    here.
    """
    values: set[str] = set()
    receipt: list[dict] = []
    if statement is None or not windows:
        return values, receipt
    for claim in _calendar_claim_spans(clause):
        if claim["granularity"] != "day":
            continue
        if (claim["month"], claim["day"], claim["year"] or statement.year) != (statement.month, statement.day, statement.year):
            continue
        expression = _anchored_relative_expression(clause[:claim["span"][0]])
        if not expression:
            continue
        for anchored in _relative_time_windows(expression, statement):
            meets = next((window for window in windows if window["direction"] == anchored["direction"]
                          and anchored["start"] <= window["end"] and window["start"] <= anchored["end"]), None)
            if meets is None:
                continue
            found = set(claim["values"]) | {f"y{statement.year}"}
            values |= found
            receipt.append({"rule": "relative_anchor", "statement": statement.isoformat(),
                            "anchored_expression": expression, "source_expression": meets["expression"],
                            "window": [anchored["start"].isoformat(), anchored["end"].isoformat()],
                            "values": sorted(found)})
            break
    return values, receipt


def _asked_event_terms(question: str) -> set[str]:
    """Content terms of the interrogative sentence(s) of a turn; the whole turn when none is marked."""
    asked = [part for part in re.split(r"(?<=[?])\s+|\n+", str(question or "")) if part.rstrip().endswith("?")]
    return _support_terms(" ".join(asked) if asked else str(question or ""))


#: The user's own request to date the event by when it was said: "use the date of the
#: conversation", "answer with an approximate date", "roughly when", "based on when it was said".
_STATEMENT_TIME_LICENSE_RE = re.compile(
    r"\b(?:use|using|from|by|with|based\s+on|according\s+to|relative\s+to)\s+(?:the\s+)?(?:date|day|time)s?\s+"
    r"(?:of|on)\s+(?:the\s+|each\s+|that\s+)?(?:conversation|chat|session|record|message|entry|note|statement)s?\b"
    r"|\bapproximate(?:ly)?\s+(?:date|day|time|when)\b"
    r"|\b(?:roughly|approximately)\s+when\b"
    r"|\b(?:based\s+on|from|by)\s+when\s+(?:it|this|that|they|he|she|i|you|we)\s+(?:(?:was|were)\s+)?"
    r"(?:said|mentioned|stated|written|wrote|recorded|posted|shared|told)\b",
    re.I,
)
_LICENSE_NEGATION_RE = re.compile(r"\b(?:not|never|without|instead\s+of|rather\s+than)\b|n['’]t\b", re.I)
#: An instruction after the license phrase in its own sentence that takes it back ("..., ignore
#: that", "... but give me the exact day", "... give the real day instead").
_LICENSE_COUNTERMAND_RE = re.compile(
    r"\b(?:ignor(?:e|es|ed|ing)|disregard(?:s|ed|ing)?|exact(?:ly)?|instead(?!\s+of\b))\b", re.I)
#: ... unless the word names the alternative the request turns down ("an approximate date, not the
#: exact day", "... instead of the exact one", "... rather than exactly").
_LICENSE_REJECTED_ALTERNATIVE_RE = re.compile(
    r"\b(?:instead\s+of|rather\s+than|not|no|than|over)\s+(?:[\w'’-]+\s+){0,3}$", re.I)
#: The end of the sentence a license phrase sits in: a sentence mark that is not part of an
#: ellipsis ("...", "…" trail on inside the sentence), or a line break.
_LICENSE_SENTENCE_END_RE = re.compile(r"(?<!\.)[.!?](?!\.)|\n")
_QUOTE_PAIRS = {'"': '"', "“": "”", "„": "“", "«": "»", "'": "'", "‘": "’"}


def _quoted_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges of the text that sit inside quotation marks.

    Double quotes (straight or curly) pair in order; an unclosed one runs to the end of the text.
    A single quote (straight or curly) opens only at a word start (after the text start, a space or
    an opening bracket/dash) and closes only at a word end, so an apostrophe inside a word
    ("don't", "Dave's") is never a quotation mark; an unclosed single quote quotes nothing.
    """
    ranges: list[tuple[int, int]] = []
    at = 0
    while at < len(text):
        char = text[at]
        if char not in _QUOTE_PAIRS:
            at += 1
            continue
        closer = _QUOTE_PAIRS[char]
        if char in "'‘":
            before = text[at - 1] if at else " "
            after = text[at + 1] if at + 1 < len(text) else " "
            if not (before.isspace() or before in "([{—–-:;,") or after.isspace():
                at += 1
                continue
            end = -1
            for found in re.finditer(re.escape(closer) if closer != "'" else "['’]", text[at + 1:]):
                close_at = at + 1 + found.start()
                following = text[close_at + 1] if close_at + 1 < len(text) else " "
                if not text[close_at - 1].isspace() and not following.isalnum():
                    end = close_at
                    break
            if end < 0:
                at += 1
                continue
        else:
            end = text.find(closer, at + 1)
            if end < 0:
                end = len(text)
        ranges.append((at, end + 1))
        at = end + 1
    return ranges


def _request_licenses_statement_time(question: str) -> bool:
    """The user's request asks for the event's date read from when it was said.

    The license is the request's own grammar, never the evidence's; a negated request ("do not use
    the date of the conversation") licenses nothing. Nor does a license phrase the user quotes (a
    friend's or an old template's words, "... said 'use the date of the conversation'") or one the
    same sentence takes back after it ("..., ignore that", "... but give me the exact day").
    """
    text = str(question or "")
    quoted = _quoted_ranges(text)
    for match in _STATEMENT_TIME_LICENSE_RE.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in quoted):
            continue
        sentence_start = max((m.end() for m in re.finditer(r"[.!?\n]", text[:match.start()])), default=0)
        end = _LICENSE_SENTENCE_END_RE.search(text, match.end())
        rest = text[match.end():end.start() if end else len(text)]
        if any(not _LICENSE_REJECTED_ALTERNATIVE_RE.search(rest[:word.start()])
               for word in _LICENSE_COUNTERMAND_RE.finditer(rest)):
            continue
        if not _LICENSE_NEGATION_RE.search(text[sentence_start:match.start()]):
            return True
    return False


#: Words a dated answer uses to place a value against a record (time grammar, approximation and
#: report vocabulary). A licensed clause made only of these, the asked event and the unit's own
#: words names no event other than the asked one.
_LICENSED_CLAUSE_GRAMMAR = (
    "date dates day days week weeks weekend month months year years time today yesterday tomorrow "
    "last next this past previous prior following same earlier later early late mid ago before after "
    "around about roughly approximately approximate estimate circa nearly almost just shortly "
    # Epistemic hedges qualify the placement; they name no event ("so they likely started ...").
    "likely probably possibly presumably perhaps apparently "
    # The same vocabulary as typed in a hurry ("approx 2020, 4 yrs b4 her msg", "i.e.", "2020-ish").
    "approx abt ca b4 msg msgs convo convos yr yrs mo mos wk wks hr hrs min mins ie eg e ish "
    "give take few couple plus minus "
    "monday tuesday wednesday thursday friday saturday sunday january february march april may june "
    "july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec "
    "conversation chat session record message entry note statement said mentioned stated told wrote "
    "shared posted reported talked spoke based recalled recall remembered remember noted described explained recounted"
)


@functools.lru_cache(maxsize=1)
def _licensed_clause_grammar_terms() -> frozenset[str]:
    return frozenset(_support_terms(_LICENSED_CLAUSE_GRAMMAR) | set(_LICENSED_CLAUSE_GRAMMAR.split()))


def _head_terms(terms) -> set[str]:
    """Terms read at their head word.

    :func:`_support_terms` keeps a contraction or a possessive as one token ("that's" -> "that'",
    "day's" -> "day'", "they'd", "Calvin's" -> "calvin'"). The head word is the term: "that" and
    "they" are stopwords and vanish, "day" is time grammar, "calvin" is the name.
    """
    result: set[str] = set()
    for term in terms:
        head = re.split(r"['’]", term)[0]
        result |= _support_terms(head) if head != term else {term}
    return result


def _asked_action_terms(question: str) -> set[str]:
    """The verb of a direct time request ("When did <Name> visit the fair?"): an action, never the
    asked event's object. A clause bound to a record only through this verb may be about another
    object ("visited the opera"); one that names the asked object is about the asked event."""
    terms: set[str] = set()
    for part in re.split(r"(?<=[?])\s+|\n+", str(question or "")):
        direct = _SUPPORT_DIRECT_TIME_RE.search(part)
        if direct:
            terms |= _support_terms(direct.group(1))
    return terms


#: Irregular past forms are their base verb when a clause is compared with a request or a record:
#: "they got together" repeats "get together", "she took the class" repeats "take the class".
_IRREGULAR_BASE_FORMS = {
    "got": "get", "gotten": "get", "took": "take", "taken": "take", "went": "go", "gone": "go", "met": "meet",
    "began": "begin", "begun": "begin", "made": "make", "bought": "buy", "saw": "see", "seen": "see", "came": "come",
    "left": "leave", "found": "find", "gave": "give", "given": "give", "held": "hold", "ran": "run", "sold": "sell",
    "told": "tell", "wrote": "write", "written": "write", "flew": "fly", "flown": "fly", "rode": "ride",
    "drove": "drive", "swam": "swim", "sang": "sing", "won": "win", "lost": "lose", "brought": "bring",
    "kept": "keep", "spent": "spend", "built": "build", "sent": "send", "paid": "pay", "taught": "teach",
    "caught": "catch", "chose": "choose", "ate": "eat", "drank": "drink", "grew": "grow", "knew": "know",
    "sat": "sit", "stood": "stand", "threw": "throw", "wore": "wear",
}
#: A year written with a suffix ("2020-ish", "the 2010s") is a value, not a word of the clause.
_SUFFIXED_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}(?:-?ish|['’]?s)\b", re.I)


@functools.lru_cache(maxsize=1)
def _irregular_base_terms() -> dict[str, frozenset[str]]:
    """Each irregular past form, as the term :func:`_support_terms` reads ("made" -> "mad"), mapped to
    its base verb's terms (empty for a function verb such as "get")."""
    table: dict[str, frozenset[str]] = {}
    for form, base in _IRREGULAR_BASE_FORMS.items():
        for term in {form, *_support_terms(form)}:
            table[term] = frozenset(_support_terms(base))
    return table


def _base_form_terms(terms) -> set[str]:
    """Terms with each irregular past form read as its base verb's term (none for a function verb)."""
    table = _irregular_base_terms()
    result: set[str] = set()
    for term in terms:
        result |= table.get(term, {term})
    return result


def _licensed_clause_names_only_the_asked_event(clause: str, asked: set[str], unit_terms: set[str], actors, speakers=()) -> bool:
    """The clause names nothing beyond the asked event, the unit's own words, time/report grammar and
    the conversation's people (the asked subjects and the records' own speaker labels: "Dave
    mentioned it in that day's conversation with Calvin" reports who was spoken to, not another
    event). Terms are read at their head word, so a contraction ("that's") or a possessive ("day's")
    is the word it is built on, and at their base verb ("got together" is "get together"); a year
    with a suffix ("2020-ish") is a value, not a word."""
    grammar = _licensed_clause_grammar_terms()
    names = {term for actor in (*actors, *speakers) if actor for term in (actor, *_support_terms(actor))}
    clause_terms = _base_form_terms(_head_terms(_support_terms(_SUFFIXED_YEAR_RE.sub(" ", str(clause or "")))))
    return not (clause_terms - grammar - _base_form_terms(_head_terms(asked)) - _base_form_terms(_head_terms(unit_terms))
                - _head_terms(names))


#: The evidence span the user's own request for an approximate date opens: a claimed calendar value
#: is supported when it lies within this many days of the statement time of ANY record the reader saw
#: (a day-level claim) ...
_LICENSED_EVIDENCE_SPAN_DAYS = 31
#: ... within this many calendar months of one (a month-level claim, "Around May 2023", checked as
#: its month, never as its year) ...
_LICENSED_EVIDENCE_SPAN_MONTHS = 1
#: ... or in a record's statement year or this many years before it (a year-level claim).
_LICENSED_EVIDENCE_SPAN_YEARS_BACK = 1

#: A month name with a year and no day, in any of its written forms: "May 2023", "May of 2023",
#: "September, 2023", "Dec. 2023", "Sept.2023", "Oct-2023".
_CLAIM_MONTH_YEAR_RE = re.compile(rf"\b({_PAST_MONTH_WORDS})(?![a-z])[.,\s-]*(?:of\s+)?(19\d{{2}}|20\d{{2}})(?!\d)", re.I)
#: A month-year written in numerals: "2023-12", "2023/09" (year first) or "12/2023", "9/2023",
#: "09-2023" (month first). Neither side may continue into a fuller date ("2023-12-05",
#: "12/05/2023", "2023/09/15"); those are day claims or no calendar claim at all.
_CLAIM_NUMERIC_MONTH_YEAR_RE = re.compile(
    r"(?<![\d/.-])(?:(?P<year_first>19\d{2}|20\d{2})[-/](?P<month_after>\d{1,2})"
    r"|(?P<month_first>\d{1,2})[-/](?P<year_after>19\d{2}|20\d{2}))(?![\d/]|[-.]\d)"
)


def _calendar_claims(clause: str) -> list[dict]:
    """The clause's calendar claims at their own granularity.

    A dated day ("16 May 2023", "May 16", "2023-05-16") is a day claim; a month with a year and no
    day ("Around May 2023", "May of 2023", "Dec. 2023", "2023-12", "12/2023") is a month claim; any
    other year ("in 2022") is a year claim. Each carries the canonical values
    :func:`_canonical_time_values` reads from it.
    """
    return [{key: value for key, value in claim.items() if key != "span"} for claim in _calendar_claim_spans(clause)]


def _calendar_claim_spans(clause: str) -> list[dict]:
    """:func:`_calendar_claims`, each with the character ``span`` it was read from (same order)."""
    claims: list[dict] = []
    taken: list[tuple[int, int]] = []
    for match in _PAST_DATE_RE.finditer(clause):
        values = _canonical_time_values(match.group(0))
        day_value = next((value for value in values if value.startswith("m")), "")
        if not day_value:
            continue
        parts = day_value[1:].split(":")
        claims.append({"granularity": "day", "month": int(parts[0]), "day": int(parts[1]),
                       "year": int(parts[2]) if len(parts) == 3 else None, "claimed_value": day_value,
                       "values": sorted(values), "span": match.span()})
        taken.append(match.span())
    for match in _CLAIM_MONTH_YEAR_RE.finditer(clause):
        if any(start < match.end() and match.start() < end for start, end in taken):
            continue
        month, year = _MONTH_NUMBERS.get(match.group(1)[:3].lower(), 0), int(match.group(2))
        if month:
            claims.append({"granularity": "month", "month": month, "year": year,
                           "claimed_value": f"y{year}", "values": [f"y{year}"], "span": match.span()})
            taken.append(match.span())
    for match in _CLAIM_NUMERIC_MONTH_YEAR_RE.finditer(clause):
        if any(start < match.end() and match.start() < end for start, end in taken):
            continue
        month = int(match.group("month_after") or match.group("month_first"))
        year = int(match.group("year_first") or match.group("year_after"))
        if 1 <= month <= 12:
            claims.append({"granularity": "month", "month": month, "year": year,
                           "claimed_value": f"y{year}", "values": [f"y{year}"], "span": match.span()})
            taken.append(match.span())
    for match in _PAST_YEAR_RE.finditer(clause):
        if any(start <= match.start() and match.end() <= end for start, end in taken):
            continue
        year = int(match.group(1))
        claims.append({"granularity": "year", "year": year, "claimed_value": f"y{year}", "values": [f"y{year}"],
                       "span": match.span()})
    return claims


def _evidence_span_match(claim: dict, statements) -> dict | None:
    """The record statement time nearest to a calendar claim, when the claim lies in its evidence span.

    ``distance_days`` is the distance from that statement time to the nearest day of the claimed
    unit (0 inside it).
    """
    import calendar

    best = None
    for statement in statements:
        if claim["granularity"] == "day":
            years = [claim["year"]] if claim["year"] else [statement.year - 1, statement.year, statement.year + 1]
            for year in years:
                try:
                    distance = abs((date(year, claim["month"], claim["day"]) - statement).days)
                except ValueError:
                    continue
                if distance <= _LICENSED_EVIDENCE_SPAN_DAYS and (best is None or distance < best[0]):
                    best = (distance, statement)
            continue
        if claim["granularity"] == "month":
            months_apart = abs((claim["year"] * 12 + claim["month"]) - (statement.year * 12 + statement.month))
            if months_apart > _LICENSED_EVIDENCE_SPAN_MONTHS:
                continue
            first = date(claim["year"], claim["month"], 1)
            last = date(claim["year"], claim["month"], calendar.monthrange(claim["year"], claim["month"])[1])
        else:
            if not statement.year - _LICENSED_EVIDENCE_SPAN_YEARS_BACK <= claim["year"] <= statement.year:
                continue
            first, last = date(claim["year"], 1, 1), date(claim["year"], 12, 31)
        distance = 0 if first <= statement <= last else min(abs((statement - first).days), abs((statement - last).days))
        if best is None or distance < best[0]:
            best = (distance, statement)
    if best is None:
        return None
    return {"rule": "request_licensed_evidence_span", "claimed_value": claim["claimed_value"],
            "granularity": claim["granularity"], "nearest_statement": best[1].isoformat(),
            "distance_days": best[0], "values": claim["values"]}


def _licensed_evidence_span(clause: str, statements, supported: set[str], core: set[str]):
    """Request-licensed support of a clause's calendar claims by the evidence span.

    The user's own request asked for an approximate date read from the conversation date. A claimed
    calendar value is then supported when it lies in the evidence span of the statement time of any
    record the reader saw (:func:`_evidence_span_match`); no actor, event or clause binding is
    required. A month-level claim is checked as its month: its year is supported only by that month
    check or by support the guard reads without the license (``core``), never by a licensed year.

    Returns (values to add, year values to withdraw, receipt entries).
    """
    claims = _calendar_claims(clause)
    accepted: set[str] = set()
    receipt: list[dict] = []
    occurrences: dict[str, list[bool]] = {}
    for claim in claims:
        match = _evidence_span_match(claim, statements)
        if claim["granularity"] == "day":
            value = claim["claimed_value"]
            if match is not None and value not in supported:
                accepted.add(value)
                receipt.append(match)
            if claim["year"]:
                year_value = f"y{claim['year']}"
                occurrences.setdefault(year_value, []).append(match is not None or year_value in supported)
            continue
        value = claim["claimed_value"]
        base = core if claim["granularity"] == "month" else supported
        ok = value in base or match is not None
        occurrences.setdefault(value, []).append(ok)
        if match is not None and value not in base:
            receipt.append(match)
    withdrawn: set[str] = set()
    for value, oks in occurrences.items():
        if all(oks):
            accepted.add(value)
        elif value in supported:
            withdrawn.add(value)
    return accepted - withdrawn, withdrawn, receipt


def _statement_time_values(question: str, clause: str, answer_actor: str, spans) -> tuple[set[str], list[dict]]:
    """Values a same-subject source supports through its own statement time.

    A source unit about the asked subject (the request-support contract: actor and event) that
    carries a statement time S supports a claimed date or year when
      (a) the value equals S at the claim's granularity and the unit anchors its own act at S: it
          reports a completed arrangement against a forward window ("I booked a trip for next
          month", "we reserved the hall for next Friday"). A bare past-tense report ("I had the
          guitar serviced", "I recently repainted") dates nothing at S -- the act may lie at any
          earlier time -- and neither does a unit that dates its act before S ("yesterday", "last
          week", an earlier written date, "recently"), a plan in a future form ("I'm visiting next
          month", "we will go tomorrow"), or a need with no completed act ("I just need ... to
          finish"); or
      (b) one of the unit's own relative time expressions, read against S, names a window that
          contains the value at its granularity ("last Friday" a day; "next month" a month, so
          only the year of a month-level answer is a checked value); or
      (c) the user's own request asks for the event's date read from when it was said ("use the
          date of the conversation", "an approximate date", "roughly when"): then the value equals
          S. This is the requested approximation, recorded as request_licensed_statement_time.
          The licensed clause must name nothing beyond the asked event, the unit's own words, the
          conversation's people and time/report grammar (a verb shared with the unit cannot carry
          another object's date), unless it names the asked event's own object itself ("that's when
          she first mentioned her new kayak"): then its further words describe the asked event. It
          may be a bare dated answer that does not repeat the asked event, for (b) too.
    The unit must be spoken by an asked subject (the question's actor or each named conjunct of its
    compound subject, else the clause's own actor), and the asked event must be named by both the
    clause and the unit -- a term shared by the question, the claim clause and the unit. Uncertain
    and negated units support nothing. Without the request, (c) does not apply. Under the request the
    wider evidence span (:func:`_licensed_evidence_span`) is applied by the caller, with no binding.
    """
    references = _support_question_actors(question) or ((answer_actor,) if answer_actor else ())
    if not references:
        return set(), []
    licensed = _request_licenses_statement_time(question)
    asked = _asked_event_terms(question)
    clause_terms = _support_terms(clause)
    action_terms = _asked_action_terms(question) if licensed else set()
    # The conversation's people are its speaker labels ("Dave:", a bound record prefix), never a
    # clause subject the evidence happens to capitalise ("Opera tickets are expensive" names no person).
    speakers = {span["speaker"] for span in spans if span.get("speaker") and span["speaker"] not in {"user", "assistant"}} if licensed else set()
    values: set[str] = set()
    receipt: list[dict] = []
    for span in spans:
        statement = span.get("statement")
        if ("record_fields" in span or statement is None or span.get("uncertain")
                or span.get("polarity", "positive") != "positive"
                or _support_reference_actor(span["actor"], question) not in references
                or not _span_supports_request(span, question, clause)):
            continue
        actor_terms = {*references, span["actor"], *(_support_terms(" ".join(references)))}
        unit_terms = _support_terms(span["context"])
        shared = (asked & clause_terms & unit_terms) - actor_terms - _OPERAND_NON_EVENT_TERMS
        # Under the request's license the clause must name nothing beyond the asked event, this
        # unit's own words and time/report grammar; a shared verb cannot carry another object's date.
        unit_event = (asked & unit_terms) - actor_terms - _OPERAND_NON_EVENT_TERMS
        # A bare licensed clause takes the asked event from the unit, which must name it. A clause
        # that names the asked object itself (not only the request's verb) is about the asked event.
        object_anchored = bool(shared - action_terms)
        licensed_here = licensed and (
            _licensed_clause_names_only_the_asked_event(clause, asked, unit_terms, references, speakers) or object_anchored)
        licensed_bare = licensed_here and not shared
        if licensed_bare:
            shared = unit_event
        if not shared:
            continue
        windows = span.get("windows") or []
        # A present-perfect state's start dates the state, not the act the unit reports at S.
        dated_before = (any(window["direction"] == "past" and window.get("form") != "perfect_state" for window in windows)
                        or any(day < statement for day in span["dates"])
                        or bool(_VAGUE_PAST_RE.search(span["body"])))
        forward = any(window["direction"] == "future" for window in windows) or any(day > statement for day in span["dates"])
        anchored = (forward and not dated_before and bool(_COMPLETED_ACT_RE.search(span["body"]))
                    and not _FUTURE_FORM_RE.search(span["body"]))
        if anchored or licensed_here:
            found = {f"m{statement.month}:{statement.day}:{statement.year}", f"y{statement.year}"}
            values |= found
            receipt.append({"source_unit": span["unit"],
                            "rule": "statement_time" if anchored else "request_licensed_statement_time",
                            "statement": statement.isoformat(), "shared_event_terms": sorted(shared),
                            "bare_dated_answer": licensed_bare, "values": sorted(found)})
        clause_claims = _calendar_claims(clause)
        for window in windows:
            # A stated offset ("two months ago", "for two months") is checked at its own granularity:
            # each claim of the clause its window holds (a month offset checks the month, not only its
            # year). Other relative windows keep their claim-granularity values.
            found = (set().union(*(claim["values"] for claim in clause_claims if _offset_window_holds_claim(window, claim)))
                     if window.get("offset") else _window_values(window))
            values |= found
            receipt.append({"source_unit": span["unit"], "rule": "relative_window", "statement": statement.isoformat(),
                            "expression": window["expression"], "direction": window["direction"],
                            "window": [window["start"].isoformat(), window["end"].isoformat()],
                            "shared_event_terms": sorted(shared), "values": sorted(found)})
        # (d) the clause keeps the unit's relative time and names S as its anchor ("the weekend
        # before S" for "last weekend" said on S): S is the anchor there, not the event's date.
        anchor_values, anchor_receipt = _relative_anchor_values(clause, statement, windows)
        values |= anchor_values
        receipt.extend({"source_unit": span["unit"], "shared_event_terms": sorted(shared), **entry}
                       for entry in anchor_receipt)
    return values, receipt


_OFFSET_UNITS = ("day", "week", "month", "year")


def _clause_cited_offsets(clause: str) -> set[tuple[int, str]]:
    """The offsets a clause states, as (whole quantity, unit): "five years", "2 months", "a year (ago)",
    "a couple of weeks"."""
    cited: set[tuple[int, str]] = set()
    for match in _PAST_DURATION_RE.finditer(clause):
        quantity = _duration_quantity(match.group(1))
        unit = _UNIT_NORMALIZER.get(match.group(2).lower(), "").replace("days", "day")
        if quantity is not None and quantity == int(quantity) and quantity > 0 and unit in _OFFSET_UNITS:
            cited.add((int(quantity), unit))
    for match in _RECORD_ARTICLE_DURATION_RE.finditer(clause):
        unit = _UNIT_NORMALIZER.get(match.group("unit").lower(), "").replace("days", "day")
        if unit in _OFFSET_UNITS:
            cited.add((2 if match.group("couple") else 1, unit))
    return cited


def _claim_is_the_statement_time(claim: dict, statement, unit: str) -> bool:
    """The calendar claim names the record's statement time S, at a granularity the offset can use:
    the day S; S's month for a month or year offset; S's year for a year offset."""
    if claim["granularity"] == "day":
        return (claim["month"], claim["day"]) == (statement.month, statement.day) and claim["year"] in (None, statement.year)
    if claim["granularity"] == "month":
        return unit in {"month", "year"} and (claim["month"], claim["year"]) == (statement.month, statement.year)
    return unit == "year" and claim["year"] == statement.year


def _offset_checks_claim(unit: str, claim: dict) -> bool:
    """An offset in ``unit`` can check the claim: a day only by a day or week offset; a month or a year
    by any offset (a year offset checks a month claim's year, as its window always has)."""
    return claim["granularity"] != "day" or unit in {"day", "week"}


def _offset_window_holds_claim(window: dict, claim: dict) -> bool:
    """The claim lies in the offset's window AT THE OFFSET'S GRANULARITY: a year offset checks a year
    (of a year claim, or of a month claim -- its values are only that year, exactly what the window
    supported before); a month offset checks the month; a week or day offset the day. A day claim from
    a month or year offset ("14 March 2019" from "for five years") is not checked by it."""
    unit, start, end = window["offset"]["unit"], window["start"], window["end"]
    if claim["granularity"] == "day":
        year = claim["year"] or (start.year if start.year == end.year else None)
        if unit not in {"day", "week"} or year is None:
            return False
        try:
            day = date(year, claim["month"], claim["day"])
        except ValueError:
            return False
        return start <= day <= end
    if claim["granularity"] == "month" and unit != "year":
        first, last = _month_window(claim["year"], claim["month"])
        return first <= end and start <= last
    return start.year <= claim["year"] <= end.year


def _prior_question(spans) -> str:
    """The conversation's latest earlier question that names an asked subject: a user turn with no
    statement time and no speaker label that asks something ("- user said: When did <Name> ...?").
    A follow-up naming nobody ("roughly when was that?") asks about that subject and that event."""
    for span in reversed(list(spans or [])):
        text = str(span.get("source_text") or span.get("body") or "")
        if (span.get("role") == "user" and span.get("statement") is None and not span.get("speaker")
                and "record_fields" not in span and "?" in text and _support_question_actors(text)):
            return text
    return ""


#: The asked event places its subject at a place ("visit Chile", "live in Morocco", "travel to Peru",
#: "<Name> in Chile - roughly when?").
_ASKED_PLACE_EVENT_RE = re.compile(
    r"\b(?:visit\w*|tour\w*|(?:travel\w*|trip|went|go|goes|going|gone|mov(?:e|ed|es|ing)|fl(?:y|ew|ies|ying|own)|"
    r"return\w*|relocat\w*)\s+to|(?:liv(?:e|ed|es|ing)|stay\w*|settl\w*|vacation\w*|holiday\w*|be|been|was|were)\s+in)\b"
    rf"|\bin\s+(?!(?:{_PAST_MONTH_WORDS}|{_WEEKDAY_PATTERN})\b)(?-i:[A-Z][\w'’-]+)",
    re.I,
)
#: A record placing its speaker at a named place ("I was in Valparaíso", "we lived in Fez", "I
#: crewed at the Cowes regatta", "I visited Oaxaca"). A record naming no place ("I adopted my cat
#: Pistache") places nobody; a month, weekday or holiday after the preposition is a time, not a place.
_RECORD_PLACE_RE = re.compile(
    r"\b(?:in|at|to|across|through|around|visit(?:ed|ing)?|toured)\s+(?:the\s+)?"
    rf"(?!(?:{_PAST_MONTH_WORDS}|{_WEEKDAY_PATTERN}|christmas|easter|thanksgiving|halloween|hanukkah|diwali|"
    r"ramadan|eid|new\s+year)\b)(?-i:[A-Z][\w'’-]+)",
    re.I,
)
#: A relationship and its state: "start dating", "get together", "get married" are asked about through
#: "we've been together", "a couple", "married" in a record. Read on the words themselves: the term
#: reader drops "couple" as a quantity word.
_RELATIONSHIP_RE = re.compile(
    r"\b(?:together|couple|dat(?:ed|ing)|partners?|married|marry|marriage|wedding|relationship|engaged|engagement|"
    r"boyfriend|girlfriend|husband|wife|spouse)\b",
    re.I,
)


def _offset_record_relates_to_the_asked_event(question: str, span, references) -> bool:
    """An offset record cited for the asked event must be about that event, by what the guard can read:
    it names the asked event's object ("since I quit the orchestra" for "quit the orchestra"), or it
    states the same kind of event -- a stay at a named place for an asked visit or stay ("I was in
    Valparaíso" for "visit Chile": that the place lies in the asked country is the reader's inference,
    which the user's request for an approximate date accepts), or a relationship's state for an asked
    relationship ("we've been together" for "start dating"). A record of anything else ("six years ago I
    adopted my cat" for "live in Morocco") dates something else, however right its arithmetic."""
    asked = _asked_event_terms(question)
    unit = _support_terms(span["context"])
    actor_terms = {*references, span["actor"], *_support_terms(" ".join(references))}
    objects = (asked - _asked_action_terms(question) - actor_terms - _OPERAND_NON_EVENT_TERMS
               - _licensed_clause_grammar_terms())
    if objects & unit:
        return True
    asked_text = " ".join(part for part in re.split(r"(?<=[?])\s+|\n+", str(question or ""))
                          if part.rstrip().endswith("?")) or str(question or "")
    if _ASKED_PLACE_EVENT_RE.search(asked_text) and _RECORD_PLACE_RE.search(span["context"]):
        return True
    return bool(_RELATIONSHIP_RE.search(asked_text) and _RELATIONSHIP_RE.search(span["context"]))


def _cited_offset_values(question: str, clause: str, answer_actor: str, spans, *,
                         prior_question: str = "") -> tuple[set[str], list[dict]]:
    """Request-licensed: a record-stated offset that the clause cites together with the record's own
    statement time.

    Measured on the archived paid replies (license present): "<N> years before <S>, so around
    <S - N years>." and "Around <S - N years> -- on <S> <subject> said they'd been <in a state> <N>
    years." were withdrawn although a record of the asked subject, stated on S, says "<N> years ago
    I was ..." / "we've been <...> for <N> years". The record names the asked event only by
    knowledge the guard does not have (a city for its country, a state for its start), so the
    event-term binding of the licensed statement-time rule cannot hold, and the offset's year lies
    beyond every statement time's evidence span.

    The law, under the user's own request to answer from the conversation date only: a source unit
    spoken by an asked subject (the question's actor or a named conjunct, else the clause's own actor,
    else a record speaker the turn names; a turn naming nobody asks about the subject of the
    conversation's latest earlier question, :func:`_prior_question`, and with none it binds nothing),
    positive and certain, about the asked event (:func:`_offset_record_relates_to_the_asked_event`),
    carrying statement time S and a stated offset back from S ("N units ago", a present-perfect state
    "for N units" -- :func:`_relative_time_windows`), supports
      * the offset's duration value, and
      * each calendar claim of the clause lying in the offset's window at the offset's granularity
        (:func:`_offset_window_holds_claim`),
    when the clause CITES that record: it states the same offset (quantity and unit) and names S itself
    (:func:`_claim_is_the_statement_time`), and it names nothing beyond the asked event, the unit's own
    words, the conversation's people and time/report grammar. A different offset, wrong arithmetic, an
    unnamed S, another speaker's record, a record of another event, or a clause about another event
    supports nothing here.
    """
    cited = _clause_cited_offsets(clause)
    if not cited:
        return set(), []
    speakers = {span["speaker"] for span in spans if span.get("speaker") and span["speaker"] not in {"user", "assistant"}}
    # The asked subjects; else the clause's own actor; else the record speakers the turn or the clause
    # names ("<Name> in <country> - roughly when?" names its subject without a verb).
    references = (_support_question_actors(question) or ((answer_actor,) if answer_actor else ())
                  or tuple({name.lower() for name in re.findall(r"\b[A-Z][a-zA-Z'’-]+\b", f"{question} {clause}")} & speakers))
    binding = "asked_subject"
    if not references and prior_question:
        # A follow-up that names nobody ("roughly when was that?") asks about the conversation's subject
        # and event; a record another speaker made fits no such follow-up, however well it fits the citation.
        references = _support_question_actors(prior_question)
        question = f"{prior_question}\n{question}"
        binding = "conversation_subject"
    if not references:
        return set(), []
    claims = _calendar_claims(clause)
    asked = _asked_event_terms(question)
    matches = []
    for span in spans:
        statement = span.get("statement")
        offsets = [window for window in span.get("windows") or [] if window.get("offset")]
        if (not offsets or statement is None or "record_fields" in span or span.get("uncertain")
                or span.get("polarity", "positive") != "positive"
                or _support_reference_actor(span["actor"], question) not in references
                or not _offset_record_relates_to_the_asked_event(question, span, references)):
            continue
        for window in offsets:
            quantity, unit = window["offset"]["quantity"], window["offset"]["unit"]
            if (quantity, unit) not in cited:
                continue
            anchors = [claim for claim in claims if _claim_is_the_statement_time(claim, statement, unit)]
            # Every other claim the offset can check must be its arithmetic: "<D>, four days before <S>"
            # with D not four days before S cites the offset and gets it wrong.
            checked = [claim for claim in claims if claim not in anchors and _offset_checks_claim(unit, claim)]
            if (anchors and all(_offset_window_holds_claim(window, claim) for claim in checked)
                    and _licensed_clause_names_only_the_asked_event(
                        clause, asked, _support_terms(span["context"]), references, speakers)):
                matches.append((span, window, anchors))
    values: set[str] = set()
    receipt: list[dict] = []
    for span, window, anchors in matches:
        quantity, unit = window["offset"]["quantity"], window["offset"]["unit"]
        found = {f"d{quantity}{'days' if unit == 'day' else unit}"}
        for claim in claims:
            if claim not in anchors and _offset_window_holds_claim(window, claim):
                found.update(claim["values"])
        values |= found
        receipt.append({"source_unit": span["unit"], "rule": "request_licensed_cited_offset",
                        "statement": span["statement"].isoformat(), "expression": window["expression"],
                        "offset": dict(window["offset"]), "binding": binding,
                        "window": [window["start"].isoformat(), window["end"].isoformat()],
                        "values": sorted(found)})
    return values, receipt


#: A canonical duration value ("d6week", "d0.5hour") split into quantity and unit.
_OPERAND_DURATION_VALUE_RE = re.compile(r"d(\d+(?:\.\d+)?)(year|month|week|days|hour|minute)")
#: Exact conversions between neighbouring duration units (no month-to-week or day-to-hour guesses).
_EXACT_UNIT_CONVERSIONS = {
    "minute": (("hour", 1 / 60),), "hour": (("minute", 60),),
    "days": (("week", 1 / 7),), "week": (("days", 7),),
    "month": (("year", 1 / 12),), "year": (("month", 12),),
}


#: A question about a difference in age ("How many years older am I than when I graduated?").
_AGE_QUESTION_RE = re.compile(r"\b(?:years?\s+(?:older|younger|old)|how\s+old|older|younger|(?:my|your|his|her|their)\s+age)\b", re.I)
#: The asker describing their own age: "As a 32-year-old ...", "I'm 32", "I turned 40", "... which I completed at
#: the age of 25". A third person's age ("my 35-year-old brother", "my dad retired at 60") is not the asker's.
_AGE_SELF_RE = re.compile(
    r"\b(?:as|i(?:'|\u2019)m|i\s+am)\s+an?\s+(\d{1,3})[-\s]years?[-\s]old\b"
    r"|\b(?:i(?:'|\u2019)m|i\s+am|i\s+turned|i\s+was|i\s+will\s+be|i(?:'|\u2019)ll\s+be)\s+(\d{1,3})\b(?!\s*(?:%|percent|minutes?|hours?|days?|weeks?|months?|times|kg|lbs?|pounds|miles|km|dollars|euros))"
    r"|\bI\b[^.!?;]*?\b(?:at\s+(?:the\s+)?age\s+(?:of\s+)?|aged\s+)(\d{1,3})\b",
    re.I,
)


def _checked_age_differences(question: str, spans, items) -> tuple[set[str], list[int]]:
    """Year differences between two ages the answer cites, each one stated by the asker as their own age.

    "7 years -- you're 32 now, and you finished your Bachelor's at 25" against records "As a 32-year-old ..."
    and "... which I completed at the age of 25" derives 7 years. An age the records do not state as the
    asker's, or a single cited age, derives nothing.
    """
    if not _AGE_QUESTION_RE.search(str(question or "")):
        return set(), []
    stated = {int(n) for span in spans if span.get("polarity", "positive") == "positive" and not span.get("uncertain")
              and span.get("actor") in {"user", ""} and span.get("role", "user") != "assistant"
              for match in _AGE_SELF_RE.finditer(str(span.get("body") or "")) for n in match.groups() if n}
    cited = sorted({int(n) for item in items if not item["conflict"] and item["polarity"] == "positive"
                    for n in re.findall(r"(?<![\d.:/-])(\d{1,3})(?![\d:/])", item["clause"]) if 5 <= int(n) <= 120} & stated)
    derived = {f"d{second - first:g}year" for index, first in enumerate(cited) for second in cited[index + 1:]}
    return derived, cited


def _checked_operand_arithmetic(question: str, spans, items) -> tuple[set[str], dict]:
    """Durations an answer computes from operands it cites that the evidence already supports.

    Readers state a computed value with its operands: "Ordered February 5th, arrived February 10th
    -- that's 5 days", or "19 days. You launched your website on 2023-02-10 and signed your first
    client contract on 2023-03-01." Re-deriving the interval from the QUESTION's frame wording failed
    whenever that wording did not line up with the source clauses, and the correct answer was
    withdrawn. Here the operands are the answer's own cited dates and relative points ("3 months
    ago", "last month"), and each one must already pass this module's request-support contract in
    the sentence that cites it -- actor, action, object and episode checks unchanged. Only the
    arithmetic is new: a day difference between two supported dates (and its whole weeks, completed
    calendar months, or a weeks-and-days split), or the difference of two supported relative points
    in one unit. A hedged value ("about 11.5 weeks") may round by at most half a unit.

    The reference clock is never an operand: "how many days ago ... when I made the cake" measured
    from today is a different interval from the one between the two recorded events.
    """
    receipt: dict = {"operand_dates": [], "operand_relative_points": [], "operand_durations": [], "derived_values": []}
    dates: set = set()
    points: list[tuple[str, float]] = []
    hedged: set[str] = set()
    durations: dict[str, set[float]] = {}
    for item in items:
        if item["conflict"] or item["polarity"] != "positive":
            continue
        clause = item["clause"]
        # Durations the answer cites that already pass the request-support contract in their own
        # sentence ("six weeks of lessons", "booked 3 months in advance").
        for value in _canonical_time_values(clause):
            parsed = _OPERAND_DURATION_VALUE_RE.fullmatch(value)
            if parsed and value in item["supported"]:
                durations.setdefault(parsed.group(2), set()).add(float(parsed.group(1)))
        # A total written with its addends as bare numbers ("8 days: 5 at the lake and 3 in the hills",
        # "15 hours (6 to the coast, 4 north, 5 east)"): each bare number after the total reads in the
        # total's unit, and counts only when the evidence supports that duration in this sentence.
        total = _PAST_DURATION_RE.search(clause)
        if total:
            unit = _UNIT_NORMALIZER.get(str(total.group(2) or "").lower(), "")
            tail = re.sub(r"\d{1,2}:\d{2}|\d{4}|\b\d+(?:st|nd|rd|th)\b", " ", clause[total.end():])
            for bare in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.%$])", tail):
                value = f"d{float(bare.group(1)):g}{unit}" if unit else ""
                if value and value in item["supported"]:
                    durations.setdefault(unit, set()).add(float(bare.group(1)))
        request_spans = [span for span in spans if span.get("polarity", "positive") == "positive"
                         and "record_fields" not in span and _span_supports_request(span, question, clause)]
        for match in _PAST_DATE_RE.finditer(clause):
            clause_terms = _operand_clause_terms(clause, match.start(), match.end())
            values = _canonical_time_values(match.group(0))
            if not any(value in item["supported"] or (value.startswith("m") and value.count(":") == 1
                       and any(ev.startswith(value + ":") for ev in item["supported"])) for value in values if value.startswith("m")):
                continue
            month_day = next((":".join(v.split(":")[:2]) for v in values if v.startswith("m")), "")
            year = next((int(v[1:]) for v in values if v.startswith("y")), None)
            if not month_day:
                continue
            month, day = (int(part) for part in month_day[1:].split(":"))

            def same(d, month=month, day=day, year=year):
                return d.month == month and d.day == day and (year is None or d.year == year)

            resolved = {d for span in request_spans for d in span["dates"]
                        if same(d) and _operand_span_is_the_clause_event(clause_terms, span, request_spans, same)}
            if len(resolved) == 1:
                dates |= resolved
                receipt["operand_dates"].append(next(iter(resolved)).isoformat())
        for ago in _AGO_PHRASE_RE.finditer(clause):
            stated = _ago_points(ago.group(0), None)
            if not stated:
                continue
            unit, quantity, _ = stated[0]
            clause_terms = _operand_clause_terms(clause, ago.start(), ago.end())
            anchored = {point for span in request_spans for (u, q, point) in _ago_points(span["body"], span.get("statement"))
                        if u == unit and q == quantity and point == point
                        and len(clause_terms & _support_terms(span["context"])) >= 1}
            if len(anchored) == 1:
                points.append((unit, next(iter(anchored))))
                receipt["operand_relative_points"].append({"unit": unit, "quantity": quantity})
        hedged |= {value for m in _PAST_DURATION_RE.finditer(clause) if _OPERAND_HEDGE_RE.search(clause[:m.start()])
                   for value in _canonical_time_values(m.group(0))}
    unit_days = {"week": 7.0, "month": 30.44, "year": 365.25}
    derived: set[str] = set()
    ordered = sorted(dates)
    whole_text = " ".join(item["clause"] for item in items)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            delta = (second - first).days
            derived.add(f"d{delta}days")
            if delta % 7 == 0:
                derived.add(f"d{delta // 7}week")
            months = _completed_calendar_months(first, second)
            if months is not None and second.day == first.day:
                derived.add(f"d{months}month")
                if months % 12 == 0:
                    derived.add(f"d{months // 12}year")
            for value in hedged:
                parsed = re.fullmatch(r"d(-?\d+(?:\.\d+)?)(week|month|year)", value)
                if parsed and abs(float(parsed.group(1)) - delta / unit_days[parsed.group(2)]) <= 0.5:
                    derived.add(value)
            for split in re.finditer(r"(\d+)\s+weeks?\s*(?:,|and)?\s*(\d+)\s+days?", whole_text, re.I):
                if int(split.group(1)) * 7 + int(split.group(2)) == delta:
                    derived.update((f"d{int(split.group(1))}week", f"d{int(split.group(2))}days"))
    for index, (unit, point) in enumerate(points):
        for other_unit, other_point in points[index + 1:]:
            difference = abs(point - other_point)
            if unit == other_unit and difference and abs(difference - round(difference)) < 1e-9:
                derived.add(f"d{round(difference)}{unit}")
    # The sum or difference of supported durations in one unit ("six weeks of lessons, the amp two
    # weeks before that: four weeks"; "booked 3 months ahead for a trip 2 months ago: 5 months"),
    # the total of three or more, and each duration's exact value in a neighbouring unit (30 minutes
    # is 0.5 hours). Only the arithmetic is new; every operand passed its own support check.
    for unit, quantities in sorted(durations.items()):
        receipt["operand_durations"].extend(f"d{quantity:g}{unit}" for quantity in sorted(quantities))
        ordered = sorted(quantities)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                derived.update((f"d{first + second:g}{unit}", f"d{second - first:g}{unit}"))
        if len(ordered) >= 3:
            derived.add(f"d{sum(ordered):g}{unit}")
        for quantity in ordered:
            for other, factor in _EXACT_UNIT_CONVERSIONS.get(unit, ()):
                derived.add(f"d{quantity * factor:g}{other}")
    ages, receipt["operand_ages"] = _checked_age_differences(question, spans, items)
    derived |= ages
    receipt["derived_values"] = sorted(derived)
    return derived, receipt


#: What a record is called when an answer cites it as the source of a time ("the 6:55 pm
#: conversation", "her 10:20 am message", "the session at 4:05 pm").
_CLOCK_SOURCE_NOUN = r"(?:conversation|convo|session|chat|message|msg|text|email|exchange|call|entry|note|record|post|update|talk|reply|discussion)s?"
_CLOCK_SOURCE_NOUN_RE = re.compile(rf"\b{_CLOCK_SOURCE_NOUN}\b", re.I)
_CLOCK_SOURCE_FOLLOWS_RE = re.compile(rf"[ \t]*(?P<noun>{_CLOCK_SOURCE_NOUN})\b", re.I)
_CLOCK_SOURCE_PRECEDES_RE = re.compile(
    rf"\b{_CLOCK_SOURCE_NOUN}\b[^.!?]{{0,40}}?(?:\b(?:at|from|of|on|dated|timestamped|stamped)\s*|\(\s*)$"
    # "message sent 9:05 am", "note posted at 9:05 am": the record and the act of making it.
    rf"|\b{_CLOCK_SOURCE_NOUN}\s+(?:sent|posted|written|logged|received)(?:\s+at)?\s*$", re.I)
#: Record nouns that also name events of their own: "a recording session", "a call with his sister", "a
#: talk on beekeeping", "a chat with the mayor". One of these is the record only where the answer cites it
#: (:func:`_record_noun_names_the_record`); so is any record noun the question asks about ("When did
#: she have a conversation with her mother?").
_EVENT_CAPABLE_RECORD_NOUNS = frozenset({"session", "chat", "exchange", "call", "post", "update", "talk", "discussion"})
_RECORD_NOUN_BASES = frozenset({"conversation", "convo", "session", "chat", "message", "msg", "text", "email", "exchange",
                                "call", "entry", "note", "record", "post", "update", "talk", "reply", "discussion"})
_NOUN_PHRASE_DETERMINERS = frozenset("the a an this that these those her his their my your our its".split())
#: Words that may stand inside a record's noun phrase without making it an event: the conversation's
#: time words and record kinds ("her last message", "that day's chat", "a text message", "the morning
#: session"); a number, clock, month or date ("the 6:55 pm conversation", "her 12 May note") is one too,
#: and so is a record speaker's possessive ("Bram's 12 May reply").
_RECORD_NOUN_NEUTRAL_WORDS = frozenset((
    "same earlier later last first next previous prior latest final recent following original whole entire own "
    "other another one morning evening afternoon night tonight late early overnight midnight late-night today "
    "yesterday day week weekend group text voice written short long brief quick follow-up followup recorded "
    "logged dated timestamped stamped am pm a.m. p.m. a.m p.m o'clock o’clock " + " ".join(_WEEKDAYS)).split())
#: Words that end a noun phrase when read backwards from its noun (a preposition, conjunction or
#: pronoun): "per the 9:12 chat", "said in 6:55 pm messages", "she mentioned it 6:55 pm chat".
_NOUN_PHRASE_BOUNDARY_WORDS = frozenset((
    "before after b4 bfr preceding following since until till than per from by in during at on through via within "
    "to of with about for into and or but so then i we you he she they it me us him them").split())
#: Words that relate a time to a cited record ("the night before the 6:55 pm conversation", "per the 9:12
#: chat", "two days ahead of the 12 May message", "according to the session on 4 May").
_RECORD_RELATION_WORDS = frozenset("before after b4 bfr preceding following since until till than per from by".split())
_RECORD_RELATION_PAIRS = frozenset({("prior", "to"), ("ahead", "of"), ("according", "to"), ("as", "of"), ("up", "to")})
#: ... and words that place a report inside it ("she mentioned it in the 6:55 pm session").
_RECORD_REPORTED_IN_WORDS = frozenset("in during at on through via within".split())
_CLOCK_REPORT_VERB = rf"(?:{_REPORT_VERB}|says|tells|writes|mentions|asked|asks|sent|texted|replied)"
_CLOCK_REPORT_VERB_RE = re.compile(rf"\b{_CLOCK_REPORT_VERB}\b", re.I)
#: The verb a cited record reports with ("the 6:55 pm chat says ...", "her 12 May note shows ...").
_RECORD_REPORTS_RE = re.compile(rf"(?:{_CLOCK_REPORT_VERB}|notes|states|shows|showed|reads|confirms|confirmed)$", re.I)
#: A report verb with what it reports or whom it addresses before the time, in at most four words:
#: pronouns and particles ("she mentioned it at", "told her at", "said so at") or the conversation's
#: people by name ("told Corvin at", "wrote to Lisbeth at"; :func:`_report_before`).
_CLOCK_REPORT_PRECEDES_RE = re.compile(
    rf"\b(?P<verb>{_CLOCK_REPORT_VERB})\b(?P<object>(?:\s+[A-Za-z][a-zA-Z'’-]*){{0,4}}?)\s+(?:at|around|by)\s*$"
    # The preposition dropped after the reported object ("she said it 9:05 am"); a bare "said 9 pm"
    # may be what was said, so the object is required there.
    rf"|\b(?P<bare_verb>{_CLOCK_REPORT_VERB})\s+(?:it|this|that|so)\s+$", re.I)
#: The words a report's object may be made of besides a speaker's name.
_REPORT_OBJECT_WORDS = frozenset("this that it me us you him her them about back again also down so first to with".split())
_CLOCK_REPORTER_FOLLOWS_RE = re.compile(
    r"\s*[,(\u2014\u2013-]?\s*(?:(?:that\s+is|i\.e\.)\s*,?\s*)?(?P<when>when\s+)?"
    r"(?:(?:the|her|his|their|my|your|our)\s+)?[A-Za-z][\w'’-]*\s+(?:(?:[a-z]+ly|just|first|also)\s+)?"
    rf"(?P<verb>{_CLOCK_REPORT_VERB})\b",
    re.I)
#: Report verbs whose bare object is whom they address ("told her sister", "texted the plumber").
_ADDRESSEE_REPORT_VERBS = frozenset({"told", "tells", "texted", "messaged", "asked", "asks"})
#: Object pronouns: the conversation's own partner ("told her at", "chatted with him").
_PARTNER_PRONOUNS = frozenset({"him", "her", "them", "me", "you", "us"})
#: Words after "her" that keep it an object pronoun ("told her at 9", "with her about it"); any other
#: word makes it a third party's possessive ("told her sister", "a call with her mother").
_PRONOUN_OBJECT_FOLLOWERS = frozenset((
    "at on in about that this it so and but or when before after yesterday today then again too also back by "
    "around during while because the").split())
#: What a report verb's object may open with when it is what was reported, not whom it addressed.
_REPORTED_CONTENT_OPENERS = frozenset(
    "that about it this so of how what when why where if whether everything nothing something".split())


def _record_speakers(spans) -> frozenset[str]:
    """The conversation's people: the records' own speaker labels."""
    return frozenset(span["speaker"] for span in spans
                     if span.get("speaker") and span["speaker"] not in {"user", "assistant"})


def _names_a_time(text: str) -> bool:
    """``text`` opens with a time, not a topic or a place ("12 May", "the 12th", "that day", "Monday")."""
    words = re.findall(r"[A-Za-z0-9][\w'’:.-]*", str(text or ""))[:2]
    if not words:
        return True
    first = words[0].lower().rstrip(".")
    if first in {"the", "that", "this", "same"} and len(words) > 1:
        first = words[1].lower().rstrip(".")
    return bool(re.match(r"\d", first) or re.fullmatch(_PAST_MONTH_WORDS, first) or first in _WEEKDAYS
                or re.sub(r"['’]s$", "", first) in _RECORD_NOUN_NEUTRAL_WORDS | {"time", "date", "noon", "month", "year"})


def _names_the_partner_or_a_time(text: str, speakers) -> bool:
    """What follows "with", "to", "from" or an addressing verb names the conversation's own partner --
    a record speaker, an object pronoun ("with him", "told her at") -- or a time, not a third party
    ("with his sister", "to the mayor", "told her mother", "with Frank")."""
    words = re.findall(r"[A-Za-z0-9][\w'’-]*", str(text or ""))[:2]
    if not words:
        return True
    first = words[0].lower()
    if first in speakers or (first == "each" and len(words) > 1 and words[1].lower() == "other"):
        return True
    if first in _PARTNER_PRONOUNS:
        # "her sister": a possessive of a third party, not the partner.
        return not (first == "her" and len(words) > 1 and words[1].isalpha() and words[1].lower() not in _PRONOUN_OBJECT_FOLLOWERS)
    return _names_a_time(text)


def _report_reaches_a_third_party(clause: str, verb_end: int, verb: str, speakers) -> bool:
    """The report verb ending at ``verb_end`` is an act with somebody outside the conversation: it is
    with or to a third party ("chatted with the mayor", "talked to his sister", "mentioned it to her
    mom"), or an addressing verb's object is one ("told her sister", "texted the plumber"). Telling
    the conversation's own partner ("told Corvin", "wrote to Lisbeth", "told her") is the record."""
    rest = clause[verb_end:]
    reached = re.match(r"\s+(?:(?:back|again|also|later|then|first|it|this|that|so)\s+)*(?:to|with)\s+(?P<who>.*)",
                       rest, re.I | re.S)
    if reached:
        return not _names_the_partner_or_a_time(reached.group("who"), speakers)
    if verb.lower() in _ADDRESSEE_REPORT_VERBS:
        addressed = re.match(r"\s+(?P<who>[A-Za-z].*)", rest, re.S)
        if addressed and re.match(r"[A-Za-z]+", addressed.group("who")).group(0).lower() not in _REPORTED_CONTENT_OPENERS:
            return not _names_the_partner_or_a_time(addressed.group("who"), speakers)
    return False


def _report_before(before: str, pattern, speakers):
    """The report verb nearest the end of ``before`` whose object (``pattern``'s "object" group) is made of
    pronouns, particles and the records' speakers only, as (verb end, verb), else None. "said she
    rehearsed at <clock>" reports a rehearsal time; "told her sister at" addresses a third party."""
    for verb in reversed(list(_CLOCK_REPORT_VERB_RE.finditer(before))):
        found = pattern.match(before, verb.start())
        if found is None:
            continue
        group = "verb" if found.group("verb") else "bare_verb"
        words = [word.lower() for word in re.findall(r"[A-Za-z][a-zA-Z'’-]*", found.groupdict().get("object") or "")]
        if all(word in _REPORT_OBJECT_WORDS or word in speakers for word in words):
            return found.end(group), found.group(group)
    return None


def _clause_reports(clause: str, speakers) -> bool:
    """The clause reports something said, by a verb that reaches no third party."""
    return any(not _report_reaches_a_third_party(clause, verb.end(), verb.group(0), speakers)
               for verb in _CLOCK_REPORT_VERB_RE.finditer(clause))


def _neutral_record_noun_word(word: str, speakers) -> bool:
    """A word that may stand inside a record's noun phrase (:data:`_RECORD_NOUN_NEUTRAL_WORDS`)."""
    low = word.lower()
    if re.match(r"\d", low):
        return True
    stem = re.sub(r"['’]s$", "", low)
    if stem in _RECORD_NOUN_NEUTRAL_WORDS or low in _RECORD_NOUN_NEUTRAL_WORDS or re.fullmatch(_PAST_MONTH_WORDS, stem.rstrip(".")):
        return True
    return stem != low and stem in speakers


_PARENTHESIS_RE = re.compile(r"\([^()]*\)")


def _after_record_attachments(clause: str, at: int) -> int:
    """The position after the dates, times and glosses a cited record carries ("on 12 May 2024", "at 6:55
    pm", ", 12 May", "(6:55 pm)")."""
    while True:
        lead = re.match(r"\s*,?\s*(?:(?:on|at|of|from|dated|timestamped)\s+)?", clause[at:], re.I)
        position = at + lead.end()
        found = (_PAST_DATE_RE.match(clause, position) or _PAST_CLOCK_TIME_RE.match(clause, position)
                 or _PARENTHESIS_RE.match(clause, position))
        if found is None or found.end() == position:
            return at
        at = found.end()


def _record_noun_names_the_record(clause: str, noun_start: int, noun_end: int, *, value_start: int, value_end: int,
                                  speakers=frozenset(), asked=frozenset()) -> bool:
    """The record noun at ``clause[noun_start:noun_end]``, to which a cited time or date attaches, names
    the record itself and not an event.

    Measured on reviewer near-misses: "<Name>'s recording session with the quartet was at <the record's
    clock>", "<Name>'s call with his sister was at <clock>" and "the <clock> call with the boatyard"
    were kept as record citations because "session" and "call" are also what a record is called.

    A noun phrase carrying an event's own modifier is that event: an activity ("glazing session",
    "physiotherapy session", "beekeeping talk", "phone call"), a third party's possessive ("his sister's
    call"), a third party it is with ("a call with his sister", "a conversation with her mother"; a
    record speaker or an object pronoun is the conversation's own partner), a topic or place it is on
    or at ("a talk on beekeeping", "the session at the library"). An event-capable noun
    (:data:`_EVENT_CAPABLE_RECORD_NOUNS`), or one the question asks about, is the record only where the
    answer cites it -- relates a time to it ("before", "after", "per", "from", "prior to"), has it report
    ("the 6:55 pm chat says ..."), or reports inside it ("she mentioned it in the 6:55 pm session"); as a
    bare subject or object ("his session was at <clock>", "she had the <clock> call", "she mended the net
    during the <clock> call") it is the event.
    """
    noun = clause[noun_start:noun_end].lower()
    if noun not in _RECORD_NOUN_BASES and noun[:-1] in _RECORD_NOUN_BASES:
        noun = noun[:-1]
    strict = noun in _EVENT_CAPABLE_RECORD_NOUNS or bool(_support_terms(noun) & set(asked))
    phrase_start = noun_start
    for token in reversed(list(re.finditer(r"[A-Za-z0-9][\w'’.:-]*|\S", clause[:noun_start]))):
        word = token.group(0)
        low = word.lower()
        if not re.match(r"[A-Za-z0-9]", word) or low in _NOUN_PHRASE_BOUNDARY_WORDS:
            break
        if low in _NOUN_PHRASE_DETERMINERS:
            phrase_start = token.start()
            break
        if not _neutral_record_noun_word(word, speakers):
            # A content word inside the phrase names the event's kind or owner: "glazing session",
            # "beekeeping talk", "his sister's call", "had 9:12 call".
            return False
        phrase_start = token.start()
    after = re.match(r"\s*(?P<prep>with|to|from|between|on|at|of)\s+(?P<rest>.*)", clause[noun_end:], re.I | re.S)
    if after:
        prep, rest = after.group("prep").lower(), after.group("rest")
        if prep in {"with", "to", "from", "between"} and not _names_the_partner_or_a_time(rest, speakers):
            return False
        if prep in {"on", "at", "of"} and not _names_a_time(rest):
            return False
    if not strict:
        return True
    following = re.match(r"\s*([A-Za-z][\w'’-]*)", clause[_after_record_attachments(clause, max(noun_end, value_end)):])
    if following and _RECORD_REPORTS_RE.match(following.group(1)):
        return True
    governing = re.search(r"([A-Za-z][\w'’-]*)\s+([A-Za-z0-9][\w'’-]*)\s*$|([A-Za-z0-9][\w'’-]*)\s*$",
                          clause[:phrase_start])
    if governing is None:
        return False
    pair = ((governing.group(1) or "").lower(), (governing.group(2) or governing.group(3) or "").lower())
    if pair[1] in _RECORD_RELATION_WORDS or pair in _RECORD_RELATION_PAIRS:
        return True
    return pair[1] in _RECORD_REPORTED_IN_WORDS and _clause_reports(clause, speakers)


def _record_noun_before(before: str, pattern) -> re.Match | None:
    """The nearest record noun from which ``pattern`` reaches the end of ``before`` ("the session at ")."""
    for noun in reversed(list(_CLOCK_SOURCE_NOUN_RE.finditer(before))):
        if pattern.match(before, noun.start()):
            return noun
    return None


def _clock_cites_a_record(clause: str, start: int, end: int, *, speakers=frozenset(), asked=frozenset()) -> bool:
    """The clock time at ``clause[start:end]`` is presented as WHEN A RECORD WAS MADE, not as when the
    asked event happened.

    It names the record ("the 6:55 pm conversation", "the session at 4:05 pm") by a noun that names the
    record and not an event (:func:`_record_noun_names_the_record`), is the time of a report ("she
    mentioned it at 4:05 pm", "Odile wrote at 9:10 am", "told Corvin at 9:12 am") that reaches no third
    party (:func:`_report_reaches_a_third_party`), or is glossed as one, fronting its clause or followed
    by "when" ("6:55 pm on <date>, when Odile said ..."). A clock time standing as the event's own time
    ("he reached the depot at 9 pm", "he reached the depot at 9 pm, he said", "his glazing session was
    at 9 pm", "the 9:12 am call with the boatyard", "at 9:12 am she chatted with the mayor") is none of
    these: a record's statement clock never becomes the reported event's time.
    """
    follows = _CLOCK_SOURCE_FOLLOWS_RE.match(clause, end)
    if follows:
        return _record_noun_names_the_record(clause, follows.start("noun"), follows.end("noun"), value_start=start,
                                             value_end=end, speakers=speakers, asked=asked)
    before = clause[:start]
    noun = _record_noun_before(before, _CLOCK_SOURCE_PRECEDES_RE)
    if noun is not None:
        return _record_noun_names_the_record(clause, noun.start(), noun.end(), value_start=start, value_end=end,
                                             speakers=speakers, asked=asked)
    report = _report_before(before, _CLOCK_REPORT_PRECEDES_RE, speakers)
    if report:
        return not _report_reaches_a_third_party(clause, *report, speakers)
    at = end + re.match(r"\s*,?\s*(?:on\s+)?", clause[end:], re.I).end()
    following_date = _PAST_DATE_RE.match(clause, at)
    reporter = _CLOCK_REPORTER_FOLLOWS_RE.match(clause, following_date.end() if following_date else end)
    if reporter is None:
        return False
    fronted = not re.search(r"[A-Za-z0-9]", re.sub(r"\b(?:on|at|around|by)\b", "", _PAST_DATE_RE.sub("", before), flags=re.I))
    return ((fronted or bool(reporter.group("when")))
            and not _report_reaches_a_third_party(clause, reporter.end("verb"), reporter.group("verb"), speakers))


def _clock_citations(clause: str, *, speakers=frozenset(), asked=frozenset()) -> list[tuple[int, int, set[str]]]:
    """Each clock time of the clause presented as a record's time, with its canonical values."""
    return [(match.start(), match.end(), _canonical_time_values(match.group(0)))
            for match in _PAST_CLOCK_TIME_RE.finditer(clause)
            if _clock_cites_a_record(clause, match.start(), match.end(), speakers=speakers, asked=asked)]


#: What may join a clock time to the date it is stated with: "<clock> conversation on <date>", "<date>
#: at <clock>", "<clock>, <date>", "<date> note (<clock>)", "<date> message, timestamped <clock>".
_CLOCK_DATE_JOIN_RE = re.compile(
    rf"(?:[\s,()]|\b(?:on|of|from|at|the|a|an|her|his|their|my|your|our|that|this|dated|timestamped|stamped|sent|posted|"
    rf"written|logged|received)\b|\b{_CLOCK_SOURCE_NOUN}\b)*", re.I)


def _clock_attached_day(clause: str, start: int, end: int) -> dict | None:
    """The day claim a clock time is stated with ("the 6:55 pm conversation on 12 May", "on 12 May at
    6:55 pm", "her 12 May note (6:55 pm)"), else None. An attached date names WHICH record the time
    belongs to: a date inside the clock's own parenthesis first ("<D> (message sent <clock>, <S>)" is
    S's time, not D's), then the date joined to it through the record's noun ("<D> (from the <clock>
    conversation on <S>)"), else the nearest joined date."""
    def parenthesis(position):
        # The innermost open parenthesis around ``position`` (-1 outside any).
        opened = clause.rfind("(", 0, position)
        return -1 if opened < 0 or clause.find(")", opened, position) >= 0 else opened

    best = None
    for claim in _calendar_claim_spans(clause):
        if claim["granularity"] != "day":
            continue
        first, last = claim["span"]
        between = clause[end:first] if first >= end else clause[last:start] if last <= start else None
        if between is not None and len(between) <= 40 and _CLOCK_DATE_JOIN_RE.fullmatch(between):
            rank = (parenthesis(first) != parenthesis(start), not re.search(rf"\b{_CLOCK_SOURCE_NOUN}\b", between, re.I),
                    len(between))
            if best is None or rank < best[0]:
                best = (rank, claim)
    return best[1] if best else None


#: Days per unit for an interval read off two dates; a day count is exact.
_INTERVAL_UNIT_DAYS = {"week": 7.0, "month": 365.2425 / 12, "year": 365.2425}


def _interval_matches(quantity: float, unit: str, delta_days: int) -> bool:
    """A stated interval equals a day difference within the unit's granularity: days and nights exactly;
    weeks, months and years to the nearest whole unit (a fractional or hedged value within half a unit)."""
    if unit in {"days", "night"}:
        return quantity == delta_days
    length = _INTERVAL_UNIT_DAYS.get(unit)
    return length is not None and abs(quantity - delta_days / length) <= 0.5


#: A date an answer presents as WHEN A RECORD WAS MADE ("the 19 May conversation", "her 2 May 2024
#: message", "the session on 2 May", "she said on 2 May", "as of 2 May").
_DATE_SOURCE_FOLLOWS_RE = re.compile(
    rf"[ \t]*(?:\d{{1,2}}:\d{{2}}\s*(?:[ap](?:\.\s?m\.?|m\b))?[ \t]*)?(?P<noun>{_CLOCK_SOURCE_NOUN})\b", re.I)
_DATE_NOUN_PRECEDES_RE = re.compile(rf"\b{_CLOCK_SOURCE_NOUN}\b[^.!?]{{0,40}}?\b(?:on|from|of|dated)\s*$", re.I)
_DATE_REPORT_PRECEDES_RE = re.compile(
    rf"\b(?P<verb>{_CLOCK_REPORT_VERB})\b(?P<object>(?:\s+[A-Za-z][a-zA-Z'’-]*){{0,4}}?)\s+(?:on|around)\s*$", re.I)
_DATE_AS_OF_RE = re.compile(r"\bas\s+of\s*$", re.I)


def _date_cites_a_record(clause: str, start: int, end: int, *, speakers=frozenset(), asked=frozenset()) -> bool:
    """The date at ``clause[start:end]`` is presented as when a record was made, not as an event's date:
    it names the record ("the 19 May conversation", "the session on 19 May") by a noun that names the
    record and not an event (:func:`_record_noun_names_the_record`), is the date of a report that reaches
    no third party ("she said on 19 May", "told Corvin on 19 May"), "as of 19 May", or fronts its clause
    before the reporter ("On 19 May, Odile said ..."). "his 14 April call with his sister" dates a call."""
    follows = _DATE_SOURCE_FOLLOWS_RE.match(clause, end)
    if follows:
        return _record_noun_names_the_record(clause, follows.start("noun"), follows.end("noun"), value_start=start,
                                             value_end=end, speakers=speakers, asked=asked)
    before = clause[:start]
    noun = _record_noun_before(before, _DATE_NOUN_PRECEDES_RE)
    if noun is not None:
        return _record_noun_names_the_record(clause, noun.start(), noun.end(), value_start=start, value_end=end,
                                             speakers=speakers, asked=asked)
    report = _report_before(before, _DATE_REPORT_PRECEDES_RE, speakers)
    if report:
        return not _report_reaches_a_third_party(clause, *report, speakers)
    if _DATE_AS_OF_RE.search(before):
        return True
    reporter = _CLOCK_REPORTER_FOLLOWS_RE.match(clause, end)
    fronted = not re.search(r"[A-Za-z0-9]", re.sub(r"\b(?:on|at|around|by)\b", "", before, flags=re.I))
    return bool(reporter and (fronted or reporter.group("when"))
                and not _report_reaches_a_third_party(clause, reporter.end("verb"), reporter.group("verb"), speakers))


#: A day of the month named by its ordinal alone ("on the 23rd", "since the 3rd"): not followed by a month
#: (a full date) or by a noun it counts ("the 17th floor", "the 3rd attempt").
_ORDINAL_DAY_RE = re.compile(
    rf"\b(?:on|by|since|from|until|till|before|after)\s+the\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)\b"
    rf"(?!\s*(?:of\s+)?(?:{_PAST_MONTH_WORDS})\b)", re.I)
_ORDINAL_DAY_FOLLOWERS = frozenset((
    "and but so when at in with for to i we he she they it my our his her their the a an this that after before "
    "because then too as already which who while if or since until last").split())


def _ordinal_days(text: str, statement) -> set:
    """Days of the month a record names by ordinal alone, read against its statement time: the latest such
    day on or before it, or in a future form ("I'll paint it on the 3rd") the nearest on or after it."""
    found: set = set()
    if statement is None:
        return found
    body = str(text or "")
    future = bool(_FUTURE_FORM_RE.search(body))
    for match in _ORDINAL_DAY_RE.finditer(body):
        following = re.match(r"\s*([A-Za-z]+)", body[match.end():])
        if following and following.group(1).lower() not in _ORDINAL_DAY_FOLLOWERS:
            continue
        day = int(match.group("day"))
        for step in ((0, 1) if future else (0, -1)):
            year, month = statement.year + (statement.month - 1 + step) // 12, (statement.month - 1 + step) % 12 + 1
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if (candidate >= statement) if future else (candidate <= statement):
                found.add(candidate)
                break
    return found


def _record_stated_days(spans) -> dict:
    """The days each record states, by its statement time, with the record's actor: its written dates, the
    single day a relative time of it names ("yesterday", "two days ago", "last Friday") and a day of the
    month it names by ordinal ("on the 23rd", :func:`_ordinal_days`)."""
    stated: dict = {}
    for span in spans:
        statement = span.get("statement")
        if statement is None or "record_fields" in span or span.get("uncertain") or span.get("polarity", "positive") != "positive":
            continue
        days = set(span["dates"]) | _ordinal_days(span["body"], statement)
        days |= {window["start"] for window in span.get("windows") or []
                 if window["start"] == window["end"] and window["direction"] == "past"}
        for day in days:
            stated.setdefault(statement, set()).add((day, span["actor"]))
    return stated


def _date_interval_values(clause: str, day_candidates, *, statement_days, event_operand, span_only=None,
                          stated_days=None, references=(), speakers=frozenset(), asked=frozenset()) -> tuple[set[str], list[dict]]:
    """Intervals the clause states between one of its dates and a record time it cites in the same clause.

    Measured on the archived paid replies: "<D> (two days before the <D+2> conversation)." and "<D>
    (two days before her <D+2> message)." were withdrawn for "two days" although the record, stated on
    D+2, says the event was two days earlier, and both dates were supported in that very sentence.

    Arithmetic over dates is only as good as each date's role, and a date that is merely supported
    somewhere in a sentence may belong to another action (a return date is not a paddling date; the
    checked-operand contract refuses it). So an interval is checked here only between two OPERANDS of
    the clause, each a supported day (``day_candidates``) in one of these roles:
      * a record time -- a date the clause presents as when a record was made
        (:func:`_date_cites_a_record`) that is the statement time of a record the reader saw
        (``statement_days``); at least one operand must be this;
      * a bare date -- its own part of the clause names no event (the answer's own dated value);
      * an event date -- its part names an event, and ``event_operand(claim)`` binds it to that event.
    The stated interval must equal the operands' difference at its unit's granularity
    (:func:`_interval_matches`). Wrong arithmetic, an unsupported or unbound operand, a pair with no
    cited record time (two bare dates, two event dates: the checked-operand contract's own ground) or an
    operand in another sentence leaves the interval unsupported.

    An operand that only the user's request for an approximate date supports (``span_only``: a day
    within the evidence span of a statement time) is no exact operand: "26 July, two weeks before the 9
    August conversation" is self-consistent arithmetic over a date nothing states. It counts only when a
    record made at the paired record time -- of an asked subject (``references``), when the turn or the
    conversation names one -- states that very day (:func:`_record_stated_days`: a written date, a
    relative day, "on the 6th").
    """
    def placed(claim, day, partner):
        # A floating yearless day takes the year of the date it is paired with.
        if day is not None:
            return day
        try:
            return date(partner.year, claim["month"], claim["day"])
        except ValueError:
            return None

    grammar = _licensed_clause_grammar_terms()
    operands = []
    for claim in _calendar_claim_spans(clause):
        if claim["granularity"] != "day":
            continue
        candidates = day_candidates(claim)
        if not candidates:
            continue
        first, last = claim["span"]
        if _date_cites_a_record(clause, first, last, speakers=speakers, asked=asked):
            candidates = [day for day in candidates
                          if (day is not None and day in statement_days)
                          or (day is None and any((s.month, s.day) == (claim["month"], claim["day"]) for s in statement_days))]
            role = "record_time"
        elif not (_head_terms(_operand_clause_terms(clause, first, last)) - grammar):
            role = "bare"
        else:
            role = "event" if event_operand(claim) else ""
        if candidates and role:
            operands.append((claim, candidates, role, bool(span_only and role != "record_time" and span_only(claim))))

    # The asked subjects by name: a possessed subject ("When did <Name>'s father ...") is asked of <Name>.
    named = {re.sub(r"['’]s$", "", str(reference)) for reference in references or ()}

    def stated_at(record_day, day):
        # A record made at the record time (of an asked subject, when one is named) states the day.
        return any(found == day and (not named or actor in named)
                   for found, actor in (stated_days or {}).get(record_day, ()))

    pairs = []
    for index, (first_claim, first, first_role, first_approximate) in enumerate(operands):
        for second_claim, second, second_role, second_approximate in operands[index + 1:]:
            if "record_time" not in {first_role, second_role}:
                continue
            for left in first:
                for right in second:
                    if left is None and right is None:
                        continue
                    left_day, right_day = placed(first_claim, left, right), placed(second_claim, right, left)
                    if left_day is None or right_day is None or left_day == right_day:
                        continue
                    if (first_approximate and not stated_at(right_day, left_day)) or (
                            second_approximate and not stated_at(left_day, right_day)):
                        continue
                    pairs.append((*sorted((left_day, right_day)), first_role, second_role,
                                  first_approximate or second_approximate))
    values: set[str] = set()
    receipt: list[dict] = []
    for match in _PAST_DURATION_RE.finditer(clause) if pairs else ():
        quantity = _duration_quantity(match.group(1))
        unit = _UNIT_NORMALIZER.get(match.group(2).lower(), "")
        hit = next((pair for pair in pairs if quantity is not None and unit
                    and _interval_matches(quantity, unit, (pair[1] - pair[0]).days)), None)
        if hit is None:
            continue
        found = {value for value in _canonical_time_values(match.group(0)) if value.startswith("d")}
        values |= found
        receipt.append({"rule": "interval_to_a_cited_record_time", "dates": [hit[0].isoformat(), hit[1].isoformat()],
                        "operand_roles": sorted(hit[2:4]), "days": (hit[1] - hit[0]).days,
                        "approximate_operand_stated_by_record": hit[4], "values": sorted(found)})
    return values, receipt


def stated_past_time_claims(
    answer: Any, *, question: str, evidence_texts: Sequence[str] | None = None,
    current_year: int | None = None, reference_clock: ReferenceClock | None = None,
    decision_receipt: dict | None = None,
) -> tuple[str, ...]:
    """Unsupported asserted temporal values, using admitted event evidence.

    ``current_year`` is retained for call compatibility, not authority. Question
    values may be echoed as questions, never affirmed without evidence. A typed
    reference clock supports its own clause and one checked calendar-ago operand.
    Each sentence is checked with the same source view and inherited named
    subject; splitting cannot create a new person or donate a reference date.
    """
    body = str(answer or "")
    if not body.strip():
        return ()
    spans = _past_support_spans(evidence_texts, question=question)
    question_actors = _support_question_actors(question)
    question_actor = question_actors[0] if question_actors else ""
    # The user's own request for an approximate date read from the conversation opens the evidence
    # span of every record statement time the reader saw (_licensed_evidence_span).
    licensed = _request_licenses_statement_time(question)
    statement_times = _evidence_statement_times(evidence_texts) if licensed else []
    # The clock time each statement label carries, by statement date (_statement_time_clocks), and
    # every statement date: a record time an answer cites is read against these.
    statement_clocks = _evidence_statement_clocks(evidence_texts)
    statement_days = set(statement_times) if licensed else set(_evidence_statement_times(evidence_texts))
    # The conversation's people, and the request a turn naming nobody is read with: the conversation's
    # latest earlier question ("roughly when was that?" asks about its subject and event).
    speakers = _record_speakers(spans)
    prior_question = "" if question_actors else _prior_question(spans)
    binding_question = f"{prior_question}\n{question}" if prior_question else question
    asked_terms = _asked_event_terms(binding_question)
    interval_references = question_actors or (_support_question_actors(prior_question) if prior_question else ())
    stated_days = _record_stated_days(spans)
    unsupported = set()
    checks = []
    items = []
    previous_actor = ""
    for sentence, _separator in _answer_sentences(body):
        clause, clock_clause_verified = _without_verified_reference_clause(sentence, reference_clock)
        # Clock times the clause presents as when a record was made ("the 6:55 pm conversation").
        citations = _clock_citations(clause, speakers=speakers, asked=asked_terms)
        clock_receipt: list[dict] = []
        bound_clocks: set[str] = set()
        explicit_actor = _support_actor(clause)
        if _support_record_request(question) and _support_record_answer(clause) and (clause.lstrip().startswith("{") or any((label := re.match(r"\s*([a-zA-Z][a-zA-Z _-]*)\s*:", clause)) and _support_record_field_name(label.group(1)) in {_support_record_field_name(key) for key in span["record_fields"]} for span in spans if "record_fields" in span)):
            explicit_actor = ""
        if re.match(r"\s*(?:I|we|you)\b", clause, re.I):
            answer_actor = "user"
        elif explicit_actor:
            answer_actor = explicit_actor
        elif previous_actor and re.match(r"\s*(?:he|she|they|it)\b", clause, re.I):
            answer_actor = previous_actor
        else:
            answer_actor = ""
        if answer_actor:
            previous_actor = answer_actor
        answer_actor = _support_reference_actor(answer_actor, question)
        conflict = bool(question_actors and answer_actor and answer_actor not in question_actors)
        polarity = "negative" if _assertion_negated(clause) else "positive"
        supported = set()
        units = []
        communicative_receipt: list[dict] = []
        act_units: set = set()
        if not conflict:
            for span in spans:
                if span.get("polarity", "positive") == polarity and _span_supports_request(span, question, clause):
                    record_values = _record_span_values(span, question, clause) if "record_fields" in span else None
                    supported.update(record_values if record_values is not None else span["values"])
                    units.append(span["unit"])
                    if span["statement"] and _statement_is_answer_provenance(
                            clause, span["statement"], speaker=_span_speaker(span), question=question,
                            record=span.get("source_text", "")):
                        day = span["statement"]
                        supported.update((f"m{day.month}:{day.day}:{day.year}", f"y{day.year}"))
                    # The clock time of this request-bound record's own statement label.
                    bound_clocks.update(span.get("statement_clocks") or ())
                if span.get("polarity", "positive") == polarity and span["unit"] not in act_units:
                    act_units.add(span["unit"])
                    # The record's own speaker said, shared or asked it at the record's statement time.
                    # The record IS that act, so the question's act verb need not be in its words.
                    act_values, act_receipt = _communicative_act_values(clause, span, question, answer_actor)
                    supported |= act_values
                    communicative_receipt += act_receipt
                    if act_values:
                        units.append(span["unit"])
        derived = _derived_past_values(question, clause, spans) if not conflict and polarity == "positive" else set()
        calendar_values, calendar_receipt = _derived_reference_values(question, spans, reference_clock)
        subject_record = (_anaphoric_subject_record_values(question, clause, spans)
                          if not conflict and polarity == "positive" else set())
        statement_values, statement_receipt = (_statement_time_values(question, clause, answer_actor, spans)
                                               if not conflict and polarity == "positive" else (set(), []))
        # What the guard supports without the license's own statement-time rule: a month-level claim
        # under the license is read against this and its month, never against a licensed year.
        core = set(supported)
        if not conflict and polarity == "positive":
            supported.update(derived | calendar_values | subject_record | statement_values)
            core.update(derived | calendar_values | subject_record)
            core.update(value for entry in statement_receipt if entry["rule"] != "request_licensed_statement_time"
                        for value in entry["values"])
        items.append(dict(sentence=sentence, clause=clause, clock_clause_verified=clock_clause_verified, core=core,
                          answer_actor=answer_actor, conflict=conflict, polarity=polarity, supported=supported,
                          units=units, derived=derived, calendar_receipt=calendar_receipt,
                          subject_record=subject_record, statement_receipt=communicative_receipt + statement_receipt,
                          citations=citations, clock_receipt=clock_receipt, bound_clocks=bound_clocks))
    # An answer's computed value may sit in a different sentence from its operands ("19 days. You
    # launched ... on 2023-02-10 and signed ... on 2023-03-01."), so the arithmetic is answer-level.
    operand_values, operand_receipt = _checked_operand_arithmetic(question, spans, items)
    for item in items:
        if not item["conflict"] and item["polarity"] == "positive":
            item["supported"] |= operand_values
            item["core"] |= operand_values
        item["span_values"], item["span_receipt"] = set(), []
        if licensed:
            # No actor, event or clause binding: the request asked for the conversation date, and
            # any record's statement time the reader saw bounds what that approximation may be.
            accepted, withdrawn, item["span_receipt"] = _licensed_evidence_span(
                item["clause"], statement_times, item["supported"], item["core"])
            item["supported"] = (item["supported"] - withdrawn) | accepted
            item["span_values"] = accepted
            if not item["conflict"] and item["polarity"] == "positive":
                # A record-stated offset the clause cites with its record's statement time. Checked at
                # the offset's own granularity, after the span: a licensed year never carries a month.
                cited_values, cited_receipt = _cited_offset_values(question, item["clause"], item["answer_actor"], spans,
                                                                   prior_question=prior_question)
                item["supported"] |= cited_values
                item["span_receipt"] = item["span_receipt"] + cited_receipt
    # A yearless month-day repeats a full date: one the answer states and that is supported in its
    # own sentence ("November 16, 2023 ... Nov 16"), or a same-subject record's statement time that
    # the answer is entitled to (the request licenses it, or the answer presents it as when the
    # subject spoke). Only full dates the sentences already earned count; a yearless value never
    # earns its own year.
    stated_full_dates = {value for item in items if not item["conflict"] and item["polarity"] == "positive"
                         for value in _canonical_time_values(item["clause"])
                         if value.startswith("m") and value.count(":") == 2 and value in item["supported"]}
    for item in items:
        conflict, supported, clause = item["conflict"], item["supported"], item["clause"]
        values = _canonical_time_values(clause)
        def supported_value(value, supported=supported, conflict=conflict, span_values=item["span_values"]):
            if value in span_values:
                return True
            if value.startswith("unparsed_duration:") or conflict:
                return False
            return value in supported or (value.startswith("m") and value.count(":") == 1
                       and any(ev.startswith(value + ":") for ev in supported | stated_full_dates))

        def day_candidates(claim, supported=supported, span_values=item["span_values"]):
            # The supported full dates a day claim stands for; [None] for a yearless day supported
            # without a full date (it takes the year of the date it is read against).
            try:
                if claim["year"]:
                    value = f"m{claim['month']}:{claim['day']}:{claim['year']}"
                    return [date(claim["year"], claim["month"], claim["day"])] if value in supported or value in span_values else []
                value = f"m{claim['month']}:{claim['day']}"
                full = sorted({ev for ev in supported | stated_full_dates if ev.startswith(value + ":")})
                if full:
                    return [date(int(ev.split(":")[2]), claim["month"], claim["day"]) for ev in full]
                return [None] if value in supported or value in span_values else []
            except ValueError:
                return []

        interval_receipt: list[dict] = []
        if not conflict:
            # A clock time the clause cites as a record's time is that record's statement clock: the
            # record stated on the date the clock is stated with, else a request-bound record.
            for start, end, clock_values in item["citations"]:
                attached = _clock_attached_day(clause, start, end)
                if attached is None:
                    if clock_values & item["bound_clocks"]:
                        supported |= clock_values
                        item["clock_receipt"].append({"rule": "statement_clock", "binding": "request_bound_record",
                                                      "values": sorted(clock_values)})
                    continue
                days = [day for day in statement_clocks if (day.month, day.day) == (attached["month"], attached["day"])
                        and attached["year"] in (None, day.year)]
                found = next((day for day in days if clock_values & statement_clocks[day]), None)
                if found is not None:
                    supported |= clock_values
                    item["clock_receipt"].append({"rule": "statement_clock", "binding": "stated_date",
                                                  "statement": found.isoformat(), "values": sorted(clock_values)})
            if item["polarity"] == "positive":
                def event_operand(claim, clause=clause):
                    # Under the license a supported date's event binding is relaxed, as for the date
                    # itself; otherwise the date must be its own clause's event (checked-operand law).
                    if licensed:
                        return True
                    request_spans = [span for span in spans if span.get("polarity", "positive") == "positive"
                                     and "record_fields" not in span and _span_supports_request(span, question, clause)]
                    clause_terms = _operand_clause_terms(clause, *claim["span"])

                    def same(day, claim=claim):
                        return (day.month, day.day) == (claim["month"], claim["day"]) and claim["year"] in (None, day.year)

                    def span_days(span):
                        # Its written dates, and the single day a stated offset names ("two days ago").
                        return set(span["dates"]) | {window["start"] for window in span.get("windows") or []
                                                     if window["start"] == window["end"] and window["direction"] == "past"}

                    resolved = {day for span in request_spans for day in span_days(span)
                                if same(day) and _operand_span_is_the_clause_event(clause_terms, span, request_spans, same)}
                    return len(resolved) == 1

                def span_only(claim, span_values=item["span_values"]):
                    # Supported only by the evidence span the request's approximation opens.
                    return (f"m{claim['month']}:{claim['day']}" + (f":{claim['year']}" if claim["year"] else "")) in span_values

                interval_values, interval_receipt = _date_interval_values(
                    clause, day_candidates, statement_days=statement_days, event_operand=event_operand,
                    span_only=span_only, stated_days=stated_days, references=interval_references,
                    speakers=speakers, asked=asked_terms)
                supported |= interval_values
        missing = set() if _question_echo_or_decline(clause, question) else {value for value in values if not supported_value(value)}
        # A clock time written with no meridiem is one value with two readings: either one supports it.
        explicit_clock_values = {value for match in _PAST_CLOCK_TIME_RE.finditer(clause)
                                 for value in _canonical_time_values(match.group(0))
                                 if not any(found.endswith("x") for found in _canonical_time_values(match.group(0)))}
        for group in _ambiguous_clock_groups(clause):
            if any(supported_value(value) for value in group):
                missing -= group - explicit_clock_values
        unsupported.update(missing)
        checks.append({"sentence_sha256": hashlib.sha256(item["sentence"].encode()).hexdigest(),
                       "question_actor": question_actor, "answer_actor": item["answer_actor"], "actor_conflict": conflict,
                       "polarity": item["polarity"], "extracted_values": sorted(values), "supported_values": sorted(supported),
                       "unsupported_values": sorted(missing), "supporting_source_units": sorted(set(item["units"])),
                       "day_interval_values": sorted(item["derived"]), "calendar_derivation": item["calendar_receipt"],
                       "anaphoric_subject_record_values": sorted(item["subject_record"]),
                       "statement_time_derivation": item["statement_receipt"] + item["span_receipt"],
                       "statement_clock_derivation": item["clock_receipt"],
                       "date_interval_derivation": interval_receipt,
                       "reference_clause_verified": item["clock_clause_verified"],
                       "rejection_reasons": ([] if not missing else
                           ["actor_conflict"] if conflict else
                           ["unsupported_numeral_form"] if any(value.startswith("unparsed_duration:") for value in missing) else
                           ["no_compatible_asserted_source_or_checked_derivation"])})
    if isinstance(decision_receipt, dict):
        decision_receipt.update({"checks": checks, "unsupported_values": sorted(unsupported),
                                 "operand_derivation": operand_receipt,
                                 "reference_request_sha256": reference_clock.request_sha256 if isinstance(reference_clock, ReferenceClock) else "",
                                 "reference_clock_sha256": reference_clock.clock_sha256 if isinstance(reference_clock, ReferenceClock) else ""})
    return tuple(sorted(unsupported))


#: The frame an incidental date stands in: the preposition introducing it ("on", "in", "as of",
#: "back in", "dated", "since", "until", ...), at a sentence start or after a space, comma or bracket.
_INCIDENTAL_TIME_FRAME_RE = re.compile(
    r"(?:^|(?<=[\s,(]))(?:(?:just\s+)?(?:before|after)|(?:back\s+)?(?:on|in|around)|at|during|as\s+of|dated|by"
    r"|(?:ever\s+)?since|until|till|from)\s*$",
    re.I | re.M,
)
#: Asks for a calendar unit the time-value request grammar does not name ("what month", "which week").
_ASKS_CALENDAR_UNIT_RE = re.compile(
    r"\b(?:what|which)\s+(?:month|week|weekend|season|decade|period|time\s+of\s+(?:the\s+)?year)\b"
    r"|\bhow\s+(?:recently|long\s+ago)\b|\b(?:since|by|until)\s+when\b",
    re.I,
)


def _requests_a_time(question: str) -> bool:
    """The user's request asks for a time: a time-value question, a calendar unit, or the license.

    Anything else ("What picture did Dana share?") asks for no time, and a date the answer adds to
    it is incidental to the answer.
    """
    return bool(_support_requests_time_value(question) or _ASKS_CALENDAR_UNIT_RE.search(str(question or ""))
                or _request_licenses_statement_time(question))


#: A duration stands as an adjunct behind one of these ("for two years", "over the past three
#: months", "within about a week"); a bare quantity after a verb ("it took two weeks") is an argument.
_INCIDENTAL_DURATION_LEAD_RE = re.compile(
    r"(?:^|(?<=[\s,(]))(?:(?:in|over|during|for|across)\s+the\s+(?:past|last|previous|first|following)"
    r"|for|within|after|during|throughout|in)"
    r"(?:\s+(?:about|around|roughly|approximately|nearly|almost|over|under|more\s+than|less\s+than"
    r"|just\s+(?:over|under)|only|some|at\s+least))?\s+$",
    re.I,
)
#: ... or in front of one of these ("two years earlier", "three weeks ago", "a month before that").
_INCIDENTAL_DURATION_TAIL_RE = re.compile(
    r"\s+(?:ago|(?:earlier|later)(?!\s+than)|prior(?!\s+to)|back|previously|beforehand|afterwards?"
    r"|(?:before|after)(?:\s+that)?(?=\s*(?:[,.;:!?)\u2014\u2013]|$)))\b",
    re.I,
)
#: A hedge in front of a removed quantity goes with it ("about two years ago").
_INCIDENTAL_QUANTITY_HEDGE_RE = re.compile(
    r"(?:^|(?<=[\s,(]))(?:about|around|roughly|approximately|nearly|almost|over|under|more\s+than"
    r"|less\s+than|just|only|some|at\s+least|a\s+good|like|maybe|probably)\s+$",
    re.I,
)
#: A determiner, with or without a preposition, in front of a duration ("for the six months prior").
#: "her" and "that" are left out: "adopted her two years earlier" and "said that two years ago" carry
#: an object or a complementizer, not a determiner.
_INCIDENTAL_DETERMINED_DURATION_RE = re.compile(
    r"(?:^|(?<=[\s,(]))(?:(?P<preposition>for|in|over|during|within|after|across)\s+)?"
    r"(?:the|a|an|this|these|those|his|their|my|your|our|its)\s+$",
    re.I,
)
#: A clause that states a condition or an example ("if you finished in 40 minutes", "e.g. ...") is
#: not detachable: removing it leaves its consequence asserted on its own.
_HYPOTHETICAL_CLAUSE_RE = re.compile(
    r"\b(?:if|unless|suppose|supposing|e\.g|i\.e|for\s+(?:example|instance))\b", re.I)
#: A clock time stands as an adjunct behind one of these ("at 9 am", "until 10:30").
_INCIDENTAL_CLOCK_LEAD_RE = re.compile(
    r"(?:^|(?<=[\s,(]))(?:at\s+(?:about|around)|at|by|before|after|until|till|from|around)\s+$", re.I)
#: Edges of a clause inside one claim unit: a comma (not one before a year, "May 3, 2023"), a
#: semicolon, a dash, or a bracket.
_RELATIVE_CLAUSE_OPENING_RE = re.compile(r"\s*(?:which|who|whom|whose|where)\b", re.I)
_TIME_CLAUSE_EDGE_RE = re.compile(r",(?=\s)(?!\s*\d{4}\b)|;|[\u2014\u2013]|\s-\s|[()]")


def _unsupported_time_occurrences(text: str, unsupported: set[str]) -> list[tuple[int, int, str]]:
    """Every extracted time expression in ``text`` carrying an unsupported value, with its kind."""
    found: list[tuple[int, int, str]] = []
    for claim in _calendar_claim_spans(text):
        first, last = claim["span"]
        if unsupported & _canonical_time_values(text[first:last]):
            found.append((first, last, "day" if claim["granularity"] == "day" else "calendar"))
    for kind, pattern in (("duration", _PAST_DURATION_RE), ("clock", _PAST_CLOCK_TIME_RE)):
        for match in pattern.finditer(text):
            if unsupported & _canonical_time_values(match.group(0)):
                found.append((match.start(), match.end(), kind))
    return found


def _without_incidental_times(text: str, unsupported: set[str]) -> str:
    """``text`` with each unsupported time removed in its adverbial frame, the rest kept.

    A date stands in "On <date>, ...", "..., on <date>", "(<date>)", "in <month year>", "as of
    <date>"; a duration in "for two years", "over the past three months", "two years earlier",
    "three weeks ago"; a clock time in "at 9 am", "until 10:30". A time that is no adjunct ("the 2019
    trip", "it took two weeks") stays for the caller's recheck to find.
    """
    def optional_parenthesis(match):
        inner = match.group(1)
        if not unsupported & _canonical_time_values(inner):
            return match.group(0)
        remainder = _PAST_DATE_RE.sub("", inner)
        remainder = _CLAIM_MONTH_YEAR_RE.sub("", remainder)
        remainder = _PAST_YEAR_RE.sub("", remainder)
        remainder = re.sub(r"\b(?:on|at|in|before|after|around|that|was|back|as|of|about|it|said|you|i|then)\b", "", remainder, flags=re.I)
        return "" if not re.search(r"[a-zA-Z]", remainder) else match.group(0)

    text = re.sub(r"\(([^()]*)\)", optional_parenthesis, text)
    # A day is removable wherever it stands; a month-year or a bare year only as an adverbial
    # ("in March 2023", "back in 2019") -- "the 2019 trip" is no adjunct and stays to fail the recheck.
    targets = []
    for first, last, kind in _unsupported_time_occurrences(text, unsupported):
        if kind in ("day", "calendar"):
            if kind != "day" and not _INCIDENTAL_TIME_FRAME_RE.search(text[:first]):
                continue
            targets.append((first, last, None))
            continue
        lead_re = _INCIDENTAL_DURATION_LEAD_RE if kind == "duration" else _INCIDENTAL_CLOCK_LEAD_RE
        lead = lead_re.search(text[:first])
        tail = _INCIDENTAL_DURATION_TAIL_RE.match(text, last) if kind == "duration" else None
        if not lead and not tail:
            continue
        hedge = _INCIDENTAL_QUANTITY_HEDGE_RE.search(text[:first])
        start = lead.start() if lead else hedge.start() if hedge else first
        if not lead:
            # "for the six months prior" is one adjunct; "the six months prior" with no preposition is
            # a noun phrase the sentence needs, and is left for the clause or sentence rule.
            determined = _INCIDENTAL_DETERMINED_DURATION_RE.search(text[:start])
            if determined and not determined.group("preposition"):
                continue
            if determined:
                start = determined.start()
        targets.append((start, tail.end() if tail else last, start))
    kept: list[tuple[int, int, int | None]] = []
    for target in sorted(targets, key=lambda item: (item[0], -item[1])):
        if kept and target[0] < kept[-1][1]:
            continue
        kept.append(target)
    for first, last, framed_start in sorted(kept, reverse=True):
        start = first
        # An explicit dash introduces a detachable explanation such as
        # "gray — done just before May 27". Do not remove a main count
        # following the date ("on November 30 — 25 postcards total").
        dash = max(text.rfind("—", 0, start), text.rfind("–", 0, start))
        # Detach only an explanation the date ENDS; "— your notes mention it on Feb 11, 2023,
        # but nothing about who" continues past the date, and cutting at the date left
        # "I don't have that, but nothing about who gave it to you".
        ends_clause = re.match(r"\s*(?:[.!?)\u2014\u2013]|$)", text[last:])
        if dash >= 0 and ends_clause and not re.search(r"[.!?]", text[dash:start]) and not re.search(r"\d", text[dash:start]):
            text = text[:dash].rstrip() + text[last:]
            continue
        frame = _INCIDENTAL_TIME_FRAME_RE.search(text[:start]) if framed_start is None else True
        if frame:
            start = frame.start() if framed_start is None else framed_start
            # The frame takes the space in front of it, except at a sentence start.
            space = re.search(r"[ \t]+$", text[:start])
            if space and not re.search(r"(?:^|[.!?\n])[ \t]*$", text[:start]):
                start = space.start()
        before, after = text[:start], text[last:]
        if re.search(r"(?:^|[.!?]\s+|\n\s*)$", before) and re.match(r"\s*,", after):
            # A fronted adverbial ("On <date>, Dana shared ...") leaves its clause behind.
            after = re.sub(r"^\s*,\s*", "", after)
            after = after[:1].upper() + after[1:]
        elif framed_start is not None and re.search(r"(?:^|[.!?]\s+|\n\s*)$", before):
            # "Two years ago she adopted ..." leaves a sentence that starts with a capital.
            after = after.lstrip(" \t")
            after = after[:1].upper() + after[1:]
        elif re.search(r"[:;\u2014\u2013]\s*$", before) and re.match(r"\s*,", after):
            # ... and after a colon, semicolon or dash ("two numbers: on <date>, you said").
            before = before.rstrip() + " "
            after = re.sub(r"^\s*,\s*", "", after)
        elif frame and re.search(r",\s*$", before) and re.match(r"\s*,", after):
            # A parenthetical adverbial ("Dana, on <date>, shared ...") takes both commas with it;
            # an appositive date ("on Saturday, Feb 11, 2023, but ...") leaves one comma.
            before = re.sub(r",\s*$", " ", before)
            after = re.sub(r"^\s*,", "", after)
        text = before + after
    return text


def _without_time_clauses(text: str, unsupported: set[str]) -> str:
    """``text`` without the dash, comma or bracket clause that carries an unsupported time.

    "... the perfect pet for her — she adopted her two years earlier when lonely, and ..." loses
    "— she adopted her two years earlier when lonely". The opening clause of a sentence is its main
    clause and is never removed here, and neither is a clause stating a condition or an example. When
    any unsupported time has no detachable clause, ``text`` is returned unchanged so the sentence rule
    decides instead of a partial cut.
    """
    edges = list(_TIME_CLAUSE_EDGE_RE.finditer(text))
    cuts: list[tuple[int, int]] = []
    for first, last, _kind in _unsupported_time_occurrences(text, unsupported):
        left = next((edge for edge in reversed(edges) if edge.end() <= first), None)
        if left is None or not text[:left.start()].strip() or _HYPOTHETICAL_CLAUSE_RE.search(text[:last]):
            return text
        if left.group(0) == "(":
            right = next((edge for edge in edges if edge.start() >= last and edge.group(0) == ")"), None)
            if right is None:
                return text
            cuts.append((left.start(), right.end()))
            continue
        kind_of_left = left.group(0).strip()
        if kind_of_left == ",":
            right = next((edge for edge in edges if edge.start() >= last), None)
        else:
            # A semicolon or dash clause runs to the next edge of its own kind: its inner commas are
            # its own ("; Omar has been throwing pots since <date>, three evenings a week").
            right = next((edge for edge in edges if edge.start() >= last and edge.group(0).strip() == kind_of_left), None)
        if right is not None and right.group(0) == "(":
            right = None
        if right is not None and (
                (left.group(0).strip() in "\u2014\u2013-" and left.group(0).strip() == right.group(0).strip())
                or (left.group(0) == right.group(0) == "," and _RELATIVE_CLAUSE_OPENING_RE.match(text, left.end()))):
            # A dash pair or a comma-bounded relative clause is parenthetical: both edges go with it
            # ("the move, which took two years, was hard" -> "the move was hard").
            cuts.append((left.start(), right.end()))
        elif right is not None:
            cuts.append((left.start(), right.start()))
        else:
            end = re.search(r"[.!?]*\s*$", text[last:])
            cuts.append((left.start(), last + end.start()))
    merged: list[tuple[int, int]] = []
    for cut in sorted(cuts):
        if merged and cut[0] < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], cut[1]))
        else:
            merged.append(cut)
    for start, end in reversed(merged):
        after = text[end:]
        text = text[:start].rstrip() + (" " + after.lstrip() if re.match(r"[ \t]*[A-Za-z0-9]", after) else after)
    return text


def _without_time_tokens(text: str, unsupported: set[str]) -> str:
    """``text`` with each unsupported time expression itself deleted ("for the 2019 festival" ->
    "for the festival"): the last resort before an answer to a question that asks no time is lost."""
    for first, last, kind in sorted(_unsupported_time_occurrences(text, unsupported), reverse=True):
        hedge = _INCIDENTAL_QUANTITY_HEDGE_RE.search(text[:first])
        start = hedge.start() if hedge and kind in ("duration", "clock") else first
        suffix = re.match(r"(?:-[a-zA-Z]+)+", text[last:])
        tail = _INCIDENTAL_DURATION_TAIL_RE.match(text, last) if kind == "duration" else None
        end = last + suffix.end() if suffix else tail.end() if tail else last
        text = text[:start] + text[end:]
    return text


def _tidy_incidental_edit(text: str) -> str:
    # Emphasis emptied by the edit ("he began **** and") goes with what it emphasized.
    text = re.sub(r"(\*\*|__)[ \t]*\1", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    # A relative copula whose whole complement was the removed time ("..., which is 10 days ago.",
    # "— that's two weeks ago") goes with it. Left behind, "which is." read as a fragment, the edit
    # was refused and a correct answer to a question asking no time was replaced whole (measured
    # 2026-10-06: "A smoker — you said you got it on March 15, which is 10 days ago.").
    text = re.sub(r"[,;]?\s*\bwhich\s+(?:is|was|were)\s*(?=[.!?)\]]|$)", "", text, flags=re.I)
    text = re.sub(r"[,;]?\s*[\u2014\u2013]?\s*\b(?:that's|that\u2019s|i\.e\.)\s*(?=[.!?)\]]|$)", "", text, flags=re.I)
    text = re.sub(r"\s+([,.!?])", r"\1", text).strip()
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"[,;]\s*([.!?])", r"\1", text)
    text = re.sub(r"\s*[\u2014\u2013]\s*([.!?]|$)", r"\1", text)
    # A fronted time removed before its dash or comma ("Since <year> — his ...") leaves no lead mark.
    stripped = re.sub(r"^[,;:\u2014\u2013]+\s*", "", text)
    if stripped != text:
        text = stripped[:1].upper() + stripped[1:]
    return text


def _without_incidental_time_claims(answer: str, claims, receipt: dict, *, question: str,
                                    evidence_texts, reference_clock) -> str:
    """The answer to a request that asks no time, without the unsupported times it adds.

    The question asks for no time, so a time the answer adds is incidental and the answer is never
    replaced whole. Each claim unit loses only the times unsupported in it, at the smallest grain
    that holds: the time phrase in its adverbial frame, then the dash, comma or bracket clause that
    carries it, then the sentence. When every sentence carries one, the first sentence ships without
    its time phrases. Every edit is rechecked through the same support contract, so an unsupported
    time never ships; the notice remains only when nothing with content would be left.
    """
    units, checks = _answer_sentences(answer), receipt.get("checks") or []
    per_unit = len(units) == len(checks)
    fragments = len(_EDIT_FRAGMENT_RE.findall(answer))

    def rechecked(candidate: str, *, grammar: bool = True) -> str | None:
        candidate = _tidy_incidental_edit(candidate)
        if grammar and len(_EDIT_FRAGMENT_RE.findall(candidate)) > fragments:
            return None
        if _support_terms(candidate) and not stated_past_time_claims(
            candidate, question=question, evidence_texts=evidence_texts, reference_clock=reference_clock,
        ):
            return candidate
        return None

    def unit_edit(edit, sentence: str, unsupported: set[str]) -> str:
        result = edit(sentence, unsupported)
        # A labelled line whose value was the time ("- started: 14 months ago") has nothing left to say.
        return "" if re.search(r":[ \t*_]*$", result) and not re.search(r":[ \t*_]*$", sentence) else result

    def edited(edit) -> str:
        # A value another sentence supports stays where it is supported.
        if per_unit:
            parts = []
            for (sentence, separator), check in zip(units, checks):
                text = unit_edit(edit, sentence, set(check["unsupported_values"])) if check["unsupported_values"] else sentence
                if text.strip():
                    parts.append(text + separator)
            return "".join(parts)
        return unit_edit(edit, answer, set(claims))

    for edit in (_without_incidental_times,
                 # A time its frame already took needs no clause; the clause rule sees only what is left.
                 lambda text, unsupported: _without_time_clauses(_without_incidental_times(text, unsupported), unsupported)):
        result = rechecked(edited(edit))
        if result is not None:
            return result
    if per_unit:
        surviving = [unit for unit, check in zip(units, checks) if not check["unsupported_values"]]
        if surviving and len(surviving) < len(units):
            result = "".join(sentence + separator for sentence, separator in surviving).strip()
            if _support_terms(result):
                return result
    candidate = units[0][0] if units else answer
    for _attempt in range(4):
        found = set(stated_past_time_claims(candidate, question=question, evidence_texts=evidence_texts,
                                            reference_clock=reference_clock))
        if not found:
            break
        candidate = _tidy_incidental_edit(_without_time_tokens(_without_incidental_times(candidate, found), found))
    result = rechecked(candidate)
    return result if result is not None else unverified_past_time_notice(question)


def replace_unsupported_past_time_claims(
    answer: str,
    *,
    question: str,
    evidence_texts: Sequence[str] | None = None,
    reference_clock: ReferenceClock | None = None,
    decision_receipt: dict | None = None,
) -> str:
    """Withdraw sentences stating past-event times nothing supplied supports.

    A time added to an answer whose question asks for no time (a reason, a color, a location, a
    count) is an optional claim: remove that time without erasing the primary answer
    (:func:`_without_incidental_time_claims`). Direct time requests retain the existing
    sentence-granularity law. The notice states the gap and names no value, exactly like the
    live-value notice.
    """

    receipt = decision_receipt if isinstance(decision_receipt, dict) else {}
    claims = stated_past_time_claims(answer, question=question, evidence_texts=evidence_texts,
                                    reference_clock=reference_clock, decision_receipt=receipt)
    if not claims:
        return str(answer or "")
    if not _requests_a_time(question):
        return _without_incidental_time_claims(str(answer or ""), claims, receipt, question=question,
                                               evidence_texts=evidence_texts, reference_clock=reference_clock)
    sentences = _answer_sentences(str(answer or ""))
    if len(sentences) <= 1:
        return unverified_past_time_notice(question)
    surviving: list[tuple[str, str]] = []
    convicted = False
    for index, unit in enumerate(sentences):
        if receipt["checks"][index]["unsupported_values"]:
            convicted = True
            continue
        surviving.append(unit)
    if not surviving or not convicted:
        return unverified_past_time_notice(question)
    result = "".join(sentence + separator for sentence, separator in surviving).strip()
    return result + " " + unverified_past_time_notice(question)


def unverified_past_time_notice(question: Any = "") -> str:
    """What ships instead of an invented past-event time. Names no value.

    ``question`` is kept for call compatibility. The notice does not repeat the request: echoing
    the user's turn pasted their own instructions back as part of the answer.
    """

    return (
        "I don't have that time in anything I can see from our conversation, so I am not "
        "going to state one."
    )


# --- Wrong-relation answers: a date delivered where a cost was asked ---------------------------
#
# V corpus F11-03 on the frozen head 9174b42c: "What did the ridge mast anemometer cost to
# service?" against a maintenance log that records only a recalibration date. The near-miss
# record is legitimately retrieved (right subject), and the owner review's direction is an
# ANSWER contract, not evidence suppression: a reply that delivers the date AS the answer to
# the cost ask has answered a different question. Three conjuncts, all structural:
#
#   1. the question asks a MONETARY relation (closed class: cost/price/charge/fee, or the
#      "how much did/does ... cost" construction) and does NOT also carry a temporal
#      interrogative half ("when did you book it") that a date answer may belong to;
#   2. the answer asserts date values and NO monetary value -- a date beside a cost is
#      labeled context, a date with no cost anywhere is the whole delivery;
#   3. nothing the request carried (current user text, hydrated history, retrieved
#      candidates, assembled context, notes) contains a monetary value, so no cost existed
#      to quote.
#
# A sentence that already DECLINES the asked relation ("no recorded cost", "don't have the
# cost") may present the related record as labeled context -- that is the honest answer, not
# the failure. The notice names no value, exactly like the family's other notices.
_MONETARY_ASK_RE = re.compile(
    r"\bhow\s+much\s+(?:did|does|was|is|do|were|are|would|will)\b[^.?!]{0,60}?\b(?:cost|charge|fee)\b"
    r"|\b(?:cost|price|charge[d]?|fee|fares?)\b",
    re.IGNORECASE,
)
_TEMPORAL_INTERROGATIVE_RE = re.compile(
    r"\b(?:when|what\s+(?:date|day|time)|which\s+day|how\s+long)\b",
    re.IGNORECASE,
)
_CALENDAR_DATE_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b"
    r"|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_COST_DECLINE_MARKER_RE = re.compile(
    r"\b(?:no\s+(?:recorded\s+|known\s+|listed\s+|noted\s+)?(?:cost|price|charge|fee)"
    r"|don'?t\s+have\s+(?:the\s+|a\s+|any\s+)?(?:recorded\s+|known\s+|listed\s+)?(?:cost|price|charge|fee)"
    r"|no\s+cost\s+(?:on\s+(?:file|record)|recorded|listed)"
    r"|cost\s+(?:isn'?t|is\s+not|wasn'?t|was\s+not)\s+(?:recorded|listed|noted))\b",
    re.IGNORECASE,
)


def stated_wrong_relation_cost_answers(
    answer: Any,
    *,
    question: Any,
    evidence_texts: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """The date values this reply delivers where the question asked a cost.

    Returns the convicted date strings (sentence-cleaned set), or ``()`` when any
    conjunct fails. Purely textual; the caller supplies the request material.
    """

    q = " ".join(str(question or "").split())
    body = str(answer or "")
    if not q or not body.strip():
        return ()
    if not _MONETARY_ASK_RE.search(q) or _TEMPORAL_INTERROGATIVE_RE.search(q):
        return ()
    if not _CALENDAR_DATE_RE.search(body):
        return ()
    if _LIVE_PRICE_RE.search(body):
        # A monetary value in the answer answers the asked relation; the date is
        # context beside it, exactly the labeled-mention shape that must survive.
        return ()
    material = "\n".join(str(text or "") for text in (evidence_texts or []))
    if _LIVE_PRICE_RE.search(material):
        # A cost existed in the request material; the answer may quote it however
        # it likes -- this guard has no standing.
        return ()
    convicted: list[str] = []
    for match in _CALENDAR_DATE_RE.finditer(body):
        window = body[max(0, match.start() - _LIVE_CLAIM_WINDOW) : match.end() + _LIVE_CLAIM_WINDOW]
        if _COST_DECLINE_MARKER_RE.search(window):
            continue
        value = " ".join(match.group(0).split())
        if value not in convicted:
            convicted.append(value)
    return tuple(convicted)


def replace_wrong_relation_cost_answers(
    answer: str,
    *,
    question: str,
    evidence_texts: Sequence[str] | None = None,
) -> str:
    """Withdraw sentences delivering dates where a cost was asked; keep the rest.

    Same sentence-granularity law as the past-time twin: the claim lives in the
    sentence that delivers the wrong-relation value; sentences that decline the
    asked relation may keep their labeled context; nothing separable surviving
    means the whole answer goes.
    """

    claims = stated_wrong_relation_cost_answers(
        answer, question=question, evidence_texts=evidence_texts
    )
    if not claims:
        return str(answer or "")
    sentences = [part for part in _SENTENCE_SPLIT_RE.split(str(answer or "")) if part.strip()]
    if len(sentences) <= 1:
        return unverified_cost_notice(question)
    surviving: list[str] = []
    convicted = False
    for sentence in sentences:
        if stated_wrong_relation_cost_answers(
            sentence, question=question, evidence_texts=evidence_texts
        ):
            convicted = True
            continue
        surviving.append(sentence)
    if not surviving or not convicted:
        return unverified_cost_notice(question)
    return " ".join(surviving).rstrip() + " " + unverified_cost_notice(question)


def unverified_cost_notice(question: Any = "") -> str:
    """What ships instead of a wrong-relation date. Names no value."""

    subject = " ".join(str(question or "").split())
    if len(subject) > 120:
        subject = subject[:117].rstrip() + "..."
    if subject:
        return (
            "I don't have a recorded cost for that in anything I can see from our conversation, "
            "so I am not going to state one. The request was: " + subject
        )
    return (
        "I don't have a recorded cost for that in anything I can see from our conversation, "
        "so I am not going to state one."
    )


__all__ = [
    "StreamReleaseGate",
    "answer_is_unfulfilled_intent",
    "claims_pending_tool",
    "delivers_a_withdrawal_notice",
    "foreign_markers",
    "has_restored_evidence_provenance",
    "is_reasoning_only",
    "is_ungrounded",
    "live_value_windows",
    "reasoning_only_markers",
    "replace_unobserved_live_claims",
    "replace_unsupported_past_time_claims",
    "replace_wrong_relation_cost_answers",
    "scrub_foreign_markers",
    "stated_past_time_claims",
    "stated_wrong_relation_cost_answers",
    "strip_reasoning_block",
    "turn_ran_observations",
    "unobserved_live_value_claims",
    "unverified_live_value_notice",
    "unverified_past_time_notice",
]
