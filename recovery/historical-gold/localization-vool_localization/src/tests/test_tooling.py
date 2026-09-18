"""Extraction + audit tooling tests."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]      # swarm-localization worktree root
LOC = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(LOC))


def test_extractor_finds_real_repo_strings(tmp_path):
    from tools.extract_strings import extract
    records = extract(REPO)
    texts = {r["text"] for r in records}
    assert len(records) > 400
    assert "Type /exit to quit." in texts                       # apps/vool_chat.py
    assert any(r["surface"] == "installer" for r in records)
    assert any(r["file"].startswith("core/dashboard/") for r in records)


def test_audit_passes_on_current_registry(tmp_path):
    from tools.audit_strings import audit
    report, ok = audit(LOC / "registry")
    assert ok, report
    assert report["baseline_keys"] >= 40
    br = report["locales"]["pt-BR"]
    assert br["missing_keys"] == 0            # pt-BR example is complete
    assert br["placeholder_errors"] == []


def test_audit_catches_placeholder_mismatch(tmp_path):
    from tools.audit_strings import audit
    reg = tmp_path / "registry"
    reg.mkdir()
    src = json.loads((LOC / "registry" / "en.json").read_text())
    (reg / "en.json").write_text(json.dumps(src))
    bad = {"_meta": {"locale": "xx-X", "generated": True}, "strings": {
        **{k: v for k, v in list(src["strings"].items())[:5]},
        "desktop.cli.agent_ready": "pronto sem variavel",
    }}
    (reg / "xx-X.json").write_text(json.dumps(bad))
    report, ok = audit(reg)
    assert not ok
    assert report["locales"]["xx-X"]["placeholder_errors"]


def test_pseudo_locale_generator_deterministic():
    from tools.gen_pseudo_locale import REGISTRY
    import importlib
    from vool_localization.pseudo import pseudolocalize
    sample = pseudolocalize("Cancel")
    assert sample == pseudolocalize("Cancel")
    assert REGISTRY.exists()
