"""Named runtime flags with declared defaults.

`core/` already carries 164 distinct `VOOL_*` environment names read inline at their use
sites, so a flag is only discoverable by grepping for the string that reads it, and nothing
can enumerate what is currently switchable. That is workable for a permanent knob and wrong
for a migration flag, where the whole point is that both paths stay exercised until the old
one is deleted.

A flag declared here is enumerable (`all_flags()`), so `tests/conftest.py` can parametrize a
flagged test over both states. Without that, "behind a flag" quietly means "the new path is
never exercised in CI", which is how a migration ships broken.

`override()` is for tests and for a runtime that wants to answer one turn with the other
path. It restores the previous value on exit, including the un-set case, so a flag flipped
inside a test cannot leak into the next one.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_TRUTHY = {"1", "true", "yes", "on"}
_FALSEY = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class RuntimeFlag:
    """One switchable runtime behaviour.

    ``default`` is what ships. A migration flag starts ``False``, flips to ``True`` when the
    new path has been driven against real output, and is deleted along with the old path.
    """

    name: str
    env_var: str
    default: bool
    description: str


_FLAGS: tuple[RuntimeFlag, ...] = (
    RuntimeFlag(
        name="plain_text_tool_catalog",
        env_var="VOOL_PLAIN_TEXT_TOOL_CATALOG",
        # OFF until the crash is found. With it on, the first cloud turn killed the daemon twice —
        # once on a scratch daemon and once on the installed app — with no Python traceback and no
        # crash report, which points below the interpreter. The clock fact in the same commit is a
        # string in the system prompt and is not a plausible cause; this ships 58 tool definitions
        # on every ordinary turn and is. Default off so `main` is safe to pull while that is
        # diagnosed; the code stays so the diagnosis has something to run against.
        default=False,
        description=(
            "Offer the full tool catalog on an ordinary plain_text turn, with tool_choice 'auto', "
            "not only on a tool_intent turn. A contextual follow-up such as 'so?' or 'audit the "
            "skills in there' classifies as plain_text and previously carried NO tools, so a cloud "
            "model invented its own call shape and the runtime refused it. The tools are VOOL's; "
            "every capable model gets them regardless of which lane answers."
        ),
    ),
    RuntimeFlag(
        name="ollama_native_tools",
        env_var="VOOL_OLLAMA_NATIVE_TOOLS",
        default=True,
        description=(
            "Send the tool catalog to Ollama as native `tools` and read `message.tool_calls` "
            "back, instead of pasting a text catalog into the prompt and parsing prose."
        ),
    ),
    RuntimeFlag(
        name="always_on_tool_catalog",
        env_var="VOOL_ALWAYS_ON_CATALOG",
        default=False,
        description=(
            "Attach the tool catalog on every turn that reaches a model, instead of only when "
            "a pre-model keyword matcher already decided the turn was tool-shaped. Stays OFF: "
            "widening `near_miss` to count an explicit path already routes every measured "
            "phrasing correctly (8/8, ~1.5s each) without it, and turning it on additionally "
            "overrides the deliberate advice-only and builder-style skips that "
            "tests/test_tool_intent_executor.py asserts. Available as an opt-in for anyone who "
            "wants the model, not a matcher, deciding on every turn."
        ),
    ),
    RuntimeFlag(
        name="capability_tool_dialect",
        env_var="VOOL_CAPABILITY_TOOL_DIALECT",
        default=True,
        description=(
            "Choose the tool dialect from the provider's advertised capability rather than "
            "from the endpoint hostname, so a free model that supports tools gets native ones."
        ),
    ),
    RuntimeFlag(
        name="cloud_tool_reasoning_reserve",
        env_var="VOOL_CLOUD_TOOL_REASONING_RESERVE",
        default=True,
        description=(
            "Size a cloud turn's output budget against the lane that serves it: room to think "
            "before a tool call, and the cost-class answer target (paid 760 / verified-free 1800, "
            "before physical caps) for an ordinary chat turn, so an answer is not cut off "
            "mid-sentence at a table sized for small local models. Only emitted tokens are "
            "billed. Separate flag because it changes token spend, which the user pays for."
        ),
    ),
    RuntimeFlag(
        name="execution_records",
        env_var="VOOL_EXECUTION_RECORDS",
        default=True,
        description=(
            "Write an execution record for every tool call including read-only ones. This is the "
            "ground truth the answer binder checks against, so turning it off disables grounding "
            "entirely. On by default; it exists as a kill switch, not as a migration gate."
        ),
    ),
    RuntimeFlag(
        name="answer_binder_shadow",
        env_var="VOOL_ANSWER_BINDER_SHADOW",
        default=True,
        description=(
            "Compute and log the answer-to-receipt binding without acting on it. The acting "
            "path stays off until the logged false-positive rate is measured."
        ),
    ),
    RuntimeFlag(
        name="answer_binder_enforce",
        env_var="VOOL_ANSWER_BINDER_ENFORCE",
        default=False,
        description=(
            "Act on a failed binding by regenerating once against the receipts. Requires "
            "answer_binder_shadow to have been measured first."
        ),
    ),
    RuntimeFlag(
        name="plugin_runtime_tools",
        env_var="VOOL_PLUGIN_RUNTIME_TOOLS",
        default=False,
        description=(
            "Load the `runtime` and `tools` blocks from a plugin manifest and register the "
            "declared tools. Manifests without those blocks load exactly as they do today."
        ),
    ),
    RuntimeFlag(
        name="derived_taint",
        env_var="VOOL_DERIVED_TAINT",
        default=False,
        description=(
            "GOBLIN inv 9 (DERIVED TAINT). When a rendered answer cites a receipt from an "
            "untrusted origin (web/page/gap/machine read), mark the answer as derived from "
            "untrusted content so the taint is not laundered away by the model's summary. "
            "Default OFF: the render path is byte-identical for every existing caller until "
            "enabled. Advisory only — it marks, it does not withhold."
        ),
    ),
    RuntimeFlag(
        name="bug_report_cloud_synthesis",
        env_var="VOOL_BUG_REPORT_CLOUD_SYNTHESIS",
        default=False,
        description=(
            "Let a model restructure an ALREADY-sanitized bug-report draft (opt-in). "
            "Sanitization itself is deterministic and model-free; the model only ever sees "
            "material that passed the outbound scanner, its output may only carry sections "
            "the local rendering built, and it is re-scanned before use -- anything unsafe "
            "falls back to the local formatting. Default OFF: local, model-free formatting "
            "is the working path and needs no model."
        ),
    ),
)

_BY_NAME: dict[str, RuntimeFlag] = {flag.name: flag for flag in _FLAGS}

# Process-local overrides win over the environment. Guarded because a runtime may flip a flag
# for one turn while another thread is answering a different one.
_overrides: dict[str, bool] = {}
_lock = threading.RLock()


def all_flags() -> tuple[RuntimeFlag, ...]:
    return _FLAGS


def flag(name: str) -> RuntimeFlag:
    try:
        return _BY_NAME[name]
    except KeyError:
        known = ", ".join(sorted(_BY_NAME)) or "none"
        raise KeyError(f"unknown runtime flag {name!r}; declared flags: {known}") from None


def flag_enabled(name: str) -> bool:
    """Resolve a flag: process override, then environment, then declared default.

    An environment value that is neither truthy nor falsey is ignored rather than treated as
    true, so a typo (`VOOL_ALWAYS_ON_CATALOG=ture`) leaves the declared default in place
    instead of silently enabling a migration path.
    """

    entry = flag(name)
    with _lock:
        if name in _overrides:
            return _overrides[name]
    raw = str(os.environ.get(entry.env_var) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSEY:
        return False
    return entry.default


@contextmanager
def override(name: str, value: bool) -> Iterator[None]:
    """Force a flag for the duration of the block, restoring the prior state on exit."""

    entry = flag(name)
    with _lock:
        had_previous = entry.name in _overrides
        previous = _overrides.get(entry.name)
        _overrides[entry.name] = bool(value)
    try:
        yield
    finally:
        with _lock:
            if had_previous:
                _overrides[entry.name] = bool(previous)
            else:
                _overrides.pop(entry.name, None)


def clear_overrides() -> None:
    with _lock:
        _overrides.clear()


def flag_state() -> dict[str, bool]:
    """Every declared flag and its resolved value, for diagnostics and bug reports."""

    return {entry.name: flag_enabled(entry.name) for entry in _FLAGS}


__all__ = [
    "RuntimeFlag",
    "all_flags",
    "clear_overrides",
    "flag",
    "flag_enabled",
    "flag_state",
    "override",
]
