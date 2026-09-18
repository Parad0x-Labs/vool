"""Fast paths cross the SAME permission and effect authority as the model tool loop.

Confirmed defect (2026-09-02, base a6c8e3c4): the machine, PDF, skill, media and currency
deterministic fast paths executed their tools by calling ``execute_runtime_tool`` directly --
BELOW ``decide_tool_call``, the one permission authority the model tool-intent route crosses.
Their intent is safe today, but execution authority differed by route, so the same user request
was decided two different ways depending only on which router happened to claim the turn:

    "Create a text file gate-notes.txt on my Desktop ..."  via the model route in Manual mode
        -> REQUIRE_APPROVAL, nothing written until approved.
    The same sentence, answered by the machine fast path in Manual mode
        -> written to disk immediately, no decision ever taken.

The repair is ONE canonical boundary, ``core/authorized_tool_execution.execute_authorized_runtime_tool``,
used by BOTH routes: it takes an explicit permission decision (obtained from the same
``decide_tool_call`` the model loop uses), refuses typed on DENY / REQUIRE_APPROVAL, and only on
ALLOW crosses ``execute_runtime_tool`` -- the single effect lifecycle (reservation scope,
execution, outcome, receipts, Activity). Lanes whose effect is not a runtime-tool dispatch
(media local render, currency live-rate retrieval) take their decision from the same authority
through ``authorize_runtime_tool`` in the same module.

Route-parity matrix this file pins (fast path vs the model route's own ``decide_tool_call``):

    lane      | MANUAL            | AUTO              | PLAN (denied)     | cancelled
    ----------|-------------------|-------------------|-------------------|------------------
    machine W | pending_approval  | allow (executes)  | deny              | cancelled
    machine R | allow (executes)  | allow (executes)  | allow (executes)  | n/a (read)
    pdf       | allow (executes)  | allow (executes)  | deny (project)    | n/a (read)
    skill W   | pending_approval  | see per-tool      | deny              | cancelled
    media     | pending_approval  | allow (renders)   | deny              | cancelled
    currency  | allow (fetches)   | allow (fetches)   | deny (no fetch)   | n/a (read)

Every refusal carries the authority's typed decision; every execution carries it in its
receipt details; no route mints, widens or skips a decision.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from core.mode_permission_policy import (
    OperatingMode,
    PermissionAction,
    PermissionDecision,
    PermissionEffect,
    decide_tool_call,
    grant_internal_authority,
    reset_mode_permission_state,
    set_active_mode,
)

# ---------------------------------------------------------------- fixtures ----


@pytest.fixture(autouse=True)
def _clean_permission_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Machine-lane writes resolve under a disposable home, never the operator's real one."""
    home = tmp_path / "machinehome"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_machine_home", lambda: home)
    return home


class _Agent:
    """The only lane surface: answer recorder + runtime-event recorder."""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def _fast_path_result(self, **kwargs: Any) -> dict[str, Any]:
        self.results.append(kwargs)
        return dict(kwargs)

    def _emit_runtime_event(self, source_context: Any, **kwargs: Any) -> None:
        self.events.append(kwargs)


def _ctx(mode: str | None = None, session: str = "s1", **extra: Any) -> dict[str, Any]:
    context: dict[str, Any] = {"session_id": session, "surface": "api"}
    if mode is not None:
        set_active_mode(session, mode)
        context["operating_mode"] = mode
    context.update(extra)
    return context


def _model_route_decision(
    intent: str,
    arguments: dict[str, Any],
    *,
    session: str,
    context: dict[str, Any] | None = None,
    task_id: str = "t1",
) -> PermissionDecision:
    """The canonical route's own decision for the same call -- the parity comparator."""
    merged = dict(context or {})
    merged.setdefault("session_id", session)
    return decide_tool_call(
        intent=intent, arguments=arguments, task_id=task_id, source_context=merged
    )


def _permission(reply: dict[str, Any] | None) -> dict[str, Any]:
    return dict(((reply or {}).get("details") or {}).get("permission") or {})


MACHINE_WRITE_TEXT = (
    "Create a text file gate-notes.txt on my Desktop with exactly this content: parity check"
)
MACHINE_WRITE_ARGS = {"path": "~/Desktop/gate-notes.txt", "content": "parity check"}


# ------------------------------------------------------------------ machine ----


def test_machine_write_in_manual_prompts_like_the_model_route(fake_home: Path) -> None:
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    decision = _model_route_decision(
        "machine.write_file", MACHINE_WRITE_ARGS, session="s1", context=_ctx("manual")
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL

    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("manual"),
    )
    assert reply is not None, "the lane must answer, not fall open"
    assert not (fake_home / "Desktop" / "gate-notes.txt").exists(), (
        "the fast path wrote the file with no permission decision taken -- the defect"
    )
    permission = _permission(reply)
    assert permission.get("effect") == "require_approval"
    assert reply.get("status") == "pending_approval"
    request = dict(reply.get("approval_request") or {})
    assert request.get("intent") == "machine.write_file"


def test_machine_write_in_auto_executes_like_the_model_route(fake_home: Path) -> None:
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    decision = _model_route_decision(
        "machine.write_file", MACHINE_WRITE_ARGS, session="s1", context=_ctx("auto")
    )
    assert decision.effect is PermissionEffect.ALLOW

    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("auto"),
    )
    assert reply is not None
    assert (fake_home / "Desktop" / "gate-notes.txt").exists(), (
        "Auto permits this write on the model route; the fast path must keep working"
    )
    assert _permission(reply).get("effect") == "allow"


def test_machine_write_in_plan_is_denied_like_the_model_route(fake_home: Path) -> None:
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    decision = _model_route_decision(
        "machine.write_file", MACHINE_WRITE_ARGS, session="s1", context=_ctx("plan")
    )
    assert decision.effect is PermissionEffect.DENY

    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("plan"),
    )
    assert reply is not None
    assert not (fake_home / "Desktop" / "gate-notes.txt").exists()
    assert _permission(reply).get("effect") == "deny"
    assert reply.get("status") == "blocked_by_mode"


def test_machine_write_overwrite_in_auto_prompts_like_the_model_route(fake_home: Path) -> None:
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    existing = fake_home / "Desktop" / "gate-notes.txt"
    existing.write_text("original", encoding="utf-8")

    decision = _model_route_decision(
        "machine.write_file", MACHINE_WRITE_ARGS, session="s1", context=_ctx("auto")
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, (
        "overwriting an existing file must prompt even in Auto"
    )

    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("auto"),
    )
    assert reply is not None
    assert existing.read_text(encoding="utf-8") == "original", (
        "the fast path overwrote a real file the model route would have prompted for"
    )
    assert _permission(reply).get("effect") == "require_approval"


def test_machine_write_cancelled_before_dispatch_does_not_run(fake_home: Path) -> None:
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    cancel = threading.Event()
    cancel.set()
    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("auto", cancel_event=cancel),
    )
    assert reply is not None
    assert not (fake_home / "Desktop" / "gate-notes.txt").exists(), (
        "a cancelled turn must not execute on the fast path either"
    )
    assert reply.get("status") == "cancelled"


def test_machine_read_in_manual_still_executes_like_the_model_route(fake_home: Path) -> None:
    """Invariant 10: where policy permits, today's fast-path behavior is unchanged."""
    from core.authorized_tool_execution import execute_authorized_runtime_tool

    specs = fake_home / "Documents" / "specs.txt"
    specs.write_text("cpu: 8 cores\nram: 16 GB\n", encoding="utf-8")

    decision = _model_route_decision(
        "machine.read_file", {"path": str(specs)}, session="s1", context=_ctx("manual")
    )
    assert decision.effect is PermissionEffect.ALLOW

    execution = execute_authorized_runtime_tool(
        "machine.read_file", {"path": str(specs)}, task_id="t1", source_context=_ctx("manual")
    )
    assert execution is not None and execution.ok, (
        f"a Manual-mode read must still run; got {getattr(execution, 'status', None)}"
    )
    assert "cpu: 8 cores" in (execution.response_text or "")


def test_machine_write_with_narrow_internal_authority_runs_without_a_prompt(
    fake_home: Path,
) -> None:
    """Typed, narrow, bounded internal authority decides -- never the caller's location."""
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    token = grant_internal_authority(
        label="parity-fixture-nightly-export",
        actions=[PermissionAction.CREATE_FILES],
        intents=["machine.write_file"],
        session_id="s-auth",
        duration_seconds=300,
    )
    decision = _model_route_decision(
        "machine.write_file",
        MACHINE_WRITE_ARGS,
        session="s-auth",
        context=_ctx("manual", session="s-auth", internal_authority_token=token),
    )
    assert decision.effect is PermissionEffect.ALLOW, "the covering scope must decide allow"

    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s-auth", source_surface="api",
        source_context=_ctx("manual", session="s-auth", internal_authority_token=token),
    )
    assert reply is not None
    assert (fake_home / "Desktop" / "gate-notes.txt").exists()
    permission = _permission(reply)
    assert permission.get("effect") == "allow"
    assert "parity-fixture-nightly-export" in str(permission.get("reason") or ""), (
        "an allow taken on internal authority must name the scope that decided it"
    )


def test_machine_write_with_forged_token_fails_closed(fake_home: Path) -> None:
    """Unknown/missing authority NEVER becomes permission (invariant 9)."""
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("manual", internal_authority_token="forged-token-not-minted"),
    )
    assert reply is not None
    assert not (fake_home / "Desktop" / "gate-notes.txt").exists()
    assert _permission(reply).get("effect") == "require_approval"


def test_machine_write_with_wrong_intent_scope_fails_closed(fake_home: Path) -> None:
    """A scope minted for another intent cannot carry this one (narrow means narrow)."""
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_write_request

    token = grant_internal_authority(
        label="parity-fixture-workspace-only",
        actions=[PermissionAction.CREATE_FILES],
        intents=["workspace.write_file"],
        session_id="s1",
        duration_seconds=300,
    )
    agent = _Agent()
    reply = maybe_handle_direct_machine_write_request(
        agent, MACHINE_WRITE_TEXT, session_id="s1", source_surface="api",
        source_context=_ctx("manual", internal_authority_token=token),
    )
    assert reply is not None
    assert not (fake_home / "Desktop" / "gate-notes.txt").exists()
    assert _permission(reply).get("effect") == "require_approval"


# ---------------------------------------------------------------------- pdf ----


def test_pdf_read_in_manual_runs_like_the_model_route(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read

    pdf = fake_home / "Documents" / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake fixture body")
    import core.runtime_execution_tools as ret

    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_extract(arguments: dict[str, Any]) -> Any:
        calls.append(("pdf.extract_text", dict(arguments)))
        return ret.RuntimeExecutionResult(
            handled=True, ok=True, status="ok",
            response_text="Quarterly totals 4821",
            details={},
        )

    monkeypatch.setattr(ret, "_pdf_extract_text", _fake_extract)

    decision = _model_route_decision(
        "pdf.extract_text", {"path": str(pdf)}, session="s1", context=_ctx("manual")
    )
    assert decision.effect is PermissionEffect.ALLOW

    agent = _Agent()
    reply = maybe_handle_pdf_read(
        agent, f"Extract the text from {pdf}", session_id="s1",
        source_context=_ctx("manual"),
    )
    assert reply is not None
    assert len(calls) == 1, "the read must run once, like the model route allows"
    assert _permission(reply).get("effect") == "allow"


def test_pdf_read_denied_by_project_permissions_is_refused_like_the_model_route(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read

    pdf = fake_home / "Documents" / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake fixture body")
    import core.runtime_execution_tools as ret

    calls: list[tuple[str, dict[str, Any]]] = []

    def _fake_extract(arguments: dict[str, Any]) -> Any:
        calls.append(("pdf.extract_text", dict(arguments)))
        return ret.RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text="x", details={})

    monkeypatch.setattr(ret, "_pdf_extract_text", _fake_extract)

    context = _ctx("manual", project_permissions={"read_files": False})
    decision = _model_route_decision(
        "pdf.extract_text", {"path": str(pdf)}, session="s1", context=context
    )
    assert decision.effect is PermissionEffect.DENY

    agent = _Agent()
    reply = maybe_handle_pdf_read(
        agent, f"Extract the text from {pdf}", session_id="s1",
        source_context=context,
    )
    assert reply is not None
    assert calls == [], "a project-level deny must hold on the fast path too"
    assert _permission(reply).get("effect") == "deny"


# -------------------------------------------------------------------- skill ----


def _fake_skill_handler(calls: list[str], ok_text: str = "skill staged"):
    import core.runtime_execution_tools as ret

    def _handler(arguments: dict[str, Any]) -> Any:
        calls.append(str(arguments.get("name") or arguments.get("path") or ""))
        return ret.RuntimeExecutionResult(
            handled=True, ok=True, status="ok", response_text=ok_text, details={},
        )

    return _handler


SKILL_CREATE_TEXT = "Create a skill called parity-skill that echoes input."


def test_skill_create_in_manual_prompts_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_skill_create", _fake_skill_handler(calls))

    decision = _model_route_decision(
        "skill.create",
        {"name": "parity-skill", "description": "d", "body": SKILL_CREATE_TEXT},
        session="s1",
        context=_ctx("manual"),
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL

    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, SKILL_CREATE_TEXT, session_id="s1", source_context=_ctx("manual")
    )
    assert reply is not None
    assert calls == [], "skill.create wrote with no permission decision -- the defect"
    assert _permission(reply).get("effect") == "require_approval"
    assert reply.get("status") == "pending_approval"


def test_skill_create_in_auto_executes_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_skill_create", _fake_skill_handler(calls))

    decision = _model_route_decision(
        "skill.create",
        {"name": "parity-skill", "description": "d", "body": SKILL_CREATE_TEXT},
        session="s1",
        context=_ctx("auto"),
    )
    assert decision.effect is PermissionEffect.ALLOW

    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, SKILL_CREATE_TEXT, session_id="s1", source_context=_ctx("auto")
    )
    assert reply is not None
    assert calls == ["parity-skill"], "Auto permits this create; the fast path must keep working"
    assert _permission(reply).get("effect") == "allow"


def test_skill_install_in_auto_prompts_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skill.install classifies create_files + change_settings: Auto PROMPTS for it."""
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_skill_install", _fake_skill_handler(calls))

    text = "Install the skill parity-skill."
    decision = _model_route_decision(
        "skill.install", {"path": "parity-skill"}, session="s1", context=_ctx("auto")
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL

    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, text, session_id="s1", source_context=_ctx("auto")
    )
    assert reply is not None
    assert calls == [], "install changes what the runtime loads; Auto must prompt, fast path too"
    assert _permission(reply).get("effect") == "require_approval"


def test_skill_create_in_plan_is_denied_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_skill_create", _fake_skill_handler(calls))

    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, SKILL_CREATE_TEXT, session_id="s1", source_context=_ctx("plan")
    )
    assert reply is not None
    assert calls == []
    assert _permission(reply).get("effect") == "deny"
    assert reply.get("status") == "blocked_by_mode"


def test_skill_list_in_manual_still_executes_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reads stay reads: withdrawing them would be a denial wider than the sentence."""
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(ret, "_skill_list", _fake_skill_handler(calls, ok_text="no skills yet"))

    decision = _model_route_decision("skill.list", {"workspace": ""}, session="s1", context=_ctx("manual"))
    assert decision.effect is PermissionEffect.ALLOW

    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, "List my skills.", session_id="s1", source_context=_ctx("manual")
    )
    assert reply is not None
    assert len(calls) == 1
    assert _permission(reply).get("effect") == "allow"


# -------------------------------------------------------------------- media ----


def _patch_media_stack(monkeypatch: pytest.MonkeyPatch, *, local_ok: bool) -> dict[str, int]:
    """Deterministic local+cloud media stack; returns the call counters."""
    import core.local_media_render as lmr
    import core.media_tools
    import core.resource_governor as gov

    counts = {"local": 0, "cloud": 0}

    monkeypatch.setattr(lmr, "local_render_available", lambda: local_ok)
    monkeypatch.setattr(lmr, "wants_local_render", lambda _text: False)
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda: local_ok)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: False)

    class _Room:
        actions: list[str] = []
        usable_after_gb = 8.0

        def note(self) -> str:
            return ""

    def _plan_render():
        if local_ok:
            return ({"low_memory": False, "width": 512, "height": 512, "steps": 4, "label": "512px"}, _Room())
        return (None, None)

    monkeypatch.setattr(gov, "plan_render", _plan_render)

    def _run_local(prompt: str, **_kwargs: Any):
        counts["local"] += 1
        return (True, "/tmp/fake-render.png")

    monkeypatch.setattr(lmr, "run_local_image_render", _run_local)

    class _Result:
        ok = True
        image_ref = "https://fake.example/img.png"
        status = "ok"
        message = "Generated the image."

    def _generate(prompt: str = "", account: str = "default"):
        counts["cloud"] += 1
        return _Result()

    monkeypatch.setattr(core.media_tools, "generate_image", _generate)
    monkeypatch.setattr(core.media_tools, "has_image_service", lambda: True)
    monkeypatch.setattr(gov, "reclaim_if_pressured", lambda: None)
    return counts


def test_media_generation_in_manual_prompts_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_media import maybe_handle_image_generation

    counts = _patch_media_stack(monkeypatch, local_ok=True)

    decision = _model_route_decision(
        "image.generate", {"prompt": "a lighthouse"}, session="s1", context=_ctx("manual")
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL

    agent = _Agent()
    reply = maybe_handle_image_generation(
        agent, "Generate an image of a lighthouse", session_id="s1",
        source_context=_ctx("manual"),
    )
    assert reply is not None
    assert counts == {"local": 0, "cloud": 0}, "the fast path rendered an image with no decision"
    assert _permission(reply).get("effect") == "require_approval"
    assert reply.get("status") == "pending_approval"


def test_media_generation_in_auto_still_renders_like_the_model_route_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_media import maybe_handle_image_generation

    counts = _patch_media_stack(monkeypatch, local_ok=True)

    decision = _model_route_decision(
        "image.generate", {"prompt": "a lighthouse"}, session="s1", context=_ctx("auto")
    )
    assert decision.effect is PermissionEffect.ALLOW

    agent = _Agent()
    reply = maybe_handle_image_generation(
        agent, "Generate an image of a lighthouse", session_id="s1",
        source_context=_ctx("auto"),
    )
    assert reply is not None
    assert counts["local"] == 1, "Auto permits media generation; local rendering must keep working"
    assert counts["cloud"] == 0
    assert _permission(reply).get("effect") == "allow"


def test_media_generation_in_plan_is_denied_like_the_model_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_media import maybe_handle_image_generation

    counts = _patch_media_stack(monkeypatch, local_ok=True)

    decision = _model_route_decision(
        "image.generate", {"prompt": "a lighthouse"}, session="s1", context=_ctx("plan")
    )
    assert decision.effect is PermissionEffect.DENY

    agent = _Agent()
    reply = maybe_handle_image_generation(
        agent, "Generate an image of a lighthouse", session_id="s1",
        source_context=_ctx("plan"),
    )
    assert reply is not None
    assert counts == {"local": 0, "cloud": 0}
    assert _permission(reply).get("effect") == "deny"


def test_media_generation_cancelled_does_not_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.agent_runtime.fast_paths_media import maybe_handle_image_generation

    counts = _patch_media_stack(monkeypatch, local_ok=True)
    cancel = threading.Event()
    cancel.set()

    agent = _Agent()
    reply = maybe_handle_image_generation(
        agent, "Generate an image of a lighthouse", session_id="s1",
        source_context=_ctx("auto", cancel_event=cancel),
    )
    assert reply is not None
    assert counts == {"local": 0, "cloud": 0}
    assert reply.get("status") == "cancelled"


def test_media_cloud_generation_runs_through_one_seam_without_double_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No local engine: the cloud path crosses the canonical boundary exactly once."""
    from core.agent_runtime.fast_paths_media import maybe_handle_image_generation

    counts = _patch_media_stack(monkeypatch, local_ok=False)

    agent = _Agent()
    reply = maybe_handle_image_generation(
        agent, "Generate an image of a lighthouse", session_id="s1",
        source_context=_ctx("auto"),
    )
    assert reply is not None
    assert counts == {"local": 0, "cloud": 1}, (
        "the cloud generator must run exactly once through the one boundary"
    )
    assert _permission(reply).get("effect") == "allow"


# ----------------------------------------------------------------- currency ----


def _fx_context(mode: str, fetches: list[tuple[str, float | None]]) -> dict[str, Any]:
    def _fake_fetch(url: str, timeout_s: float = 8.0, headers: Any = None) -> dict[str, Any]:
        fetches.append((url, timeout_s))
        return {
            "amount": 1.0,
            "base": "USD",
            "date": "2026-09-01",
            "rates": {"EUR": 0.92},
        }

    return _ctx(
        mode,
        workspace="/tmp",
        fx_fetch_json=_fake_fetch,
        live_lookup_timeout_s=4.0,
    )


def test_currency_live_rate_in_manual_fetches_like_the_model_route_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.policy_engine
    from core.agent_runtime import turn_frontdoor

    monkeypatch.setattr(core.policy_engine, "allow_web_fallback", lambda: True)
    fetches: list[tuple[str, float | None]] = []

    decision = _model_route_decision(
        "web.fetch", {"url": "https://api.frankfurter.dev/"}, session="s1", context=_ctx("manual")
    )
    assert decision.effect is PermissionEffect.ALLOW, (
        "public read-only retrieval is allowed in Manual on the model route"
    )

    reply = turn_frontdoor._currency_reply(
        "Convert 100 USD to EUR", session_id="s1", source_context=_fx_context("manual", fetches)
    )
    assert reply is not None
    assert len(fetches) == 1, "Manual allows public read-only retrieval; the fetch must happen"
    assert reply.get("grounded") == "live_rate"


def test_currency_live_rate_in_plan_is_not_fetched_like_the_model_route_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.policy_engine
    from core.agent_runtime import turn_frontdoor

    monkeypatch.setattr(core.policy_engine, "allow_web_fallback", lambda: True)
    fetches: list[tuple[str, float | None]] = []

    decision = _model_route_decision(
        "web.fetch", {"url": "https://api.frankfurter.dev/"}, session="s1", context=_ctx("plan")
    )
    assert decision.effect is PermissionEffect.DENY

    reply = turn_frontdoor._currency_reply(
        "Convert 100 USD to EUR", session_id="s1", source_context=_fx_context("plan", fetches)
    )
    assert reply is not None
    assert fetches == [], (
        "Plan denies this retrieval on the model route; the fast path fetched anyway"
    )
    assert reply.get("grounded") in {
        "no_rate_declined",
        "retrieval_prohibited_declined",
        "retrieval_disabled_declined",
        "retrieval_denied_by_permission",
    }


# ------------------------------------------------------- the boundary itself ----


def test_the_boundary_refuses_without_a_decision_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing/unknown authority is a denial, never an execution (invariants 1, 9)."""
    from core import authorized_tool_execution as ate

    monkeypatch.setattr(
        ate, "decide_tool_call",
        lambda **_kwargs: None,
        raising=True,
    )
    executed: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(
        ret, "execute_runtime_tool",
        lambda intent, arguments, **_kw: executed.append(intent) or None,
        raising=True,
    )
    result = ate.execute_authorized_runtime_tool(
        "machine.write_file", MACHINE_WRITE_ARGS, task_id="t1",
        source_context=_ctx("manual"),
    )
    assert result is not None
    assert executed == [], "no decision means no execution, whatever the caller"
    assert result.ok is False and result.handled is True


def test_the_boundary_holds_one_decision_no_double_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that already holds an ALLOW decision is not re-decided (invariant 6)."""
    from core import authorized_tool_execution as ate

    decisions: list[int] = []

    def _counting_decide(**_kwargs: Any) -> PermissionDecision:
        decisions.append(1)
        return PermissionDecision(
            effect=PermissionEffect.ALLOW, mode=OperatingMode.AUTO,
            actions=(PermissionAction.CREATE_FILES,), reason="counted",
        )

    monkeypatch.setattr(ate, "decide_tool_call", _counting_decide, raising=True)
    executions: list[str] = []
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(
        ret, "execute_runtime_tool",
        lambda intent, arguments, **_kw: executions.append(intent)
        or ret.RuntimeExecutionResult(handled=True, ok=True, status="ok", response_text="done", details={}),
        raising=True,
    )
    held = PermissionDecision(
        effect=PermissionEffect.ALLOW, mode=OperatingMode.AUTO,
        actions=(PermissionAction.CREATE_FILES,), reason="already decided at the controller",
    )
    result = ate.execute_authorized_runtime_tool(
        "machine.write_file", MACHINE_WRITE_ARGS, task_id="t1",
        source_context=_ctx("auto"), permission_decision=held,
    )
    assert result is not None and result.ok
    assert decisions == [], "a held typed decision must be consumed, not re-consulted"
    assert executions == ["machine.write_file"]


def test_the_boundary_records_the_decision_on_the_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Receipt/observation carry WHICH decision authorized them (invariant 5)."""
    from core import authorized_tool_execution as ate

    monkeypatch.setattr(
        ate, "decide_tool_call",
        lambda **_kwargs: PermissionDecision(
            effect=PermissionEffect.ALLOW, mode=OperatingMode.MANUAL,
            actions=(PermissionAction.READ_FILES,), reason="Manual mode allows this bounded action.",
        ),
        raising=True,
    )
    import core.runtime_execution_tools as ret

    monkeypatch.setattr(
        ret, "execute_runtime_tool",
        lambda intent, arguments, **_kw: ret.RuntimeExecutionResult(
            handled=True, ok=True, status="ok", response_text="read", details={"kept": 1},
        ),
        raising=True,
    )
    result = ate.execute_authorized_runtime_tool(
        "machine.read_file", {"path": "x.txt"}, task_id="t1", source_context=_ctx("manual")
    )
    assert result is not None
    permission = dict((result.details or {}).get("permission") or {})
    assert permission.get("effect") == "allow"
    assert permission.get("actions") == ["read_files"]
    assert (result.details or {}).get("kept") == 1, "existing detail keys are preserved, not replaced"
