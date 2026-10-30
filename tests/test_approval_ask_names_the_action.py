"""The approval ask names the action in words a person reads -- never a raw dotted tool id.

The ask used to lead with the tool's id ("machine.ensure_directory needs your approval before
it runs"), and the approval card's folded details carried bookkeeping ids alongside it. The
laws this pins:

1. The transcript mint (core.authorized_tool_execution) derives its first sentence from the
   tool's OWN contract description, so it stays true to what the tool does, and a tool with no
   builtin contract (a plugin, an MCP tool, anything unknown) gets the one general sentence --
   never its id, never an invented name. The rule asserted is the SHAPE (no dotted tool id
   where the person reads, plain words present), not one canned sentence.
2. The card's folded details render no task id (the browser stopped rendering it), and the
   Activity panel never names a chat by a slice of its session id -- an unknown chat is
   "Another window" in words.
3. The interrupted-recovery note and its button read through catalog keys like their sibling
   branches, and the unresolved-approval toast is one keyed sentence without the raw error.

The python-level mint runs for real under the mode-permission authority (Manual mode); the
card assertions drive the REAL page under the node DOM harness with only the network replaced,
the same rig as tests/test_manual_mode_approval_is_actionable.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.i18n.catalog import CATALOGS_DIR
from core.mode_permission_policy import reset_mode_permission_state, set_active_mode
from tests.test_multichat_navigation_unlock import _drive

SID = "sess-approval-words"
PAGE_SOURCE = Path("core/vool_chat_page.py").read_text(encoding="utf-8")
# The shape of a raw dotted tool id (machine.ensure_directory, acme.magic_widget). The rule is
# that this shape never appears in what a person reads; the fragment after the first dot must
# be at least two characters so an "e.g." inside a sentence is not mistaken for an id.
TOOL_ID = re.compile(r"\b[a-z_]+\.[a-z_]{2,}(?:\.[a-z_]+)*\b")

NEW_KEYS = (
    "permissions.activity_chat_fallback",
    "permissions.recovery_interrupted",
    "permissions.recovery_resume_or_cancel",
    "permissions.resolve_failed",
)


@pytest.fixture(autouse=True)
def _clean_mode_state() -> None:
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _ask(intent: str, arguments: dict[str, object]) -> str:
    """Mint the REAL approval ask for one tool under Manual mode and return the text."""
    set_active_mode(SID, "manual", project_id="", client_turn_id="turn-words")
    context = {"runtime_session_id": SID, "operating_mode": "manual", "workspace_root": "/tmp"}
    result = execute_authorized_runtime_tool(intent, arguments, task_id="task-77", source_context=context)
    assert result.status == "pending_approval", getattr(result, "response_text", "")
    return str(result.response_text or "")


# ---- 1. the ask a person reads never leads with (or carries) a dotted tool id --------------------


@pytest.mark.parametrize(
    ("intent", "arguments", "plain_word"),
    [
        ("machine.ensure_directory", {"path": "~/Desktop/Reports"}, "directory"),  # a folder
        ("machine.write_file", {"path": "~/Documents/notes", "content": "x"}, "file"),  # a file write
        ("acme.magic_widget", {"deck": "q4"}, ""),  # no contract at all (plugin/MCP/unknown)
    ],
)
def test_the_approval_ask_carries_no_dotted_tool_id(intent: str, arguments: dict[str, object], plain_word: str) -> None:
    text = _ask(intent, arguments)
    assert intent not in text, "the tool's own id must never be what the person reads"
    assert not TOOL_ID.search(text), text
    # It still asks, in words, and the first sentence names the action with words, not an id.
    assert "needs your approval" in text, text
    first = text.split(".")[0].strip()
    assert len(first.split()) >= 3, first
    if plain_word:
        assert plain_word in first.lower(), first


def test_a_tool_without_a_contract_gets_the_one_general_sentence() -> None:
    text = _ask("acme.magic_widget", {"deck": "q4"})
    assert text.startswith("This request needs your approval first."), text
    assert not TOOL_ID.search(text), text


def test_every_builtin_tool_derives_a_phrase_with_no_id_in_it() -> None:
    """The derivation is general, not a hand-picked table: every registered contract must
    yield plain words, and no contract's phrase may carry the id shape."""
    from core.runtime_tool_contracts import plain_action_phrase, runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    assert len(contracts) > 50, "the builtin catalogue unexpectedly small -- parser rot?"
    empty = [intent for intent in contracts if not plain_action_phrase(intent)]
    assert not empty, f"tools that would fall to the general sentence despite a contract: {empty[:5]}"
    with_id = [intent for intent in contracts if TOOL_ID.search(plain_action_phrase(intent))]
    assert not with_id, f"phrases carrying a dotted id: {[(i, plain_action_phrase(i)) for i in with_id[:5]]}"


# ---- 2. the card's folded details carry no task id; activity never names a chat by an id slice ----


APPROVAL = {
    "approval_id": "appr-words-1",
    "task_id": "task-77",
    "intent": "machine.ensure_directory",
    "action": "Create a directory inside a safe local directory",
    "affected_resources": ["~/Desktop/vool_test"],
    "expected_side_effects": "one new empty directory",
    "reversible": True,
    "scope_options": ["once"],
}


def test_the_cards_folded_details_carry_no_task_id_and_no_tool_id() -> None:
    data = _drive(f"""
    setDisplayedChat('{SID}');
    runTurn('can you create a folder on the desktop for me? name it vool_test', null, {{ chatId: '{SID}' }});
    await tick();
    __streams['{SID}'].push(ev({{ type: 'permission.required', seq: 2, stage: 'Waiting for permission', summary: 'Needs approval', approval: {json.dumps(APPROVAL)} }}));
    __streams['{SID}'].push(chunk('This needs your approval first.'));
    __streams['{SID}'].close();
    await tick(6);
    out({{
      pending: !!chatState('{SID}').pendingApproval,
      barText: permMsgEl ? String(permMsgEl.textContent) : '',
      detailText: permDetailEl ? String(permDetailEl.textContent) : '',
    }});
    """)
    assert data["pending"] is True, data
    assert data["barText"] == APPROVAL["action"], data
    details = data["detailText"]
    assert "task-77" not in details and "Task:" not in details, details
    assert not TOOL_ID.search(details), details
    assert "Tool:" in details and "directory" in details, details


def test_activity_never_names_a_chat_by_a_slice_of_its_session_id() -> None:
    data = _drive(f"""
    setDisplayedChat('{SID}');
    panelScope = 'all';
    activityHistory = [{{ session_id: 'openclaw:frag-4d2e9c31', execution_history: {{ request_preview: 'Draft the quarterly summary', status: 'completed' }} }}];
    _lastSessions = [];
    out({{ html: String(renderActivityHistoryHtml()) }});
    """)
    # What the person reads is the rendered text, not the row's data attributes.
    visible = re.sub(r'data-[a-z-]+="[^"]*"', "", data["html"])
    assert "4d2e9c31" not in visible and "frag-" not in visible, visible
    assert "Another window" in visible, visible


# ---- 3. the recovery note and the toast read through catalog keys ---------------------------------


def test_the_interrupted_recovery_note_and_button_read_from_the_catalog() -> None:
    data = _drive(f"""
    setDisplayedChat('{SID}');
    const st = chatState('{SID}');
    st.recoveredTask = {{ session_id: '{SID}', resume_available: true, execution_history: {{ status: 'interrupted', request_preview: 'make a poster' }} }};
    paintRecoveryNote(st);
    const note = logEl.querySelector('.recovery-note');
    out({{
      text: note ? String(note.textContent) : '',
      buttonText: (note && note.querySelector('button')) ? String(note.querySelector('button').textContent) : '',
    }});
    """)
    en = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    assert en["permissions.recovery_interrupted"] in data["text"], data
    assert data["buttonText"] == en["permissions.recovery_resume_or_cancel"], data


def test_the_unresolved_approval_toast_is_one_keyed_sentence_without_the_raw_error() -> None:
    # The raw error goes to the console for diagnosis; the person reads one plain sentence.
    resolver = PAGE_SOURCE.split("async function resolvePermission", 1)[1].split("\nif (permBarEl)", 1)[0]
    assert "resolve_failed_prefix" not in resolver
    assert "pageT('permissions.resolve_failed'," in resolver
    assert "+ e.message)" not in resolver


def test_every_locale_carries_the_new_permissions_keys_translated() -> None:
    en_path = CATALOGS_DIR / "en.json"
    en = json.loads(en_path.read_text(encoding="utf-8"))
    assert "source_catalog_sha256" not in en, "the source catalog must not carry a stamp"
    for key in NEW_KEYS:
        assert key in en["messages"], key
    shipped = [path for path in sorted(CATALOGS_DIR.glob("*.json")) if path.stem != "en"]
    assert len(shipped) == 16, [path.stem for path in shipped]
    for path in shipped:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in NEW_KEYS:
            assert key in data["messages"], f"{path.stem}: {key}"
            assert data["messages"][key] != en["messages"][key], f"{path.stem}: {key} copied English"


# ---- 5. the payload carries the plain name beside the exact id-bearing action line ----------------
#
# The exact `action` line still carries the dotted id (an existing test pins that wording, so it
# stays until its owner okays the edit). The payload also carries the plain name of the same
# action, and the page reads THAT first -- what the person reads carries no id.


def _approval_request(intent: str, arguments: dict[str, object]) -> dict:
    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    set_active_mode(SID, "manual", project_id="", client_turn_id="turn-plain")
    context = {"runtime_session_id": SID, "operating_mode": "manual", "workspace_root": "/tmp"}
    decision = decide_tool_call(
        intent=intent, arguments=arguments, task_id="turn-plain", source_context=context
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, decision
    return dict(decision.approval_request or {})


def test_the_approval_payload_carries_a_plain_name_for_the_page() -> None:
    request = _approval_request("machine.ensure_directory", {"path": "~/Desktop/Reports"})
    # The exact line keeps its id (pinned by an existing test); the plain name rides beside it.
    assert "machine.ensure_directory" in str(request.get("action") or "")
    plain = str(request.get("action_plain") or "")
    assert plain and not TOOL_ID.search(plain), request
    assert "directory" in plain.lower(), plain


def test_a_command_approval_has_no_second_name_and_an_unknown_tool_keeps_it_empty() -> None:
    command = _approval_request("sandbox.run_command", {"command": "python check.py"})
    assert command.get("command") == "python check.py", command
    assert command.get("action_plain") == "", command  # the exact command line IS the name
    unknown = _approval_request("acme.magic_widget", {"deck": "q4"})
    assert unknown.get("action_plain") == "", unknown  # no invented names for tools without a contract


def test_the_page_prefers_the_plain_name_where_the_person_reads() -> None:
    assert "a.action_plain || a.action) || ev.summary" in PAGE_SOURCE, "permission bar main line"
    detail = PAGE_SOURCE.split("const detailBits = [];", 1)[1].split("permDetailEl.textContent", 1)[0]
    assert "(a.action_plain || a.action))" in detail, "folded details tool line"
