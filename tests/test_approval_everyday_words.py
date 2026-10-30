"""The approval ask says what will happen in everyday words, keyed per action.

The ask no longer reads the tool contract's developer description ("Create a directory inside
a safe local directory...", "Run one bounded shell command..."): every intent that can
REQUIRE APPROVAL has ONE keyed phrase (approval.action.<intent>) written for a person,
translated in every shipped locale, and the ask names the target by its name when it knows
one ("VOOL wants to make a new folder called Receipts."). The laws this pins:

1. Coverage: every approval-capable intent (derived from the mode-permission matrix, not
   hand-listed) has a keyed phrase; the phrase table in core/approval_action_phrases.py and
   en.json carry it byte-identical (one source, no drift).
2. Vocabulary: no person-facing approval phrase carries the contracts' developer words
   ("bounded", "workspace", "directory", "HTTP", "shell") or a dotted tool id.
3. The minted ask names the action and, where the ask knows it, the target's own name --
   folder name, file name, recipient -- never a path the person did not give, never an id;
   a tool without a keyed phrase keeps the one general sentence.
4. The ask and the payload phrase speak the page's own language (catalog keys, English on
   any failure), and the folded planned/excluded bullets read the same words -- the dotted
   p.intent ids are gone from what a person reads.

The python-level mint runs for real under the mode-permission authority (Manual mode); the
card assertions drive the REAL page under the node DOM harness (AE1 rig).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.authorized_tool_execution import execute_authorized_runtime_tool
from core.i18n.catalog import CATALOGS_DIR
from core.mode_permission_policy import (
    MODE_PERMISSION_MATRIX,
    PermissionEffect,
    actions_for_tool,
    reset_mode_permission_state,
    set_active_mode,
)
from tests.test_multichat_navigation_unlock import _drive

SID = "sess-approval-everyday"
PAGE_SOURCE = Path("core/vool_chat_page.py").read_text(encoding="utf-8")
# The shape of a raw dotted tool id (machine.ensure_directory, acme.magic_widget); the
# fragment after the first dot must be at least two characters so an "e.g." is not one.
TOOL_ID = re.compile(r"\b[a-z_]+\.[a-z_]{2,}(?:\.[a-z_]+)*\b")
# The contracts' developer vocabulary never belongs in an ask a person reads.
CONTRACT_WORDS = ("bounded", "workspace", "directory", "http", "shell")

from core.approval_action_phrases import (  # noqa: E402
    APPROVAL_ACTION_PHRASES,
    approval_action_key,
    approval_target_name,
    localized_approval_ask,
)

NEW_KEYS = sorted(
    key
    for key in json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    if key.startswith("approval.") or key == "permissions.action_general"
)


def _approval_capable_intents() -> set[str]:
    """Every builtin intent whose actions hit a REQUIRE_APPROVAL cell in any mode.

    Derived from the live matrix, the same derivation that decides real asks, so a new
    prompting tool cannot ship without a phrase.
    """
    from core.runtime_tool_contracts import runtime_tool_contracts

    capable: set[str] = set()
    argsets = ({}, {"path": "x"}, {"file_path": "x"}, {"command": "echo hi"}, {"target": "x"})
    for contract in runtime_tool_contracts():
        actions: set = set()
        for args in argsets:
            try:
                actions.update(actions_for_tool(contract.intent, args))
            except Exception:
                continue
        if any(
            MODE_PERMISSION_MATRIX[mode].get(action) is PermissionEffect.REQUIRE_APPROVAL
            for mode in MODE_PERMISSION_MATRIX
            for action in actions
        ):
            capable.add(contract.intent)
    return capable


@pytest.fixture(autouse=True)
def _clean_mode_state() -> None:
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _ask(intent: str, arguments: dict[str, object], *, locale: str = "") -> str:
    """Mint the REAL approval ask for one tool under Manual mode and return the text."""
    set_active_mode(SID, "manual", project_id="", client_turn_id="turn-everyday")
    context: dict[str, object] = {
        "runtime_session_id": SID, "operating_mode": "manual", "workspace_root": "/tmp",
    }
    if locale:
        context["ui_locale"] = locale
    result = execute_authorized_runtime_tool(
        intent, arguments, task_id="task-everyday", source_context=context
    )
    assert result.status == "pending_approval", getattr(result, "response_text", "")
    return str(result.response_text or "")


# ---- 1. coverage: every approval-capable intent has one keyed phrase, one source ----------


def test_every_approval_capable_intent_has_a_keyed_phrase() -> None:
    capable = _approval_capable_intents()
    assert len(capable) >= 100, "the capability derivation unexpectedly thin -- parser rot?"
    missing = sorted(intent for intent in capable if intent not in APPROVAL_ACTION_PHRASES)
    assert not missing, f"approval-capable tools with no keyed phrase: {missing[:5]}"
    # And the table carries nothing that can never ask (dead names nobody reads).
    never = sorted(set(APPROVAL_ACTION_PHRASES) - capable)
    assert not never, f"phrases for tools that can never ask: {never[:5]}"


def test_the_phrase_table_and_en_json_are_one_source() -> None:
    en = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    for intent, phrase in APPROVAL_ACTION_PHRASES.items():
        key = approval_action_key(intent)
        assert key and key in en, f"{intent}: missing from en.json"
        assert en[key] == phrase, f"{key}: en.json and the table drifted"
    keyed = {key for key in en if key.startswith("approval.action.")}
    assert keyed == {f"approval.action.{i}" for i in APPROVAL_ACTION_PHRASES}, "extra action keys"


def test_every_locale_carries_the_phrases_translated() -> None:
    en = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    assert len(NEW_KEYS) >= 126, NEW_KEYS
    shipped = [path for path in sorted(CATALOGS_DIR.glob("*.json")) if path.stem != "en"]
    assert len(shipped) == 16, [path.stem for path in shipped]
    for path in shipped:
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        for key in NEW_KEYS:
            assert key in messages, f"{path.stem}: {key} missing"
            assert messages[key] != en[key], f"{path.stem}: {key} copied English"


# ---- 2. vocabulary: the contracts' developer words never reach the person ------------------


@pytest.mark.parametrize("key", NEW_KEYS)
def test_no_phrase_carries_contract_vocabulary_or_an_id(key: str) -> None:
    en = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    value = en[key].lower()
    for word in CONTRACT_WORDS:
        assert word not in value, f"{key}: developer word '{word}' in person-facing text"
    assert not TOOL_ID.search(en[key]), f"{key}: dotted id in person-facing text"


def test_the_contract_description_is_no_longer_read_for_the_ask() -> None:
    """The words a person reads come from the keyed table, not the contract text: the ask
    for a folder create says folder words the contract never uses, and the derivation
    helper is gone from the module."""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    description = str(runtime_tool_contract_map()["machine.ensure_directory"].description)
    assert "directory" in description.lower()
    ask = _ask("machine.ensure_directory", {"path": "~/Desktop/Receipts"})
    assert "make a new folder called Receipts" in ask, ask
    for word in CONTRACT_WORDS:
        assert word not in ask.lower(), ask
    assert not TOOL_ID.search(ask), ask


# ---- 3. the ask names the action and the target's own name, never a path or an id ----------


@pytest.mark.parametrize(
    ("intent", "arguments", "expected_lead"),
    [
        ("machine.ensure_directory", {"path": "~/Desktop/Receipts"},
         "VOOL wants to make a new folder called Receipts."),
        ("workspace.write_file", {"path": "notes/plan.md", "content": "x"},
         "VOOL wants to save changes to a file called plan.md."),
        ("email.send", {"to": "ann@example.com", "subject": "hi", "body": "x"},
         "VOOL wants to send an email to ann@example.com."),
        ("sandbox.run_command", {"command": "python check.py"},
         "VOOL wants to run a command on this computer."),
        ("acme.magic_widget", {"deck": "q4"},
         "This request needs your approval first."),
    ],
)
def test_the_ask_says_what_will_happen(intent: str, arguments: dict[str, object], expected_lead: str) -> None:
    text = _ask(intent, arguments)
    assert text.startswith(expected_lead), text
    assert "needs your approval" in text, text
    assert intent not in text, text
    # The ask's own words carry no dotted id. The person's own names -- the file name or the
    # recipient the ask names -- and the Affected-resources detail after this point may
    # legitimately look dotted ("plan.md", "ann@example.com"); a file extension is not a tool id.
    head = text.split(" Affected: ")[0]
    own_name = approval_target_name(intent, arguments)
    if own_name:
        head = head.replace(own_name, "")
    assert not TOOL_ID.search(head), text


def test_the_target_is_never_a_path_the_person_did_not_give() -> None:
    assert approval_target_name("machine.ensure_directory", {"path": "~/Desktop/Receipts"}) == "Receipts"
    assert approval_target_name("workspace.write_file", {"path": "notes/plan.md"}) == "plan.md"
    assert approval_target_name("email.send", {"to": ["a@x.com", "b@x.com"]}) == "a@x.com"
    # No name known -> the ask simply says the action, with no dangling connector.
    ask = _ask("machine.ensure_directory", {})
    assert ask.startswith("VOOL wants to make a new folder."), ask
    assert "called" not in ask, ask


def test_a_tool_without_a_keyed_phrase_keeps_the_one_general_sentence() -> None:
    text = _ask("acme.magic_widget", {"deck": "q4"})
    assert text.startswith("This request needs your approval first."), text
    assert not TOOL_ID.search(text), text
    assert localized_approval_ask("acme.magic_widget", "en", {}) == "This request needs your approval first."


# ---- 4. the ask speaks the page's language; the payload and bullets carry the same words ----


def test_the_ask_speaks_the_pages_language() -> None:
    de = json.loads((CATALOGS_DIR / "de.json").read_text(encoding="utf-8"))["messages"]
    lead = de["approval.action.machine.ensure_directory"]
    text = _ask("machine.ensure_directory", {"path": "~/Desktop/Receipts"}, locale="de")
    assert lead and lead in text, text
    assert "make a new folder" not in text, text  # not the English phrase
    for word in CONTRACT_WORDS:
        assert word not in text.lower(), text


def _approval_request(intent: str, arguments: dict[str, object]) -> dict:
    from core.mode_permission_policy import decide_tool_call

    set_active_mode(SID, "manual", project_id="", client_turn_id="turn-everyday")
    context = {"runtime_session_id": SID, "operating_mode": "manual", "workspace_root": "/tmp"}
    decision = decide_tool_call(
        intent=intent, arguments=arguments, task_id="turn-everyday", source_context=context
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL, decision
    return dict(decision.approval_request or {})


def test_the_payload_phrase_names_the_target_like_the_ask() -> None:
    request = _approval_request("machine.ensure_directory", {"path": "~/Desktop/Receipts"})
    assert request["action_plain"] == "make a new folder called Receipts", request
    assert not TOOL_ID.search(str(request["action_plain"])), request
    # The exact `action` line keeps its id (pinned by an existing test); the plain name rides beside it.
    assert "machine.ensure_directory" in str(request["action"])


APPROVAL_BULLETS = {
    "approval_id": "appr-everyday-1",
    "task_id": "task-everyday",
    "intent": "workspace.write_file",
    "action": "Run workspace.write_file",
    "action_plain": "save changes to a file called plan.md",
    "affected_resources": ["plan.md"],
    "expected_side_effects": "one file write",
    "reversible": True,
    "scope_options": ["once"],
    "planned_actions": [
        {"intent": "workspace.write_file", "target": "notes/plan.md",
         "phrase": "save changes to a file", "target_plain": "called plan.md"},
        {"intent": "machine.ensure_directory", "target": "~/Desktop/Receipts",
         "phrase": "make a new folder", "target_plain": "called Receipts"},
    ],
    "excluded_actions": [
        {"intent": "sandbox.run_command", "target": "python check.py",
         "phrase": "", "target_plain": ""},
        {"intent": "acme.magic_widget", "target": "",
         "phrase": "", "target_plain": ""},
    ],
}


def test_the_folded_bullets_read_words_not_ids() -> None:
    data = _drive(f"""
    setDisplayedChat('{SID}');
    runTurn('save the plan and make a Receipts folder', null, {{ chatId: '{SID}' }});
    await tick();
    __streams['{SID}'].push(ev({{ type: 'permission.required', seq: 2, stage: 'Waiting for permission', summary: 'Needs approval', approval: {json.dumps(APPROVAL_BULLETS)} }}));
    __streams['{SID}'].close();
    await tick(6);
    out({{ detailText: permDetailEl ? String(permDetailEl.textContent) : '' }});
    """)
    details = data["detailText"]
    assert "save changes to a file called plan.md" in details, details
    assert "make a new folder called Receipts" in details, details
    # An excluded row whose phrase the server left empty (a command card: the exact command
    # line is the name) still resolves its keyed words here from the catalog; an intent with
    # NO keyed phrase at all reads the ONE general phrase -- never the dotted id.
    assert "run a command on this computer" in details, details
    assert "do this" in details, details
    for dotted in ("workspace.write_file", "machine.ensure_directory", "sandbox.run_command", "acme.magic_widget", "action "):
        assert dotted not in details, f"{dotted}: the bullet still renders the raw intent"


def test_the_bullet_fallback_chain_is_keyed_not_raw() -> None:
    block = PAGE_SOURCE.split("function approvalBulletWords", 1)[1].split("function showPermBar", 1)[0]
    assert "pageT('approval.action.'" in block, "old payloads resolve the phrase from the catalog"
    assert "permissions.action_general" in block, "unknown intents read the one general phrase"
    assert "p.intent || 'action'" not in PAGE_SOURCE, "no bullet renders the raw dotted intent any more"
