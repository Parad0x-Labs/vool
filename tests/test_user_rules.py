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


# ── exceptions: a chat or project lifting a broader rule on the user's command ──


def _gate(cmd: str, root: Path, chat: str) -> dict:
    from core.execution_gate import ExecutionGate
    from core.remote_fetch_policy import remote_fetch_policy_scope

    with remote_fetch_policy_scope({"surface": "openclaw", "session_id": chat}):
        return ExecutionGate.evaluate_command(cmd, cwd=str(root))


def test_a_chat_exception_lifts_a_global_word_ban_in_that_chat_only(home, tmp_path):
    root = _repo(tmp_path)
    _say(GLOBAL_GIT_RULE, "chat-a")
    reply, saved, kind = _say("in this chat, allow ffff", "chat-a")
    assert kind == "saved" and saved[0].is_exception and saved[0].scope == "chat"
    assert "rule 1" in reply

    allowed = _gate('git commit -am "ffff cleanup"', root, "chat-a")
    assert allowed["decision"] == "advice_only"  # back to the ordinary approval gate, not refused
    assert "exception" in allowed["reason"] and "rule 1" in allowed["reason"]
    # The exception lifts only the word it names, and only here.
    assert _gate('git commit -am "xxx cleanup"', root, "chat-a")["decision"] == "blocked"
    assert _gate('git commit -am "ffff cleanup"', root, "chat-b")["decision"] == "blocked"


def test_ignoring_a_rule_by_number_lifts_it_whole_and_the_reply_says_so(home):
    _say("add global rule: never say 'alpha' or 'beta'", "chat-a")
    _, saved, kind = _say("ignore global rule 1 in this chat", "chat-a")
    assert kind == "saved" and saved[0].overrides_rule_no == 1
    review = ur.review_reply("alpha and beta", chat_id="chat-a")
    assert review.text == "alpha and beta" and not review.hits
    assert "rule 1" in ur.override_note(review.overrides)
    assert ur.mask_reply_terms("alpha and beta", chat_id="chat-b")[1]


def test_removing_the_exception_restores_the_ban(home, tmp_path):
    root = _repo(tmp_path)
    _say(GLOBAL_GIT_RULE, "chat-a")
    _, saved, _ = _say("allow ffff in this chat", "chat-a")
    assert _gate('git commit -am "ffff"', root, "chat-a")["decision"] == "advice_only"
    _, removed, kind = _say(f"remove rule {saved[0].rule_no}", "chat-a")
    assert kind == "removed" and removed[0].is_exception
    assert _gate('git commit -am "ffff"', root, "chat-a")["decision"] == "blocked"


def test_a_project_exception_binds_the_project_and_a_chat_rule_still_wins_inside_it(home, tmp_path):
    from core import project_store
    from core.context_namespace import ensure_chat_namespace

    folder = tmp_path / "orion"
    folder.mkdir()
    ok, project = project_store.create_project("Orion", str(folder))
    assert ok, project
    pid = str(project["id"])
    for chat in ("p-1", "p-2"):
        ensure_chat_namespace(chat, project_id=pid)
    _say("add global rule: never say 'alpha'", "p-1")
    _, saved, kind = _say("in this project, allow alpha", "p-1")
    assert kind == "saved" and saved[0].scope == "project" and saved[0].scope_ref == pid

    assert ur.mask_reply_terms("alpha", chat_id="p-2")[1] == []  # every chat in the project
    assert ur.mask_reply_terms("alpha", chat_id="outside")[1]  # not outside it
    # A narrower chat rule inside the project bans the word again: the narrowest scope wins.
    _say("add chat rule: never say 'alpha'", "p-2")
    assert ur.mask_reply_terms("alpha", chat_id="p-2")[1]
    assert ur.mask_reply_terms("alpha", chat_id="p-1")[1] == []


def test_an_exception_never_lifts_its_own_or_a_narrower_scope(home, tmp_path):
    from core import project_store
    from core.context_namespace import ensure_chat_namespace

    _say("add chat rule: never say 'alpha'", "chat-a")
    reply, saved, kind = _say("ignore rule 1 in this chat", "chat-a")
    assert kind == "refused" and not saved and "remove rule 1" in reply

    folder = tmp_path / "p"
    folder.mkdir()
    ok, project = project_store.create_project("P", str(folder))
    assert ok, project
    ensure_chat_namespace("p-chat", project_id=str(project["id"]))
    _say("add chat rule: never say 'beta'", "p-chat")
    reply, saved, kind = _say("in this project, allow beta", "p-chat")
    assert kind == "refused" and not saved and "remove rule" in reply  # a project exception cannot lift a chat rule

    _say("add global rule: never say 'gamma'", "no-project-chat")
    reply, saved, kind = _say("in this project, allow gamma", "no-project-chat")
    assert kind == "refused" and not saved and "isn't a project" in reply
    assert ur.mask_reply_terms("beta", chat_id="p-chat")[1]


def test_allow_with_no_rule_behind_it_is_ordinary_chat(home):
    assert ur.handle_rule_command("allow cookies in this chat", chat_id="chat-a") is None
    reply, saved, kind = _say("in this chat, ignore rule 9", "chat-a")
    assert kind == "refused" and not saved and "no active rule 9" in reply
    assert ur.active_rules(chat_id="chat-a", include_exceptions=True) == []


def test_the_prompt_block_names_lifted_rules_and_stays_byte_stable(home):
    _say(GLOBAL_GIT_RULE, "chat-a")
    _say("in this chat, allow ffff", "chat-a")
    first = ur.rules_prompt_block(chat_id="chat-a")
    assert first == ur.rules_prompt_block(chat_id="chat-a")
    assert "allows here: ffff" in first and "Do not refuse because of a lifted rule" in first
    git_line = next(line for line in first.splitlines() if line.startswith("Never put these words"))
    assert "xxx" in git_line and "ffff" not in git_line
    other = ur.rules_prompt_block(chat_id="chat-b")
    assert "ffff" in next(line for line in other.splitlines() if line.startswith("Never put these words"))


def test_repoops_commit_with_a_lifted_word_runs_and_names_the_exception(repo_world):
    from tests.repoops._harness import door, head

    root, _bare = repo_world
    _say(GLOBAL_GIT_RULE, "chat-a")
    _say("in this chat, allow ffff", "chat-a")
    sid, ctx = _open_bound_session(root, "chat-a")
    before = head(root)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b  # ffff\n", encoding="utf-8")
    done = door("repo.git", {"repo_session_id": sid, "operation": "commit", "message": "ffff fix"}, ctx)
    assert done.ok is True and head(root) != before
    assert "exception" in done.response_text and "rule 1" in done.response_text


def test_the_reply_guard_is_idempotent_over_a_sealed_reply(home):
    # The served turn re-runs the honesty seal verify-only on the sealed bytes; a guard that added
    # its note twice crashed the turn ("final-action-honesty would mutate post-admission bytes").
    from core.agent_runtime.action_honesty_validator import _enforce_user_rule_words

    _say("add global rule: never say 'alpha' or 'beta'", "chat-a")
    _say("in this chat, allow alpha", "chat-a")
    for chat, text in (("chat-a", "the alpha team"), ("chat-b", "the alpha team"), ("chat-a", "alpha and beta")):
        once = _enforce_user_rule_words({"response": text}, session_id=chat, source_context={"chat_id": chat})
        twice = _enforce_user_rule_words(dict(once), session_id=chat, source_context={"chat_id": chat})
        assert once["response"] != text
        assert twice["response"] == once["response"], (chat, text)


# ── T-0048 audit repairs: what a push sends, and every source of a message ──


def _side_branch_with(repo: Path, branch: str, text: str) -> None:
    _git(repo, "checkout", "-q", "-b", branch)
    (repo / f"{branch}.txt").write_text(text, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"{branch} change")
    _git(repo, "checkout", "-q", "main")


def test_a_push_is_checked_on_the_ref_it_sends_not_on_head(home, tmp_path):
    # Audit P1: HEAD (main) is clean while the branch being pushed carries the banned word.
    repo = _repo(tmp_path)
    _side_branch_with(repo, "side", "the ffff plan\n")
    _say(GLOBAL_GIT_RULE, "chat-a")
    for argv in (
        ["git", "push", "origin", "side"],
        ["git", "push", "origin", "side:refs/heads/release"],
        ["git", "push", "origin", "+side:main"],
        ["git", "push", "--all", "origin"],
    ):
        verdict = ur.sandbox_git_check(argv, cwd=str(repo))
        assert verdict.refusal is not None and "ffff" in verdict.refusal.reason, argv
    assert _gate("git push origin side", repo, "chat-a")["decision"] == "blocked"


def test_a_push_of_a_clean_ref_passes_even_when_head_is_not(home, tmp_path):
    repo = _repo(tmp_path)
    _side_branch_with(repo, "clean", "nothing to see\n")
    (repo / "README.md").write_text("ffff on main\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "main change")
    _say(GLOBAL_GIT_RULE, "chat-a")
    assert ur.sandbox_git_check(["git", "push", "origin", "clean"], cwd=str(repo)).refusal is None
    assert ur.sandbox_git_check(["git", "push", "origin", "main"], cwd=str(repo)).refusal is not None


def test_a_push_whose_source_cannot_be_read_is_refused(home, tmp_path):
    repo = _repo(tmp_path)
    _say(GLOBAL_GIT_RULE, "chat-a")
    for argv in (
        ["git", "push", "origin", "no-such-branch"],
        ["git", "push", "origin", "refs/heads/*:refs/heads/*"],
    ):
        verdict = ur.sandbox_git_check(argv, cwd=str(repo))
        assert verdict.refusal is not None and verdict.refusal.incomplete, argv


def test_a_push_naming_its_remote_with_repo_checks_every_refspec(home, tmp_path):
    # Re-audit P1: with --repo the remote is already named, so the first positional is a refspec.
    repo = _repo(tmp_path)
    _side_branch_with(repo, "side", "the ffff plan\n")
    _side_branch_with(repo, "clean", "nothing to see\n")
    _say(GLOBAL_GIT_RULE, "chat-a")
    for argv in (
        ["git", "push", "--repo", "origin", "side"],
        ["git", "push", "--repo=origin", "side"],
        ["git", "push", "--repo", "origin", "clean", "side:refs/heads/release"],
    ):
        verdict = ur.sandbox_git_check(argv, cwd=str(repo))
        assert verdict.refusal is not None and "ffff" in verdict.refusal.reason, argv
    assert ur.sandbox_git_check(["git", "push", "--repo", "origin", "clean"], cwd=str(repo)).refusal is None
    # a banned word in a ref name after --repo is a push ref too
    named = ur.sandbox_git_check(["git", "push", "--repo", "origin", "clean:refs/heads/xxx"], cwd=str(repo))
    assert named.refusal is not None


def test_a_matching_or_configured_push_checks_every_branch_it_could_send(home, tmp_path):
    # Re-audit P1: ":" is the matching refspec, not a deletion; a configured remote push refspec
    # decides what a bare push sends. Both are checked over every local branch.
    repo = _repo(tmp_path)
    _side_branch_with(repo, "side", "the ffff plan\n")
    _say(GLOBAL_GIT_RULE, "chat-a")
    for argv in (["git", "push", "origin", ":"], ["git", "push", "origin", "+:"],
                 ["git", "push", "--repo", "origin", ":"]):
        verdict = ur.sandbox_git_check(argv, cwd=str(repo))
        assert verdict.refusal is not None and "ffff" in verdict.refusal.reason, argv
    # deleting a remote ref still sends no content
    assert ur.sandbox_git_check(["git", "push", "origin", ":old-branch"], cwd=str(repo)).refusal is None
    # with no refspec and no push config, only the clean current branch is sent
    assert ur.sandbox_git_check(["git", "push", "origin"], cwd=str(repo)).refusal is None
    _git(repo, "config", "remote.origin.push", "refs/heads/side:refs/heads/side")
    configured = ur.sandbox_git_check(["git", "push", "origin"], cwd=str(repo))
    assert configured.refusal is not None and "ffff" in configured.refusal.reason


def test_a_message_read_from_a_file_or_an_old_commit_is_checked(home, tmp_path):
    # Audit P2: -F/--file (and reused messages) were never read, so a banned message got through.
    repo = _repo(tmp_path)
    _say(GLOBAL_GIT_RULE, "chat-a")
    banned = tmp_path / "banned.txt"
    banned.write_text("release ffff support\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("release support\n", encoding="utf-8")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "ship xxx")  # an old message to reuse
    (repo / "msg.txt").write_text("ffff notes\n", encoding="utf-8")
    for argv in (
        ["git", "commit", "--allow-empty", "-F", str(banned)],
        ["git", "commit", "--allow-empty", f"--file={banned}"],
        ["git", "commit", "--allow-empty", "-F", "msg.txt"],  # relative to the working directory
        ["git", "commit", "--allow-empty", "-C", "HEAD"],
        ["git", "tag", "-a", "v1", "-F", str(banned)],
    ):
        verdict = ur.sandbox_git_check(argv, cwd=str(repo))
        assert verdict.refusal is not None and not verdict.refusal.incomplete, argv
    # git -C moves the directory a relative message file is read from, exactly as git does.
    moved = ur.sandbox_git_check(["git", "-C", str(repo), "commit", "--allow-empty", "-F", "msg.txt"], cwd=str(tmp_path))
    assert moved.refusal is not None and not moved.refusal.incomplete
    assert ur.sandbox_git_check(["git", "commit", "--allow-empty", "-F", str(clean)], cwd=str(repo)).refusal is None
    missing = ur.sandbox_git_check(["git", "commit", "--allow-empty", "-F", str(tmp_path / "gone.txt")], cwd=str(repo))
    assert missing.refusal is not None and missing.refusal.incomplete
    stdin = ur.sandbox_git_check(["git", "commit", "--allow-empty", "-F", "-"], cwd=str(repo))
    assert stdin.refusal is not None and stdin.refusal.incomplete
