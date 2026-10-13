"""Native trust must survive hostile author labels and incomplete validation."""
import json

from core import plugin_catalog, skill_tools


def test_external_author_text_cannot_claim_first_party(tmp_path):
    manifest = tmp_path / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"name": "fake", "author": {"name": "VOOL sls_0x"},
                                    "interface": {"developerName": "Parad0x Labs"}}))
    assert plugin_catalog._plugin_entry(tmp_path)["first_party"] is False


def test_tool_contract_failure_cannot_validate_clean(tmp_path, monkeypatch):
    from core import runtime_tool_contracts

    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: test\ndescription: Do a test\nallowed-tools: [read_file]\n---\nRead it.\n")

    def unavailable():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(runtime_tool_contracts, "runtime_tool_contract_map", unavailable)
    result = skill_tools.validate_skill(str(path))
    assert result["status"] == "invalid"
    assert any("unavailable" in problem for problem in result["problems"])


def test_validation_rejects_instructions_the_loader_would_truncate(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: test\ndescription: Do a test\n---\n" + "A" * 8001)
    result = skill_tools.validate_skill(str(path))
    assert result["status"] == "invalid"
    assert any("8000" in problem for problem in result["problems"])
