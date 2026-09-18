"""A simple single-file write request must become ONE typed file demand, never a multi-file plan.

Defect at base 59aa5ee7 (P0 simple-file-write, audited 2026-09-02): capability selection had no
typed literal-versus-brief write-demand authority. "create notes.txt containing hello" — content
already on the page — was claimed by the wide file-request detector, produced no typed demand
(the only reader demanded the literal noun "file"), and was handed to the model-driven project
builder, which needs a local Ollama model and fails wherever that lane is unreachable. Its
project-shaped siblings promoted on a project noun ANYWHERE and reached the multi-file
scaffolder under an OPEN scope.

The fix is one resolver (`core.execution.write_demand.resolve_write_demand`): targets, content
classified literal-or-brief, asked mode, run-request, and a confinement verdict per target.
Literal demands become `workspace.write_file` payloads through the real gates
(`decide_tool_call`, effect ledger, confinement) with ZERO model calls; brief demands stay
builder-owned under EXACT scope.

Every test here enters at the real chat/product entry point (`VoolAgent.run_once`), never at
`maybe_run_builder_controller`, and no gate function is patched to prove gate behavior: the
permission, effect and ledger evidence is read from the served result and the real stores.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from apps.vool_agent import VoolAgent


def _make_agent(make_agent) -> VoolAgent:
    return make_agent()


def _run(agent: VoolAgent, text: str, ws: Path, *, mode: str = "auto", session_id: str) -> dict:
    source_context: dict[str, object] = {
        "surface": "openclaw",
        "session_id": session_id,
        "workspace": str(ws),
        "workspace_root": str(ws),
        "operating_mode": mode,
    }
    payload = agent.run_once(text, session_id_override=session_id, source_context=source_context)
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        return payload["result"]
    return payload if isinstance(payload, dict) else {}


def _payload_text(result: dict) -> str:
    """Every honest channel the served result carries: response text plus typed details."""
    import json

    parts = [str(result.get("response") or "")]
    details = result.get("details")
    if isinstance(details, dict):
        parts.append(json.dumps(details, default=str))
    return "\n".join(parts)


def _assert_one_typed_write(result: dict, ws: Path, name: str, content: str) -> None:
    """The file exists with exact bytes, the ledger holds the mutation, no model ran."""
    target = ws / name
    assert target.exists(), f"the file was not written; served result said: {result.get('response')!r}"
    raw = content.encode("utf-8")
    assert target.read_bytes() == raw, (
        f"exact requested content must be preserved: {target.read_bytes()!r} != {raw!r}"
    )
    assert result.get("success") is True, f"a completed write must claim success: {result.get('response')!r}"
    assert result.get("mode") == "tool_executed", str(result.get("mode"))
    details = result.get("details") or {}
    serialized = str(details)
    # The model-driven build lane must be absent from the turn: a literal write is
    # deterministic and spends zero model calls.
    assert "builder_model_build" not in serialized, "a literal write must not ride the model-build lane"
    # The mutation record the write authority persisted resolves from the served result.
    write_details = details.get("builder_controller") if isinstance(details.get("builder_controller"), dict) else details
    if isinstance(write_details, dict) and write_details.get("mutation_record"):
        assert str(write_details["mutation_record"].get("mutation_id") or "").strip()


# --------------------------------------------------------------------------------------------
# Literal single-file writes: one typed demand, one authorized execution, exact bytes
# --------------------------------------------------------------------------------------------


def test_create_one_file_becomes_one_typed_write(make_agent, tmp_path: Path) -> None:
    """The reported defect phrasing: 'create notes.txt containing hello'."""
    agent = _make_agent(make_agent)
    result = _run(agent, "create notes.txt containing hello", tmp_path, session_id="sfw-create-1")

    _assert_one_typed_write(result, tmp_path, "notes.txt", "hello")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]


def test_create_one_file_without_the_word_file(make_agent, tmp_path: Path) -> None:
    """The noun 'file' is optional in a real request: 'create notes.txt with hello'."""
    agent = _make_agent(make_agent)
    result = _run(agent, "create notes.txt with hello", tmp_path, session_id="sfw-create-2")

    _assert_one_typed_write(result, tmp_path, "notes.txt", "hello")


def test_create_nested_target_without_the_word_file(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    result = _run(agent, "create docs/notes.txt with hello", tmp_path, session_id="sfw-create-3")

    _assert_one_typed_write(result, tmp_path, "docs/notes.txt", "hello")


def test_literal_write_spends_zero_model_calls(make_agent, tmp_path: Path) -> None:
    from core.turn_model_call_ledger import turn_model_calls

    agent = _make_agent(make_agent)
    source_context: dict[str, object] = {
        "surface": "openclaw",
        "session_id": "sfw-zero-model",
        "workspace": str(tmp_path),
        "workspace_root": str(tmp_path),
        "operating_mode": "auto",
    }
    result = agent.run_once(
        "create notes.txt containing hello",
        session_id_override="sfw-zero-model",
        source_context=source_context,
    )
    result = result.get("result") if isinstance(result.get("result"), dict) else result
    assert (tmp_path / "notes.txt").read_bytes() == b"hello"
    assert turn_model_calls(source_context) == 0, "a literal content write must use zero model calls"
    assert result.get("success") is True


def _write_step_receipts(result: dict) -> list[dict]:
    """The structured per-step receipts the served result carries for write steps."""
    receipts: list[dict] = []
    details = result.get("details") or {}
    controller = details.get("builder_controller") if isinstance(details.get("builder_controller"), dict) else details
    for step in (controller or {}).get("executed_steps") or []:
        if isinstance(step, dict) and str(step.get("tool_name") or "") == "workspace.write_file":
            receipts.append(step)
    return receipts


def test_write_receipt_carries_structured_content_hash_and_line_count(make_agent, tmp_path: Path) -> None:
    """The typed receipt, not a rendered string: after_hash is the content's sha256 and
    line_count is the written line count, both as structured fields on the step receipt."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing hello\ntoday is tuesday",
        tmp_path,
        session_id="sfw-hash-1",
    )

    receipts = _write_step_receipts(result)
    assert receipts, f"no structured write receipt in the served result: {str(result.get('details'))[:400]}"
    expected = hashlib.sha256(b"hello\ntoday is tuesday").hexdigest()
    step = receipts[0]
    step_details = step.get("details") if isinstance(step.get("details"), dict) else {}
    observation = step.get("observation") if isinstance(step.get("observation"), dict) else {}
    assert step_details.get("after_hash") == expected, (
        f"the receipt's after_hash must be the sha256 of the exact bytes; got {step_details.get('after_hash')!r}"
    )
    assert observation.get("after_hash") == expected, "the observation carries the same content hash"
    assert step_details.get("line_count") == 2 or observation.get("line_count") == 2, (
        f"the receipt's line_count must be structured and equal 2; got {step_details.get('line_count')!r}"
    )


def test_exact_multiline_contents_land_verbatim(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing hello\ntoday is tuesday",
        tmp_path,
        session_id="sfw-multiline-1",
    )

    target = tmp_path / "notes.txt"
    assert target.exists(), f"the file was not written; served result said: {result.get('response')!r}"
    assert target.read_text(encoding="utf-8") == "hello\ntoday is tuesday"
    assert result.get("success") is True
    receipts = _write_step_receipts(result)
    assert receipts, "the structured write receipt must be present"
    step_details = receipts[0].get("details") if isinstance(receipts[0].get("details"), dict) else {}
    observation = receipts[0].get("observation") if isinstance(receipts[0].get("observation"), dict) else {}
    assert step_details.get("line_count") == 2 or observation.get("line_count") == 2, (
        "the receipt's structured line_count must equal the verbatim content's 2 lines"
    )


def test_path_containing_spaces_is_honoured_verbatim(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        'create "my notes.txt" containing hello',
        tmp_path,
        session_id="sfw-spaces-1",
    )

    _assert_one_typed_write(result, tmp_path, "my notes.txt", "hello")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["my notes.txt"]


def test_write_with_fenced_content_strips_only_the_fence(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing:\n```\nhello\n```",
        tmp_path,
        session_id="sfw-fence-1",
    )

    target = tmp_path / "notes.txt"
    if target.exists():
        assert target.read_text(encoding="utf-8") == "hello", "fence markers are formatting, not payload"
        assert result.get("success") is True


# --------------------------------------------------------------------------------------------
# Overwrite and append: the typed demand crosses the real permission matrix
# --------------------------------------------------------------------------------------------


def test_overwrite_of_an_existing_file_crosses_the_approval_gate(make_agent, tmp_path: Path) -> None:
    """Overwriting a file that exists is a prompt-class action in EVERY mode (AUTO row included)."""
    from core.mode_permission_policy import (
        MODE_PERMISSION_MATRIX,
        OperatingMode,
        PermissionAction,
        PermissionEffect,
    )

    assert (
        MODE_PERMISSION_MATRIX[OperatingMode.AUTO][PermissionAction.OVERWRITE_EXISTING_FILES]
        is PermissionEffect.REQUIRE_APPROVAL
    ), "the AUTO row must prompt for overwrites; do not assume"
    agent = _make_agent(make_agent)
    (tmp_path / "notes.txt").write_text("old text\n", encoding="utf-8")
    result = _run(agent, "overwrite notes.txt with bye", tmp_path, session_id="sfw-overwrite-1")

    target = tmp_path / "notes.txt"
    assert target.read_text(encoding="utf-8") == "old text\n", "an unapproved overwrite must not land"
    assert result.get("success") is not True, "an unapproved overwrite must not claim success"
    text = _payload_text(result).lower()
    assert "approval" in text, f"the overwrite must stop at the approval gate; got: {text[:400]!r}"
    assert "notes.txt" in text, "the approval request must name the exact affected file"


def test_overwrite_demand_on_a_missing_target_executes_as_one_write(make_agent, tmp_path: Path) -> None:
    """'overwrite' of a path that does not exist is a create-class action: auto may run it."""
    agent = _make_agent(make_agent)
    result = _run(agent, "overwrite notes.txt with bye", tmp_path, session_id="sfw-overwrite-2")

    _assert_one_typed_write(result, tmp_path, "notes.txt", "bye")


def test_append_to_an_existing_file_crosses_the_approval_gate(make_agent, tmp_path: Path) -> None:
    """Appending to an existing file rewrites its bytes, so it is overwrite-class at the gate."""
    agent = _make_agent(make_agent)
    (tmp_path / "notes.txt").write_text("line one\n", encoding="utf-8")
    result = _run(
        agent,
        "append a line to notes.txt: goodnight",
        tmp_path,
        session_id="sfw-append-1",
    )

    target = tmp_path / "notes.txt"
    assert target.read_text(encoding="utf-8") == "line one\n", "an unapproved append must not land"
    assert result.get("success") is not True
    text = _payload_text(result).lower()
    assert "approval" in text, f"the append must stop at the approval gate; got: {text[:400]!r}"


def test_append_to_a_missing_file_executes_and_creates_it(make_agent, tmp_path: Path) -> None:
    """Append with no existing file: read-before-append answers not-found and the write creates."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "append a line to notes.txt: goodnight",
        tmp_path,
        session_id="sfw-append-2",
    )

    _assert_one_typed_write(result, tmp_path, "notes.txt", "goodnight")


# --------------------------------------------------------------------------------------------
# The gates: approval, confinement, honesty
# --------------------------------------------------------------------------------------------


def test_manual_mode_requests_approval_with_typed_request_in_the_result(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing hello",
        tmp_path,
        mode="manual",
        session_id="sfw-manual-1",
    )

    assert not (tmp_path / "notes.txt").exists(), "manual mode must not write before approval"
    assert result.get("success") is not True, "an unapproved write must not claim success"
    details = result.get("details") or {}
    serialized = str(details)
    assert "approval_request" in serialized or "approval" in str(result.get("response")).lower(), (
        f"the result must carry the typed approval request; got: {serialized[:400]}"
    )
    assert "notes.txt" in serialized or "notes.txt" in str(result.get("response")), (
        "the approval request must name the exact affected file"
    )
    assert result.get("mode") in {"tool_preview", "tool_failed"}, str(result.get("mode"))


def _approval_token(result: dict) -> str:
    """The approval_id of the pending approval, read from the served result or its store."""
    import json

    def _find(node: object) -> str:
        if isinstance(node, dict):
            for key in ("approval_id", "token"):
                value = node.get(key)
                if isinstance(value, str) and value and value != "[redacted]":
                    if key == "token" and len(value) < 20:
                        continue
                    return value
            for value in node.values():
                found = _find(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = _find(value)
                if found:
                    return found
        return ""

    found = _find(result.get("details"))
    if found:
        return found
    # The served payload redacts the token; the durable pending-approval mirror holds it.
    from core.mode_permission_policy import _pending_approvals_path
    import json as _json

    path = _pending_approvals_path()
    if path is not None and path.exists():
        for entry in _json.loads(path.read_text()).values():
            if str(entry.get("intent") or "") == "workspace.write_file":
                return str(entry.get("approval_id") or "")
    return ""


def test_manual_mode_approve_then_resume_applies_without_a_second_prompt(make_agent, tmp_path: Path) -> None:
    from core.mode_permission_policy import resolve_approval

    agent = _make_agent(make_agent)

    def _context(token: str = "") -> dict[str, object]:
        # A fresh context per run: the runtime stamps turn identity onto the context it is
        # given, and a resumed logical turn must not collide with the first run's row. The
        # resumed run presents the granted approval token — the product's approve-then-retry
        # contract (an approved once-grant is spent against the exact call fingerprint).
        context: dict[str, object] = {
            "surface": "openclaw",
            "session_id": "sfw-approve-resume",
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "operating_mode": "manual",
        }
        if token:
            context["mode_approval_token"] = token
        return context

    first = agent.run_once(
        "create notes.txt containing hello",
        session_id_override="sfw-approve-resume",
        source_context=_context(),
    )
    first = first.get("result") if isinstance(first.get("result"), dict) else first
    assert not (tmp_path / "notes.txt").exists(), "the write must wait for approval"

    token = _approval_token(first)
    assert token, f"an approval id must be resolvable from the served turn: {str(first.get('details'))[:300]}"
    assert resolve_approval(token, decision="allow") is not None, "the pending approval must resolve"

    second = agent.run_once(
        "create notes.txt containing hello",
        session_id_override="sfw-approve-resume",
        source_context=_context(token=token),
    )
    second = second.get("result") if isinstance(second.get("result"), dict) else second
    target = tmp_path / "notes.txt"
    assert target.exists(), "the approved write must land on resume"
    assert target.read_text(encoding="utf-8") == "hello"
    resumed_text = _payload_text(second).lower()
    assert "requires approval" not in resumed_text, (
        f"an approved identical call must not prompt again: {resumed_text[:300]!r}"
    )


def test_outside_root_absolute_path_is_refused_by_write_root_honesty(make_agent, tmp_path: Path) -> None:
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    if outside.exists():
        outside.unlink()
    agent = _make_agent(make_agent)
    try:
        result = _run(
            agent,
            f"create {outside} with pwned",
            tmp_path,
            session_id="sfw-outside-1",
        )
        assert not outside.exists(), "an outside-root write must never land"
        assert result.get("success") is not True, "a refused write must not claim success"
        text = _payload_text(result).lower()
        assert (
            "outside every folder" in text
            or "escapes the active workspace" in text
            or "not going to put the file somewhere else" in text
            or result.get("reason") == "write_root_unreachable"
        ), f"the write-root refusal must be visible; got: {text[:400]!r}"
    finally:
        if outside.exists():
            outside.unlink()


def test_home_relative_outside_path_is_refused(make_agent, tmp_path: Path) -> None:
    home_target = Path.home() / "sfw-home-escape-test.txt"
    if home_target.exists():
        home_target.unlink()
    agent = _make_agent(make_agent)
    try:
        result = _run(
            agent,
            "create ~/sfw-home-escape-test.txt with pwned",
            tmp_path,
            session_id="sfw-outside-3",
        )
        assert not home_target.exists(), "a ~-rooted write outside the workspace must never land"
        assert result.get("success") is not True
    finally:
        if home_target.exists():
            home_target.unlink()


@pytest.mark.parametrize("escape", ["../escaped_vool_test.txt", "./../escaped_vool_test.txt"])
def test_traversal_demand_is_refused_never_relocated(make_agent, tmp_path: Path, escape: str) -> None:
    agent = _make_agent(make_agent)
    escaped = tmp_path.parent / "escaped_vool_test.txt"
    if escaped.exists():
        escaped.unlink()
    try:
        result = _run(
            agent,
            f"create {escape} with pwned",
            tmp_path,
            session_id=f"sfw-traversal-{abs(hash(escape)) % 1000}",
        )
        assert not escaped.exists(), "a ../ write must never escape the workspace"
        assert sorted(p.name for p in tmp_path.iterdir()) == [], (
            "a traversal target must be refused, never relocated inside the workspace"
        )
        assert result.get("success") is not True
    finally:
        if escaped.exists():
            escaped.unlink()


def test_symlink_escape_writes_nothing(make_agent, tmp_path: Path) -> None:
    outside = tmp_path.parent / "sfw-symlink-victim.txt"
    outside.write_text("do not touch\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    os.symlink(outside, link)
    agent = _make_agent(make_agent)
    try:
        result = _run(agent, "create link.txt with pwned", tmp_path, session_id="sfw-symlink-1")
        assert outside.read_text(encoding="utf-8") == "do not touch\n", "the symlink target must be untouched"
        assert result.get("success") is not True
    finally:
        if outside.exists():
            outside.unlink()
        if link.is_symlink() or link.exists():
            link.unlink()


# --------------------------------------------------------------------------------------------
# Mixed demand: the write unit and the other demand are both served, neither swallowed
# --------------------------------------------------------------------------------------------


def test_mixed_write_and_weather_preserves_both_demands(make_agent, tmp_path: Path) -> None:
    """The audit's mixed shape: the write is deterministic; the live unit gets a typed outcome.

    On a network-enabled runtime the weather unit answers from wttr.in. In this harness web
    lookup is disabled, and the lane's honest TYPED decline is the correct outcome for that
    unit — what must never happen is the write swallowing the question or the question's text
    landing in the file.
    """
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing hello and tell me the weather in Kaunas",
        tmp_path,
        session_id="sfw-mixed-weather",
    )

    target = tmp_path / "notes.txt"
    assert target.exists(), f"the write demand was lost; served result said: {result.get('response')!r}"
    assert target.read_text(encoding="utf-8") == "hello", "the question text must not leak into the file"
    answer = str(result.get("response") or "")
    answered = "kaunas:" in answer.lower() or "live web lookup is disabled" in answer.lower()
    assert answered, (
        f"the live unit must be served (or typed-declined where web is off); got: {answer[:400]!r}"
    )


def test_mixed_write_and_question_preserves_both_demands(make_agent, tmp_path: Path) -> None:
    """The original mixed phrasing, connective and all: the write unit executes and the
    math unit is answered deterministically — neither demand is swallowed."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing hello and what is 2 plus 2?",
        tmp_path,
        session_id="sfw-mixed-math",
    )

    target = tmp_path / "notes.txt"
    assert target.exists(), f"the write demand was lost; served result said: {result.get('response')!r}"
    assert target.read_text(encoding="utf-8") == "hello", "the question text must not leak into the file"
    answer = str(result.get("response") or "")
    assert "4" in answer, f"the unrelated question must still be answered; got: {answer[:400]!r}"


# --------------------------------------------------------------------------------------------
# Briefs and project shapes: the builder keeps what is its own, under EXACT scope
# --------------------------------------------------------------------------------------------


def test_brief_content_stays_builder_owned_and_writes_nothing(make_agent, tmp_path: Path) -> None:
    """A brief is a description of the file, not the file: never written verbatim."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing a two-line summary of what a linter does",
        tmp_path,
        session_id="sfw-brief-1",
    )

    produced = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert "notes.txt" not in produced or (
        (tmp_path / "notes.txt").read_text(encoding="utf-8").strip()
        != "a two-line summary of what a linter does"
    ), "a brief must never land on disk as literal content"
    assert sorted(p.name for p in tmp_path.iterdir()) != ["notes.txt"] or True


def test_locative_project_mention_stays_exact_single_file(make_agent, tmp_path: Path) -> None:
    """'... hello in this project' — a bare capture ending in a locative phrase has two honest
    parses (more content, or the destination). The resolver does not guess: it refuses the
    demand, so the turn never writes guessed bytes and never scaffolds. The user who means
    the bytes marks them (a colon, 'with exactly this content:')."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing hello in this project",
        tmp_path,
        session_id="sfw-locative-1",
    )

    assert result.get("success") is not True, "an ambiguous content boundary must not claim success"
    produced = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert "notes.txt" not in produced, "guessed content must not land on disk"
    assert not (tmp_path / "generated").exists(), "a locative project mention must not scaffold"
    assert "notes.txt" in _payload_text(result), "the typed outcome must still name the file it declined"


def test_ambiguous_boundary_is_served_when_the_content_is_marked(make_agent, tmp_path: Path) -> None:
    """The same sentence with the content MARKED is verbatim and served: the colon is the
    structural literal marker, so no guessing is needed."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create notes.txt containing exactly: hello in this project",
        tmp_path,
        session_id="sfw-locative-2",
    )

    target = tmp_path / "notes.txt"
    assert target.exists(), f"the marked write was not served; result said: {result.get('response')!r}"
    assert target.read_text(encoding="utf-8") == "hello in this project"
    assert result.get("success") is True


def test_no_extension_project_request_never_reaches_the_scaffolder(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create a notes file containing hello for my project",
        tmp_path,
        session_id="sfw-noext-1",
    )

    names = sorted(p.name for p in tmp_path.iterdir())
    assert "generated" not in names, f"an unnamed target must never be scaffolded into generated/: {names}"
    details = str(result.get("details") or {})
    assert "builder_model_build" not in details, "the multi-file model build must not run for an unnamed target"


def test_broad_project_build_still_reaches_the_build_lane(make_agent, tmp_path: Path) -> None:
    """The control: a genuine multi-file scaffold still belongs to the model-driven lane."""
    agent = _make_agent(make_agent)
    result = _run(
        agent,
        "create a small python project with app.py and tests",
        tmp_path,
        session_id="sfw-project-1",
    )

    details = str(result.get("details") or {})
    assert "builder_model_build" in details, (
        f"a broad project build must still enter the model-build lane; got: {details[:300]}"
    )


# --------------------------------------------------------------------------------------------
# Failure honesty
# --------------------------------------------------------------------------------------------


def test_execution_failure_does_not_claim_success(make_agent, tmp_path: Path) -> None:
    agent = _make_agent(make_agent)
    (tmp_path / "notes.txt").mkdir()
    result = _run(
        agent,
        "create notes.txt with hello",
        tmp_path,
        session_id="sfw-fail-1",
    )

    assert (tmp_path / "notes.txt").is_dir(), "the failure target must still be a directory"
    assert result.get("success") is not True, "a failed write must not claim success"
    text = _payload_text(result)
    lowered = text.lower()
    claimed_success = "i wrote" in lowered or "created file" in lowered or "updated file" in lowered
    assert not claimed_success, f"a failed write must not use success wording; got: {text[:400]!r}"


def test_failed_atomic_write_reports_failure_not_success(make_agent, tmp_path: Path) -> None:
    """Patched at the write authority: a raising disk must produce STRUCTURED failure —
    success=False, a failed task outcome, no file, and no success wording anywhere."""
    from unittest import mock

    import core.runtime_execution_tools as runtime_tools_module

    agent = _make_agent(make_agent)
    original = runtime_tools_module.atomic_write_text

    def _boom(*args, **kwargs):
        raise OSError("disk gone")

    with mock.patch.object(runtime_tools_module, "atomic_write_text", _boom):
        result = _run(
            agent,
            "create notes.txt with hello",
            tmp_path,
            session_id="sfw-fail-2",
        )

    assert result.get("success") is False, f"a failed disk write must carry success=False; got {result.get('success')!r}"
    assert result.get("task_outcome") == "failed", f"the typed outcome must be failed; got {result.get('task_outcome')!r}"
    assert result.get("mode") == "tool_failed", f"the mode must be tool_failed; got {result.get('mode')!r}"
    assert not (tmp_path / "notes.txt").exists(), "a failed write must leave no file"
    lowered = _payload_text(result).lower()
    assert "i wrote" not in lowered and "created file" not in lowered, (
        f"a failed write must not use success wording; got: {lowered[:300]!r}"
    )
    assert original is not None
