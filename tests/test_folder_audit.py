"""Folder-audit fast-path: "audit/analyse my <name> folder" reads the real folder.

Regression for the reported failure where "check my Token Hunter folder ... audit why it fails"
returned only a list of matching paths (find_folder) and never read anything, so the model talked
about an audit it never ran. An analysis verb on a named folder now resolves it under the user's
home roots and answers with a grounded overview; a pure locate ("where is X") is unchanged.
"""
from core.execution.constants import (
    machine_folder_audit_intent,
    machine_folder_search_intent,
    resolve_named_folder_under_home,
)


def test_audit_intent_fires_on_analysis_verbs():
    assert machine_folder_audit_intent("audit my token hunter folder") == "token hunter"
    assert machine_folder_audit_intent("analyze the token-hunter folder") == "token-hunter"
    assert machine_folder_audit_intent("what's in my reports folder") == "reports"
    assert machine_folder_audit_intent("go through the edge lab folder") == "edge lab"
    # the exact reported message
    assert machine_folder_audit_intent(
        "check my Token Hunter folder on desktop pls - i need audit on why system is failing"
    ) == "token hunter"


def test_audit_intent_skips_locate_create_and_remote():
    assert machine_folder_audit_intent("find my dropbox folder") is None       # pure locate -> stays find_folder
    assert machine_folder_audit_intent("where is my token hunter folder") is None
    assert machine_folder_audit_intent("create a token hunter folder") is None  # write flow
    assert machine_folder_audit_intent("audit my folder in google drive") is None  # remote


def test_locate_still_routes_to_find_folder():
    # A plain "where is X" must remain a locator, not an audit.
    assert machine_folder_search_intent("where is my token hunter folder") == ("machine.find_folder", "token hunter")


def test_resolver_prefers_desktop_and_is_separator_insensitive(tmp_path, monkeypatch):
    (tmp_path / "Desktop" / "token-hunter").mkdir(parents=True)
    (tmp_path / "Documents" / "token hunter").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_named_folder_under_home("token hunter") == str(tmp_path / "Desktop" / "token-hunter")
    assert resolve_named_folder_under_home("TOKEN_HUNTER") == str(tmp_path / "Desktop" / "token-hunter")


def test_resolver_miss_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    assert resolve_named_folder_under_home("no-such-folder-xyz") is None


def test_resolver_uses_userprofile_when_home_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "Desktop" / "profile-folder").mkdir(parents=True)
    assert resolve_named_folder_under_home("profile folder") == str(tmp_path / "Desktop" / "profile-folder")
