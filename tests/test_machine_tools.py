"""The read-only machine assistant tools (disk_usage, event_log_errors, list_processes) are wired as
model-callable tools: registered as read-only contracts, dispatched by execute_runtime_tool, and
producing structured results. These are the tools the transcript needed ("check my drives", "check
windows for errors") that VOOL was missing.
"""
from __future__ import annotations

from unittest import mock

import core.machine_diagnostics as md
from core.runtime_execution_tools import execute_runtime_tool, runtime_execution_tool_specs

_NEW_TOOLS = {"machine.disk_usage", "machine.event_log_errors", "machine.list_processes"}


def test_new_tools_registered_read_only_no_approval() -> None:
    specs = {s["intent"]: s for s in runtime_execution_tool_specs()}
    assert set(specs) >= _NEW_TOOLS, f"missing tools: {_NEW_TOOLS - set(specs)}"
    for intent in _NEW_TOOLS:
        assert specs[intent]["read_only"] is True, intent
        assert specs[intent]["side_effect_class"] == "read_only", intent
        assert specs[intent]["approval_requirement"] == "none", intent


def test_disk_usage_tool_reports_space(monkeypatch) -> None:
    monkeypatch.setattr(md, "disk_usage", lambda: [
        {"mount": "C:\\", "total_gb": 100.0, "used_gb": 75.0, "free_gb": 25.0, "percent_used": 75.0},
        {"mount": "D:\\", "total_gb": 500.0, "used_gb": 100.0, "free_gb": 400.0, "percent_used": 20.0},
    ])
    result = execute_runtime_tool("machine.disk_usage", {})
    assert result is not None and result.ok and result.status == "executed"
    assert "25.0 GB free of 100.0 GB" in result.response_text
    assert result.details["drives"][1]["mount"] == "D:\\"
    assert result.details["observation"]["total_free_gb"] == 425.0


def test_disk_scope_extracts_named_drive() -> None:
    from core.execution.constants import machine_disk_scope

    assert machine_disk_scope("how much free space is on C:?") == "C:\\"
    assert machine_disk_scope("free space on the C drive") == "C:\\"
    assert machine_disk_scope("what is on drive D") == "D:\\"
    # No specific drive named -> report all drives.
    assert machine_disk_scope("how much free space do i have?") is None
    assert machine_disk_scope("how many drives do i have?") is None


def test_disk_usage_scoped_to_one_drive_reports_that_drive_only(monkeypatch) -> None:
    monkeypatch.setattr(md, "disk_usage", lambda drives=None: (
        [{"mount": "C:\\", "total_gb": 232.2, "used_gb": 206.4, "free_gb": 25.8, "percent_used": 88.9}]
        if drives == ["C:\\"]
        else [
            {"mount": "C:\\", "total_gb": 232.2, "used_gb": 206.4, "free_gb": 25.8, "percent_used": 88.9},
            {"mount": "D:\\", "total_gb": 500.0, "used_gb": 100.0, "free_gb": 400.0, "percent_used": 20.0},
        ]
    ))
    result = execute_runtime_tool("machine.disk_usage", {"drive": "C:\\"})
    assert result is not None and result.ok and result.status == "executed"
    assert "Drive C:\\ — 25.8 GB free of 232.2 GB" in result.response_text
    # Must NOT report the other drive or an all-drive total.
    assert "D:\\" not in result.response_text
    assert "Total free" not in result.response_text
    assert "across" not in result.response_text


def test_disk_usage_tool_no_results(monkeypatch) -> None:
    monkeypatch.setattr(md, "disk_usage", lambda: [])
    result = execute_runtime_tool("machine.disk_usage", {})
    assert result is not None and result.ok is False and result.status == "no_results"


def test_event_log_tool_unsupported_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(md, "event_log_errors", lambda **kw: {"platform": "linux", "supported": False, "logs": []})
    result = execute_runtime_tool("machine.event_log_errors", {})
    assert result is not None and result.ok is False and result.status == "unsupported_platform"


def test_event_log_tool_reports_errors(monkeypatch) -> None:
    monkeypatch.setattr(md, "event_log_errors", lambda **kw: {
        "platform": "win32", "supported": True,
        "logs": [{"log": "System", "text": "Disk error on \\Device\\Harddisk0", "truncated": False}],
    })
    result = execute_runtime_tool("machine.event_log_errors", {"limit": "10"})
    assert result is not None and result.ok and "Disk error" in result.response_text


def test_list_processes_tool(monkeypatch) -> None:
    monkeypatch.setattr(md, "top_processes", lambda **kw: {
        "source": "psutil",
        "processes": [{"pid": 200, "name": "python.exe", "rss_mb": 1024.0}],
    })
    result = execute_runtime_tool("machine.list_processes", {})
    assert result is not None and result.ok and "python.exe" in result.response_text
    assert result.details["source"] == "psutil"


def test_limit_argument_is_parsed(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(md, "top_processes", lambda **kw: seen.update(kw) or {"source": "psutil", "processes": [{"pid": 1, "name": "x", "rss_mb": 1.0}]})
    execute_runtime_tool("machine.list_processes", {"limit": "7"})
    assert seen.get("limit") == 7


def test_find_folder_tool_finds_nested_matches(tmp_path, monkeypatch) -> None:
    (tmp_path / "work" / "Dropbox").mkdir(parents=True)
    (tmp_path / "misc" / "dropbox-backup").mkdir(parents=True)
    (tmp_path / "unrelated").mkdir()
    monkeypatch.setattr(md, "disk_usage", lambda: [{"mount": str(tmp_path), "total_gb": 1.0, "used_gb": 0.5, "free_gb": 0.5, "percent_used": 50.0}])
    result = execute_runtime_tool("machine.find_folder", {"name": "dropbox"})
    assert result is not None and result.ok and result.status == "executed"
    assert "Dropbox" in result.response_text
    assert "dropbox-backup" in result.response_text
    assert len(result.details["matches"]) == 2
    assert result.details["observation"]["match_count"] == 2


def test_find_folder_tool_matches_across_separators(tmp_path, monkeypatch) -> None:
    # The reported bug: "token hunter" (space) must find "token-hunter" (hyphen) — a folder plainly
    # visible in a listing read as "not found" only because the query used a different separator.
    (tmp_path / "token-hunter").mkdir()
    (tmp_path / "alpha_beta").mkdir()
    (tmp_path / "unrelated").mkdir()
    monkeypatch.setattr(md, "disk_usage", lambda: [{"mount": str(tmp_path), "total_gb": 1.0, "used_gb": 0.5, "free_gb": 0.5, "percent_used": 50.0}])
    for query in ("token hunter", "token-hunter", "token_hunter", "TOKEN HUNTER", "tokenhunter"):
        result = execute_runtime_tool("machine.find_folder", {"name": query})
        assert result is not None and result.ok, query
        assert "token-hunter" in result.response_text, f"query {query!r} should find token-hunter"
    # hyphen/underscore/space are interchangeable the other way too
    result = execute_runtime_tool("machine.find_folder", {"name": "alpha beta"})
    assert "alpha_beta" in result.response_text


def test_find_folder_tool_honest_not_found(tmp_path, monkeypatch) -> None:
    (tmp_path / "something").mkdir()
    monkeypatch.setattr(md, "disk_usage", lambda: [{"mount": str(tmp_path), "total_gb": 1.0, "used_gb": 0.5, "free_gb": 0.5, "percent_used": 50.0}])
    result = execute_runtime_tool("machine.find_folder", {"name": "dropbox"})
    assert result is not None and result.ok and result.status == "executed"
    assert "No folder matching 'dropbox'" in result.response_text
    assert result.details["matches"] == []


def test_find_folder_tool_skips_system_dirs(tmp_path, monkeypatch) -> None:
    (tmp_path / "$RECYCLE.BIN" / "dropbox").mkdir(parents=True)
    (tmp_path / "node_modules" / "dropbox").mkdir(parents=True)
    monkeypatch.setattr(md, "disk_usage", lambda: [{"mount": str(tmp_path), "total_gb": 1.0, "used_gb": 0.5, "free_gb": 0.5, "percent_used": 50.0}])
    result = execute_runtime_tool("machine.find_folder", {"name": "dropbox"})
    assert result is not None and result.ok
    assert result.details["matches"] == []


def test_find_folder_tool_requires_a_name(monkeypatch) -> None:
    monkeypatch.setattr(md, "disk_usage", lambda: [{"mount": "C:\\", "total_gb": 1.0, "used_gb": 0.5, "free_gb": 0.5, "percent_used": 50.0}])
    result = execute_runtime_tool("machine.find_folder", {})
    assert result is not None and result.ok is False and result.status == "missing_argument"


def test_folder_search_intent_routes_local_folder_requests() -> None:
    from core.execution.constants import machine_folder_search_intent

    assert machine_folder_search_intent("find me dropbox folder pls, im not sure where it is") == (
        "machine.find_folder",
        "dropbox",
    )
    assert machine_folder_search_intent("where is my Dropbox folder") == ("machine.find_folder", "dropbox")
    assert machine_folder_search_intent("find the Website V3 folder on this pc") == (
        "machine.find_folder",
        "website v3",
    )
    assert machine_folder_search_intent("locate the folder named VOOL-DELIVERY") == (
        "machine.find_folder",
        "vool-delivery",
    )


def test_folder_search_intent_rejects_non_local_and_mutations() -> None:
    from core.execution.constants import machine_folder_search_intent

    # Mutations go to the operator action lane, never to a read tool.
    assert machine_folder_search_intent("delete the dropbox folder") is None
    assert machine_folder_search_intent("clean up my downloads folder") is None
    # Explicitly remote targets are not a machine search.
    assert machine_folder_search_intent("find my dropbox folder in google drive") is None
    # Non-folder requests keep their own routing.
    assert machine_folder_search_intent("find the biggest files on C:") is None
    assert machine_folder_search_intent("how many drives do i have?") is None
    assert machine_folder_search_intent("I drive to work every day") is None


def test_biggest_files_phrase_routes_to_operator_disk_inspection() -> None:
    from core.operator.parser import parse_operator_action_intent

    intent = parse_operator_action_intent("find the biggest files on C:")
    assert intent is not None
    assert intent.kind == "inspect_disk_usage"


def test_largest_intent_routes_biggest_files_or_folders() -> None:
    from core.execution.constants import machine_largest_intent

    assert machine_largest_intent("what are the biggest 5 folders on C drive?") == "machine.find_largest"
    assert machine_largest_intent("show me the largest files on my disk") == "machine.find_largest"
    assert machine_largest_intent("top folders by size") == "machine.find_largest"
    # Phrasings a real user typed that previously fell through to "couldn't map to a real action"
    # or wrongly returned display info: bare "takes the most space" (no "up"), "top N" after the
    # subject, files/folders as the subject, and space-hog slang.
    assert machine_largest_intent("what takes the most space what folders and single files? give me top 5 pls") == "machine.find_largest"
    assert machine_largest_intent("on this machine what takes the most space what folders and single files? give me top 5 pls") == "machine.find_largest"
    assert machine_largest_intent("which folders eat the most space") == "machine.find_largest"
    assert machine_largest_intent("show me the biggest space hogs") == "machine.find_largest"
    # Mutations belong to the cleanup lane, not a read.
    assert machine_largest_intent("delete the biggest folder") is None
    assert machine_largest_intent("how many drives do i have?") is None
    # Advisory / definitional questions stay chat, not a tool.
    assert machine_largest_intent("how should i organize my folders") is None


def test_classify_delete_safety_never_flags_system_as_deletable() -> None:
    assert md.classify_delete_safety("Windows") == "system-managed (do not delete)"
    assert md.classify_delete_safety("Program Files") == "system-managed (do not delete)"
    assert md.classify_delete_safety("hiberfil.sys") == "system-managed (do not delete)"
    assert "app-managed" in md.classify_delete_safety("node_modules")
    assert "review before deleting" in md.classify_delete_safety("Documents")
    assert md.classify_delete_safety("RandomProjectDir") == "review before deleting"


class _FakeEntry:
    def __init__(self, name: str, path: str, is_dir: bool) -> None:
        self.name = name
        self.path = path
        self._is_dir = is_dir

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return self._is_dir


def test_largest_entries_measures_and_sorts(monkeypatch) -> None:
    # Size lookup is by basename so the test is filesystem-separator agnostic (the code uses
    # os.path.join, which differs on Windows vs the Linux CI runner).
    import os as _os

    entries = [
        _FakeEntry("Windows", "root/Windows", True),
        _FakeEntry("bigfile.iso", "root/bigfile.iso", False),
        _FakeEntry("SmallDir", "root/SmallDir", True),
    ]
    walk_map = {
        "root/Windows": [("root/Windows", [], ["a.dll", "b.dll"])],
        "root/SmallDir": [("root/SmallDir", [], ["s.txt"])],
    }
    sizes = {
        "a.dll": 3 * (1024 ** 3),
        "b.dll": 1 * (1024 ** 3),
        "s.txt": 5 * (1024 ** 2),
        "bigfile.iso": 6 * (1024 ** 3),
    }
    result = md.largest_entries(
        "root",
        scandir_fn=lambda root: iter(entries),
        walk_fn=lambda path: iter(walk_map.get(path, [])),
        getsize_fn=lambda p: sizes[_os.path.basename(p)],
        monotonic_fn=lambda: 0.0,
    )
    names = [e["name"] for e in result["entries"]]
    assert names[0] == "bigfile.iso"  # 6 GB
    assert names[1] == "Windows"      # 4 GB
    assert names[2] == "SmallDir"     # 0.005 GB
    assert result["entries"][1]["delete_safety"] == "system-managed (do not delete)"
    assert result["complete"] is True


def test_find_largest_tool_renders_measured_list_with_safety(monkeypatch) -> None:
    monkeypatch.setattr(md, "largest_entries", lambda root, **kw: {
        "root": root,
        "complete": True,
        "entries": [
            {"name": "bigfile.iso", "path": r"C:\bigfile.iso", "size_bytes": 6 * (1024 ** 3), "size_gb": 6.0, "kind": "file", "complete": True, "delete_safety": "review before deleting"},
            {"name": "Windows", "path": r"C:\Windows", "size_bytes": 4 * (1024 ** 3), "size_gb": 4.0, "kind": "folder", "complete": True, "delete_safety": "system-managed (do not delete)"},
        ],
    })
    with mock.patch("core.runtime_execution_tools._is_windows_platform", return_value=True):
        result = execute_runtime_tool("machine.find_largest", {"drive": "C:\\"})
    assert result is not None and result.ok and result.status == "executed"
    assert "bigfile.iso" in result.response_text and "6.0 GB" in result.response_text
    assert "system-managed (do not delete)" in result.response_text
    assert "won't delete anything without your explicit go-ahead" in result.response_text


def test_machine_diagnostics_intent_routes_drive_questions() -> None:
    from core.execution.constants import machine_diagnostics_intent

    for prompt in (
        "how many drives does my PC have",
        "how much disk space is free?",
        "list my drives",
        "show me my drives",
        "how much free space is on my drive",  # space cue + a real disk noun
    ):
        assert machine_diagnostics_intent(prompt) == "machine.disk_usage", prompt


def test_machine_diagnostics_intent_ignores_mutations_and_unrelated_text() -> None:
    from core.execution.constants import machine_diagnostics_intent

    # A cleanup/format verb (any tense) is a mutation, not a read — left to the operator action lane.
    assert machine_diagnostics_intent("clean up my disk space") is None
    assert machine_diagnostics_intent("cleaning up disk space") is None
    assert machine_diagnostics_intent("removing files to free disk space") is None
    assert machine_diagnostics_intent("delete drive D and free up space") is None
    assert machine_diagnostics_intent("format the disk") is None
    # Ambiguous space-family cues WITHOUT a storage-device noun must fall through to the model.
    assert machine_diagnostics_intent("is there any free space in my calendar this week?") is None
    assert machine_diagnostics_intent("how much storage does my iCloud plan give me?") is None
    assert machine_diagnostics_intent("we need more storage space for the team") is None
    # Unrelated prose must not be claimed by a bare word.
    assert machine_diagnostics_intent("I drive to work every day") is None
    assert machine_diagnostics_intent("write a poem about the drives of ambition") is None
    assert machine_diagnostics_intent("what's the weather today?") is None


def test_folder_search_intent_accepts_read_verbs_over_specs_hijack() -> None:
    # The reported mis-route: "check Token hunter folder on this machine and run audit" answered with
    # MACHINE SPECS — "check" wasn't a folder-search verb, and " this machine " matched the specs
    # markers. Read verbs (check/inspect/audit) must map to machine.find_folder with the right name.
    from core.execution.constants import machine_folder_search_intent as intent

    got = intent("right, can you check Token hunter folder on this machine and run audit, but only audit no changes")
    assert got == ("machine.find_folder", "token hunter")
    assert intent("inspect the dropbox folder") == ("machine.find_folder", "dropbox")
    assert intent("audit the token-hunter folder") == ("machine.find_folder", "token-hunter")
    # guards: writes/creates/remote/spec-questions still do NOT match
    assert intent("create a folder named demo") is None
    assert intent("delete the dropbox folder") is None
    assert intent("check my machine specs") is None
    assert intent("check the folder in google drive") is None
    # An expletive/filler between the verb and generic "folder" is not a folder name.
    assert intent("let him inspect the fuckign folder and see why your tooling is failing") is None
    assert intent("hey lets audit thsi folder?") is None


def test_typoed_current_folder_is_scope_reference_not_a_folder_name() -> None:
    from core.execution.constants import machine_folder_search_intent, refers_to_current_scope

    assert refers_to_current_scope("hey lets audit thsi folder?") is True
    assert refers_to_current_scope("the folder we are started this project on") is True
    assert machine_folder_search_intent("find the folder named thsi") == ("machine.find_folder", "thsi")
