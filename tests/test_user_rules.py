"""User rules: saved from plain words, scoped, kept across restarts, obeyed in replies and refused
at every git door that creates or publishes history.

Every git check here runs against a REAL repository (and, for pushes, a real local bare remote),
through the same doors production uses: the command gate a shell `git` crosses, and the RepoOps
production tool door (`execute_runtime_tool`). Nothing is mocked.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from core import user_rules as ur

GLOBAL_GIT_RULE = "add this to global rules: never push the words xxx or ffff to git"
CHAT_RULE = "add to chat rules: never fff and ggg"


@pytest.fixture
def home(tmp_path):
    """A private runtime home: the rules store, chat namespaces and projects live here only.

    The root conftest pins BOTH the runtime home and the database path for the session; moving
    only the home would leave every test writing rules into the one shared database.
    """
    from core import runtime_paths
    from storage import db as storage_db

    old_env = os.environ.get("VOOL_HOME")
    old_override = runtime_paths._VOOL_HOME_OVERRIDE
    old_db = storage_db._DEFAULT_DB_PATH_OVERRIDE
    runtime = tmp_path / "home"
    (runtime / "data").mkdir(parents=True)
    os.environ["VOOL_HOME"] = str(runtime)
    runtime_paths.configure_runtime_home(runtime)
    storage_db.configure_default_db_path(runtime / "data" / "vool_web0_v2.db")
    try:
        yield runtime
    finally:
        storage_db._DEFAULT_DB_PATH_OVERRIDE = old_db
        runtime_paths.configure_runtime_home(old_override)
        if old_env is None:
            os.environ.pop("VOOL_HOME", None)
        else:
            os.environ["VOOL_HOME"] = old_env


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fx@local")
    _git(root, "config", "user.name", "fx")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _say(text: str, chat: str, **kw):
    handled = ur.handle_rule_command(text, chat_id=chat, **kw)
    assert handled is not None, f"not read as a rules command: {text!r}"
    return handled


# ── saving, scope, persistence, removal ─────────────────────────────────────


def test_plain_words_save_a_global_rule_that_every_chat_sees(home):
    reply, changed, kind = _say(GLOBAL_GIT_RULE, "chat-a")
    assert kind == "saved"
    assert changed[0].scope == "global"
    assert changed[0].terms == ("xxx", "ffff")
    assert changed[0].targets == (ur.TARGET_GIT,)
    assert "Saved rule" in reply and "xxx, ffff" in reply

    listing, _, kind = _say("what are my rules?", "a-chat-that-did-not-exist-before")
    assert kind == "listed"
    assert "never push the words xxx or ffff to git" in listing


def test_rules_survive_a_restart(home):
    _say(GLOBAL_GIT_RULE, "chat-a")
    _say(CHAT_RULE, "chat-a")
    # A restart is a new process reading the same store: the rules come from SQLite, not memory.
    out = subprocess.run(
        [
            os.sys.executable,
            "-c",
            "from core.user_rules import active_rules;"
            "print('|'.join(r.scope + ':' + r.text for r in active_rules(chat_id='chat-a')))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "VOOL_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "global:never push the words xxx or ffff to git|chat:never fff and ggg"


def test_a_chat_rule_binds_only_its_own_chat(home):
    _say(CHAT_RULE, "chat-a")
    masked, hits = ur.mask_reply_terms("Sure: fff and ggg.", chat_id="chat-a")
    assert "fff" not in masked and "ggg" not in masked and len(hits) == 2
    other, other_hits = ur.mask_reply_terms("Sure: fff and ggg.", chat_id="chat-b")
    assert other == "Sure: fff and ggg." and other_hits == []


def test_a_git_only_rule_does_not_touch_replies(home):
    _say(GLOBAL_GIT_RULE, "chat-a")
    text = "The commit would say xxx."
    assert ur.mask_reply_terms(text, chat_id="chat-a") == (text, [])


def test_a_project_rule_binds_every_chat_in_the_project_and_no_other(home, tmp_path):
    from core import project_store
    from core.context_namespace import ensure_chat_namespace

    folder = tmp_path / "orion"
    folder.mkdir()
    ok, project = project_store.create_project("Orion", str(folder))
    assert ok, project
    pid = str(project["id"])
    ensure_chat_namespace("chat-in-1", project_id=pid)
    ensure_chat_namespace("chat-in-2", project_id=pid)

    _, changed, kind = _say("add a project rule: never say 'Orion'", "chat-in-1")
    assert kind == "saved" and changed[0].scope == "project" and changed[0].scope_ref == pid

    assert ur.mask_reply_terms("Project Orion", chat_id="chat-in-2")[1]
    assert ur.mask_reply_terms("Project Orion", chat_id="outside")[1] == []
    # And it binds git work inside the project's folder from any chat.
    assert ur.project_ids_for_path(folder / "src") == [pid]


def test_a_project_rule_in_a_chat_with_no_project_saves_nothing(home):
    reply, changed, kind = _say("add a project rule: never say zzz", "loose-chat")
    assert kind == "refused" and not changed
    assert ur.active_rules(chat_id="loose-chat") == []
    assert "Nothing was saved" in reply


def test_global_and_chat_bans_add_up_and_removal_lifts_only_that_rule(home):
    _say(GLOBAL_GIT_RULE, "chat-a")
    _say(CHAT_RULE, "chat-a")
    git_words = {t for r in ur.active_rules(chat_id="chat-a") if r.applies_to(ur.TARGET_GIT) for t in r.terms}
    assert git_words == {"xxx", "ffff", "fff", "ggg"}

    reply, removed, kind = _say("forget the rule about ffff", "chat-a")
    assert kind == "removed" and removed[0].scope == "global"
    remaining = ur.active_rules(chat_id="chat-a")
    assert [r.text for r in remaining] == ["never fff and ggg"]
    # Removal is a tombstone, never a silent delete.
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT status, removed_at FROM user_rules WHERE rule_no = ?", (removed[0].rule_no,)).fetchone()
    finally:
        conn.close()
    assert row[0] == "removed" and row[1]


def test_removal_that_names_nothing_or_two_rules_removes_nothing(home):
    _say("add global rule: never say 'alpha'", "c")
    _say("add chat rule: never say 'alpha' or 'beta'", "c")
    _, removed, kind = _say("remove rule 99", "c")
    assert kind == "refused" and not removed
    reply, removed, kind = _say("remove the rule about alpha", "c")
    assert kind == "refused" and not removed and "More than one rule" in reply
    assert len(ur.active_rules(chat_id="c")) == 2


def test_secrets_and_remote_principals_cannot_write_rules(home):
    # Built at run time so no key-shaped literal sits in the source.
    fake_key = "sk-" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
    _, _, kind = _say(f"add global rule: never print {fake_key}", "c")
    assert kind == "refused"
    _, _, kind = _say("add global rule: never say qqq", "c", owner_local=False)
    assert kind == "refused"
    assert ur.active_rules(chat_id="c") == []


def test_a_rules_command_leaves_no_second_unremovable_copy_in_the_chat_mission(home):
    from core.active_mission import capture_active_mission_slots, current_active_mission_slots

    # The same input path runs mission capture on every turn. A refused project rule must leave
    # nothing behind, and a saved chat rule lives only in the rules store, where removal reaches it.
    assert capture_active_mission_slots("loose-chat", "add a project rule: never say zzz") == 0
    assert capture_active_mission_slots("chat-a", CHAT_RULE) == 0
    assert current_active_mission_slots("loose-chat") == []
    assert current_active_mission_slots("chat-a") == []
    # An ordinary in-chat instruction is still the mission lane's to keep.
    assert capture_active_mission_slots("chat-a", "do not mention qqq") == 1


def test_a_rule_whose_text_lost_a_carved_out_secret_is_not_saved(home):
    # The secret intake removes a pasted key before any lane sees the turn; what is left is not
    # what the user typed, so it must not become a rule.
    reply, changed, kind = _say("add global rule: never print", "c", secret_carved=True)
    assert kind == "refused" and not changed and "Nothing was saved" in reply
    _, changed, kind = _say("add global rule: never print [redacted-api-key]", "c")
    assert kind == "refused" and not changed
    assert ur.active_rules(chat_id="c") == []


def test_the_rules_markers_cannot_be_written_by_a_caller():
    from core.request_trust import RESERVED_TRUST_KEYS

    assert ur.RULES_TURN_MARKER in RESERVED_TRUST_KEYS
    assert "_secret_intake_consumed" in RESERVED_TRUST_KEYS


def test_ordinary_messages_are_not_rule_commands():
    for text in (
        "put it in the rules folder please",
        "how do rules engines work in Drools?",
        "create a skill for this chat that always follows rule 2",
        "never mind",
    ):
        assert ur.parse_rule_command(text) is None, text


def test_the_prompt_block_is_byte_stable_until_a_rule_changes(home):
    _say(GLOBAL_GIT_RULE, "chat-a")
    _say(CHAT_RULE, "chat-a")
    first = ur.rules_prompt_block(chat_id="chat-a")
    assert first == ur.rules_prompt_block(chat_id="chat-a")
    assert "never fff and ggg" in first and "xxx" in first
    _say("remove rule 2", "chat-a")
    assert ur.rules_prompt_block(chat_id="chat-a") != first
    assert ur.rules_prompt_block(chat_id="no-rules-chat") == ur.rules_prompt_block(chat_id="other-no-rules-chat")


# ── git: the shell command gate ─────────────────────────────────────────────


def test_the_command_gate_refuses_a_banned_commit_before_any_approval(home, tmp_path):
    from core.execution_gate import ExecutionGate
    from core.remote_fetch_policy import remote_fetch_policy_scope

    root = _repo(tmp_path)
    _say(GLOBAL_GIT_RULE, "chat-a")
    with remote_fetch_policy_scope({"surface": "openclaw", "session_id": "chat-a"}):
        refused = ExecutionGate.evaluate_command('git commit -am "add xxx support"', cwd=str(root))
        clean = ExecutionGate.evaluate_command('git commit -am "add support"', cwd=str(root))
    assert refused["decision"] == "blocked" and "rule 1" in refused["reason"]
    # The clean commit still meets the ordinary approval gate: no rule grants anything.
    assert clean["decision"] == "advice_only"


def test_the_command_gate_reads_the_staged_change_not_just_the_message(home, tmp_path):
    from core.execution_gate import ExecutionGate
    from core.remote_fetch_policy import remote_fetch_policy_scope

    root = _repo(tmp_path)
    (root / "README.md").write_text("hello ffff\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _say(GLOBAL_GIT_RULE, "chat-a")
    with remote_fetch_policy_scope({"surface": "openclaw", "session_id": "chat-a"}):
        refused = ExecutionGate.evaluate_command('git commit -m "update readme"', cwd=str(root))
    assert refused["decision"] == "blocked" and "ffff" in refused["reason"]


def test_a_chat_rule_does_not_refuse_git_in_another_chat(home, tmp_path):
    from core.execution_gate import ExecutionGate
    from core.remote_fetch_policy import remote_fetch_policy_scope

    root = _repo(tmp_path)
    _say(CHAT_RULE, "chat-a")
    with remote_fetch_policy_scope({"surface": "openclaw", "session_id": "chat-b"}):
        other = ExecutionGate.evaluate_command('git commit -m "fff"', cwd=str(root))
    with remote_fetch_policy_scope({"surface": "openclaw", "session_id": "chat-a"}):
        own = ExecutionGate.evaluate_command('git commit -m "fff"', cwd=str(root))
    assert other["decision"] == "advice_only"
    assert own["decision"] == "blocked"


def test_an_unreadable_push_range_refuses_instead_of_passing(home, tmp_path):
    root = _repo(tmp_path)
    _say(GLOBAL_GIT_RULE, "chat-a")
    refusal = ur.git_outbound_refusal(root, "push", rev_range=("0" * 40, "HEAD"), chat_id="chat-a")
    assert refusal is not None and refusal.incomplete


# ── git: the RepoOps production tool door, real repo and real bare remote ────


@pytest.fixture
def repo_world(home, tmp_path, monkeypatch):
    from core import policy_engine
    from core.mode_permission_policy import reset_mode_permission_state
    from core.repoops import forge as forge_bridge
    from core.repoops.plane import repo_ops_runtime
    from tests.repoops._forge_fixture import RecordedForge
    from tests.repoops._harness import build_repo

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path / "repo_sessions"))
    repo_ops_runtime().reset()
    root, bare = build_repo(tmp_path)
    forge = RecordedForge()
    forge_bridge.install_transport_factory(forge.factory)
    original_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda key, default=None: True if key == "repo.real_push_enabled" else original_get(key, default),
    )
    try:
        yield root, bare
    finally:
        forge_bridge.install_transport_factory(None)
        repo_ops_runtime().reset()
        reset_mode_permission_state()


def _open_bound_session(root: Path, chat: str):
    from tests.repoops._harness import context, door

    ctx = context(root, session=chat)
    sid = door(
        "repo.session.open",
        {"objective": "rules", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"},
        ctx,
    ).details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    return sid, ctx


def test_repoops_commit_and_tag_carrying_a_banned_word_are_refused(repo_world):
    from tests.repoops._harness import door, head

    root, _bare = repo_world
    _say(GLOBAL_GIT_RULE, "chat-a")
    sid, ctx = _open_bound_session(root, "chat-a")
    before = head(root)

    (root / "calc.py").write_text("def add(a, b):\n    return a + b  # xxx\n", encoding="utf-8")
    refused = door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "fix add"}, ctx)
    assert refused.ok is False and refused.status == "refused_by_user_rule"
    assert head(root) == before

    tagged = door("repo.git", {"repo_session_id": sid, "operation": "tag", "name": "ffff-1", "message": "cut"}, ctx)
    assert tagged.ok is False and tagged.status == "refused_by_user_rule"

    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    clean = door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "fix add"}, ctx)
    assert clean.ok is True and head(root) != before


def test_repoops_push_of_a_banned_commit_is_refused_before_consent_and_runs_after_removal(repo_world):
    from tests.repoops._harness import FIXED, context, door, git, head, remote_ref

    root, bare = repo_world
    (root / "NOTES.md").write_text("the ffff plan\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "notes")
    _say(GLOBAL_GIT_RULE, "chat-a")
    sid, ctx = _open_bound_session(root, "chat-a")
    # The vertical's own stage order: a push is planned only after a diagnosed, tested, reviewed repair.
    door("repo.step", {"repo_session_id": sid, "step_id": "red", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.diagnose", {"repo_session_id": sid, "path": "calc.py", "reason": "add subtracts", "evidence_step_id": "red"}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "fix", "intent": "workspace.write_file",
                       "arguments": {"path": "calc.py", "content": FIXED}}, ctx)
    door("repo.step", {"repo_session_id": sid, "step_id": "green", "intent": "workspace.run_tests", "arguments": {}}, ctx)
    door("repo.review", {"repo_session_id": sid, "verdict": "approve"}, ctx)

    planned = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx)
    assert planned.ok is False and planned.status == "refused_by_user_rule", planned.response_text
    assert "ffff" in planned.response_text
    assert remote_ref(bare, "feature") == ""

    _say("remove rule 1", "chat-a")
    planned = door("repo.push.request", {"repo_session_id": sid, "ref": "feature"}, ctx)
    assert planned.ok is True, planned.response_text
    plan_hash = planned.details["plan_hash"]
    operator_ctx = context(root, session="chat-a", repo_push_authorization={"plan_hash": plan_hash})
    door("repo.push.authorize", {"repo_session_id": sid, "plan_hash": plan_hash}, operator_ctx)
    pushed = door("repo.push", {"repo_session_id": sid, "simulate": False}, ctx)
    assert pushed.ok is True, pushed.response_text
    assert remote_ref(bare, "feature") == head(root)
