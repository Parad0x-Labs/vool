"""Causal contract tests for Phase-A workspace-change truth hardening."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core.execution.artifacts import (
    _store_mutation_records,
    content_sha256,
    record_workspace_mutation,
)
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    list_runtime_session_events,
    reset_runtime_continuity_state,
)
from core.runtime_execution_tools import execute_runtime_tool
from core.runtime_paths import configure_runtime_home
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get
from core.workspace_change_projection import (
    BROWSER_DIFF_PREVIEW_LIMIT,
    MAX_APPROVAL_AFFECTED_RESOURCES,
    MAX_APPROVAL_SCOPE_OPTIONS,
    MAX_FILES_PER_MUTATION,
    PATH_DISPLAY_LIMIT,
    build_workspace_change_v1,
)
from storage.migrations import run_migrations


@pytest.fixture()
def change_runtime():
    with tempfile.TemporaryDirectory() as root_text, tempfile.TemporaryDirectory() as workspace_text:
        root = Path(root_text)
        db_path = root / "runtime-continuity.db"
        configure_runtime_home(root / "vool-home")
        run_migrations(db_path=db_path)
        configure_runtime_continuity_db_path(str(db_path))
        reset_runtime_continuity_state()
        try:
            yield Path(workspace_text)
        finally:
            reset_runtime_continuity_state()
            configure_runtime_continuity_db_path(None)
            configure_runtime_home(None)


def _execute(workspace: Path, session_id: str, intent: str, arguments: dict, **context):
    source_context = {
        "workspace": str(workspace),
        "session_id": session_id,
        "cancel_turn_id": context.pop("client_turn_id", ""),
        **context,
    }
    return execute_runtime_tool(intent, arguments, source_context=source_context)


def _projection(session_id: str, *, events=None) -> dict:
    if events is None:
        events = list_runtime_session_events(session_id, after_seq=0, limit=200)
    return build_workspace_change_v1(session_id=session_id, events=events)


def _record_change(workspace: Path, session_id: str, change: dict, *, intent: str = "workspace.write_file") -> dict:
    return record_workspace_mutation(
        session_id=session_id,
        workspace_root=workspace,
        intent=intent,
        changes=[change],
    )


@pytest.mark.parametrize(
    ("before_exists", "before_text", "after_exists", "after_text", "action", "expected_before_hash", "expected_after_hash", "added", "removed"),
    [
        (False, "", True, "", "created", None, content_sha256(""), 0, 0),
        (False, "", True, "hello\n", "created", None, content_sha256("hello\n"), 1, 0),
        (True, "", False, "", "deleted", content_sha256(""), None, 0, 0),
        (True, "hello\n", False, "", "deleted", content_sha256("hello\n"), None, 0, 1),
    ],
)
def test_absent_and_empty_are_distinct_states(
    change_runtime: Path,
    before_exists: bool,
    before_text: str,
    after_exists: bool,
    after_text: str,
    action: str,
    expected_before_hash: str | None,
    expected_after_hash: str | None,
    added: int,
    removed: int,
) -> None:
    session_id = f"state-{action}-{int(before_exists)}-{int(after_exists)}-{len(before_text)}"
    _record_change(
        change_runtime,
        session_id,
        {
            "path": "state.txt",
            "action": action,
            "existed_before": before_exists,
            "existed_after": after_exists,
            "before_text": before_text,
            "after_text": after_text,
        },
    )
    file_change = _projection(session_id, events=[])["changes"][0]["files"][0]
    assert file_change["before_exists"] is before_exists
    assert file_change["after_exists"] is after_exists
    assert file_change["before_hash"] == expected_before_hash
    assert file_change["after_hash"] == expected_after_hash
    assert file_change["content_changed"] is True
    assert file_change["line_delta_available"] is True
    assert (file_change["lines_added"], file_change["lines_removed"]) == (added, removed)
    if before_text == after_text:
        assert "empty file" in file_change["diff_preview"]


def test_existing_empty_to_existing_empty_is_preserved_as_a_real_noop_record(change_runtime: Path) -> None:
    session_id = "state-empty-noop"
    record = _record_change(
        change_runtime,
        session_id,
        {
            "path": "empty.txt",
            "action": "updated",
            "existed_before": True,
            "existed_after": True,
            "before_text": "",
            "after_text": "",
        },
    )
    change = _projection(session_id, events=[])["changes"][0]
    assert change["mutation_id"] == record["mutation_id"]
    file_change = change["files"][0]
    assert file_change["content_changed"] is False
    assert file_change["content_change_reason"] == "stored_text_equal"
    assert file_change["line_delta_available"] is True
    assert (file_change["lines_added"], file_change["lines_removed"]) == (0, 0)
    assert file_change["before_hash"] == file_change["after_hash"] == content_sha256("")


@pytest.mark.parametrize(
    ("before", "after"),
    [("single line", "single line\n"), ("single line\n", "single line")],
)
def test_final_newline_only_mutations_have_nonzero_line_delta_and_preview(
    change_runtime: Path,
    before: str,
    after: str,
) -> None:
    session_id = f"newline-eof-{len(before)}-{len(after)}"
    (change_runtime / "line.txt").write_text(before, encoding="utf-8")
    result = _execute(
        change_runtime,
        session_id,
        "workspace.write_file",
        {"path": "line.txt", "content": after},
    )
    assert result is not None and result.ok
    file_change = _projection(session_id)["changes"][0]["files"][0]
    assert file_change["content_changed"] is True
    assert file_change["line_delta_available"] is True
    assert (file_change["lines_added"], file_change["lines_removed"]) == (1, 1)
    assert file_change["diff_preview"]
    assert "No newline at end of file" in file_change["diff_preview"]


def test_crlf_to_lf_projects_the_byte_exact_before_state(change_runtime: Path) -> None:
    session_id = "newline-crlf-to-lf"
    (change_runtime / "line.txt").write_bytes(b"line\r\n")
    result = _execute(
        change_runtime,
        session_id,
        "workspace.write_file",
        {"path": "line.txt", "content": "line\n"},
    )
    assert result is not None and result.ok
    file_change = _projection(session_id)["changes"][0]["files"][0]
    assert file_change["content_changed"] is True
    assert file_change["before_hash"] == content_sha256("line\r\n")
    assert file_change["after_hash"] == content_sha256("line\n")
    assert file_change["line_delta_available"] is True
    assert (file_change["lines_added"], file_change["lines_removed"]) == (1, 1)
    assert file_change["diff_preview_available"] is True
    assert "-line\n\\ Line ending: CRLF\n+line" in file_change["diff_preview"]


def test_lf_to_crlf_is_projected_when_the_ledger_retains_both_terminators(change_runtime: Path) -> None:
    session_id = "newline-lf-to-crlf"
    (change_runtime / "line.txt").write_bytes(b"line\n")
    result = _execute(
        change_runtime,
        session_id,
        "workspace.write_file",
        {"path": "line.txt", "content": "line\r\n"},
    )
    assert result is not None and result.ok
    file_change = _projection(session_id)["changes"][0]["files"][0]
    assert file_change["content_changed"] is True
    assert file_change["line_delta_available"] is True
    assert (file_change["lines_added"], file_change["lines_removed"]) == (1, 1)
    assert "Line ending: CRLF" in file_change["diff_preview"]


def test_legacy_missing_content_is_unavailable_not_guessed_empty(change_runtime: Path) -> None:
    session_id = "legacy-missing-content"
    _store_mutation_records(
        session_id,
        [
            {
                "mutation_id": "mutation-legacy-missing",
                "intent": "workspace.replace_in_file",
                "created_at": "2026-08-10T00:00:00+00:00",
                "changes": [
                    {
                        "path": "legacy.txt",
                        "action": "updated",
                        "existed_before": True,
                        "existed_after": True,
                        "after_text": "now\n",
                        "before_hash": content_sha256("then\n"),
                        "after_hash": content_sha256("now\n"),
                    }
                ],
            }
        ],
    )
    change = _projection(session_id, events=[])["changes"][0]
    assert change["mutation_id"] == "mutation-legacy-missing"
    file_change = change["files"][0]
    assert file_change["before_exists"] is True
    assert file_change["before_content_available"] is False
    assert file_change["before_hash"] is None
    assert file_change["hash_semantics"] == "stored_text_sha256"
    assert file_change["content_changed"] is None
    assert file_change["line_delta_available"] is False
    assert file_change["lines_added"] is None
    assert file_change["lines_removed"] is None
    assert file_change["diff_preview"] == ""


def test_action_is_a_closed_browser_vocabulary(change_runtime: Path) -> None:
    session_id = "change-unknown-action"
    _record_change(
        change_runtime,
        session_id,
        {
            "path": "renamed.txt",
            "action": "renamed_by_magic",
            "existed_before": True,
            "existed_after": True,
            "before_text": "before",
            "after_text": "after",
        },
    )
    assert _projection(session_id, events=[])["changes"][0]["files"][0]["action"] == "unknown"


def test_large_unicode_file_preview_is_bounded_redacted_and_counts_use_complete_state(change_runtime: Path) -> None:
    session_id = "change-large-unicode"
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    content = "\n".join([f"雪-{index}-{secret if index == 0 else 'safe'}" for index in range(180)]) + "\n"
    result = _execute(
        change_runtime,
        session_id,
        "workspace.write_file",
        {"path": "src/large-雪.txt", "content": content},
        client_turn_id="turn-write-1",
    )
    assert result is not None and result.ok
    payload = _projection(session_id)
    change = payload["changes"][0]
    file_change = change["files"][0]
    assert change["client_turn_id"] == "turn-write-1"
    assert file_change["lines_added"] == 180
    assert len(file_change["diff_preview"]) <= BROWSER_DIFF_PREVIEW_LIMIT
    assert "雪" in file_change["diff_preview"]
    assert "[redacted-api-key]" in file_change["diff_preview"]
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "before_text" not in serialized
    assert "after_text" not in serialized
    assert content not in serialized
    assert secret not in serialized


def test_multiline_secrets_are_redacted_before_line_oriented_diff_rendering(change_runtime: Path) -> None:
    session_id = "change-multiline-secret-redaction"
    pem_body = "MIICWwIBAAKBgQC7PRIVATECANARY\nline-two-private-canary\nline-three-private-canary"
    split_bearer = "splitBearerCanaryABCDEF1234567890"
    split_api_key = "sk-splitApiKeyCanary1234567890"
    content = (
        "safe heading\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        f"{pem_body}\n"
        "-----END RSA PRIVATE KEY-----\n"
        "Authorization: Bearer\n"
        f"{split_bearer}\n"
        "api_key:\n"
        f"{split_api_key}\n"
    )
    _record_change(
        change_runtime,
        session_id,
        {
            "path": "secrets.txt",
            "action": "created",
            "existed_before": False,
            "existed_after": True,
            "before_text": "",
            "after_text": content,
        },
    )

    payload = _projection(session_id, events=[])
    preview = payload["changes"][0]["files"][0]["diff_preview"]
    serialized = json.dumps(payload, sort_keys=True)
    assert len(preview) <= BROWSER_DIFF_PREVIEW_LIMIT
    assert "[redacted-private-key]" in preview
    assert "Bearer [redacted]" in preview
    assert "api_key: [redacted]" in preview
    for canary in (pem_body, split_bearer, split_api_key, "BEGIN RSA PRIVATE KEY"):
        assert canary not in serialized


def test_same_prefix_long_mutation_ids_remain_distinct_and_activity_uses_exact_identity(
    change_runtime: Path,
) -> None:
    session_id = "change-exact-mutation-identity"
    common_prefix = "mutation-" + ("x" * 192)
    first_id = f"{common_prefix}A"
    second_id = f"{common_prefix}B"
    raw_change = {
        "path": "same.txt",
        "action": "created",
        "existed_before": False,
        "existed_after": True,
        "before_text": "",
        "after_text": "same\n",
        "after_hash": content_sha256("same\n"),
    }
    _store_mutation_records(
        session_id,
        [
            {"mutation_id": first_id, "intent": "workspace.write_file", "changes": [raw_change]},
            {"mutation_id": second_id, "intent": "workspace.write_file", "changes": [raw_change]},
        ],
    )
    truncated_identity_mutant = {
        "event_type": "workspace_mutation_completed",
        "ok": True,
        "rollback_id": first_id[:160],
        "tool_intent": "workspace.write_file",
        "operation_completed_at": "2026-08-10T00:00:00Z",
        "client_turn_id": "turn-poison",
        "approval_authority": "explicit_user",
        "approval_state": "approved",
        "approval_request": {"approval_id": "approval-poison"},
    }
    exact_second_event = {
        **truncated_identity_mutant,
        "rollback_id": second_id,
        "client_turn_id": "turn-second",
        "approval_request": {"approval_id": "approval-second"},
    }

    payload = build_workspace_change_v1(
        session_id=session_id,
        events=[truncated_identity_mutant, exact_second_event],
    )
    by_id = {change["mutation_id"]: change for change in payload["changes"]}
    assert set(by_id) == {first_id, second_id}
    assert all(change["mutation_id_representation"] == "exact" for change in payload["changes"])
    assert by_id[first_id]["client_turn_id"] == ""
    assert by_id[first_id]["user_approval"] is None
    assert by_id[second_id]["client_turn_id"] == "turn-second"
    assert by_id[second_id]["user_approval"]["approval_id"] == "approval-second"

    limited = build_workspace_change_v1(session_id=session_id, events=[], limit=1)
    assert limited["changes_returned"] == 1
    assert limited["changes_total"] == 2
    assert limited["changes_truncated"] is True


def test_valid_stored_hash_must_match_available_content(change_runtime: Path) -> None:
    session_id = "change-hash-evidence-conflict"
    _store_mutation_records(
        session_id,
        [
            {
                "mutation_id": "mutation-hash-conflict",
                "intent": "workspace.write_file",
                "changes": [
                    {
                        "path": "hash.txt",
                        "action": "created",
                        "existed_before": False,
                        "existed_after": True,
                        "before_text": "",
                        "after_text": "authoritative content\n",
                        "after_hash": content_sha256("different content\n"),
                    }
                ],
            }
        ],
    )

    file_change = _projection(session_id, events=[])["changes"][0]["files"][0]
    assert file_change["before_hash"] is None
    assert file_change["before_hash_status"] == "side_absent"
    assert file_change["after_hash"] is None
    assert file_change["after_hash_status"] == "stored_hash_mismatch"
    assert file_change["hash_semantics"] == "stored_text_sha256"


def test_multi_file_patch_stays_one_group_with_truthful_states(change_runtime: Path) -> None:
    session_id = "change-multi-patch"
    (change_runtime / "existing.txt").write_text("old one\nkeep\n", encoding="utf-8")
    (change_runtime / "removed.txt").write_text("gone\n", encoding="utf-8")
    patch = """--- a/existing.txt
+++ b/existing.txt
@@ -1,2 +1,3 @@
-old one
+new one
 keep
+added
--- /dev/null
+++ b/created.txt
@@ -0,0 +1,2 @@
+first
+second
--- a/removed.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""
    result = _execute(
        change_runtime,
        session_id,
        "workspace.apply_unified_diff",
        {"patch": patch},
    )
    assert result is not None and result.ok, result
    group = _projection(session_id)["changes"][0]
    assert group["mutation_id"] == group["change_group_id"]
    by_path = {item["path"]: item for item in group["files"]}
    assert set(by_path) == {"existing.txt", "created.txt", "removed.txt"}
    assert by_path["created.txt"]["before_exists"] is False
    assert by_path["created.txt"]["before_hash"] is None
    assert by_path["removed.txt"]["after_exists"] is False
    assert by_path["removed.txt"]["after_hash"] is None
    assert (by_path["existing.txt"]["lines_added"], by_path["existing.txt"]["lines_removed"]) == (2, 1)


def test_permission_allow_is_not_user_approval_and_proven_approval_is_separate(change_runtime: Path) -> None:
    session_id = "change-permission-semantics"
    result = _execute(change_runtime, session_id, "workspace.write_file", {"path": "a.txt", "content": "one\n"})
    assert result is not None and result.ok
    real_event = next(
        item
        for item in list_runtime_session_events(session_id, after_seq=0, limit=200)
        if item["event_type"] == "workspace_mutation_completed"
    )
    mutation_id = real_event["rollback_id"]

    merely_allowed = {
        **real_event,
        "approval_request": {"approval_id": "approval-unproven", "diff_preview": "+one"},
    }
    allowed_change = build_workspace_change_v1(session_id=session_id, events=[merely_allowed])["changes"][0]
    assert allowed_change["permission_decision"] == "allowed"
    assert allowed_change["permission_authority"] == "runtime_tool_policy"
    assert allowed_change["user_approval"] is None

    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    proven = {
        **real_event,
        "rollback_id": mutation_id,
        "approval_authority": "explicit_user",
        "approval_state": "approved",
        "approval_request": {
            "approval_id": "approval-proven",
            "affected_resources": ["a.txt"],
            "expected_side_effects": "writes one file",
            "scope_options": ["once", "task", "invalid"],
            "diff_preview": f"+雪 {secret}\n" + ("x" * 4000),
        },
    }
    approved = build_workspace_change_v1(session_id=session_id, events=[proven])["changes"][0]["user_approval"]
    assert approved is not None
    assert approved["status"] == "approved"
    assert approved["authority"] == "explicit_user"
    assert approved["scope_options"] == ["once", "task"]
    assert len(approved["diff_preview"]) <= BROWSER_DIFF_PREVIEW_LIMIT
    assert "雪" in approved["diff_preview"]
    assert "[redacted-api-key]" in approved["diff_preview"]
    assert secret not in approved["diff_preview"]


def test_operator_allowed_is_permission_evidence_not_explicit_user_approval(change_runtime: Path) -> None:
    session_id = "change-operator-is-not-user"
    record = _record_change(
        change_runtime,
        session_id,
        {
            "path": "operator.txt",
            "action": "created",
            "existed_before": False,
            "existed_after": True,
            "before_text": "",
            "after_text": "allowed\n",
        },
    )
    event = {
        "event_type": "workspace_mutation_completed",
        "ok": True,
        "rollback_id": record["mutation_id"],
        "permission_decision": "allowed",
        "permission_authority": "operator",
        "approval_authority": "operator",
        "approval_state": "allowed",
        "approval_request": {"approval_id": "operator-allow"},
    }

    change = build_workspace_change_v1(session_id=session_id, events=[event])["changes"][0]
    assert change["permission_decision"] == "allowed"
    assert change["permission_authority"] == "operator"
    assert change["user_approval"] is None


def test_approval_collections_are_bounded_with_explicit_completeness_counts(change_runtime: Path) -> None:
    session_id = "change-bounded-approval-collections"
    record = _record_change(
        change_runtime,
        session_id,
        {
            "path": "approval.txt",
            "action": "created",
            "existed_before": False,
            "existed_after": True,
            "before_text": "",
            "after_text": "approved\n",
        },
    )
    affected_resources = [f"resource-{index}" for index in range(100)]
    scope_options = ["once"] * 5000
    event = {
        "event_type": "workspace_mutation_completed",
        "ok": True,
        "rollback_id": record["mutation_id"],
        "approval_authority": "explicit_user",
        "approval_state": "approved",
        "approval_request": {
            "approval_id": "approval-many-options",
            "affected_resources": affected_resources,
            "scope_options": scope_options,
        },
    }

    approval = build_workspace_change_v1(session_id=session_id, events=[event])["changes"][0][
        "user_approval"
    ]
    assert approval is not None
    assert len(approval["affected_resources"]) == MAX_APPROVAL_AFFECTED_RESOURCES
    assert approval["affected_resources_returned"] == MAX_APPROVAL_AFFECTED_RESOURCES
    assert approval["affected_resources_total"] == len(affected_resources)
    assert approval["affected_resources_truncated"] is True
    assert len(approval["scope_options"]) == MAX_APPROVAL_SCOPE_OPTIONS
    assert approval["scope_options_returned"] == MAX_APPROVAL_SCOPE_OPTIONS
    assert approval["scope_options_total"] == len(scope_options)
    assert approval["scope_options_truncated"] is True


def test_file_collection_is_bounded_with_truthful_total_and_response_size(change_runtime: Path) -> None:
    session_id = "change-bounded-files"
    raw_files = [
        {
            "path": f"generated/file-{index:04d}.txt",
            "action": "created",
            "existed_before": False,
            "existed_after": True,
            "before_text": "",
            "after_text": "",
            "after_hash": content_sha256(""),
        }
        for index in range(2000)
    ]
    _store_mutation_records(
        session_id,
        [{"mutation_id": "mutation-many-files", "intent": "workspace.apply_unified_diff", "changes": raw_files}],
    )

    payload = build_workspace_change_v1(session_id=session_id, events=[], limit=1)
    change = payload["changes"][0]
    assert len(change["files"]) == MAX_FILES_PER_MUTATION
    assert change["files_returned"] == MAX_FILES_PER_MUTATION
    assert change["files_total"] == len(raw_files)
    assert change["files_truncated"] is True
    assert len(json.dumps(payload, sort_keys=True)) < 200_000


def test_long_relative_paths_use_distinct_opaque_identity_without_absolute_exposure(
    change_runtime: Path,
) -> None:
    session_id = "change-long-path-identity"
    common = "/".join(f"segment-{index:03d}" for index in range(70))
    first_path = f"{common}/first.txt"
    second_path = f"{common}/second.txt"
    assert len(first_path) > PATH_DISPLAY_LIMIT
    assert first_path[:PATH_DISPLAY_LIMIT] == second_path[:PATH_DISPLAY_LIMIT]
    raw_files = [
        {
            "path": path,
            "action": "created",
            "existed_before": False,
            "existed_after": True,
            "before_text": "",
            "after_text": "long path\n",
            "after_hash": content_sha256("long path\n"),
        }
        for path in (first_path, second_path)
    ]
    _store_mutation_records(
        session_id,
        [{"mutation_id": "mutation-long-paths", "intent": "workspace.write_file", "changes": raw_files}],
    )

    files = _projection(session_id, events=[])["changes"][0]["files"]
    assert len(files) == 2
    assert files[0]["path_id"] != files[1]["path_id"]
    for file_change, exact_path in zip(files, (first_path, second_path), strict=True):
        assert file_change["path_truncated"] is True
        assert file_change["path_length"] == len(exact_path)
        assert len(file_change["path"]) <= PATH_DISPLAY_LIMIT
        assert not file_change["path"].startswith("/")
        assert exact_path not in json.dumps(file_change, sort_keys=True)


def test_malformed_optional_metadata_is_ignored_without_crashing(change_runtime: Path) -> None:
    session_id = "change-malformed-optional"
    _store_mutation_records(
        session_id,
        [
            {
                "mutation_id": "mutation-malformed",
                "intent": "workspace.write_file",
                "changes": [
                    None,
                    17,
                    {
                        "path": "../escape.txt",
                        "action": "updated",
                        "existed_before": True,
                        "existed_after": True,
                        "before_text": "before",
                        "after_text": "after",
                    },
                    {
                        "path": "valid.txt",
                        "action": ["updated"],
                        "existed_before": True,
                        "existed_after": True,
                        "before_text": ["not", "text"],
                        "after_text": "valid\n",
                        "before_hash": {"bad": "hash"},
                        "after_hash": content_sha256("valid\n"),
                    },
                ],
            },
            {"mutation_id": "mutation-bad-changes", "changes": 42},
        ],
    )
    event = {
        "event_type": "workspace_mutation_completed",
        "ok": True,
        "rollback_id": "mutation-malformed",
        "permission_decision": ["allowed"],
        "approval_authority": "explicit_user",
        "approval_state": "approved",
        "approval_request": {
            "approval_id": "approval-malformed",
            "affected_resources": 42,
            "scope_options": {"once": True},
            "diff_preview": ["not", "text"],
        },
    }
    payload = build_workspace_change_v1(session_id=session_id, events=[None, event, 42])
    assert len(payload["changes"]) == 1
    change = payload["changes"][0]
    assert len(change["files"]) == 1
    assert change["permission_decision"] == "unknown"
    assert change["files"][0]["action"] == "unknown"
    assert change["files"][0]["line_delta_available"] is False
    approval = change["user_approval"]
    assert approval is not None
    assert approval["affected_resources"] == []
    assert approval["scope_options"] == []
    assert approval["diff_preview"] == ""


@pytest.mark.parametrize(
    "invalid_session_id",
    ["", " bad", "bad ", "bad\x00id", "a" * 161, "a/b", "a\\b", "../ledger", "a..b", "雪-session"],
)
def test_invalid_session_ids_are_rejected_before_any_storage_lookup(
    change_runtime: Path,
    invalid_session_id: str,
) -> None:
    with mock.patch("core.workspace_change_projection._load_mutation_records") as load_records, mock.patch(
        "core.web.api.service.list_recent_runtime_session_events"
    ) as load_events:
        response = dispatch_get(
            path="/api/runtime/changes",
            query={"session": [invalid_session_id]},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
        )
    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"]["code"] == "invalid_session_id"
    assert payload["session_id"] is None
    load_records.assert_not_called()
    load_events.assert_not_called()


def test_invalid_id_is_not_sanitized_into_a_valid_session_key(change_runtime: Path) -> None:
    with mock.patch("core.workspace_change_projection._load_mutation_records", return_value=[]) as load_records:
        invalid = build_workspace_change_v1(session_id="valid/id", events=[])
        valid = build_workspace_change_v1(session_id="valid-id", events=[])
    assert invalid["error"]["code"] == "invalid_session_id"
    assert valid["session_id"] == "valid-id"
    load_records.assert_called_once_with("valid-id")


def test_rollback_eligibility_is_never_overclaimed_from_session_local_history(change_runtime: Path) -> None:
    session_id = "change-rollback-posture"
    first = _execute(change_runtime, session_id, "workspace.write_file", {"path": "a.txt", "content": "one\n"})
    second = _execute(change_runtime, session_id, "workspace.write_file", {"path": "b.txt", "content": "two\n"})
    assert first is not None and first.ok
    assert second is not None and second.ok
    for change in _projection(session_id)["changes"]:
        assert change["latest_rollback_eligible"] is None
        assert change["rollback_eligibility_reason"] == "unavailable_session_ledger_cannot_prove_workspace_latest"

    rollback = _execute(change_runtime, session_id, "workspace.rollback_last_change", {})
    assert rollback is not None and rollback.ok
    rolled_back = _projection(session_id)["changes"][1]
    assert rolled_back["operation_status"] == "rolled_back"
    assert rolled_back["latest_rollback_eligible"] is False
    assert rolled_back["rollback_eligibility_reason"] == "already_rolled_back"


def test_ledger_change_survives_without_activity_but_fake_activity_and_reads_cannot_create_changes(change_runtime: Path) -> None:
    session_id = "change-ledger-authority"
    result = _execute(change_runtime, session_id, "workspace.write_file", {"path": "real.txt", "content": "real\n"})
    assert result is not None and result.ok
    assert len(_projection(session_id, events=[])["changes"]) == 1

    no_ledger_session = "change-no-ledger"
    fake_events = [
        {
            "event_type": "workspace_mutation_completed",
            "ok": True,
            "rollback_id": "mutation-fake",
            "tool_intent": "machine.move_path",
        },
        {"event_type": "tool_executed", "tool_name": "workspace.read_file", "ok": True},
    ]
    assert build_workspace_change_v1(session_id=no_ledger_session, events=fake_events)["changes"] == []


def test_unverified_is_default_and_answer_verifier_cannot_upgrade_it(change_runtime: Path) -> None:
    session_id = "change-verifier-is-not-code-proof"
    result = _execute(change_runtime, session_id, "workspace.write_file", {"path": "a.py", "content": "x = 1\n"})
    assert result is not None and result.ok
    events = list_runtime_session_events(session_id, after_seq=0, limit=200)
    events.extend(
        [
            {"event_type": "model_lane_verifier_completed", "status": "completed", "result": "PASS"},
            {"event_type": "task_envelope_completed", "task_role": "verifier", "message": "PASS"},
        ]
    )
    change = build_workspace_change_v1(session_id=session_id, events=events)["changes"][0]
    assert change["validation_status"] == "Unverified"
    assert change["validation_evidence_ids"] == []


def test_runtime_changes_endpoint_returns_hardened_contract(change_runtime: Path) -> None:
    session_id = "change-api"
    result = _execute(change_runtime, session_id, "workspace.write_file", {"path": "api.txt", "content": "hello\n"})
    assert result is not None and result.ok
    response = dispatch_get(
        path="/api/runtime/changes",
        query={"session": [session_id], "limit": ["25"]},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
    )
    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["schema"] == "workspace_change_v1"
    assert payload["session_id"] == session_id
    file_change = payload["changes"][0]["files"][0]
    assert file_change["before_exists"] is False
    assert file_change["before_hash"] is None
    assert "before_text" not in json.dumps(payload)
    assert "after_text" not in json.dumps(payload)
