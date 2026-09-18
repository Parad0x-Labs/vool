"""Declared runtime flags: resolution order, override hygiene, and enumerability.

The enumerability test is the point of the module. A migration flag exists so both paths stay
exercised; if nothing can list the flags, "behind a flag" degrades into "the new path never
runs in CI" and the migration ships untested.
"""
from __future__ import annotations

import pytest

from core import runtime_flags


@pytest.fixture(autouse=True)
def _no_leaked_overrides():
    runtime_flags.clear_overrides()
    yield
    runtime_flags.clear_overrides()


def test_every_flag_is_enumerable_and_declares_its_env_var() -> None:
    flags = runtime_flags.all_flags()
    assert flags, "no flags declared"
    for entry in flags:
        assert entry.name and entry.env_var and entry.description
        assert entry.env_var.startswith("VOOL_"), entry.env_var
        assert isinstance(entry.default, bool)


def test_flag_names_and_env_vars_are_unique() -> None:
    flags = runtime_flags.all_flags()
    assert len({f.name for f in flags}) == len(flags)
    assert len({f.env_var for f in flags}) == len(flags)


# Flipped on 2026-07-28 after each was driven against real models and real output. The two that
# remain off are off for stated reasons, not for lack of attention.
SHIPS_OFF = {
    # Routes every turn to the model instead of the keyword allowlist. Not needed for the
    # measured win — the near_miss path fix delivers 8/8 without it — and turning it on
    # overrides deliberate advice-only / builder-style skips other tests assert.
    "always_on_tool_catalog",
    # Replaces a model's answer with a deterministic rendering when binding fails. Stays off
    # until the false-positive rate is measured on real traffic — a checker that blocks correct
    # answers is worse than the fabrication it prevents.
    "answer_binder_enforce",
    # Registers third-party tools into the live catalog. Stays off until install consent exists;
    # until then a manifest on disk would register tools nobody approved.
    "plugin_runtime_tools",
    # Ships the full 58-tool catalog on ordinary plain_text turns. Correct in principle — a turn
    # with no tools is why a cloud model invented its own call shape — but with it on, the first
    # cloud turn killed the daemon twice — once on a scratch daemon and once on the installed app —
    # with no Python traceback and no crash report. Off until that is diagnosed; the code stays so
    # the diagnosis has something to run against.
    "plain_text_tool_catalog",
    # Advisory taint marking on answers citing untrusted receipts (GOBLIN inv 9). Was added to
    # the registry WITHOUT this list — the drift this test exists to catch — and registered here
    # 2026-09-01 while wiring the bug-reporter flag below.
    "derived_taint",
    # Lets a model restructure an already-sanitized bug-report draft. Off by design: the local
    # model-free formatter is the working path; the cloud path is opt-in and fail-closed.
    "bug_report_cloud_synthesis",
}


def test_only_the_stated_flags_ship_off() -> None:
    """Turning a flag on is a claim that it was driven. Keep the list of exceptions explicit."""

    off = {entry.name for entry in runtime_flags.all_flags() if not entry.default}
    assert off == SHIPS_OFF


def test_every_flag_that_ships_on_is_actually_read() -> None:
    """A flag with no call site is a claim about behaviour that nothing honours.

    `tool_choice_auto` was declared and never wired; it read as a shipped capability for a while
    and did nothing. Deleted rather than left as decoration.
    """

    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sources = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for directory in ("core", "adapters", "apps")
        for path in (root / directory).rglob("*.py")
    )
    unread = [
        entry.name
        for entry in runtime_flags.all_flags()
        if f'flag_enabled("{entry.name}")' not in sources
    ]
    assert not unread, f"declared but never read: {unread}"


def test_unknown_flag_raises_and_names_the_declared_ones() -> None:
    with pytest.raises(KeyError, match="unknown runtime flag"):
        runtime_flags.flag_enabled("no_such_flag")


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_env_values_enable(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    entry = runtime_flags.flag("always_on_tool_catalog")
    monkeypatch.setenv(entry.env_var, raw)
    assert runtime_flags.flag_enabled(entry.name) is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
def test_falsey_env_values_disable(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    entry = runtime_flags.flag("always_on_tool_catalog")
    monkeypatch.setenv(entry.env_var, raw)
    assert runtime_flags.flag_enabled(entry.name) is False


@pytest.mark.parametrize("raw", ["ture", "enabled", "maybe", " "])
def test_unrecognised_env_value_keeps_the_declared_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """A typo must not enable a migration path."""

    entry = runtime_flags.flag("always_on_tool_catalog")
    monkeypatch.setenv(entry.env_var, raw)
    assert runtime_flags.flag_enabled(entry.name) is entry.default


def test_override_beats_environment_and_restores_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = runtime_flags.flag("always_on_tool_catalog")
    monkeypatch.setenv(entry.env_var, "0")
    assert runtime_flags.flag_enabled(entry.name) is False
    with runtime_flags.override(entry.name, True):
        assert runtime_flags.flag_enabled(entry.name) is True
    assert runtime_flags.flag_enabled(entry.name) is False


def test_nested_overrides_restore_the_outer_value() -> None:
    name = "always_on_tool_catalog"
    with runtime_flags.override(name, True):
        with runtime_flags.override(name, False):
            assert runtime_flags.flag_enabled(name) is False
        assert runtime_flags.flag_enabled(name) is True


def test_override_restores_even_when_the_block_raises() -> None:
    name = "always_on_tool_catalog"
    original = runtime_flags.flag_enabled(name)
    with pytest.raises(RuntimeError):
        with runtime_flags.override(name, not original):
            raise RuntimeError("boom")
    assert runtime_flags.flag_enabled(name) is original


def test_flag_state_reports_every_declared_flag() -> None:
    state = runtime_flags.flag_state()
    assert set(state) == {entry.name for entry in runtime_flags.all_flags()}


def test_the_binder_cannot_enforce_without_its_ground_truth() -> None:
    """Enforcing while records are off would reroot answers against an empty record set.

    The binder fails open on no records, so this is not a crash — it is worse: enforcement that
    silently never fires, giving the appearance of a guarantee.
    """

    names = {e.name: e.default for e in runtime_flags.all_flags()}
    if names["answer_binder_enforce"]:
        assert names["execution_records"], "enforce is on while its ground truth is off"
