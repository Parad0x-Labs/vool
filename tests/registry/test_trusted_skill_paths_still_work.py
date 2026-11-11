"""The import and install checks narrow what comes from outside; the trusted ways in still work.

Each test walks one trusted path end to end through the real code, with no check stubbed out:

1. A skill written in VOOL through the real skill.create tool turns on through skill.install and is
   recorded as the person's own.
2. A bundled pack that ships with VOOL still loads through the one frontmatter parser, with its
   typed header keys (which an import would refuse), and is recorded as bundled.
3. A package signed into the Skill Codex list is verified, checked again here, reviewed and
   installed, using the same real signing fixture the registry tests use.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from core import plugin_lifecycle
from core.registry.artifact import inspect
from tests.registry.audit_helpers import runtime
from tests.registry.test_skill_codex import ORIGIN
from tests.registry.test_skill_install_recheck import SAFE, skill, through_the_app
from tests.registry.test_skill_pictures import app, published


@pytest.fixture(autouse=True)
def _fresh_permissions():
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def test_a_skill_written_in_vool_turns_on_and_is_the_persons_own(tmp_path, monkeypatch):
    from core import skill_tools
    from core.mode_permission_policy import PermissionAction, grant_internal_authority
    from core.runtime_execution_tools import execute_runtime_tool
    from core.skill_origin import ORIGIN_CREATED_HERE, install_record

    runtime(tmp_path, monkeypatch)
    token = grant_internal_authority(
        label="trusted-create", actions=[PermissionAction.CREATE_FILES, PermissionAction.CHANGE_SETTINGS],
        intents=["skill.create", "skill.install"], session_id="trusted-chat", duration_seconds=300,
    )
    context = {"session_id": "trusted-chat", "operating_mode": "manual", "internal_authority_token": token}
    made = execute_runtime_tool("skill.create", {
        "name": "Mum's recipes",
        "description": "Use when the user asks for one of Mum's recipes.",
        "body": "Give the recipe as a short list of ingredients, then numbered steps.",
    }, source_context=context)
    assert made is not None and made.ok is True, made.response_text
    slug = made.details.get("slug")
    assert slug == "mum-s-recipes", made.details

    on = execute_runtime_tool("skill.install", {"path": slug}, source_context=context)
    assert on is not None and on.ok is True, on.response_text
    installed = skill_tools.plugins_root() / "plugins" / "local-skills" / "skills" / slug / "SKILL.md"
    assert installed.is_file()
    assert "numbered steps" in installed.read_text(encoding="utf-8")
    assert install_record(installed)["origin"] == ORIGIN_CREATED_HERE


def test_a_bundled_pack_still_loads_with_its_typed_header(monkeypatch):
    from core.plugin_catalog import bundled_plugins_root
    from core.runtime_paths import PROJECT_ROOT

    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(Path(PROJECT_ROOT) / "plugins"))
    from core.plugin_skills import load_skills
    from core.skill_frontmatter import parse
    from core.skill_origin import ORIGIN_BUNDLED, origin_of

    root = bundled_plugins_root()
    assert root is not None
    pack = Path(root) / "vool-database"
    text = (pack / "skills" / "vool-database" / "SKILL.md").read_text(encoding="utf-8")
    header, body = parse(text)
    assert header["id"] == "database" and header["type"] == "capability_skill"  # typed keys a file import refuses
    assert body.strip()

    skills = {s.name: s for s in load_skills(pack, plugin_id="vool-database")}
    assert "vool-database" in skills
    loaded = skills["vool-database"]
    assert "vool-database.query" in loaded.allowed_tools
    assert origin_of(loaded) == ORIGIN_BUNDLED


def test_a_signed_skill_codex_package_is_verified_checked_and_installed(tmp_path, monkeypatch):
    good = skill(tmp_path / "good", "bob/notes", SAFE)
    store, catalog, trust = published(tmp_path, [good])
    with TestClient(app(store, catalog), base_url=ORIGIN) as service:
        post, calls = through_the_app(monkeypatch, tmp_path, service, trust)
        assert post({"action": "registry_refresh", "approved": True})[0] == 200
        status, review = post({"action": "registry_prepare", "id": "bob/notes", "version": "1.0.0", "approved": True})
        assert status == 200 and review["status"] == "review", review
        assert review["review_id"] == inspect(good)["sha256"]  # bound to the exact signed bytes
        status, result = post({"action": "registry_install", "id": "bob/notes", "version": "1.0.0",
                               "review_id": review["review_id"], "accepted": True, "enable": True})
        assert status == 200 and result["status"] == "installed", result
        assert any(record for record in plugin_lifecycle.records())
    catalog.close()
