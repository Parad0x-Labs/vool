"""The clean local loop: route decision, home scoping, JSON parsing, and the step loop.

The loop tests drive the real tool table over a real home folder through VOOL's real authorized
boundary; only the model is scripted (a list of replies), because the point is what the loop does
with each reply, not what a model would say.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.clean_loop import route
from core.clean_loop.loop import GAVE_UP_TEXT, parse_action, run_loop
from core.clean_loop.prompt import STEP_LIMIT_NUDGE, build_system_prompt
from core.clean_loop.registry import Registry, ToolContext, ToolOutcome
from core.clean_loop.tools import PENDING_NOTICE, OutsideHome, home_dir, install_builtin_tools, scope_path

ON = {route.FLAG_ENV: "1"}
OWNER = {"surface": "cli"}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    (root / "Desktop").mkdir(parents=True)
    (root / "Downloads").mkdir()
    (root / "Documents").mkdir()
    (root / "Desktop" / "todo.txt").write_text("1. renew car insurance\n2. call the plumber\n", encoding="utf-8")
    (root / "Downloads" / "plumber_invoice.txt").write_text("Plumber invoice\nTotal due: EUR 140.00\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    return root


def _registry() -> Registry:
    registry = Registry()
    install_builtin_tools(registry)
    return registry


def _scripted(replies: list[str]):
    seen: list[list[dict[str, str]]] = []

    def brain(messages):
        seen.append([dict(m) for m in messages])
        return replies[len(seen) - 1] if len(seen) <= len(replies) else replies[-1]

    brain.seen = seen  # type: ignore[attr-defined]
    return brain


# --- route -------------------------------------------------------------------------------------


def _local_brain():
    return object()


def test_route_is_off_unless_the_flag_is_set():
    decision = route.decide("whats on my desktop?", source_context=OWNER, resolve_brain=_local_brain, env={})
    assert (decision.admit, decision.reason) == (False, "flag_off")


def test_route_admits_a_local_files_ask():
    decision = route.decide("whats on my desktop?", source_context=OWNER, resolve_brain=_local_brain, env=ON)
    assert decision.admit and decision.reason == "admitted"


@pytest.mark.parametrize(
    "ask",
    [
        "pay the plumber invoice in my downloads",
        "delete the old screenshots on my desktop",
        "send my cv to anna",
        "post my holiday photos",
        "cancel my subscription",
        "clear out my temp files",
        "just get rid of all the junk on here",
    ],
)
def test_route_keeps_red_asks_on_the_normal_ceremony(ask):
    asked_for_brain: list[bool] = []
    decision = route.decide(
        ask, source_context=OWNER, resolve_brain=lambda: asked_for_brain.append(True), env=ON
    )
    assert (decision.admit, decision.reason) == (False, "red_turn")
    assert not asked_for_brain, "a RED turn is refused before the brain is even resolved"


def test_route_needs_a_local_brain():
    decision = route.decide("whats on my desktop?", source_context=OWNER, resolve_brain=lambda: None, env=ON)
    assert (decision.admit, decision.reason) == (False, "brain_not_local")


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        ({**OWNER, "_trusted_project_id": "proj-1"}, "project_owns_turn"),
        ({**OWNER, "planned_subturn": True}, "planned_subturn"),
        ({"surface": "channel", "_owner_local": False}, "not_owner_local"),
    ],
)
def test_route_leaves_turns_something_else_owns(context, reason):
    decision = route.decide("whats on my desktop?", source_context=context, resolve_brain=_local_brain, env=ON)
    assert (decision.admit, decision.reason) == (False, reason)


def test_route_leaves_online_and_unrelated_asks_alone():
    online = route.decide("whats the weather in my town", source_context=OWNER, resolve_brain=_local_brain, env=ON)
    chat = route.decide("tell me a joke", source_context=OWNER, resolve_brain=_local_brain, env=ON)
    assert online.reason == "online_ask" and chat.reason == "not_about_own_files"


@pytest.mark.parametrize(
    "ask",
    [
        "stick an empty folder called Invoices on my desktop would you",
        "make me a shopping list file on the desktop: milk, bread",
        "can you move the cat photo from Pictures to my Desktop?",
        "save a note somewhere saying: call the plumber",
    ],
)
def test_route_leaves_writes_to_the_normal_ceremony(ask):
    decision = route.decide(ask, source_context=OWNER, resolve_brain=_local_brain, env=ON)
    assert (decision.admit, decision.reason) == (False, "write_turn")


@pytest.mark.parametrize(
    "ask", ["where did i put my cv? the 2026 one", "what did i write in the flat viewing notes?", "my screenshot didnt save proper, read it"]
)
def test_route_keeps_recall_phrasing_as_a_read(ask):
    assert route.decide(ask, source_context=OWNER, resolve_brain=_local_brain, env=ON).admit


def test_route_needs_the_machine_to_be_the_owners():
    general = route.decide("mac or windows, which is better?", source_context=OWNER, resolve_brain=_local_brain, env=ON)
    own = route.decide("is my laptop any good?", source_context=OWNER, resolve_brain=_local_brain, env=ON)
    assert general.reason == "not_about_own_files" and own.admit


def test_route_keeps_a_short_follow_up_but_not_a_bare_approval():
    route.note_clean_turn("sess-follow")
    follow = route.decide(
        "open the second one", source_context=OWNER, session_id="sess-follow", resolve_brain=_local_brain, env=ON
    )
    approval = route.decide(
        "yes please", source_context=OWNER, session_id="sess-follow", resolve_brain=_local_brain, env=ON
    )
    assert follow.admit
    assert not approval.admit


# --- tool scoping -------------------------------------------------------------------------------


def test_scope_path_holds_every_path_inside_home(home):
    assert scope_path("", home) == home
    assert scope_path("Downloads", home) == home / "Downloads"
    assert scope_path("~/Desktop/todo.txt", home) == home / "Desktop" / "todo.txt"
    assert scope_path(str(home / "Documents"), home) == home / "Documents"
    for outside in ("/", "/etc/passwd", "../../etc", "Desktop/../../x", str(home.parent)):
        with pytest.raises(OutsideHome):
            scope_path(outside, home)


def test_a_home_at_the_filesystem_root_is_refused(monkeypatch):
    with pytest.raises(OutsideHome):
        scope_path("Downloads", Path("/"))
    monkeypatch.setattr("core.runtime_execution_tools._machine_home", lambda: Path("/"))
    with pytest.raises(OutsideHome):
        home_dir()


def _recording_ctx(home: Path) -> tuple[ToolContext, list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def execute(intent, arguments, **_kwargs):
        calls.append((intent, dict(arguments)))
        from core.runtime_execution_tools import RuntimeExecutionResult

        return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="ok")

    return ToolContext(home=home, source_context=dict(OWNER), execute=execute), calls


def test_no_tool_ever_roots_at_slash_or_leaves_home(home):
    registry = _registry()
    ctx, calls = _recording_ctx(home)
    for name, args in [
        ("list_dir", {"path": ""}),
        ("list_dir", {"path": "/"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("read_file", {"path": "../outside.txt"}),
        ("largest", {"path": ""}),
        ("largest", {"path": "/"}),
        ("find", {"query": "invoice"}),
    ]:
        registry.tool(name).run(args, ctx)
    paths = [str(args.get("path")) for _intent, args in calls if "path" in args]
    assert paths, "the in-home calls did reach the boundary"
    assert all(p != "/" and (Path(p) == home or home in Path(p).parents) for p in paths), paths
    assert ("machine.find_largest", {"drive": str(home), "kind": "files"}) in calls
    assert not any(intent == "machine.find_folder" for intent, _ in calls), "no whole-disk folder walk"


def test_reads_go_through_the_real_authorized_boundary(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("read_file").run({"path": "Downloads/plumber_invoice.txt"}, ctx)
    assert outcome.ok, outcome.text
    assert "140.00" in outcome.text
    assert ctx.receipts[-1]["tool_name"] == "machine.read_file"
    assert ctx.receipts[-1]["permission"].get("effect") == "allow"


def test_largest_passes_the_contract_argument_through_the_real_boundary(home):
    # The Mac audit's clean-1 run: a `path` argument came back invalid_arguments on every call.
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("largest").run({"path": "Downloads"}, ctx)
    assert ctx.receipts[-1]["tool_name"] == "machine.find_largest"
    assert outcome.status != "invalid_arguments", outcome.text


def test_a_pdf_falls_back_to_a_plain_read_when_the_pdf_reader_fails(home):
    from core.runtime_execution_tools import RuntimeExecutionResult

    calls = []

    def execute(intent, arguments, **_kwargs):
        calls.append(intent)
        if intent == "pdf.extract_text":
            return RuntimeExecutionResult(handled=True, ok=False, status="missing_dependency", response_text="no reader")
        return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="Total due 129.40 EUR")

    (home / "Downloads" / "invoice_4471.pdf").write_bytes(b"%PDF-1.4 Total due 129.40 EUR")
    ctx = ToolContext(home=home, source_context=dict(OWNER), execute=execute)
    outcome = _registry().tool("read_file").run({"path": "Downloads/invoice_4471.pdf"}, ctx)
    assert calls == ["pdf.extract_text", "machine.read_file"]
    assert outcome.ok and "129.40" in outcome.text
    assert [r["mode"] for r in ctx.receipts] == ["tool_failed", "tool_executed"]


def test_a_guessed_name_comes_back_with_the_real_files(home):
    (home / "Desktop" / "flat viewing notes.md").write_text("Flat A 950, Flat B 1020", encoding="utf-8")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("read_file").run({"path": "Desktop/flat_viewing_notes.txt"}, ctx)
    assert outcome.status == "not_found_with_suggestions"
    assert "flat viewing notes.md" in outcome.text


def test_a_named_folder_is_found_where_it_really_is(home):
    (home / "Pictures" / "Holiday Lisbon 2026").mkdir(parents=True)
    (home / "Pictures" / "Holiday Lisbon 2026" / "lisbon_001.jpg").write_bytes(b"jpg")
    (home / "Movies").mkdir()
    (home / "Movies" / "kids_school_play.mov").write_bytes(b"x" * 64)
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    lisbon = _registry().tool("list_dir").run({"path": "Lisbon"}, ctx)
    assert lisbon.ok and "lisbon_001.jpg" in lisbon.text and "Holiday Lisbon 2026" in lisbon.text
    videos = _registry().tool("list_dir").run({"path": "Videos"}, ctx)
    assert videos.ok and "kids_school_play.mov" in videos.text


def test_the_folder_resolver_never_follows_a_link(home, tmp_path):
    (tmp_path / "outside" / "Secret Lisbon").mkdir(parents=True)
    (home / "Documents" / "link").symlink_to(tmp_path / "outside")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    from core.clean_loop.tools import _find_folders

    assert _find_folders("lisbon", ctx) == []


def test_the_folder_resolver_runs_on_python_3_12(home, tmp_path, monkeypatch):
    # Path.is_dir takes no follow_symlinks before 3.13; the daemon ships on 3.12.
    import os
    import pathlib

    monkeypatch.setattr(pathlib.Path, "is_dir", lambda self: os.path.isdir(self))
    (home / "Pictures" / "Holiday Lisbon 2026").mkdir(parents=True)
    (tmp_path / "outside" / "Secret Lisbon").mkdir(parents=True)
    (home / "Documents" / "link").symlink_to(tmp_path / "outside")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    from core.clean_loop.tools import _find_folders

    assert [p.name for p in _find_folders("lisbon", ctx)] == ["Holiday Lisbon 2026"]


def test_processes_is_its_own_tool(home):
    ctx, calls = _recording_ctx(home)
    _registry().tool("processes").run({"sort": "cpu"}, ctx)
    assert calls == [("machine.list_processes", {"sort": "cpu", "limit": 15})]


def test_propose_change_never_executes(home):
    ctx, calls = _recording_ctx(home)
    outcome = _registry().tool("propose_change").run({"what": "delete Desktop/todo.txt"}, ctx)
    assert calls == []
    assert outcome.status == "pending_approval"
    assert ctx.proposals == [{"what": "delete Desktop/todo.txt", "status": "pending_approval", "executed": False}]
    assert (home / "Desktop" / "todo.txt").exists()


# --- parsing ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"tool": "list_dir", "path": "Downloads"}', {"tool": "list_dir", "path": "Downloads"}),
        ('Sure, let me look.\n```json\n{"tool": "find", "query": "cv"}\n```', {"tool": "find", "query": "cv"}),
        ('<think>hmm {"tool": "x"}</think>{"answer": "It is 140 EUR."}', {"answer": "It is 140 EUR."}),
        ('{"tool": "read_file", "args": {"path": "a.txt"}}', {"tool": "read_file", "path": "a.txt"}),
        ('{broken {"answer": "fine"} trailing', {"answer": "fine"}),
        ("Your desktop has a to-do list.", {"answer": "Your desktop has a to-do list."}),
    ],
)
def test_parse_action_tolerates_prose_around_the_json(text, expected):
    assert parse_action(text) == expected


def test_the_system_prompt_is_static():
    first = build_system_prompt(_registry())
    assert first == build_system_prompt(_registry())
    assert "propose_change" in first and "20" not in first  # no clock or date inside


# --- the loop -----------------------------------------------------------------------------------


def test_list_then_read_then_answer(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    brain = _scripted(
        [
            '{"tool": "list_dir", "path": "Downloads"}',
            '{"tool": "read_file", "path": "Downloads/plumber_invoice.txt"}',
            '{"answer": "You owe the plumber EUR 140.00."}',
        ]
    )
    result = run_loop("how much do i owe the plumber?", brain=brain, registry=_registry(), ctx=ctx)
    assert result.answer == "You owe the plumber EUR 140.00."
    assert result.tools_used == ["list_dir", "read_file"]
    assert result.model_calls == 3 and result.stopped == "answered"
    assert [r["tool_name"] for r in ctx.receipts] == ["machine.list_directory", "machine.read_file"]
    last_prompt = brain.seen[-1]
    assert "plumber_invoice.txt" in last_prompt[-3]["content"]
    assert "140.00" in last_prompt[-1]["content"]


def test_a_write_becomes_a_pending_proposal(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    brain = _scripted(
        [
            '{"tool": "propose_change", "what": "create Desktop/shopping.txt with milk, bread"}',
            '{"answer": "I have the shopping list ready to save on your desktop."}',
        ]
    )
    result = run_loop("make me a shopping list file on the desktop", brain=brain, registry=_registry(), ctx=ctx)
    assert result.proposals and result.proposals[0]["status"] == "pending_approval"
    assert not (home / "Desktop" / "shopping.txt").exists()
    assert result.answer.endswith(PENDING_NOTICE)
    assert all(r["execution"]["executed"] is False for r in ctx.receipts)


def test_the_step_cap_ends_the_loop_with_a_plain_reply(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    brain = _scripted(['{"tool": "disk_usage"}'])
    result = run_loop("is my disk filling up?", brain=brain, registry=_registry(), ctx=ctx, max_steps=3)
    assert result.stopped == "max_steps"
    assert result.model_calls == 3 and len(result.steps) == 2
    assert result.answer == GAVE_UP_TEXT
    assert brain.seen[-1][-1]["content"] == STEP_LIMIT_NUDGE


def test_registered_plug_ins_run_without_touching_the_loop(home):
    registry = _registry()
    registry.register_tool("ping", '{"tool": "ping"}  says pong', lambda args, ctx: ToolOutcome(text="pong"))
    registry.register_pre_step("tray", lambda ask, ctx: "[desktop] todo.txt")
    registry.register_answer_gate("upper", lambda answer, result: answer.upper())
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    brain = _scripted(['{"tool": "ping"}', '{"answer": "done"}'])
    result = run_loop("whats on my desktop", brain=brain, registry=registry, ctx=ctx)
    assert result.answer == "DONE" and result.tools_used == ["ping"]
    assert '{"tool": "ping"}' in brain.seen[0][0]["content"]
    assert "[desktop] todo.txt" in brain.seen[0][-1]["content"]


# --- the runtime hook ---------------------------------------------------------------------------


class _Agent:
    memory_router = object()

    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        self.events.append({"event_type": event_type, **details})

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs["response"], "route_reason": kwargs["reason"], "route_skips": ["model", "web"]}


def test_the_hook_answers_an_admitted_turn_and_records_the_tools(home, monkeypatch):
    from core.clean_loop import brain as brain_module
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setattr(brain_module, "resolve_local_manifest", lambda router, ctx: "manifest")
    monkeypatch.setattr(brain_module, "model_label", lambda manifest: ("ollama-local", "qwen3:8b"))
    script = _scripted(['{"tool": "read_file", "path": "Desktop/todo.txt"}', '{"answer": "Renew car insurance."}'])
    monkeypatch.setattr(brain_module, "make_brain", lambda router, manifest, ctx: script)
    agent, context = _Agent(), dict(OWNER)
    result = hook.maybe_answer(
        agent, text="whats on my to do list", effective_input="whats on my to do list",
        session_id="sess-hook", source_context=context,
    )
    assert result is not None
    assert result["route_reason"] == "clean_local_loop" and result["response"] == "Renew car insurance."
    assert result["model_execution"]["model_id"] == "qwen3:8b" and result["model_execution"]["model_calls"] == 2
    assert result["clean_local_loop"]["tools_used"] == ["read_file"]
    assert [r["tool_name"] for r in context["tool_receipts"]] == ["machine.read_file"]
    assert agent.events == [
        {"event_type": "tool_executed", "tool_name": "machine.read_file", "status": "executed", "lane": "clean_local_loop"}
    ]
    assert "model" not in result["route_skips"]


def test_the_hook_falls_through_when_the_loop_has_no_answer(home, monkeypatch):
    from core.clean_loop import brain as brain_module
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setattr(brain_module, "resolve_local_manifest", lambda router, ctx: "manifest")
    monkeypatch.setattr(brain_module, "model_label", lambda manifest: ("ollama-local", "qwen3:8b"))
    monkeypatch.setattr(brain_module, "make_brain", lambda router, manifest, ctx: _scripted(['{"answer": ""}']))
    context = dict(OWNER)
    result = hook.maybe_answer(
        _Agent(), text="whats a dmg file? my downloads is full of them", effective_input="x",
        session_id="sess-empty", source_context=context,
    )
    assert result is None and context["clean_local_loop_route"] == "no_answer_fell_through"


def test_the_hook_answers_plainly_when_the_model_cannot_load(home, monkeypatch):
    from core.clean_loop import brain as brain_module
    from core.clean_loop import hook

    def refused(messages):
        raise RuntimeError("model_load_gated_low_memory")

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setattr(brain_module, "resolve_local_manifest", lambda router, ctx: "manifest")
    monkeypatch.setattr(brain_module, "model_label", lambda manifest: ("ollama-local", "qwen3:8b"))
    monkeypatch.setattr(brain_module, "make_brain", lambda router, manifest, ctx: refused)
    context = dict(OWNER)
    result = hook.maybe_answer(
        _Agent(), text="whats on my desktop", effective_input="x", session_id="sess-oom", source_context=context
    )
    assert context["clean_local_loop_route"] == "brain_error_answered"
    assert result is not None and result["response"] == hook.BRAIN_DOWN_TEXT
    assert "/" not in result["response"]


def test_the_hook_does_nothing_for_a_red_ask(home, monkeypatch):
    from core.clean_loop import brain as brain_module
    from core.clean_loop import hook

    monkeypatch.setenv(route.FLAG_ENV, "1")
    monkeypatch.setattr(brain_module, "resolve_local_manifest", lambda router, ctx: pytest.fail("brain resolved"))
    context = dict(OWNER)
    result = hook.maybe_answer(
        _Agent(), text="delete my old screenshots", effective_input="delete my old screenshots",
        session_id="sess-red", source_context=context,
    )
    assert result is None and context["clean_local_loop_route"] == "red_turn"


def test_the_hook_is_inert_with_the_flag_off(monkeypatch):
    from core.clean_loop import hook

    monkeypatch.delenv(route.FLAG_ENV, raising=False)
    context = dict(OWNER)
    assert hook.maybe_answer(_Agent(), text="whats on my desktop", effective_input="x", session_id="s",
                             source_context=context) is None
    assert "clean_local_loop_route" not in context


def test_reads_reach_a_lifecycle_opened_after_the_loop():
    # Mac clean-4: the publication gate opened the turn's lifecycle after the loop had already
    # recorded its reads, so five answers built from real reads were refused as "nothing retrieved".
    from core import grounding_lifecycle as gl
    from core.clean_loop.hook import _record_observations

    gl.reset_for_tests()
    context: dict[str, Any] = {"turn_id": "t-late", "session_id": "s-late"}
    receipt = {
        "tool_name": "processes",
        "execution": {"executed": True},
        "response_preview": "Google Chrome 41.2% CPU",
    }
    _record_observations(context, [receipt])
    gl.register_required(context, request_text="whats eating my cpu right now")
    gl.harvest_turn_observations(context)
    record = gl.lifecycle_for_context(context)
    assert record is not None
    assert any("Google Chrome" in str(row.get("summary")) for row in record.typed_observations)


# --- what the tools hand the model (Fable consult, 2026-10-28) ----------------------------------


def test_a_listing_shows_size_and_age_on_every_line(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("list_dir").run({"path": "Downloads"}, ctx)
    line = next(l for l in outcome.text.splitlines() if "plumber_invoice.txt" in l)
    assert "bytes" in line and "modified 20" in line and "today" in line, line
    assert ctx.receipts[-1]["response_preview"] == outcome.text[:1800]


def test_find_lines_carry_size_and_age(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("find").run({"query": "plumber"}, ctx)
    line = next(l for l in outcome.text.splitlines() if "plumber_invoice.txt" in l)
    assert "modified" in line, line


def test_find_looks_inside_documents_when_no_name_matches(home):
    (home / "Downloads" / "invoice_4471.txt").write_text("Dalkey Plumbing\nTotal due: EUR 260.00\n", encoding="utf-8")
    (home / "Documents" / "notes.md").write_text("nothing relevant here\n", encoding="utf-8")
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("find").run({"query": "dalkey"}, ctx)
    assert outcome.ok, outcome.text
    assert "invoice_4471.txt" in outcome.text and "Dalkey Plumbing" in outcome.text
    read = [r for r in ctx.receipts if r["tool_name"] == "machine.read_file"]
    assert read and all("notes.md" not in r["response_preview"] for r in read)
    assert all("nothing relevant" not in r["response_preview"] for r in ctx.receipts)


def test_find_says_what_to_try_when_nothing_matches(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("find").run({"query": "zebra"}, ctx)
    assert "nothing found for: zebra" in outcome.text and "Try another word" in outcome.text


def test_largest_shows_real_sizes_and_no_delete_advice(home):
    from core.runtime_execution_tools import RuntimeExecutionResult

    (home / "Downloads" / "trailer.mov").write_bytes(b"x" * 2_500_000)

    def execute(intent, arguments, **_kwargs):
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed",
            response_text=(
                f"Largest files on {home}/Downloads (measured, largest first):\n"
                f"- {home}/Downloads/trailer.mov — 0.0 GB [file] · review before deleting\n"
                "I won't delete anything without your explicit go-ahead on a specific path."
            ),
        )

    ctx = ToolContext(home=home, source_context=dict(OWNER), execute=execute)
    outcome = _registry().tool("largest").run({"path": "Downloads"}, ctx)
    assert "trailer.mov (2.4 MB, modified" in outcome.text, outcome.text
    assert "0.0 GB" not in outcome.text and "delet" not in outcome.text.lower()


def test_a_tool_fault_names_the_real_error(home):
    def execute(intent, arguments, **_kwargs):
        raise TypeError("is_dir() got an unexpected keyword argument 'follow_symlinks'")

    ctx = ToolContext(home=home, source_context=dict(OWNER), execute=execute)
    outcome = _registry().tool("disk_usage").run({}, ctx)
    assert "TypeError: is_dir() got an unexpected keyword argument" in outcome.text


def test_the_loop_asks_once_to_look_before_answering(home):
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    brain = _scripted(
        [
            '{"answer": "Plumbers usually charge about 100 euro."}',
            '{"tool": "read_file", "path": "Downloads/plumber_invoice.txt"}',
            '{"answer": "You owe EUR 140.00."}',
        ]
    )
    from core.clean_loop.loop import LOOK_FIRST_NUDGE

    result = run_loop("how much do i owe the plumber?", brain=brain, registry=_registry(), ctx=ctx)
    assert result.answer == "You owe EUR 140.00."
    assert brain.seen[1][-1]["content"] == LOOK_FIRST_NUDGE
    general = run_loop("hi", brain=_scripted(['{"answer": "Hello!"}']), registry=_registry(), ctx=ctx)
    assert general.answer == "Hello!" and general.model_calls == 2


def test_the_loop_never_repeats_a_failed_call(home):
    calls = []

    def execute(intent, arguments, **_kwargs):
        calls.append(intent)
        raise TypeError("boom")

    ctx = ToolContext(home=home, source_context=dict(OWNER), execute=execute)
    brain = _scripted(['{"tool": "disk_usage"}', '{"tool": "disk_usage"}', '{"answer": "I could not check the disk."}'])
    from core.clean_loop.loop import REPEAT_NOTICE

    result = run_loop("is my disk full?", brain=brain, registry=_registry(), ctx=ctx)
    assert calls == ["machine.disk_usage"]
    assert result.steps[1]["status"] == "repeated_failure"
    assert brain.seen[2][-1]["content"].endswith(REPEAT_NOTICE)
    assert all("think_ms" in step for step in result.steps)


def test_the_privacy_meter_counts_only_what_leaves(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core import privacy_meter

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool-home"))
    monkeypatch.setattr(privacy_meter, "_path", lambda: tmp_path / "vool-home" / "privacy-meter.json")
    local = SimpleNamespace(source_type="http", runtime_config={"base_url": "http://127.0.0.1:11434"}, metadata={}, provider_id="ollama")
    cloud = SimpleNamespace(source_type="http", runtime_config={"base_url": "https://api.example.com"}, metadata={}, provider_id="cloudco")
    request = SimpleNamespace(system_prompt="sys", prompt="hello", messages=[{"role": "user", "content": "héllo"}])
    privacy_meter.note_model_call(local, request)
    assert privacy_meter.today()["bytes"] == 0
    assert privacy_meter.plain_line() == "Today nothing you wrote was sent to online AI models."
    privacy_meter.note_model_call(cloud, request)
    state = privacy_meter.today()
    assert state["bytes"] == len("sys") + len("hello") + len("héllo".encode("utf-8")) and state["calls"] == 1
    assert "cloudco" in privacy_meter.plain_line(state)


def test_the_privacy_tool_answers_from_the_local_meter(home, monkeypatch):
    from core import privacy_meter

    monkeypatch.setattr(privacy_meter, "today", lambda: {"day": "x", "bytes": 0, "calls": 0, "providers": {}})
    ctx = ToolContext(home=home, source_context=dict(OWNER))
    outcome = _registry().tool("privacy").run({}, ctx)
    assert outcome.text == "Today nothing you wrote was sent to online AI models."
    assert route.asks_about_own_files_or_computer("did anything leave my computer today?")


# --- admission and exact names (Mac clean-7 and hold-out, 2026-10-28) -------------------------

_NAMES = lambda: route.ask_words(  # noqa: E731
    "Lisbon boarding pass, flat viewing notes, grandma pancakes, q3-report, warehouse audit, cinema tickets"
)


@pytest.mark.parametrize(
    "ask",
    [
        "what seat am i in on the lisbon flight",
        "how much milk goes in grandmas pancakes",
        "which flat had the balcony",
        "when do i have to pay the plumber by",
        "when did we move the warehouse audit to",
        "whats in the q3 report",
    ],
)
def test_questions_about_the_owners_own_things_come_in(ask):
    decision = route.decide(ask, source_context=OWNER, resolve_brain=_local_brain, env=ON, file_words=_NAMES)
    assert decision.admit, decision.reason


@pytest.mark.parametrize(
    ("ask", "reason"),
    [
        ("can you pay the plumber", "red_turn"),
        ("pay the plumber", "red_turn"),
        ("how do i send 2 eth to bob", "red_turn"),
        ("what is in my crypto wallet", "red_turn"),
        ("move the cat photo to my desktop", "write_turn"),
        ("tell me a joke", "not_about_own_files"),
        ("what is a dmg", "not_about_own_files"),
    ],
)
def test_orders_money_and_general_asks_stay_out(ask, reason):
    decision = route.decide(ask, source_context=OWNER, resolve_brain=_local_brain, env=ON, file_words=_NAMES)
    assert (decision.admit, decision.reason) == (False, reason)


def test_file_words_reads_names_only_and_skips_links_and_hidden(tmp_path):
    from core.clean_loop.tools import file_words

    (tmp_path / "Documents" / "trips").mkdir(parents=True)
    (tmp_path / "Documents" / "trips" / "Lisbon boarding pass.pdf").write_bytes(b"x")
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / ".secret plans.txt").write_text("x")
    (tmp_path / "Desktop" / "elsewhere").symlink_to(tmp_path / "Documents")
    words = file_words(tmp_path)
    assert {"lisbon", "boarding"} <= words
    assert "secret" not in words and "elsewhere" not in words


def test_a_shortened_file_name_is_put_back_only_when_it_is_unambiguous():
    from core.clean_loop.loop import LoopResult
    from core.clean_loop.tools import exact_names

    result = LoopResult(answer="", seen=[
        "- flat viewing notes.md (123 bytes, modified 2026-10-22, 6 days ago)\n"
        "- Screenshot 2026-10-20 at 11.40.png (1.2 MB, modified 2026-10-20, 8 days ago)\n"
        "- todo.txt (10 bytes, modified 2026-10-22, today)"
    ])
    fixed = exact_names("On your desktop: notes.md, Screenshot 2026-10-20.png and todo.txt.", result)
    assert fixed == "On your desktop: flat viewing notes.md, Screenshot 2026-10-20 at 11.40.png and todo.txt."
    result.seen.append("- old notes.md (1 bytes, modified 2026-10-22, today)")
    assert exact_names("You have notes.md", result) == "You have notes.md"
