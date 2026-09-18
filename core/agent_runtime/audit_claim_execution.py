"""Some claims are decidable. Those get executed, not voted on.

Twice now, an audit of the same file reported: *"`blob.startswith(ZSTD_MAGIC)` raises IndexError
because the empty blob has no bytes to check."* It does not. `b"".startswith(b"x")` returns `False`.
The claim is false in a way Python will tell you in twenty milliseconds.

What the runtime did instead was ask a second model to falsify the first. On a fact the model is
wrong about, it is wrong in both roles — the adversarial check returned "supported" and the false
finding shipped to the operator. A vote between two instances of the same wrong belief is not a
check, however adversarially it is worded. `AGENT_HANDOVER.md` §1A even freezes this exact
hypothesis as refuted ground truth, and no code read it.

So: before any model challenge, a claim of the form *"<receiver>.<method>(...) raises <Error>"* is
EVALUATED, when and only when the receiver is decidable without touching the audited project.

The scope is deliberately narrow, and the narrowness is what makes it safe:

* the receiver must resolve to an **empty builtin literal** — `b""`, `""`, `[]`, `()`, `{}`,
  `set()` — which the scenario itself names ("empty input", "empty blob", "on an empty list");
* the method must be a real attribute of that builtin, so nothing is imported, nothing from the
  audited project is executed, and no file, socket or process is touched;
* arguments are replaced by a type-appropriate probe, because the claim is about the RECEIVER's
  emptiness, not about what was passed;
* a refutation is only ever issued on **positive evidence** — the call completed and did not raise
  what was claimed. A `TypeError` from our own probe, an unknown method, an unresolvable receiver:
  all return "undecided" and the normal challenge runs.

This decides one narrow class completely. That class has now produced two false findings on the
same fixture, which is two more than the model-vote check has caught.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# The empty literals a scenario can name, and how it names them.
_EMPTY_RECEIVERS: dict[str, Any] = {
    "bytes": b"",
    "bytearray": bytearray(),
    "str": "",
    "list": [],
    "tuple": (),
    "dict": {},
    "set": set(),
}
# A probe argument per receiver type. The claim is about emptiness, not about the argument, so any
# well-typed value settles it.
_PROBE_ARG: dict[str, Any] = {
    "bytes": b"\x00",
    "bytearray": b"\x00",
    "str": "x",
    "list": 0,
    "tuple": 0,
    "dict": "k",
    "set": "k",
}

# "empty blob", "empty input", "an empty bytes object", "b''", "empty list"
_EMPTY_HINT_RE = re.compile(
    r"\bempty\b|\bb?['\"]{2}\b|\bb?\(\)\B|\[\]|\bzero[\s-]length\b|\bno bytes\b|\bnothing\b",
    re.IGNORECASE,
)
_TYPE_HINT_RE = re.compile(
    r"\b(bytes|bytearray|string|str|list|tuple|dict|dictionary|set|blob|buffer|input|payload|data)\b",
    re.IGNORECASE,
)
_TYPE_BY_WORD = {
    "bytes": "bytes", "bytearray": "bytearray", "blob": "bytes", "buffer": "bytes",
    "payload": "bytes", "data": "bytes", "input": "bytes",
    "string": "str", "str": "str",
    "list": "list", "tuple": "tuple",
    "dict": "dict", "dictionary": "dict", "set": "set",
}

# `something.method(args) raises SomeError` / `... raises a SomeError` / `... will raise SomeError`.
# The receiver is captured so its emptiness can be established; the args are captured only to be
# discarded.
_RAISES_CLAIM_RE = re.compile(
    r"(?P<receiver>[A-Za-z_][A-Za-z0-9_]*)\s*\.\s*(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<args>[^()]*)\)"
    r"[^.;\n]{0,80}?\b(?:raises?|raising|throws?|will\s+raise|would\s+raise)\s+"
    r"(?:an?\s+)?(?P<exception>[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\b",
    re.IGNORECASE | re.DOTALL,
)

# Methods that would mutate, block, or reach outside the process. None of them can appear on an
# empty builtin in a way worth executing, and refusing them by name keeps the door shut.
_FORBIDDEN_METHODS = frozenset(
    {"clear", "pop", "popitem", "remove", "append", "extend", "insert", "update", "add",
     "discard", "sort", "reverse", "setdefault", "write", "read", "close", "flush", "seek"}
)


@dataclass(frozen=True)
class ExecutedClaim:
    """One decidable claim and what actually happened when it ran."""

    snippet: str
    claimed_exception: str
    raised: str = ""
    result_repr: str = ""
    decided: bool = False

    @property
    def refuted(self) -> bool:
        """The call ran and did NOT raise what the claim said it would."""
        return self.decided and self.raised != self.claimed_exception

    def counterexample(self) -> str:
        # Only a REFUTATION has a counterexample. A claim that behaved as alleged would otherwise
        # render "raises ValueError, not ValueError", which reads as a contradiction of itself.
        if not self.refuted:
            return ""
        if self.raised:
            return f"`{self.snippet}` raises {self.raised}, not {self.claimed_exception}"
        return f"`{self.snippet}` returns {self.result_repr} — it does not raise {self.claimed_exception}"


def _receiver_type(receiver: str, context: str) -> str:
    """The builtin type an empty receiver stands for, or '' when it cannot be established."""
    window = context.lower()
    if not _EMPTY_HINT_RE.search(window):
        return ""
    # An explicit literal in the text wins: `b''` says bytes outright.
    if re.search(r"\bb['\"]{2}", context):
        return "bytes"
    if re.search(r"(?<!b)['\"]{2}", context):
        return "str"
    if "[]" in context:
        return "list"
    named = [
        _TYPE_BY_WORD[word.lower()]
        for word in _TYPE_HINT_RE.findall(f"{receiver} {context}")
        if word.lower() in _TYPE_BY_WORD
    ]
    return named[0] if named else ""


def _execute(receiver_type: str, method: str, snippet: str, exception: str) -> ExecutedClaim:
    """Run one method on one empty builtin. Nothing from the audited project is involved."""
    undecided = ExecutedClaim(snippet=snippet, claimed_exception=exception)
    if method in _FORBIDDEN_METHODS or method.startswith("_"):
        return undecided
    empty = _EMPTY_RECEIVERS.get(receiver_type)
    if empty is None:
        return undecided
    bound = getattr(empty, method, None)
    if bound is None or not callable(bound):
        return undecided
    probe = _PROBE_ARG.get(receiver_type)
    for args in ((probe,), ()):
        try:
            value = bound(*args)
        except TypeError:
            # Our probe did not fit this method's signature — that is a fact about the probe, not
            # about the claim. Try the other arity, then give up rather than guess.
            continue
        except Exception as exc:  # a real, claim-relevant exception
            return ExecutedClaim(
                snippet=snippet, claimed_exception=exception,
                raised=type(exc).__name__, decided=True,
            )
        return ExecutedClaim(
            snippet=snippet, claimed_exception=exception,
            result_repr=repr(value)[:80], decided=True,
        )
    return undecided


def decidable_claims(*texts: str) -> list[ExecutedClaim]:
    """Every `<empty builtin>.<method>() raises <Error>` claim in this text, executed."""
    body = " ".join(str(text or "") for text in texts)
    executed: list[ExecutedClaim] = []
    for match in _RAISES_CLAIM_RE.finditer(body):
        receiver = match.group("receiver")
        method = match.group("method")
        exception = match.group("exception")
        # The window around the claim is what establishes emptiness; the claim alone rarely says it.
        start = max(0, match.start() - 220)
        window = body[start : match.end() + 220]
        receiver_type = _receiver_type(receiver, window)
        if not receiver_type:
            continue
        snippet = f"{receiver_type_literal(receiver_type)}.{method}(...)"
        claim = _execute(receiver_type, method, snippet, exception)
        if claim.decided:
            executed.append(claim)
    return executed


def receiver_type_literal(receiver_type: str) -> str:
    return {
        "bytes": "b''", "bytearray": "bytearray()", "str": "''",
        "list": "[]", "tuple": "()", "dict": "{}", "set": "set()",
    }.get(receiver_type, receiver_type)


def refuting_claim(*texts: str) -> ExecutedClaim | None:
    """The first executed claim that DISPROVES what the finding alleged, or None.

    Only a refutation is returned. A claim that ran and behaved exactly as alleged is not promoted
    to proof here — it establishes one operation's behaviour, not the whole failure scenario, and
    treating it as proof is the shortcut this lane exists to prevent.
    """
    for claim in decidable_claims(*texts):
        if claim.refuted:
            return claim
    return None


__all__ = ["ExecutedClaim", "decidable_claims", "refuting_claim"]
