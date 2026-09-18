"""PB04 — the EXISTING conversational skill lifecycle, proven on a small DB skill.

No second creator is built here: every step is `core.skill_tools.create_skill` /
`validate_skill` / `install_skill` (the `skill.create`/`validate`/`install` tool
bodies) against an isolated plugins root. The lifecycle proven:

draft (staged, inert) -> validate (real loader) -> approved install (active tree,
catalog-visible manifest) -> a SUBSEQUENT TURN selects the recorded version (the
prompt seam injects that exact installed body, with its provenance) -> disable
(owner switch + cache reset = the "restart") -> not selected -> re-enable ->
selected again.
"""
from __future__ import annotations

import pytest

from tests._toolchain_fixtures import reset_toolchain_state
from tests._vool_database_pack import isolated_db_world

MARKER = "ROW_COUNT_REPORTING_MARKER_DBP04"

# A user-requested small DB skill: how this user wants row-count questions answered.
# allowed-tools is deliberately EMPTY, and that is a recorded capability gap, not a
# choice: skill_tools.validate_skill checks allowed-tools against
# runtime_tool_contract_map() (the BUILTIN map, core/skill_tools.py), so a skill that
# names registered plugin tools cannot pass validation even with the pack loaded and
# the tools dispatchable (proven by test_validate_refuses_plugin_tool_names_gap below).
# skill_tools.py is outside this lane's editable set, so the gap is pinned, not bypassed;
# a skill without allowed-tools keeps the full catalog on a matched turn.
SKILL_REQUEST = {
    "name": "db row count reporter",
    "description": (
        "Answer questions about database tables with exact row counts. Use when the user asks "
        "how many rows a table has, or asks for a count report of the database."
    ),
    "body": (
        "# Row count reporter\n\n"
        "When asked for row counts, always call vool-database.schema first, quote the row_count\n"
        "of the named table from that receipt, and flag any potential_pii_columns the schema\n"
        "tool reported before quoting personal data. " + MARKER + "\n"
    ),
    "allowed_tools": [],
    "triggers": ["row count", "how many rows", "count report", "database"],
}


@pytest.fixture()
def lifecycle_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    isolated_db_world(tmp_path, monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        from core import plugin_tools

        loaded, errors = plugin_tools.load_all(tmp_path)
        assert errors == ()
        assert [p.plugin_id for p in loaded] == ["vool-database"]
        yield tmp_path
    reset_mode_permission_state()
    reset_toolchain_state()


def _create() -> dict:
    from core.skill_tools import create_skill

    return create_skill(
        name=SKILL_REQUEST["name"],
        description=SKILL_REQUEST["description"],
        body=SKILL_REQUEST["body"],
        allowed_tools=SKILL_REQUEST["allowed_tools"],
        triggers=SKILL_REQUEST["triggers"],
    )


def _validate(slug: str) -> dict:
    from core.skill_tools import validate_skill

    return validate_skill(slug)


def _install(slug: str) -> dict:
    from core.skill_tools import install_skill

    return install_skill(slug)


def _selected(user_text: str) -> str:
    """What a subsequent turn's prompt seam actually injects for this request."""

    from core.prompt_normalizer import _tool_intent_catalog_text

    return _tool_intent_catalog_text(family_hint="plugin", user_text=user_text)


def test_draft_validate_install_then_a_later_turn_selects_the_recorded_version(lifecycle_world) -> None:
    # 1. Draft: staged, names no behaviour change, and is NOT loadable yet.
    draft = _create()
    assert draft["status"] == "ok", draft
    slug = draft["slug"]
    assert draft["staged"] is True
    assert MARKER not in _selected("how many rows are in the orders table")

    # 2. Validate with the REAL loader: parses, has a description, and names only
    #    tools that exist (the plugin pack is loaded through the isolated install).
    verdict = _validate(slug)
    assert verdict["status"] == "ok", verdict["problems"]
    assert verdict["unknown_tools"] == []

    # 3. Approved install: moves into the active tree the loader scans every turn,
    #    and the target pack is visible to the catalog (manifest created).
    installed = _install(slug)
    assert installed["status"] == "ok", installed
    assert installed["active"] is True
    installed_path = lifecycle_world / "plugins" / "local-skills" / "skills" / slug / "SKILL.md"
    assert installed_path.is_file()

    # The recorded version is the installed file; a later turn selects IT
    # (provenance: this plugin, this path), not the staging draft.
    from core.plugin_skills import load_skills

    skills = load_skills(lifecycle_world / "plugins" / "local-skills", plugin_id="local-skills")
    matching = [s for s in skills if s.name == verdict["name"]]
    assert matching, "the installed skill must be loadable from the active tree"
    recorded = matching[0]
    assert recorded.path == str(installed_path)
    assert MARKER in recorded.body

    # 4. A SUBSEQUENT TURN: the prompt seam injects the recorded body.
    prompt = _selected("how many rows are in the orders table")
    assert MARKER in prompt
    # A turn the skill does not match gets none of it.
    assert MARKER not in _selected("write a haiku about databases")


def test_validate_is_plugin_aware_through_the_canonical_registry(lifecycle_world) -> None:
    """The PB04 capability gap, CLOSED at integration — the contract it becomes.

    The source lane pinned validate_skill refusing a REGISTERED, DISPATCHABLE plugin
    tool because allowed-tools was checked against the builtin contract map only. The
    integration closure (core/skill_tools.py) reads the canonical registry alongside
    that map, so the same skill now validates against the same authority that
    dispatches it — and a tool that is registered NOWHERE is still refused.
    """

    from core.skill_tools import create_skill, validate_skill

    made = create_skill(
        name="db narrow probe",
        description="Probe skill naming a plugin tool in allowed-tools.",
        body="Call vool-database.query for rows.",
        allowed_tools=["vool-database.query"],
    )
    assert made["status"] == "ok"
    verdict = validate_skill(made["slug"])
    assert verdict["status"] == "ok", verdict["problems"]
    assert verdict["unknown_tools"] == []
    # The tool is registered and dispatchable, and validation now agrees.
    from core.tool_registry import tool_for_intent

    assert tool_for_intent("vool-database.query") is not None

    # Registry-awareness is not open-endedness: a name in NO authority still refuses.
    unknown = create_skill(
        name="db unknown probe",
        description="Probe skill naming a tool that exists nowhere.",
        body="Call vool-database.warp for rows.",
        allowed_tools=["vool-database.warp"],
    )
    assert unknown["status"] == "ok"
    unknown_verdict = validate_skill(unknown["slug"])
    assert unknown_verdict["status"] == "invalid"
    assert any("vool-database.warp" in problem for problem in unknown_verdict["problems"])


def test_disable_then_restart_stops_selection_and_reenable_restores_it(lifecycle_world) -> None:
    from core.plugin_catalog import set_plugin_enabled

    draft = _create()
    assert draft["status"] == "ok"
    assert _validate(draft["slug"])["status"] == "ok"
    assert _install(draft["slug"])["status"] == "ok"
    request = "how many rows are in the orders table"
    assert MARKER in _selected(request)

    # The owner switches the pack off; the next turn (fresh caches = the restart)
    # must not select the skill, and must say why rather than invent silence.
    assert set_plugin_enabled("local-skills", False)
    reset_toolchain_state()
    from core import tool_offer_assembly

    tool_offer_assembly.reset_skill_cache()
    assert MARKER not in _selected(request)

    # A restart alone does not resurrect a disabled skill...
    reset_toolchain_state()
    tool_offer_assembly.reset_skill_cache()
    assert MARKER not in _selected(request)

    # ...re-enabling does, with the same recorded file.
    assert set_plugin_enabled("local-skills", True)
    reset_toolchain_state()
    tool_offer_assembly.reset_skill_cache()
    assert MARKER in _selected(request)
