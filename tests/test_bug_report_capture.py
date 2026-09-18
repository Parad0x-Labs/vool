"""Bounded diagnostics capture for the safe bug reporter.

Cause-named families: log bounding (per-source lines, total bytes, source count), prose and
repro caps, conversation-kind refusal (message bodies never enter the pipeline), typed
structure preservation (stack frames -> file/line/function without usernames), identifier
allowlisting for lanes/tools/models, and environment capture.
"""
from __future__ import annotations

import pytest

from core.bug_report.allowlist import parse_stack, sanitize_identifier, sanitize_prose
from core.bug_report.capture import (
    MAX_LOG_LINES_PER_SOURCE,
    MAX_LOG_SOURCES,
    MAX_LOG_TOTAL_BYTES,
    MAX_PROSE_CHARS,
    MAX_REPRO_STEPS,
    capture_environment,
    capture_logs,
)


def test_capture_environment_has_version_os_arch_and_honest_git_state(tmp_path) -> None:
    env = capture_environment(tmp_path)  # tmp_path is not a git checkout
    assert env.version
    assert env.os and env.arch and env.python
    assert env.source_kind == "unknown"
    assert env.source_sha == ""


def test_capture_environment_reads_git_sha_from_checkout(tmp_path) -> None:
    import subprocess

    repo = tmp_path / "mini"
    repo.mkdir()
    def git(*args: str) -> None:
        subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-qm", "init")
    env = capture_environment(repo)
    assert env.source_kind == "git"
    assert len(env.source_sha) == 40
    assert env.source_dirty is False


# --- stack parsing preserves typed structure, drops usernames ----------------------

def test_parse_stack_yields_frames_without_home_paths() -> None:
    raw = (
        "Traceback (most recent call last):\n"
        '  File "/Users/fixtureuser/vool/core/conductor.py", line 88, in plan_turn\n'
        '  File "/home/alice/vool/core/agent_node.py", line 12, in _resolve\n'
        "ValueError: cannot merge span 17 for session sess_deadbeef"
    )
    err = parse_stack(raw)
    assert err is not None
    assert err.exc_type == "ValueError"
    assert [f.file for f in err.frames] == ["conductor.py", "agent_node.py"]
    assert [f.line for f in err.frames] == [88, 12]
    assert [f.function for f in err.frames] == ["plan_turn", "_resolve"]
    blob = repr(err.to_dict())
    assert "fixtureuser" not in blob and "alice" not in blob


def test_parse_stack_sanitizes_secret_in_exception_message() -> None:
    raw = '  File "core/x.py", line 1, in f\nRuntimeError: key ghp_' + "C1d2E3f4G5h6J7k8L9m0n1O2p3Q4 rejected"
    err = parse_stack(raw)
    assert err is not None
    assert "ghp_" not in err.message
    assert err.exc_type == "RuntimeError"


# --- log bounding ---------------------------------------------------------------------

def test_log_lines_are_capped_per_source_with_honest_truncation() -> None:
    lines = [f"2026-09-01T12:00:{i % 60:02d} INFO adapter tick {i}" for i in range(5000)]
    excerpts = capture_logs([{"name": "daemon.log", "lines": lines}])
    assert len(excerpts) == 1
    excerpt = excerpts[0]
    # absolute bound, not the imported constant: the byte cap is a backstop that would
    # keep ~1950 of these lines on its own -- pinning 200 proves the LINE cap binds.
    assert len(excerpt.lines) <= 200
    assert MAX_LOG_LINES_PER_SOURCE == 200
    assert excerpt.truncated is True
    assert excerpt.original_line_count == 5000


def test_log_total_bytes_are_capped_across_sources() -> None:
    big_line = "2026-09-01T12:00:00 INFO " + "x" * 400
    sources = [
        {"name": f"src{i}.log", "lines": [big_line] * 500}
        for i in range(3)
    ]
    excerpts = capture_logs(sources)
    total = sum(len("\n".join(e.lines).encode("utf-8")) for e in excerpts)
    # absolute bound: the per-source line cap alone would keep 3x200x425B ~ 255KB
    assert total <= 65_536
    assert MAX_LOG_TOTAL_BYTES == 65_536


def test_too_many_log_sources_are_refused() -> None:
    sources = [{"name": f"s{i}.log", "lines": ["2026-09-01T12:00:00 INFO x"]} for i in range(MAX_LOG_SOURCES + 1)]
    with pytest.raises(Exception, match=r"(?i)source"):
        capture_logs(sources)


def test_log_lines_outside_safe_shapes_are_dropped_not_kept() -> None:
    sources = [{
        "name": "daemon.log",
        "lines": [
            "2026-09-01T12:00:00 INFO adapter started",   # safe shape: kept
            "user: hey can you reorganize my invoices",   # conversation-shaped: dropped
            "assistant: of course, I will do that now",   # conversation-shaped: dropped
            "random prose with no log shape at all",      # unstructured: dropped
        ],
    }]
    excerpts = capture_logs(sources)
    kept = list(excerpts[0].lines)
    assert any("adapter started" in line for line in kept)
    assert not any("invoices" in line for line in kept)
    assert not any("assistant:" in line for line in kept)
    assert not any("random prose" in line for line in kept)
    assert excerpts[0].dropped_lines >= 3


def test_log_lines_with_secrets_are_redacted_not_dropped() -> None:
    sources = [{
        "name": "daemon.log",
        "lines": ["2026-09-01T12:00:00 INFO auth failed for token sk-or-v1-" + "q" * 48],
    }]
    excerpts = capture_logs(sources)
    line = excerpts[0].lines[0]
    assert "sk-or-v1-" not in line
    assert "[redacted" in line


# --- conversation refusal ---------------------------------------------------------------

@pytest.mark.parametrize("name", ["conversation", "chat", "messages", "session_log", "chat_history", "conversation_log"])
def test_conversation_kind_log_sources_are_refused(name: str) -> None:
    with pytest.raises(Exception, match=r"(?i)conversation|message"):
        capture_logs([{"name": name, "lines": ["2026-09-01T00:00:00 INFO x"]}])
    with pytest.raises(Exception, match=r"(?i)conversation|message"):
        capture_logs([{"name": "ok.log", "kind": name, "lines": ["2026-09-01T00:00:00 INFO x"]}])


# --- prose / repro caps -------------------------------------------------------------------

def test_prose_is_sanitized_and_capped() -> None:
    raw = "crash after login with password=hunter2secure " + "y" * (MAX_PROSE_CHARS * 2)
    sanitized = sanitize_prose(raw, max_chars=MAX_PROSE_CHARS)
    assert "hunter2secure" not in sanitized
    assert len(sanitized) <= MAX_PROSE_CHARS + 64  # cap plus at most the truncation marker


def test_repro_step_cap_is_enforced() -> None:
    from core.bug_report.capture import validate_repro_steps

    assert MAX_REPRO_STEPS <= 12
    with pytest.raises(Exception, match=r"(?i)repro"):
        validate_repro_steps(["step"] * (MAX_REPRO_STEPS + 1))
    validate_repro_steps(["step"] * MAX_REPRO_STEPS)  # at the cap: accepted


# --- identifier allowlist ---------------------------------------------------------------------

def test_component_identifiers_are_allowlisted() -> None:
    assert sanitize_identifier("live_info_fast_path", field="lane") == "live_info_fast_path"
    assert sanitize_identifier("llama-3.1-8b", field="model") == "llama-3.1-8b"
    assert sanitize_identifier("read_file.v2", field="tool") == "read_file.v2"


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",
        "lane with spaces",
        "UPPERCASE_LANE",
        "lane;rm -rf",
        "x" * 65,
        "",
        "lane\ninjection",
    ],
)
def test_unsafe_component_identifiers_are_rejected(bad: str) -> None:
    assert sanitize_identifier(bad, field="lane") == ""
    from core.bug_report.capture import require_identifier

    with pytest.raises(ValueError):
        require_identifier(bad, field="lane")
