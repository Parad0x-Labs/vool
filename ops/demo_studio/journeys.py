"""Typed demo journeys: what the studio records, and what proves it happened.

A journey is DATA, not a script: typed steps from a frozen driver vocabulary,
the public events that must appear for the journey to count, the capability
rows the boot gate must verify, and any live dependency it honestly cannot
fake. The driver interprets steps against the real served page; nothing here
can inject answers, fake responses, or reason about hidden model state.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The driver vocabulary. Deliberately frozen and free of any script/eval
#: action: a demo built on "run this JS" steps is one edit away from a
#: scripted fake UI. Public page entry points are reached through
#: ``page_action`` with a fixed allowlist of the product's own UI functions.
STEP_ACTIONS = frozenset(
    {
        "goto",
        "fill",
        "click",
        "send_message",
        "attach_files",
        "wait_visible",
        "wait_hidden",
        "wait_turn_complete",
        "wait_event",
        "wait_seconds",
        "select_model",
        "set_mode",
        "approve",
        "deny",
        "council_open",
        "council_scorecard",
        "run_blackbox",
        "restart_daemon",
        "reload",
        "page_action",
        "expect_text",
    }
)

#: Product-owned UI functions a ``page_action`` step may invoke. These are
#: the same entry points the page's own buttons call (window.VoolPageActions
#: and session navigation), not a scripting backdoor.
PAGE_ACTION_ALLOWLIST = frozenset({"newChat", "openPanel", "openModelMenu", "openCouncil"})

#: The ONLY events captions may be derived from: the public typed runtime
#: events served by /api/runtime/events. The streaming content channel
#: (``model_output_chunk``) is excluded by construction — captions never
#: carry model output chunks or anything like hidden reasoning.
PUBLIC_CAPTION_EVENTS = frozenset(
    {
        "task_received",
        "task_classified",
        "task_completed",
        "task_interrupted",
        "task_resumed",
        "task_pending_approval",
        "tool_selected",
        "tool_executed",
        "tool_failed",
        "tool_loop_completed",
        "tool_synthesizing",
        "web_retrieval_started",
        "web_retrieval_completed",
        "live_data_plan_created",
        "live_data_plan_completed",
        "model_lane_proof",
        "attachment_staged",
        "attachment_bound",
        "attachment_released",
        "attachment_refused",
        "permission_approved",
        "permission_denied",
        "task_envelope_rollback_completed",
        "attempt_claim_refused",
        "audit_budget_refused",
    }
)

#: Live dependencies a journey may declare. Any requirement not explicitly
#: allowed in the studio config refuses the run visibly — a contained machine
#: must never fabricate a live demo.
KNOWN_REQUIREMENTS = frozenset({"live_network", "operator_models"})


@dataclass(frozen=True)
class StepSpec:
    """One typed browser interaction against the real page."""

    id: str
    action: str
    target: str = ""
    value: str = ""
    expect: tuple[str, ...] = ()
    budget_s: float = 90.0
    note: str = ""

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "expect": list(self.expect),
            "budget_s": self.budget_s,
            "note": self.note,
        }


@dataclass(frozen=True)
class AssertionSpec:
    """A deterministic environmental assertion checked against the run."""

    kind: str  # workspace_file_exists | workspace_file_absent
    target: str
    note: str = ""


@dataclass(frozen=True)
class JourneySpec:
    """A typed demo journey against the real served product."""

    id: str
    title: str
    summary: str
    requires: tuple[str, ...] = ()
    capabilities_required: tuple[str, ...] = ()
    steps: tuple[StepSpec, ...] = ()
    expected_events: tuple[str, ...] = ()
    #: (match_regex, reply) rows for the deterministic provider stub lane.
    #: These script the MODEL LANE for offline determinism — never the UI.
    stub_rules: tuple[tuple[str, str], ...] = ()
    assertions: tuple[AssertionSpec, ...] = ()

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "requires": list(self.requires),
            "capabilities_required": list(self.capabilities_required),
            "steps": [s.to_public_dict() for s in self.steps],
            "expected_events": list(self.expected_events),
            "assertions": [
                {"kind": a.kind, "target": a.target, "note": a.note} for a in self.assertions
            ],
        }


def _goto(step_id: str = "open-chat", note: str = "Open the real chat page") -> StepSpec:
    return StepSpec(id=step_id, action="goto", target="/chat", note=note)


def _send(step_id: str, text: str, *, note: str = "") -> StepSpec:
    return StepSpec(
        id=step_id,
        action="send_message",
        target="#input",
        value=text,
        note=note or "Type a real message into the composer and send it",
    )


JOURNEY_IDS_REQUIRED: tuple[str, ...] = (
    "local_chat",
    "mixed_multi_demand",
    "live_search_answer_with_sources",
    "file_photo_attachment",
    "coding_file_change_with_approval",
    "council_run_scorecard",
    "refused_dangerous_action",
    "blackbox_receipt_rollback",
    "model_switching",
    "restart_resume_continuity",
)

JOURNEYS: dict[str, JourneySpec] = {
    "local_chat": JourneySpec(
        id="local_chat",
        title="Ordinary local chat",
        summary=(
            "A plain question answered by the runtime end to end: typed turn "
            "events, a real streamed answer, a completed task envelope."
        ),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            _send("ask", "In two sentences: what should a good workshop checklist cover?"),
        ),
        expected_events=("task_received", "task_completed"),
        stub_rules=(
            (r"(?i)checklist", "A good workshop checklist covers preparation, safety, materials, "
             "and a closing pass: stage the tools first, confirm protective equipment, lay out "
             "materials in order of use, and finish by walking the checklist again to catch "
             "anything skipped."),
        ),
    ),
    "mixed_multi_demand": JourneySpec(
        id="mixed_multi_demand",
        title="Mixed multi-demand request",
        summary=(
            "One turn carrying several different demands at once; the runtime "
            "splits ownership and executes each part for real."
        ),
        requires=("operator_models",),
        capabilities_required=("local_runtime", "workspace_write_tools"),
        steps=(
            _goto(),
            _send(
                "ask",
                "Create a file named priorities.txt that contains the line 'ship the demo', "
                "then list the files in the workspace and tell me how many there are.",
            ),
        ),
        expected_events=("task_received", "task_completed"),
        stub_rules=(("", "real model lane required for multi-demand execution"),),
        assertions=(
            AssertionSpec(kind="workspace_file_exists", target="priorities.txt",
                          note="the write demand must really land in the isolated workspace"),
        ),
    ),
    "live_search_answer_with_sources": JourneySpec(
        id="live_search_answer_with_sources",
        title="Live-search answer with sources",
        summary=(
            "A current-events question answered with real retrieval and a "
            "sources section. Requires live network; a contained machine "
            "refuses this journey rather than fabricating sources."
        ),
        requires=("live_network",),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            _send("ask", "What happened in local-AI runtime news this week? Answer with sources."),
        ),
        expected_events=("task_received", "web_retrieval_completed", "task_completed"),
        stub_rules=(("", "live retrieval cannot be scripted"),),
    ),
    "file_photo_attachment": JourneySpec(
        id="file_photo_attachment",
        title="File/photo attachment",
        summary=(
            "Real files attached through the product's upload door, bound to "
            "the turn, and referenced in the answer."
        ),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            StepSpec(id="attach", action="attach_files", value="note.txt,photo.png",
                     note="Attach a text note and a photo through the real upload door"),
            _send("ask", "Briefly: what does each attachment contain?"),
        ),
        expected_events=("task_received", "attachment_staged", "task_completed"),
        stub_rules=(
            (r"(?i)attachment|contain", "note.txt is a short plain-text note; photo.png is a "
             "small one-pixel PNG image. Both were staged through the attachment door."),
        ),
    ),
    "coding_file_change_with_approval": JourneySpec(
        id="coding_file_change_with_approval",
        title="Coding-assistant file change with approval",
        summary=(
            "A file-writing demand in manual mode raises the real permission "
            "bar; the operator approves once; the file lands in the workspace."
        ),
        requires=("operator_models",),
        capabilities_required=("local_runtime", "workspace_write_tools"),
        steps=(
            _goto(),
            StepSpec(id="manual", action="set_mode", target="#modeBtn", value="manual",
                     note="Switch the product to manual mode so writes require approval"),
            _send("ask", "Create a Python file named hello_demo.py that prints hello."),
            StepSpec(id="approve", action="approve", target="#permOnce",
                     note="Approve the real permission bar, once"),
        ),
        expected_events=("task_received", "permission_approved", "task_completed"),
        stub_rules=(("", "real model lane required to produce a tool call"),),
        assertions=(
            AssertionSpec(kind="workspace_file_exists", target="hello_demo.py",
                          note="the approved write must really land"),
        ),
    ),
    "council_run_scorecard": JourneySpec(
        id="council_run_scorecard",
        title="Council run with seat progress and scorecard",
        summary=(
            "A council convenes, seats report progress, and the run closes "
            "with the recomputed scorecard from the council ledger."
        ),
        requires=("operator_models",),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            StepSpec(id="council", action="council_open", target="#councilBtn",
                     note="Open the council surface"),
            StepSpec(id="card", action="wait_visible", target="#councilOverlay",
                     budget_s=30.0, note="Wait for the council UI"),
            StepSpec(id="scorecard", action="council_scorecard", budget_s=300.0,
                     note="Follow the run and read the final scorecard"),
        ),
        expected_events=("task_received",),
        stub_rules=(("", "council seats need real models"),),
    ),
    "refused_dangerous_action": JourneySpec(
        id="refused_dangerous_action",
        title="Refused dangerous action",
        summary=(
            "A destructive demand is denied on the real permission bar; the "
            "typed permission_denied event and an unchanged workspace prove it."
        ),
        requires=("operator_models",),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            StepSpec(id="manual", action="set_mode", target="#modeBtn", value="manual",
                     note="Manual mode"),
            _send("ask", "Delete the file priorities.txt from the workspace."),
            StepSpec(id="deny", action="deny", target="#permDeny",
                     note="Deny the dangerous action on the real permission bar"),
        ),
        expected_events=("task_received", "permission_denied", "task_completed"),
        stub_rules=(("", "real model lane required to produce a tool call"),),
        assertions=(
            AssertionSpec(kind="workspace_file_absent", target="priorities.txt",
                          note="a denied delete must leave the workspace untouched"),
        ),
    ),
    "blackbox_receipt_rollback": JourneySpec(
        id="blackbox_receipt_rollback",
        title="Blackbox receipt and rollback",
        summary=(
            "A real file-writing turn is recorded by the byte-exact flight "
            "recorder; the operator CLI lists the turn and rolls it back to "
            "the byte, verified against the workspace."
        ),
        requires=("operator_models",),
        capabilities_required=("local_runtime", "workspace_write_tools"),
        steps=(
            _goto(),
            StepSpec(id="manual", action="set_mode", target="#modeBtn", value="manual",
                     note="Manual mode so the write asks first"),
            _send("ask", "Create a file named blackbox_demo.txt containing the line "
                  "'recorded by the demo studio'."),
            StepSpec(id="approve", action="approve", target="#permOnce",
                     note="Approve the write"),
            StepSpec(id="bb-turns", action="run_blackbox", value="turns", budget_s=120.0,
                     note="List recorded turns through the real operator CLI"),
            StepSpec(id="bb-rollback", action="run_blackbox", value="rollback", budget_s=180.0,
                     note="Roll the recorded turn back through the real operator CLI"),
        ),
        expected_events=("task_received", "permission_approved", "task_completed"),
        stub_rules=(("", "real model lane required to produce a tool call"),),
        assertions=(
            AssertionSpec(kind="workspace_file_absent", target="blackbox_demo.txt",
                          note="after rollback the file must be gone again"),
        ),
    ),
    "model_switching": JourneySpec(
        id="model_switching",
        title="Model switching",
        summary=(
            "The operator switches the answering lane from the product's own "
            "model menu and the next turn answers on the chosen lane."
        ),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            StepSpec(id="menu", action="click", target="#modelBtn",
                     note="Open the model menu"),
            StepSpec(id="pick", action="select_model", target="#modelPop",
                     value="vool-local-only", budget_s=30.0,
                     note="Pin the local-only lane"),
            _send("ask", "Reply with one short sentence naming your lane."),
        ),
        expected_events=("task_received", "task_completed"),
        stub_rules=(
            (r"(?i)lane", "The local-only lane answered this turn."),
        ),
    ),
    "restart_resume_continuity": JourneySpec(
        id="restart_resume_continuity",
        title="Restart/resume continuity",
        summary=(
            "A turn is answered, the served daemon is really terminated and "
            "re-launched at the same exact SHA, and the transcript survives "
            "in the reopened page."
        ),
        capabilities_required=("local_runtime",),
        steps=(
            _goto(),
            _send("ask", "Remember the codeword LANTERN for this session and reply OK."),
            StepSpec(id="restart", action="restart_daemon", budget_s=240.0,
                     note="Terminate the daemon tree and re-launch the same staged app"),
            StepSpec(id="reload", action="reload", budget_s=60.0, note="Reload the real page"),
            StepSpec(id="reopen", action="click", target="#sessions .session .s-title",
                     budget_s=60.0,
                     note="Reopen the pre-restart session from the real sidebar"),
            StepSpec(id="transcript", action="expect_text", target="#log", value="LANTERN",
                     budget_s=60.0, note="The transcript must carry the codeword after restart"),
        ),
        expected_events=("task_received", "task_completed"),
        stub_rules=(
            (r"(?i)lantern|ok", "OK"),
        ),
    ),
}


def journey_ids() -> tuple[str, ...]:
    return tuple(JOURNEYS)


def journey_spec(journey_id: str) -> JourneySpec:
    """Typed lookup; an unknown id is a LookupError, never a silent fallback."""
    try:
        return JOURNEYS[journey_id]
    except KeyError:
        raise LookupError(
            f"unknown demo journey {journey_id!r}; known: {', '.join(sorted(JOURNEYS))}"
        ) from None


def spec_hash(spec: JourneySpec) -> str:
    """Stable content hash of the journey specification (manifest binding)."""
    import hashlib
    import json

    payload = json.dumps(spec.to_public_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
