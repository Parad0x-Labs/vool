"""Parse a tool call a model emitted as TEXT, in a dialect that is not ours.

VOOL speaks one tool dialect: a JSON object carrying `intent` and `arguments`. Several open models
speak a different one and emit it inside the message content rather than in a provider-native
`tool_calls` array. `core/model_output_guard.py` classifies those tags as tokenizer junk and STRIPS
them (`_TOOL_BLOCK_RE`), so the call is deleted before anything tries to read it, and the turn ends
at "the model returned an invalid tool payload with no intent name".

Measured 2026-07-31 on `laguna-s-2.1:free`, asked to audit a real file:

    </think><tool_call>read<arg_key>path</arg_key><arg_value>api/apache/...</arg_value></tool_call>

That is a correct intent expressed in the Qwen/Hermes XML dialect. It was discarded. On the retry
the model — now holding no working tools and still under instruction to read a file — FABRICATED
the read: it emitted a `<function_results>` block containing an invented Python module that does
not exist in the repository, and presented it as tool output. VOOL refused it, which is the right
outcome, but the refusal was treating a downstream symptom. A model with no working tools invents
its own call shape, then invents the result.

So this module is a compatibility layer, not a convenience: recognising the dialect is what stops
the model from hallucinating in place of a tool it could not reach.

Supported forms, all reduced to the canonical `{"intent": ..., "arguments": {...}}`:

  1. arg_key/arg_value pairs   <tool_call>read<arg_key>path</arg_key><arg_value>x.py</arg_value>...
  2. JSON inside the block     <tool_call>{"name": "read", "arguments": {"path": "x.py"}}</tool_call>
  3. Attribute-named function  <function=read>{"path": "x.py"}</function>

Nothing here EXECUTES anything. It returns a candidate payload; the existing contract layer still
resolves the name against registered intents and validates arguments, so an unknown or malformed
call fails exactly as it does today.
"""
from __future__ import annotations

import json
import re
from typing import Any

# `<tool_call>`, `<tool_code>`, `<function_call>`, `<invoke>` — the wrappers the guard already knows
# by name. Kept in sync with model_output_guard._TOOL_TAG_NAMES by intent, not by import, because
# the guard's list is about what to STRIP and this one is about what to READ.
_BLOCK_RE = re.compile(
    r"<(tool_call|tool_code|function_call|invoke|tool_use)\b[^>]*>(?P<body>.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
# `<function=read>{...}</function>` / `<invoke name="read">`
_NAMED_FN_RE = re.compile(
    r"<(?:function|invoke)[=\s]+(?:name=)?[\"']?(?P<name>[A-Za-z0-9_.\-]{1,64})[\"']?[^>]*>"
    r"(?P<body>.*?)</(?:function|invoke)>",
    re.IGNORECASE | re.DOTALL,
)
_ARG_PAIR_RE = re.compile(
    r"<arg_key>(?P<key>.*?)</arg_key>\s*<arg_value>(?P<value>.*?)</arg_value>",
    re.IGNORECASE | re.DOTALL,
)
# The leading bare name in `<tool_call>read<arg_key>…`, before the first tag.
_LEADING_NAME_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9_.\-]{1,64})\s*(?=<|\{|$)")


def _coerce_scalar(raw: str) -> Any:
    """`"3"` stays 3, `"true"` stays True, everything else stays the string it was.

    The dialect is untyped — every value arrives as text — while the contract layer downstream
    validates against a real schema. Coercing the two unambiguous cases here means an
    `max_lines`-style integer argument does not fail validation purely for being quoted.
    """

    text = raw.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d{1,18}", text):
        return int(text)
    return text


def _arguments_from_body(body: str) -> dict[str, Any]:
    pairs = _ARG_PAIR_RE.findall(body)
    if pairs:
        return {str(key).strip(): _coerce_scalar(value) for key, value in pairs}
    # A JSON object somewhere in the body — the other common shape.
    start = body.find("{")
    end = body.rfind("}")
    if start >= 0 and end > start:
        try:
            loaded = json.loads(body[start : end + 1])
        except (ValueError, TypeError):
            return {}
        if isinstance(loaded, dict):
            inner = loaded.get("arguments")
            if isinstance(inner, dict):
                return dict(inner)
            # `{"name": ..., "path": ...}` — arguments inline alongside the name.
            return {k: v for k, v in loaded.items() if k not in {"name", "intent", "tool"}}
    return {}


def _name_from(body: str) -> str:
    leading = _LEADING_NAME_RE.match(body)
    if leading:
        return str(leading.group("name")).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start >= 0 and end > start:
        try:
            loaded = json.loads(body[start : end + 1])
        except (ValueError, TypeError):
            return ""
        if isinstance(loaded, dict):
            for key in ("intent", "name", "tool"):
                candidate = str(loaded.get(key) or "").strip()
                if candidate:
                    return candidate
    return ""


def parse_text_tool_call(text: str) -> dict[str, Any] | None:
    """Return a canonical `{"intent", "arguments"}` payload, or None when there is no call.

    None means "no foreign tool call here" — NOT "this failed". Callers must treat None as the
    ordinary no-tool-call case and leave existing behaviour untouched, so prose that merely
    mentions `<tool_call>` cannot be turned into an execution.
    """

    if not text or "<" not in text:
        return None

    for match in _BLOCK_RE.finditer(text):
        body = match.group("body") or ""
        name = _name_from(body)
        if not name:
            continue
        return {"intent": name, "arguments": _arguments_from_body(body)}

    for match in _NAMED_FN_RE.finditer(text):
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        return {"intent": name, "arguments": _arguments_from_body(match.group("body") or "")}

    return None


def looks_like_fabricated_tool_result(text: str) -> bool:
    """True when the model wrote a tool RESULT itself instead of calling a tool.

    The same laguna turn that had its call stripped came back with a `<function_results>` block
    holding an invented file. A model narrating a result it never received is a different failure
    from a malformed call, and the two must not be reported with the same message: one is a dialect
    gap we can fix, the other is fabrication that must never be shown to the user as evidence.
    """

    if not text:
        return False
    return bool(
        re.search(r"<function_results\b", text, re.IGNORECASE)
        or re.search(r"<(tool_response|tool_result|observation)\b", text, re.IGNORECASE)
    )


# A JSON array of FILENAMES, immediately followed by something that is plainly not JSON. This is
# what a build lane's internal file-list prompt produces when the model keeps generating past the
# array it was asked for -- and it reached an operator's screen verbatim on 2026-08-01:
#
#     ["security.py", "test_security.py", "README.md"]import hmac
#     import secrets
#     ...
#
# The parser that should have consumed it is fixed, but the four passthrough points that printed it
# had no idea what they were holding. This is the last line of defence: internal payload is never a
# human answer, whatever went wrong upstream.
_FILENAME_ARRAY_LEAD_RE = re.compile(
    r'^\s*\[\s*"[^"\n]+\.[A-Za-z0-9]{1,8}"\s*(?:,\s*"[^"\n]+"\s*)*\]',
)


# The runtime's own internal-state vocabulary. These are field names the RUNTIME defines for the
# records it passes between steps -- a finding, a verdict, an execution policy -- not words a person
# asked for. A reply that is nothing but an object built from them is the internal state escaping
# through the response layer instead of being rendered, which is what an operator saw on
# 2026-08-01: the answer to "audit this file" was the nominate step's JSON object.
_INTERNAL_RECORD_KEYS = frozenset(
    {
        "cited_line_text", "failure_scenario", "line_start", "line_end", "terminal_state",
        "execution_policy", "blocked_reason", "finding_id", "verification_status",
        "claimed_failure_mechanism", "expected_failure_behavior", "evidence_sources",
        "target_files", "target_symbols_or_lines", "confidence", "counterexample",
        "remaining_proof", "refuted", "screened_out", "artifact_checks", "reason_code",
        "test_command", "returncode", "proof", "usage_line", "model_label", "claim_key",
    }
)
_INTERNAL_RECORD_MIN_KEYS = 3


def looks_like_internal_state_record(text: str) -> bool:
    """True when the whole reply is one of the runtime's internal records, verbatim.

    Both halves are required and both are the point. The reply must be EXACTLY one JSON object
    (nothing before it, nothing after it), so prose that quotes a record keeps its answer; and it
    must carry at least three of the runtime's own state field names, so a JSON object a person
    actually asked for is not mistaken for one that leaked. Structured state is how the runtime
    thinks; it is never how it speaks.
    """

    body = str(text or "").strip()
    if not body.startswith("{") or not body.endswith("}"):
        return False
    try:
        payload = json.loads(body)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    keys = set()
    stack: list[Any] = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            keys.update(str(key) for key in current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current[:16])
    return len(keys & _INTERNAL_RECORD_KEYS) >= _INTERNAL_RECORD_MIN_KEYS


def looks_like_internal_payload(text: str) -> bool:
    """True when a reply is machine scaffolding rather than an answer to a person.

    Deliberately narrow. Each shape below is something a human answer never opens with, and the cost
    of a false positive is one failover to another model -- never a wrong answer delivered.
    """

    body = str(text or "").strip()
    if not body:
        return False
    # A filename array with content welded to it. A reply that is ONLY a clean JSON array is left
    # alone: some turns legitimately answer with a list.
    match = _FILENAME_ARRAY_LEAD_RE.match(body)
    if match and body[match.end():].strip():
        return True
    # A reasoning block the model opened and never closed: the whole reply is monologue with no
    # answer after it. `</think>` alone is fine -- that is a normal reply with its reasoning
    # correctly delimited, and the answer follows it.
    if "<think>" in body.lower() and "</think>" not in body.lower():
        return True
    if looks_like_internal_state_record(body):
        return True
    return bool(looks_like_fabricated_tool_result(body))
