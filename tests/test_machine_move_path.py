"""machine.move_path — the destructive tier. Two independent gates must both pass: a protected-path
denylist (system/wallet locations) and a live OS consent prompt. Fail-closed everywhere. Both gates
are injectable, so the whole flow is proven without a real prompt or touching system state.
"""
from __future__ import annotations

import core.os_consent_gate as consent_gate
import core.policy_engine as policy_engine
from core.machine_file_ops import FileOpResult, move_path
from core.os_consent_gate import ConsentDeniedError, ConsentUnavailableError
from core.runtime_execution_tools import execute_runtime_tool

_ALLOW = lambda reason: True  # noqa: E731 - test consent stub
_DENY = lambda reason: False  # noqa: E731


def test_move_succeeds_when_both_gates_pass(tmp_path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "moved.txt"
    result = move_path(str(src), str(dst), consent_fn=_ALLOW, protected=[])
    assert result.ok and result.status == "moved"
    assert dst.read_text(encoding="utf-8") == "payload" and not src.exists()


def test_protected_source_is_blocked_and_nothing_moves(tmp_path) -> None:
    protected = tmp_path / "system"
    (protected).mkdir()
    src = protected / "critical.dll"
    src.write_text("x", encoding="utf-8")
    dst = tmp_path / "out.dll"
    result = move_path(str(src), str(dst), consent_fn=_ALLOW, protected=[protected])
    assert result.status == "blocked_protected_path" and not result.ok
    assert src.exists() and not dst.exists()  # untouched


def test_protected_destination_is_blocked(tmp_path) -> None:
    protected = tmp_path / "system"
    protected.mkdir()
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    result = move_path(str(src), str(protected / "a.txt"), consent_fn=_ALLOW, protected=[protected])
    assert result.status == "blocked_protected_path" and src.exists()


def test_move_refused_without_consent(tmp_path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    result = move_path(str(src), str(tmp_path / "b.txt"), consent_fn=_DENY, protected=[])
    assert result.status == "consent_declined" and src.exists()


def test_consent_denied_error_is_fail_closed(tmp_path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")

    def raise_denied(reason):
        raise ConsentDeniedError("declined")

    result = move_path(str(src), str(tmp_path / "b.txt"), consent_fn=raise_denied, protected=[])
    assert result.status == "consent_declined" and src.exists()


def test_consent_unavailable_is_fail_closed(tmp_path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")

    def raise_unavailable(reason):
        raise ConsentUnavailableError("no prompt")

    result = move_path(str(src), str(tmp_path / "b.txt"), consent_fn=raise_unavailable, protected=[])
    assert result.status == "consent_unavailable" and src.exists()


def test_source_missing(tmp_path) -> None:
    result = move_path(str(tmp_path / "nope.txt"), str(tmp_path / "b.txt"), consent_fn=_ALLOW, protected=[])
    assert result.status == "source_missing" and not result.ok


def test_destination_exists_is_refused(tmp_path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    dst = tmp_path / "b.txt"
    dst.write_text("existing", encoding="utf-8")
    result = move_path(str(src), str(dst), consent_fn=_ALLOW, protected=[])
    assert result.status == "dest_exists"
    assert dst.read_text(encoding="utf-8") == "existing" and src.exists()  # neither clobbered nor moved


def test_empty_args_rejected() -> None:
    assert move_path("", "x", consent_fn=_ALLOW, protected=[]).status == "invalid_arguments"


# --- the tool wired into the model dispatcher (write policy enabled + consent override) ---

def test_move_path_tool_dispatch_with_write_enabled_and_consent(tmp_path, monkeypatch) -> None:
    orig_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine, "get",
        lambda key, default=None: True if key == "filesystem.allow_write_workspace" else orig_get(key, default),
    )
    consent_gate.set_consent_override_for_tests(lambda reason: True)
    try:
        src = tmp_path / "doc.txt"
        src.write_text("hi", encoding="utf-8")
        dst = tmp_path / "sub"
        dst.mkdir()
        target = dst / "doc.txt"
        result = execute_runtime_tool(
            "machine.move_path",
            {"source": str(src), "destination": str(target)},
            source_context={"workspace": str(tmp_path)},
        )
        assert result is not None and result.ok and result.status == "moved"
        assert target.exists() and not src.exists()
    finally:
        consent_gate.set_consent_override_for_tests(None)


def test_move_path_tool_disabled_when_write_policy_off(tmp_path, monkeypatch) -> None:
    orig_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine, "get",
        lambda key, default=None: False if key == "filesystem.allow_write_workspace" else orig_get(key, default),
    )
    result = execute_runtime_tool("machine.move_path", {"source": str(tmp_path / "a"), "destination": str(tmp_path / "b")})
    assert result is not None and result.ok is False and result.status == "disabled"


def test_move_tool_is_highest_tier_in_specs(monkeypatch) -> None:
    from core.runtime_execution_tools import runtime_execution_tool_specs

    orig_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine, "get",
        lambda key, default=None: True if key == "filesystem.allow_write_workspace" else orig_get(key, default),
    )
    specs = {s["intent"]: s for s in runtime_execution_tool_specs()}
    assert "machine.move_path" in specs
    assert specs["machine.move_path"]["read_only"] is False
    assert specs["machine.move_path"]["approval_requirement"] == "explicit_user_opt_in"


def test_result_dataclass_shape() -> None:
    r = FileOpResult(True, "moved", "ok", "a", "b")
    assert r.ok and r.source == "a" and r.dest == "b"
