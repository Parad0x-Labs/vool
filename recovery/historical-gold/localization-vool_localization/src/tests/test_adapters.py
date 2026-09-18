"""Adapter round-trip tests: every adapter exports parseable files per locale."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT))

from vool_localization.adapters import ADAPTERS
from vool_localization.catalog import Catalog

REGISTRY = PKG_ROOT / "registry"


@pytest.fixture(scope="module")
def cat():
    return Catalog.load(REGISTRY)


@pytest.mark.parametrize("adapter_name", sorted(ADAPTERS))
def test_adapter_exports_for_pt_br(tmp_path, cat, adapter_name):
    adapter = ADAPTERS[adapter_name](cat)
    files = adapter.export("pt-BR", tmp_path / adapter_name)
    assert files, f"{adapter_name} exported nothing"
    for f in files:
        assert f.exists() and f.stat().st_size > 50


def test_android_output_is_valid_xml_and_namespaced(tmp_path, cat):
    files = ADAPTERS["android"](cat).export("pt-BR", tmp_path)
    strings_xml = next(f for f in files if f.name == "strings.xml")
    root = ET.parse(strings_xml).getroot()
    names = [el.get("name") for el in root.findall("string")]
    assert "desktop_dashboard_tab_overview" in names
    assert all("." not in n for n in names)


def test_apple_strings_parses(tmp_path, cat):
    files = ADAPTERS["apple"](cat).export("pt-BR", tmp_path)
    localizable = next(f for f in files if f.name.endswith(".strings"))
    lines = localizable.read_text(encoding="utf-8").splitlines()
    pairs = [l for l in lines if '" = "' in l]
    assert any('"desktop.cli.exit_hint"' in l for l in pairs)


def test_windows_resw_valid_xml(tmp_path, cat):
    files = ADAPTERS["windows"](cat).export("pt-BR", tmp_path)
    resw = next(f for f in files if f.name == "Resources.resw")
    root = ET.parse(resw).getroot()
    assert root.find("data[@name='common.ok']") is not None


def test_web_json_carries_provenance(tmp_path, cat):
    files = ADAPTERS["web-json"](cat).export("pt-BR", tmp_path)
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["generated"] is True
    assert payload["human_reviewed"] is False
    assert payload["direction"] == "ltr"


def test_voice_lexicon_speak_as(tmp_path, cat):
    files = ADAPTERS["voice"](cat).export("pt-BR", tmp_path)
    lex = json.loads(files[0].read_text(encoding="utf-8"))
    assert lex["speak_as"]["blocked_false_claim"] == "falso pedido bloqueado"
