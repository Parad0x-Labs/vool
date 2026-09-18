"""The derived first-run setup state (core/setup_progress.py).

Done-ness is a projection over the authorities that already own each value — never a flag the
flow sets for itself. These tests feed the projection adversarial homes: half-configured, corrupt,
legacy-shaped, fully configured, dismissed — and pin that a skipped step is never a done step.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import setup_progress
from core.web.api.runtime import RuntimeServices


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "setup-home"
    (home / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "file")
    monkeypatch.delenv("VOOL_KEYCHAIN_ALLOWED", raising=False)
    monkeypatch.delenv("VOOL_WORKSPACE_ROOT", raising=False)
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    import storage.db as sdb

    sdb.configure_default_db_path(home / "data" / "vool_web0_v2.db")
    from core import operator_profile

    operator_profile.reset_table_cache_for_tests()
    yield home
    configure_runtime_home(None)
    sdb.configure_default_db_path(None)


def _prefs_door(body: dict) -> int:
    from core.web.api.registry_authorities import set_prefs_authority

    return set_prefs_authority(body, {"Content-Type": "application/json"}, RuntimeServices(display_name="VOOL")).status


def _steps(snap: dict) -> dict:
    return {s["id"]: s for s in snap["steps"]}


def test_a_fresh_home_has_four_undone_steps_and_shows_the_entry(home):
    snap = setup_progress.snapshot()
    assert [s["id"] for s in snap["steps"]] == ["thinking", "folder", "permissions", "name"]
    assert snap["total"] == 4 and snap["done_count"] == 0
    assert all(s["done"] is False and s["skipped"] is False for s in snap["steps"])
    assert snap["complete"] is False and snap["dismissed"] is False
    assert snap["show_entry"] is True and snap["show_chat_line"] is True
    assert snap["first_undone"] == "thinking"
    for step in snap["steps"]:
        assert step["title"] and step["sentence"] and step["later"]


def test_a_project_whose_folder_was_deleted_no_longer_counts(home, tmp_path):
    from core import project_store

    folder = tmp_path / "Writing"
    folder.mkdir()
    ok, _project = project_store.create_project("Writing", str(folder))
    assert ok
    assert _steps(setup_progress.snapshot())["folder"]["done"] is True
    folder.rmdir()
    assert _steps(setup_progress.snapshot())["folder"]["done"] is False, "a registered folder that is gone is not a bound folder"


def test_an_explicit_workspace_root_counts_as_a_chosen_folder(home, tmp_path, monkeypatch):
    # VOOL_WORKSPACE_ROOT is an operator's explicit choice (no launcher sets it); the runtime
    # materialises that folder on first use, so "set" and "exists" coincide in practice.
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", "   ")
    assert _steps(setup_progress.snapshot())["folder"]["done"] is False, "whitespace is not a folder"
    real = tmp_path / "real"
    real.mkdir()
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(real))
    assert _steps(setup_progress.snapshot())["folder"]["done"] is True


def test_an_autonomy_value_without_provenance_is_not_a_choice(home):
    # A legacy-shaped preferences file: the field is present because every save writes every
    # field, not because anyone chose it. The default with no provenance must not tick the step.
    (home / "data" / "user_preferences.json").write_text(json.dumps({"autonomy_mode": "strict", "humor_percent": 5}))
    assert _steps(setup_progress.snapshot())["permissions"]["done"] is False
    # Saving an UNRELATED preference through the door still does not make autonomy chosen.
    assert _prefs_door({"humor_percent": 10}) == 200
    assert _steps(setup_progress.snapshot())["permissions"]["done"] is False
    # Choosing it through the one write door does — including choosing the default explicitly.
    assert _prefs_door({"autonomy_mode": "hands_off"}) == 200
    assert _steps(setup_progress.snapshot())["permissions"]["done"] is True


def test_the_chat_preference_command_also_counts_as_a_choice(home):
    from core.user_preferences import maybe_handle_preference_command

    handled, _reply = maybe_handle_preference_command("set autonomy strict")
    assert handled is True
    assert _steps(setup_progress.snapshot())["permissions"]["done"] is True


def test_a_corrupt_preferences_file_yields_defaults_not_a_crash(home):
    (home / "data" / "user_preferences.json").write_text("{not json")
    snap = setup_progress.snapshot()
    assert snap["dismissed"] is False
    assert _steps(snap)["permissions"]["done"] is False
    assert all(s["skipped"] is False for s in snap["steps"])


def test_skipped_is_never_done_and_resume_passes_over_skipped_steps(home):
    assert _prefs_door({"setup_skipped_steps": "thinking, folder,bogus,thinking"}) == 200
    snap = setup_progress.snapshot()
    steps = _steps(snap)
    assert steps["thinking"]["skipped"] is True and steps["thinking"]["done"] is False
    assert steps["folder"]["skipped"] is True and steps["folder"]["done"] is False
    assert steps["permissions"]["skipped"] is False
    assert snap["done_count"] == 0 and snap["complete"] is False
    assert snap["skipped"] == ["thinking", "folder"], "unknown ids are dropped, duplicates collapse, order is the step order"
    assert snap["first_undone"] == "permissions", "resuming does not nag about a step the user skipped"
    assert _prefs_door({"setup_skipped_steps": "thinking,folder,permissions,name"}) == 200
    snap = setup_progress.snapshot()
    assert snap["complete"] is False and snap["show_entry"] is True
    assert snap["first_undone"] == "thinking", "when everything left is skipped, resume from the first undone step"


def test_everything_done_hides_the_entry_and_the_chat_line(home, tmp_path):
    from core import first_run, operator_profile, project_store

    first_run.choose("local_only")
    folder = tmp_path / "Docs"
    folder.mkdir()
    assert project_store.create_project("Docs", str(folder))[0]
    assert _prefs_door({"autonomy_mode": "balanced"}) == 200
    change = operator_profile.remember(operator_profile.OWNER_PRINCIPAL, "preferred_name", "Sam", origin="explicit", actor="operator", replace=True)
    assert change.persisted, change.report
    snap = setup_progress.snapshot()
    assert snap["done_count"] == 4 and snap["complete"] is True
    assert snap["show_entry"] is False and snap["show_chat_line"] is False
    assert snap["first_undone"] is None


def test_dismissed_hides_the_entry_but_keeps_reporting_the_truth(home):
    assert _prefs_door({"setup_dismissed": True}) == 200
    snap = setup_progress.snapshot()
    assert snap["dismissed"] is True and snap["show_entry"] is False and snap["show_chat_line"] is False
    assert snap["complete"] is False and snap["done_count"] == 0
    assert _prefs_door({"setup_dismissed": False}) == 200
    assert setup_progress.snapshot()["show_entry"] is True


def test_a_stored_cloud_key_name_completes_thinking_without_touching_the_keychain(home, monkeypatch):
    from core import credential_store

    def _boom(*_a, **_k):
        raise AssertionError("the setup projection must never reach the Keychain")

    monkeypatch.setattr(credential_store, "_reconcile_index_with_keychain", _boom)
    monkeypatch.setattr(credential_store, "has_credential", _boom)
    monkeypatch.setattr(credential_store, "list_credentials", _boom)
    meta = home / "data" / "credentials.meta.json"
    meta.write_text(json.dumps({"search.web.brave": {"label": "Brave", "created": "2026-09-07T00:00:00Z"}}))
    assert _steps(setup_progress.snapshot())["thinking"]["done"] is False, "a web-search key is not a brain"
    meta.write_text(json.dumps({"llm.cloud.openrouter": {"label": "OpenRouter", "created": "2026-09-07T00:00:00Z"}}))
    assert _steps(setup_progress.snapshot())["thinking"]["done"] is True


def test_the_provider_choice_completes_thinking_and_skip_does_not(home):
    from core import first_run

    first_run.choose("skip")
    assert _steps(setup_progress.snapshot())["thinking"]["done"] is False, "skipping the provider card is not a decision"
    first_run.reset()
    first_run.choose("local_only")
    assert _steps(setup_progress.snapshot())["thinking"]["done"] is True


def test_an_existing_user_gets_no_chat_line(home):
    from core import first_run_pact

    (home / "data" / first_run_pact.FILENAME).write_text(json.dumps({
        "schema_name": first_run_pact.SCHEMA_NAME, "schema_version": 1, "state": "not_applicable",
        "revision": 0, "welcome_hidden": False, "seed_reason": "existing_user", "steps": {},
    }))
    snap = setup_progress.snapshot()
    assert snap["show_entry"] is True, "Settings still offers the entry to an existing user"
    assert snap["show_chat_line"] is False, "the chat line is for a fresh profile only"


def test_a_failing_predicate_reports_unverified_instead_of_breaking_the_page(home, monkeypatch):
    from core import project_store

    def _boom():
        raise RuntimeError("projects store unreadable")

    monkeypatch.setattr(project_store, "list_projects", _boom)
    snap = setup_progress.snapshot()
    step = _steps(snap)["folder"]
    assert step["done"] is False
    assert step["check_failed"] == "RuntimeError"
    assert snap["total"] == 4


def test_the_copy_is_short_and_free_of_jargon():
    jargon = ("llm", "inference", "api", "token", "endpoint", "credential", "provider", "runtime", "keychain", "json", "model")
    for spec in setup_progress.STEPS:
        assert len(spec.title.split()) <= 5, spec.title
        assert len(spec.sentence.split()) <= 18, spec.sentence
        low = (spec.title + " " + spec.sentence).lower()
        for word in jargon:
            assert word not in low.split() and f"{word} " not in low, (spec.id, word)
        assert spec.later, spec.id
