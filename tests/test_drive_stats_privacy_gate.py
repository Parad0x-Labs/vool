"""The drive-stats privacy gate: machine drive numbers never reach a model the person did not
ask for them.

Measured on the benchmark run 2026-10-30: "how big is the holiday photos zip" was answered by a
model that had been handed the machine's drive fill (24.7 GB free of 460.4 GB, 94.6% used)
mid-turn. The gate in ``core/agent_runtime/research_tool_loop_facade.py`` refuses a
model-initiated ``machine.disk_usage`` / ``operator.inspect_disk_usage`` call when the turn's own
ask is not a disk/storage question, with plain words and no numbers, so the refusal is all the
provider ever sees of this machine's drives.

This file proves the gate at its seam. An end-to-end drive of the mid-turn call needs a provider
dialect whose chat turns carry native tools; on this repo's served rig only certification probes
and email-class tasks get them (chat and file asks were measured ``tools=[]``), so the wiring is
pinned structurally here -- the precise-pin style ``capability_graph.assert_representatives_resolve``
already uses -- while the gate's BEHAVIOR is tested directly, on the ask texts themselves.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core.agent_runtime.research_tool_loop_facade import _drive_stats_privacy_refusal

_FACADE = Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "research_tool_loop_facade.py"

# Asks that are about ONE named thing or about nothing in particular -- never about the drives.
NON_DISK_ASKS = [
    "how big is the holiday photos zip",
    "how many megabytes is the quarterly report pdf",
    "whats the total size of the taxes folder",
    "how much space does the taxes folder take",
    "is the holiday-pictures.zip small enough to attach in gmail?",
    "what should i pack for a weekend trip to the mountains?",
    "summarize the notes from last weeks meeting",
    "read the file notes.txt in documents and tell me what it says",
    "does the iso fit on a dvd",
    # independent review 2026-10-30: "on my computer" says where the one thing lives, not that the drives are asked about
    "how much space does report.pdf take up on my computer",
    "how much space do my photos take on my computer",
    "how much space does my music library take on my computer",
    "how much space does the taxes stuff take up on my computer",
    "how much space do my downloads take on this machine",
]

# Asks whose own words ARE the disk question: these keep their drive numbers (the deterministic
# disk lane answers them; a model-initiated read on such a turn is legitimate). "free space on my
# computer" is a noun phrase, not a question -- the deterministic classifier declines it, and the
# gate's allow-rule must still recognise it (it names free space AND this host).
DISK_ASKS = [
    "how much space is left on my computer",
    "is my disk full",
    "free space on my computer",
    "how full is my drive",
    "how much storage do i have left",
    "am i running low on storage on this machine",
    "is the ssd nearly full yet",
    "how many gigabytes are free on my drive",
    "show me the disk space",
    "how much free space is on this computer",
    "do I have enough free space on my computer to download a 4GB file",
    "do I have 4.5 GB of free space on my computer?",
]

# A number about the machine's drives, in either the reply or a tool result: a bare "12.0 MB"
# file size is fine, "24.7 GB free" is the leak.
_DRIVE_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB|TiB)\s+free"
    r"|free\s+of"
    r"|%\s*(?:full|used)"
    r"|(?:free_gb|total_gb|percent_used)\"\s*:",
    re.IGNORECASE,
)


def test_a_drive_stats_call_on_a_non_disk_ask_is_refused_without_numbers():
    for intent in ("machine.disk_usage", "operator.inspect_disk_usage"):
        for ask in NON_DISK_ASKS:
            refusal = _drive_stats_privacy_refusal(intent, ask)
            assert refusal is not None, (intent, ask)
            assert refusal.handled is True, (intent, ask)
            assert refusal.mode == "refused", (intent, ask)
            assert refusal.status == "drive_stats_not_asked_for", (intent, ask)
            assert refusal.tool_name == intent, (intent, ask)
            text = str(refusal.response_text or "")
            assert text.strip(), (intent, ask)
            assert not _DRIVE_NUMBER_RE.search(text), (intent, ask, text)
            # The words are for a person as much as for the model: the runtime hands this text
            # back as the tool result, and a model may repeat it verbatim.
            assert refusal.user_safe_response_text == text, (intent, ask)
            assert (refusal.details or {}).get("executed") is False, (intent, ask)


def test_a_drive_stats_call_on_a_disk_ask_is_allowed_through():
    for ask in DISK_ASKS:
        for intent in ("machine.disk_usage", "operator.inspect_disk_usage"):
            assert _drive_stats_privacy_refusal(intent, ask) is None, (intent, ask)


def test_other_tools_are_never_gated():
    for intent in ("machine.find_file", "machine.list_directory", "workspace.read_file", "respond.direct"):
        assert _drive_stats_privacy_refusal(intent, "how big is the holiday photos zip") is None, intent


def test_an_empty_ask_fails_closed_to_refusal():
    # No ask text at all (a turn whose effective input could not be read) must not open the
    # machine's drives by default.
    for intent in ("machine.disk_usage", "operator.inspect_disk_usage"):
        refusal = _drive_stats_privacy_refusal(intent, "")
        assert refusal is not None and refusal.mode == "refused", intent
        assert not _DRIVE_NUMBER_RE.search(str(refusal.response_text or ""))


def _loop_method(tree: ast.Module) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_maybe_execute_model_tool_intent":
            return node
    pytest.fail("_maybe_execute_model_tool_intent no longer exists in the tool loop")


def test_both_loop_dispatch_sites_route_through_the_gate_before_execution():
    """The wiring pin: every ``self._execute_tool_intent`` dispatch in the tool loop's main method
    must be guarded by the privacy gate (``gate(...) or self._execute_tool_intent(...)``), so no
    model-initiated dispatch can bypass it. If a refactor splits or moves the dispatch, this fails
    and the gate must be re-attached -- which is the point of writing it down here."""
    tree = ast.parse(_FACADE.read_text(encoding="utf-8"))
    method = _loop_method(tree)
    dispatches = [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_execute_tool_intent"
    ]
    assert len(dispatches) == 2, f"expected the head + batch dispatch sites, found {len(dispatches)}"
    for dispatch in dispatches:
        guarded = isinstance(dispatch.parent, ast.BoolOp) if hasattr(dispatch, "parent") else False
        # Python's ast has no parent links; find the BoolOp that contains this dispatch instead.
        guarded = any(
            isinstance(holder, ast.BoolOp)
            and dispatch in ast.iter_child_nodes(holder)
            and any(
                isinstance(gate, ast.Call)
                and isinstance(gate.func, ast.Name)
                and gate.func.id == "_drive_stats_privacy_refusal"
                for gate in holder.values
            )
            for holder in ast.walk(method)
        )
        assert guarded, (
            f"an unguarded _execute_tool_intent dispatch at line {dispatch.lineno}: the drive-stats "
            "privacy gate must run before every model-initiated dispatch in the tool loop"
        )


def test_the_refused_result_bypasses_the_research_fallback_branch():
    """A refusal (mode="refused") must not be converted into a research fallback: the fallback
    branch only re-routes executions whose mode is "tool_failed", so the gate's plain-words
    refusal stays in the turn as the tool result the model reads."""
    from core.agent_runtime import research_tool_loop_facade as facade

    class _Minimal:
        pass

    minimal = _Minimal()
    execution = _drive_stats_privacy_refusal("machine.disk_usage", "how big is the holiday photos zip")
    assert facade.ResearchToolLoopFacadeMixin._should_fallback_after_tool_failure(
        minimal,
        execution=execution,
        effective_input="how big is the holiday photos zip",
        classification={"task_class": "chat_conversation"},
        interpretation=None,
        executed_steps=[],
    ) is False
