"""A skill made for this chat, this project or all of VOOL loads exactly there, and travels as a zip.

What each block pins, and why it can fail:

1. Scope words. "for this chat / for all project / as per whole vool" is how the request is typed;
   "a skill that posts to the chat" or "summarises global news" says what a skill DOES, not where
   it lives, and must not move it.
2. Isolation through the REAL prompt seam (``skill_guidance_for``), with real server-side chat to
   project bindings: a chat skill never reaches another chat, a project skill reaches every chat
   bound to that project and no other, and a turn with no context gets neither.
3. Activation is bound to the reviewed bytes: a draft edited after it was shown does not go live.
4. Export -> import round trip carries the exact SKILL.md bytes into another scope, and the import
   refuses archive dangers, runnable code, tampered packages, local-only and first-party skills.
5. Remove takes a skill out of the loader and keeps its files.
6. The chat lane: an unnamed request with a purpose is drafted (name shown), "turn it on" installs
   exactly that draft at its scope, and nothing else reaches back to it.
7. Plugin packs: bundle, activate, export, re-read.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core import skill_package, skill_tools
from core.skill_scopes import scope_in, scope_keys, scope_root
from core.tool_offer_assembly import skill_guidance_for

SKILL_TEXT = """---
name: "meeting-actions"
description: "Use when the user pastes meeting notes and wants the action items."
triggers: ["meeting", "notes", "action items"]
---

You turn pasted meeting notes into a numbered list of action items, each with an owner.
If no owner is named, write "owner: unassigned". Never invent attendees.
"""


@pytest.fixture(autouse=True)
def _private_plugins_tree(tmp_path, monkeypatch):
    """Global skills go to a temporary plugins tree (through the real VOOL_PLUGINS_DIR override the
    writer and the catalog both resolve); chat/project skills to the test runtime home."""
    from core.plugin_catalog import invalidate_storage_listing

    root = tmp_path / "plugins-root"
    (root / "plugins").mkdir(parents=True)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(root))
    invalidate_storage_listing()
    assert skill_tools.plugins_root() == root
    yield root
    invalidate_storage_listing()


def _bind(chat: str, project: str = "") -> dict[str, str]:
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(chat, project_id=project or None)
    return scope_keys({"session_id": chat})


def _install_scoped(keys: dict[str, str], scope: str, text: str = SKILL_TEXT, slug: str = "meeting-actions") -> dict:
    staged_root = skill_tools._scoped_dirs(scope, keys)[0] / slug
    staged_root.mkdir(parents=True, exist_ok=True)
    (staged_root / "SKILL.md").write_text(text, encoding="utf-8")
    result = skill_tools.install_skill(slug, scope=scope, keys=keys, overwrite=True)
    assert result["status"] == "ok", result
    return result


ASK = "here are my meeting notes, give me the action items"


# --------------------------------------------------------------------------- 1. scope words


@pytest.mark.parametrize(
    ("text", "scope"),
    [
        ("we need a skill for this chat that turns notes into action items", "chat"),
        ("create a skill in this conversation that answers in bullets", "chat"),
        ("create a skill for all project that writes release notes", "project"),
        ("make a skill for the whole project that reviews PRs", "project"),
        ("create a skill for this project that checks the changelog", "project"),
        ("add a skill as per whole vool that summarises articles", "global"),
        ("create a skill for all of VOOL that translates to Lithuanian", "global"),
        ("make a skill for all chats that formats tables", "global"),
        ("create a skill that posts a summary to the chat", ""),
        ("create a skill that summarises global news", ""),
        ("create a skill called release-notes that summarises CHANGELOG.md", ""),
    ],
)
def test_scope_words(text: str, scope: str) -> None:
    assert scope_in(text) == scope


# --------------------------------------------------------------------------- 2. isolation


def test_a_chat_skill_loads_only_in_its_chat() -> None:
    keys_a = _bind("chat-iso-a")
    _install_scoped(keys_a, "chat")

    in_a = skill_guidance_for(ASK, source_context={"session_id": "chat-iso-a"})
    in_b = skill_guidance_for(ASK, source_context={"session_id": "chat-iso-b"})
    no_context = skill_guidance_for(ASK)

    assert "numbered list of action items" in in_a.text
    assert [row["origin"] for row in in_a.skills if row["name"] == "meeting-actions"] == ["chat"]
    assert "numbered list of action items" not in in_b.text
    assert "numbered list of action items" not in no_context.text
    # The prompt names the scope, never the machine path or the chat key.
    assert "chat-iso-a" not in in_a.text
    assert str(scope_root("chat", keys_a)) not in in_a.text


def test_a_project_skill_loads_in_every_chat_of_that_project_only() -> None:
    keys_one = _bind("chat-proj-1", "proj_skilltest")
    _bind("chat-proj-2", "proj_skilltest")
    _bind("chat-other-proj", "proj_elsewhere")
    _bind("chat-unbound")
    assert keys_one["project"] == "proj_skilltest"
    _install_scoped(keys_one, "project")

    def sees(chat: str) -> bool:
        return "numbered list of action items" in skill_guidance_for(ASK, source_context={"session_id": chat}).text

    assert sees("chat-proj-1") and sees("chat-proj-2")
    assert not sees("chat-other-proj")
    assert not sees("chat-unbound")


def test_a_chat_skill_shadows_a_global_skill_of_the_same_name(_private_plugins_tree) -> None:
    global_text = SKILL_TEXT.replace("numbered list", "GLOBAL-VERSION list")
    staged = skill_tools.staging_root() / "meeting-actions"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text(global_text, encoding="utf-8")
    assert skill_tools.install_skill("meeting-actions")["status"] == "ok"
    keys = _bind("chat-shadow")
    _install_scoped(keys, "chat")

    here = skill_guidance_for(ASK, source_context={"session_id": "chat-shadow"}).text
    elsewhere = skill_guidance_for(ASK, source_context={"session_id": "chat-shadow-other"}).text
    assert "numbered list of action items" in here and "GLOBAL-VERSION" not in here
    assert "GLOBAL-VERSION" in elsewhere


def test_the_operators_skill_is_not_crowded_out_by_first_party_selections() -> None:
    """Measured live 2026-10-05: four first-party skills chosen on the task class filled every slot
    and the chat skill that matched the request never reached the prompt. A task class that selects
    a full budget of native skills ("debugging" picks three) must still leave the operator's
    matching skill its slot, with natives filling only what is left."""
    from core.tool_offer_assembly import MAX_SKILLS

    keys = _bind("chat-not-crowded")
    _install_scoped(keys, "chat")
    here = skill_guidance_for(ASK, task_class="debugging", source_context={"session_id": "chat-not-crowded"})
    elsewhere = skill_guidance_for(ASK, task_class="debugging", source_context={"session_id": "chat-crowded-other"})

    here_rows = [row for row in here.skills if row.get("complete", True)]
    assert here_rows[0]["name"] == "meeting-actions" and here_rows[0]["origin"] == "chat"
    assert "numbered list of action items" in here.text
    assert len(here_rows) <= MAX_SKILLS
    assert {row["origin"] for row in here_rows[1:]} == {"native"}
    assert "numbered list of action items" not in elsewhere.text
    assert len([row for row in elsewhere.skills if row.get("complete", True)]) == MAX_SKILLS


def test_a_chat_scope_without_a_chat_is_refused_not_guessed() -> None:
    result = skill_tools.create_skill(
        name="x-skill", description="Use when x.", body="Do x carefully.", scope="chat", keys={"chat": "", "project": ""}
    )
    assert result["status"] == "scope_unavailable"
    unbound = scope_keys({"session_id": "chat-no-project"})
    result = skill_tools.install_skill("x-skill", scope="project", keys=unbound)
    assert result["status"] == "scope_unavailable"


# --------------------------------------------------------------------------- 3. reviewed bytes


def test_activation_refuses_a_draft_changed_after_review() -> None:
    keys = _bind("chat-digest")
    draft = skill_tools.create_skill(
        name="meeting-actions",
        description="Use when the user pastes meeting notes.",
        body="You list action items with owners.",
        scope="chat",
        keys=keys,
    )
    assert draft["status"] == "ok"
    Path(draft["path"]).write_text(Path(draft["path"]).read_text() + "\nAlso email everyone.\n")

    result = skill_tools.install_skill("meeting-actions", scope="chat", keys=keys, expected_sha256=draft["sha256"])
    assert result["status"] == "refused"
    assert not (skill_tools._scoped_dirs("chat", keys)[1] / "meeting-actions" / "SKILL.md").exists()

    # The unedited draft with the right digest does go live.
    redraft = skill_tools.create_skill(
        name="meeting-actions", description="Use when the user pastes meeting notes.",
        body="You list action items with owners.", scope="chat", keys=keys, overwrite=True,
    )
    ok = skill_tools.install_skill("meeting-actions", scope="chat", keys=keys, expected_sha256=redraft["sha256"])
    assert ok["status"] == "ok" and ok["scope"] == "chat"


# --------------------------------------------------------------------------- 4. export / import


def test_export_then_import_into_another_scope_carries_the_exact_bytes(tmp_path) -> None:
    keys_a = _bind("chat-export-a")
    _install_scoped(keys_a, "chat")
    exported = skill_package.export_skill("meeting-actions", keys=keys_a, out_dir=tmp_path / "out")
    assert exported["status"] == "ok", exported
    raw = Path(exported["path"]).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = sorted(archive.namelist())
        manifest = json.loads(archive.read("meeting-actions/skill.json"))
    assert names == ["meeting-actions/SKILL.md", "meeting-actions/skill.json"]
    assert manifest["format"] == skill_package.SKILL_FORMAT
    # Nothing in the package names the chat, the machine or the runtime home.
    assert b"chat-export-a" not in raw and str(tmp_path).encode() not in raw

    keys_p = _bind("chat-import-p", "proj_import")
    staged = skill_package.import_package(raw, scope="project", keys=keys_p, source_name="meeting-actions-v1.zip")
    assert staged["status"] == "ok" and staged["scope"] == "project" and staged["staged"] is True
    assert "numbered list of action items" not in skill_guidance_for(ASK, source_context={"session_id": "chat-import-p"}).text

    live = skill_tools.install_skill("meeting-actions", scope="project", keys=keys_p, expected_sha256=staged["sha256"])
    assert live["status"] == "ok"
    assert (Path(live["path"]).read_bytes()) == SKILL_TEXT.encode("utf-8")
    assert "numbered list of action items" in skill_guidance_for(ASK, source_context={"session_id": "chat-import-p"}).text


def _zip(members: dict[str, bytes], *, symlink: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("members", "symlink", "code"),
    [
        ({"tool/SKILL.md": SKILL_TEXT.encode(), "tool/scripts/run.py": b"import os"}, "", "runnable_code"),
        ({"tool/SKILL.md": SKILL_TEXT.encode(), "tool/notes.csv": b"a,b"}, "", "unsupported_files"),
        ({"../evil/SKILL.md": SKILL_TEXT.encode()}, "", "archive_unsafe"),
        ({"tool/SKILL.md": SKILL_TEXT.encode()}, "tool/link", "archive_unsafe"),
        ({"tool/README.md": b"no skill here"}, "", "invalid_package"),
        ({"tool/SKILL.md": b"no frontmatter at all"}, "", "invalid_skill"),
        (
            {
                "tool/SKILL.md": SKILL_TEXT.encode(),
                "tool/skill.json": json.dumps({"files": {"SKILL.md": "0" * 64}}).encode(),
            },
            "",
            "digest_mismatch",
        ),
        (
            {
                "pack/.codex-plugin/plugin.json": json.dumps({"name": "pack", "tools": [{"intent": "x"}]}).encode(),
                "pack/skills/a/SKILL.md": SKILL_TEXT.encode(),
            },
            "",
            "runnable_code",
        ),
        (
            {
                "pack/.codex-plugin/plugin.json": json.dumps({"name": "pack"}).encode(),
                "pack/skills/a/SKILL.md": SKILL_TEXT.encode(),
                "pack/bin/handler": b"#!/bin/sh",
            },
            "",
            "runnable_code",
        ),
    ],
)
def test_import_refuses_what_is_not_a_plain_instruction_package(members, symlink, code) -> None:
    keys = _bind("chat-refuse")
    result = skill_package.import_package(_zip(members, symlink=symlink), scope="chat", keys=keys)
    assert result["status"] == "refused" and result["code"] == code, result
    # Nothing was staged for this chat.
    staged = skill_tools._scoped_dirs("chat", keys)[0]
    assert not staged.exists() or not any(staged.iterdir())


@pytest.mark.parametrize(
    ("member", "recorded"),
    [
        ("tool/skill.json", []),
        ("tool/skill.json", 42),
        ("tool/skill.json", "not-an-object"),
        ("tool/skill.json", None),
        ("tool/skill.json", {"files": ["SKILL.md"]}),
        ("tool/skill.json", {"files": {"SKILL.md": 7}}),
        ("tool/skill.json", {"slug": ["a", "b"]}),
        ("pack/plugin.export.json", []),
        ("pack/plugin.export.json", {"skills": "a"}),
        ("pack/.codex-plugin/plugin.json", []),
    ],
)
def test_a_manifest_of_the_wrong_shape_is_refused_not_a_crash(member, recorded) -> None:
    # Re-audit P2: valid JSON that is not an object (or whose digest map is not a map) crashed import.
    import hashlib

    # one chat per case: the module shares a home, and an accepted import stages into its chat
    keys = _bind("chat-shape-" + hashlib.sha256(json.dumps([member, recorded]).encode()).hexdigest()[:12])
    if member.startswith("pack/"):
        members = {"pack/.codex-plugin/plugin.json": json.dumps({"name": "pack"}).encode(),
                   "pack/skills/a/SKILL.md": SKILL_TEXT.encode()}
    else:
        members = {"tool/SKILL.md": SKILL_TEXT.encode()}
    members[member] = json.dumps(recorded).encode()
    result = skill_package.import_package(_zip(members), scope="chat", keys=keys)
    assert result["status"] == "refused" and result["code"] == "invalid_manifest", result
    staged = skill_tools._scoped_dirs("chat", keys)[0]
    assert not staged.exists() or not any(staged.iterdir())


def test_import_accepts_a_bare_skill_folder_zip_from_another_tool() -> None:
    keys = _bind("chat-bare")
    raw = _zip({"meeting-actions/SKILL.md": SKILL_TEXT.encode(), "meeting-actions/LICENSE.txt": b"MIT"})
    result = skill_package.import_package(raw, scope="chat", keys=keys)
    assert result["status"] == "ok" and result["slug"] == "meeting-actions"


def test_local_only_and_first_party_skills_are_never_exported(tmp_path) -> None:
    keys = _bind("chat-local-only")
    local_only = SKILL_TEXT.replace('triggers:', 'local-only: true\ntriggers:')
    _install_scoped(keys, "chat", text=local_only)
    refused = skill_package.export_skill("meeting-actions", keys=keys, out_dir=tmp_path)
    assert refused["status"] == "refused" and "local-only" in refused["reason"]
    assert not list(tmp_path.glob("*.zip"))

    from core.native_skill_library import native_skills_root

    native = next(p.parent.name for p in sorted(native_skills_root().glob("*/SKILL.md")))
    first_party = skill_package.export_skill(native, keys=keys, out_dir=tmp_path)
    assert first_party["status"] == "not_user_skill"


# --------------------------------------------------------------------------- 5. remove


def test_remove_takes_a_skill_out_of_the_loader_and_keeps_its_files() -> None:
    keys = _bind("chat-remove")
    _install_scoped(keys, "chat")
    assert "numbered list" in skill_guidance_for(ASK, source_context={"session_id": "chat-remove"}).text

    result = skill_tools.remove_skill("meeting-actions", keys=keys)
    assert result["status"] == "ok" and result["scope"] == "chat"
    assert "numbered list" not in skill_guidance_for(ASK, source_context={"session_id": "chat-remove"}).text
    kept = Path(result["moved_to"])
    assert (kept / "SKILL.md").read_text() == SKILL_TEXT
    assert (kept / "versions" / "history.json").is_file()
    assert skill_tools.remove_skill("meeting-actions", keys=keys)["status"] == "not_found"


def test_remove_refuses_a_pack_vool_manages(_private_plugins_tree) -> None:
    pack = _private_plugins_tree / "plugins" / "vendor-pack"
    (pack / ".codex-plugin").mkdir(parents=True)
    (pack / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "vendor-pack"}))
    (pack / "skills" / "vendor-skill").mkdir(parents=True)
    (pack / "skills" / "vendor-skill" / "SKILL.md").write_text(SKILL_TEXT)
    result = skill_tools.remove_skill("vendor-skill")
    assert result["status"] == "refused"
    assert (pack / "skills" / "vendor-skill" / "SKILL.md").is_file()


# --------------------------------------------------------------------------- 6. chat lane


def _agent():
    return SimpleNamespace(
        _fast_path_result=lambda **kw: {"response": kw.get("response"), "reason": kw.get("reason")}
    )


@pytest.mark.parametrize(
    ("text", "action", "scope"),
    [
        ("we need a skill for this chat that turns pasted meeting notes into action items", "create", "chat"),
        ("create a skill for all project that writes release notes from the changelog", "create", "project"),
        ("create a skill to delete old log files", "create", None),
        ("create a skill that exports invoices as a zip", "create", None),
        ("remove the meeting-actions skill from this chat", "remove", "chat"),
        ("export the meeting-actions skill", "export", None),
        ("import this skill for this project", "import", "project"),
        ("create a plugin called team-kit with the meeting-actions and release-notes skills", "pack", None),
        ("export the team-kit plugin", "export", None),
        ("we need a skill for this chat that does this and that", "needs_name", "chat"),
    ],
)
def test_lane_routing(text: str, action: str, scope: str | None) -> None:
    from core.agent_runtime.fast_paths_skill import skill_request

    request = skill_request(text) or {}
    assert request.get("action") == action, request
    assert request.get("scope") == scope


def test_turn_it_on_installs_exactly_the_shown_draft_in_its_scope() -> None:
    from core.agent_runtime import fast_paths_skill
    from core.mode_permission_policy import PermissionAction, grant_internal_authority

    chat = "chat-lane-flow"
    _bind(chat)
    token = grant_internal_authority(
        label="test-skill-lane",
        actions=[PermissionAction.CREATE_FILES, PermissionAction.CHANGE_SETTINGS],
        intents=["skill.create", "skill.install"],
        session_id=chat,
        duration_seconds=300,
    )
    context = {"session_id": chat, "operating_mode": "manual", "internal_authority_token": token}
    drafted = {
        "name": "meeting-actions",
        "description": "Use when the user pastes meeting notes and wants the action items.",
        "triggers": ["meeting", "notes", "action items"],
        "instructions": "You turn pasted meeting notes into a numbered list of action items, each with an owner.",
    }
    with mock.patch.object(fast_paths_skill, "_draft_with_model", return_value=(dict(drafted), "test-model")):
        reply = fast_paths_skill.maybe_handle_skill_request(
            _agent(), "we need a skill for this chat that turns pasted meeting notes into action items",
            session_id=chat, source_context=context,
        )
    assert reply["reason"] == "skill_create", reply
    assert "`meeting-actions` for this chat" in reply["response"] and "not on yet" in reply["response"]
    assert "numbered list" not in skill_guidance_for(ASK, source_context={"session_id": chat}).text

    # Something unrelated in between does not reach back to the draft...
    assert fast_paths_skill.maybe_handle_skill_request(_agent(), "save it to my notes", session_id=chat, source_context=context) is None
    assert fast_paths_skill.maybe_handle_skill_request(_agent(), "turn it on", session_id=chat, source_context=context) is None

    # ...so the draft is re-offered, then turned on.
    with mock.patch.object(fast_paths_skill, "_draft_with_model", return_value=(dict(drafted), "test-model")):
        fast_paths_skill.maybe_handle_skill_request(
            _agent(), "we need a skill for this chat that turns pasted meeting notes into action items",
            session_id=chat, source_context=context,
        )
    on = fast_paths_skill.maybe_handle_skill_request(_agent(), "turn it on", session_id=chat, source_context=context)
    assert on["reason"] == "skill_activate" and "for this chat" in on["response"], on
    assert "numbered list of action items" in skill_guidance_for(ASK, source_context={"session_id": chat}).text
    assert "numbered list of action items" not in skill_guidance_for(ASK, source_context={"session_id": "chat-lane-other"}).text


def test_without_a_model_the_draft_is_the_operators_own_words_and_says_so() -> None:
    from core.agent_runtime import fast_paths_skill

    chat = "chat-no-model"
    _bind(chat)
    from core.mode_permission_policy import PermissionAction, grant_internal_authority

    token = grant_internal_authority(
        label="test-skill-words", actions=[PermissionAction.CREATE_FILES], intents=["skill.create"],
        session_id=chat, duration_seconds=300,
    )
    reply = fast_paths_skill.maybe_handle_skill_request(
        _agent(), "we need a skill for this chat that turns pasted meeting notes into action items",
        session_id=chat,
        source_context={"session_id": chat, "operating_mode": "manual", "internal_authority_token": token},
    )
    assert reply["reason"] == "skill_create", reply
    assert "(written from your own words: " in reply["response"]
    assert "written by from" not in reply["response"]  # the fallback author reads as a sentence
    staged = list(skill_tools._scoped_dirs("chat", scope_keys({"session_id": chat}))[0].glob("*/SKILL.md"))
    assert len(staged) == 1 and "turns pasted meeting notes into action items" in staged[0].read_text()


# --------------------------------------------------------------------------- 7. plugin packs


def test_plugin_pack_bundle_activate_export_reread(tmp_path, _private_plugins_tree) -> None:
    keys = _bind("chat-pack")
    _install_scoped(keys, "chat")
    second = SKILL_TEXT.replace("meeting-actions", "release-notes").replace(
        "pasted meeting notes", "a CHANGELOG"
    )
    _install_scoped(keys, "chat", text=second, slug="release-notes")

    staged = skill_package.create_plugin(
        "team-kit", description="Our team's skills.", skill_names=["meeting-actions", "release-notes"], keys=keys
    )
    assert staged["status"] == "ok" and staged["skills"] == ["meeting-actions", "release-notes"], staged
    live = skill_package.install_plugin("team-kit", expected_sha256=staged["sha256"])
    assert live["status"] == "ok", live
    manifest = json.loads((_private_plugins_tree / "plugins" / "team-kit" / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["vool"]["origin"] == "user" and "tools" not in manifest

    exported = skill_package.export_plugin("team-kit", out_dir=tmp_path)
    assert exported["status"] == "ok", exported
    package = skill_package.read_package(Path(exported["path"]).read_bytes())
    assert package["kind"] == "plugin" and sorted(s["slug"] for s in package["skills"]) == ["meeting-actions", "release-notes"]

    # A pack whose staged bytes changed after review is not activated.
    restaged = skill_package.create_plugin("team-kit-2", description="x", skill_names=["meeting-actions"], keys=keys)
    skill_md = Path(restaged["path"]) / "skills" / "meeting-actions" / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\nextra\n")
    assert skill_package.install_plugin("team-kit-2", expected_sha256=restaged["sha256"])["status"] == "refused"


def test_a_skill_request_is_not_split_by_the_turn_planner() -> None:
    """Measured live: the planner split one skill request in two and a model answered the second
    half on its own. The planner is never asked about a skill-authoring sentence; a real
    multi-request message still is (control)."""
    from core.agent_runtime.turn_planner import plan_turn

    asked: list[str] = []

    def ask(_system: str, prompt: str) -> str:
        asked.append(prompt)
        return json.dumps([{"request": "what is the weather in Rome"}, {"request": "convert 100 USD to EUR"}])

    skill_text = (
        "create a skill for this project that writes release notes from a list of merged changes, "
        "grouped under Added, Changed and Fixed, one line per change"
    )
    assert plan_turn(skill_text, ask_model=ask) == []
    assert asked == []
    plan_turn("What is the weather in Rome? Also convert 100 USD to EUR.", ask_model=ask)
    assert asked, "control: an ordinary multi-request message must still reach the planner"


def test_turn_it_on_after_a_restart_uses_the_offer_in_the_conversation() -> None:
    """The in-process record of a draft is gone after a restart while its offer is still on screen.
    "turn it on" must then act on the offer's skill and scope -- and when that draft was never
    saved, say so instead of falling through to a model (measured live: a model answered "the skill
    is now turned on" with nothing saved)."""
    from core.agent_runtime import fast_paths_skill
    from core.mode_permission_policy import PermissionAction, grant_internal_authority

    chat = "chat-after-restart"
    keys = _bind(chat)
    offer = (
        "I drafted the skill `meeting-actions` for this chat (written by test-model).\n\n...\n\n"
        "It is not on yet. Say **turn it on** to start using it, or tell me what to change."
    )
    history = [{"role": "user", "content": "we need a skill ..."}, {"role": "assistant", "content": offer}]
    token = grant_internal_authority(
        label="test-restart", actions=[PermissionAction.CREATE_FILES, PermissionAction.CHANGE_SETTINGS],
        intents=["skill.install"], session_id=chat, duration_seconds=300,
    )
    context = {"session_id": chat, "operating_mode": "manual", "internal_authority_token": token,
               "conversation_history": history}

    nothing = fast_paths_skill.maybe_handle_skill_request(_agent(), "turn it on", session_id=chat, source_context=context)
    assert nothing["reason"] == "skill_activate_nothing_saved" and "never saved" in nothing["response"]

    staged = skill_tools._scoped_dirs("chat", keys)[0] / "meeting-actions"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    fast_paths_skill._clear_pending(chat)
    on = fast_paths_skill.maybe_handle_skill_request(_agent(), "turn it on", session_id=chat, source_context=context)
    assert on["reason"] == "skill_activate" and "for this chat" in on["response"], on
    assert "numbered list of action items" in skill_guidance_for(ASK, source_context={"session_id": chat}).text


def test_a_skill_request_is_not_a_current_information_turn() -> None:
    """Measured live: "... release notes from a list of merged changes ..." read as a fresh lookup,
    so the model-drafted skill was withheld by the publication gate for lacking retrieved support.
    A real lookup with the same words keeps its evidence contract (control)."""
    from core.execution_requirements import requirements_for

    skill = requirements_for(
        "create a skill for this project that turns a list of merged changes into release notes "
        "grouped under Added, Changed and Fixed, one line per change"
    )
    lookup = requirements_for("what are the latest release notes for python 3.13")
    assert skill.current_information_required is False
    assert lookup.current_information_required is True


def test_turn_it_on_after_a_restart_refuses_a_draft_replaced_since_it_was_shown() -> None:
    """Measured live: two chats of one project drafted the same name; the second save replaced the
    first. "turn it on" in the first chat (record gone) must not activate the other chat's bytes."""
    from core.agent_runtime import fast_paths_skill

    chat = "chat-replaced-draft"
    keys = _bind(chat)
    staged = skill_tools._scoped_dirs("chat", keys)[0] / "meeting-actions"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    offer = (
        "I drafted the skill `meeting-actions` for this chat (written by test-model).\n\n"
        "**When it applies:** Use when the user wants something else entirely.\n\n"
        "It is not on yet. Say **turn it on** to start using it, or tell me what to change."
    )
    context = {"session_id": chat, "operating_mode": "manual",
               "conversation_history": [{"role": "assistant", "content": offer}]}
    fast_paths_skill._clear_pending(chat)
    reply = fast_paths_skill.maybe_handle_skill_request(_agent(), "turn it on", session_id=chat, source_context=context)
    assert reply["reason"] == "skill_activate_draft_changed", reply
    assert not (skill_tools._scoped_dirs("chat", keys)[1] / "meeting-actions").exists()


@pytest.mark.parametrize("intent", ["skill.export", "skill.import", "skill.pack", "skill.remove"])
def test_every_new_skill_intent_is_dispatchable_not_refused_for_coverage(intent: str) -> None:
    """Measured live: skill.export reached the dispatcher and was refused "carries no Blackbox
    coverage declaration" -- a mutation-capable intent with no declaration does not run. Every new
    intent must clear that gate (its own refusal, if any, is about its arguments)."""
    from core.blackbox.coverage.registry import mutation_coverage_decision

    decision = mutation_coverage_decision(intent)
    assert decision.covered, decision


def test_an_import_approved_on_a_later_turn_reads_the_held_package_of_its_own_chat_only() -> None:
    """Measured live: the attachment's bytes end with its turn, and the approved import arrives on a
    later turn without it. The package the lane held is readable by the SAME chat only."""
    from core.runtime_execution_tools import _skill_package_bytes

    raw = _zip({"meeting-actions/SKILL.md": SKILL_TEXT.encode()})
    skill_package.hold_package("chat-hold-a", "att_0123456789abcdef", "meeting-actions.zip", raw)

    mine = _skill_package_bytes({"attachment_id": "att_0123456789abcdef"}, {"session_id": "chat-hold-a"})
    other = _skill_package_bytes({"attachment_id": "att_0123456789abcdef"}, {"session_id": "chat-hold-b"})
    assert mine[0] == raw and mine[1] == "meeting-actions.zip"
    assert other[0] is None and "not one this chat holds" in other[2]

    skill_package.release_package("chat-hold-a", "att_0123456789abcdef")
    assert _skill_package_bytes({"attachment_id": "att_0123456789abcdef"}, {"session_id": "chat-hold-a"})[0] is None


def test_a_skill_bundled_into_a_plugin_is_injected_once(_private_plugins_tree) -> None:
    """Measured live: after bundling, the plugin's copy and the local-skills copy of one skill both
    matched; one name gets one slot."""
    staged = skill_tools.staging_root() / "meeting-actions"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    assert skill_tools.install_skill("meeting-actions")["status"] == "ok"
    keys = _bind("chat-bundle-once")
    pack = skill_package.create_plugin("team-kit", description="x", skill_names=["meeting-actions"], keys=keys)
    assert skill_package.install_plugin("team-kit", expected_sha256=pack["sha256"])["status"] == "ok"

    guidance = skill_guidance_for(ASK, source_context={"session_id": "chat-bundle-once"})
    names = [row["name"] for row in guidance.skills if row.get("complete", True)]
    assert names.count("meeting-actions") == 1
    assert guidance.text.count("numbered list of action items") == 1
