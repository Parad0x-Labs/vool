"""Settings → Local models speaks plain words over the unchanged certification gate.

1. A real probe run maps to one plain readiness: a model that answers in prose instead of calling
   tools (the qwen2.5:7b report) is "not_suitable"; a timeout or a refused request is
   "check_unfinished", so a slow or closed Ollama is never blamed on the model.
2. The run route and the listing both carry that readiness.
3. The one-click download door only takes a recommended model, only from the owner's own
   session, and only for an Ollama on this computer.
4. The widget shows no certification jargon and keeps every word in the catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from core.local_model_readiness import (
    CHECK_UNFINISHED,
    NEEDS_RECHECK,
    NOT_CHECKED,
    NOT_SUITABLE,
    READY,
    plain_readiness,
    recommended_local_model,
)
from core.local_model_tool_certification import run_local_model_tool_certification
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post
from tests.test_local_model_tool_certification import ProbeExchange, _manifest, _reply

ROOT = Path(__file__).resolve().parents[1]


def _json(response) -> dict:
    return json.loads(bytes(response.body or b"{}").decode("utf-8"))


class _ProseOnly(ProbeExchange):
    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return _reply(content="The sum is 42.")


class _Refused(ProbeExchange):
    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return _reply(status=500)


@pytest.mark.parametrize(
    ("exchange", "expected"),
    [
        (ProbeExchange(), READY),
        (_ProseOnly(), NOT_SUITABLE),
        (ProbeExchange(first_mode="wrong_args"), NOT_SUITABLE),
        (ProbeExchange(continuation_ok=False), NOT_SUITABLE),
        (ProbeExchange(timeout=True), CHECK_UNFINISHED),
        (_Refused(), CHECK_UNFINISHED),
    ],
)
def test_a_real_probe_run_maps_to_one_plain_readiness(tmp_path: Path, exchange, expected) -> None:
    result = run_local_model_tool_certification(_manifest(), exchange=exchange, db_path=tmp_path / "probe.db")
    assert plain_readiness(result) == expected
    # The gate itself is untouched: only a verified run is ready.
    assert (result["state"] == "verified") == (expected == READY)


def test_states_without_a_finished_run_are_not_ready() -> None:
    assert plain_readiness(None) == NOT_CHECKED
    assert plain_readiness({"state": "unknown"}) == NOT_CHECKED
    assert plain_readiness({"state": "stale"}) == NEEDS_RECHECK
    assert plain_readiness({"state": "incompatible"}) == NOT_SUITABLE
    assert plain_readiness({"state": "degraded"}) == CHECK_UNFINISHED


def test_the_recommended_model_fits_the_machine() -> None:
    assert recommended_local_model(32.0)["model_name"] == "qwen3:8b"
    assert recommended_local_model(8.0)["model_name"] == "qwen3:4b"


def test_the_run_route_and_the_listing_carry_readiness(monkeypatch, tmp_path: Path) -> None:
    manifest = _manifest()
    monkeypatch.setattr("core.model_registry.ModelRegistry.get_manifest", lambda self, p, m: manifest)
    monkeypatch.setattr(
        "core.local_model_tool_certification.run_local_model_tool_certification",
        lambda m, timeout_seconds: run_local_model_tool_certification(
            m, exchange=_ProseOnly(), db_path=tmp_path / "run.db"
        ),
    )
    response = dispatch_post(
        path="/api/model-tool-certification/run",
        body={"provider_name": "ollama-local", "model_name": "qwen-test:4b"},
        headers={"Content-Type": "application/json"},
        runtime=RuntimeServices(),
        model_name="vool",
        client_host="127.0.0.1",
        workspace_root_provider=lambda: "/tmp",
    )
    assert response.status == 200
    assert _json(response)["readiness"] == NOT_SUITABLE

    monkeypatch.setattr(
        "core.web.api.runtime.installed_ollama_model_inventory",
        lambda **kwargs: (type("Item", (), {"name": "qwen-test:4b", "size_bytes": 2_500_000_000})(),),
    )
    monkeypatch.setattr(
        "core.local_model_tool_certification.certification_status", lambda _m: {"state": "verified"}
    )
    listing = _json(
        dispatch_get(
            path="/api/models/local", query={}, runtime=RuntimeServices(), model_name="vool",
            client_host="127.0.0.1", headers={},
        )
    )
    assert listing["models"][0]["readiness"] == READY
    assert listing["recommended"]["model_name"] in {"qwen3:8b", "qwen3:4b"}


def test_the_download_door_takes_only_a_recommended_model_from_the_owner(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(
        "core.local_model_readiness.start_recommended_pull",
        lambda name: started.append(name) or {"model": name, "status": "starting", "percent": 0},
    )
    base = dict(
        path="/api/models/local/pull",
        headers={"Content-Type": "application/json"},
        runtime=RuntimeServices(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )
    assert dispatch_post(body={"model_name": "qwen3:8b"}, client_host="10.0.0.4", **base).status == 403
    assert dispatch_post(body={"model_name": "llama3:70b"}, client_host="127.0.0.1", **base).status == 400
    assert dispatch_post(body={"model_name": "qwen3:8b", "x": 1}, client_host="127.0.0.1", **base).status == 400
    monkeypatch.setattr("core.web.api.runtime.ollama_base_url", lambda env=None: "http://10.0.0.9:11434")
    assert dispatch_post(body={"model_name": "qwen3:8b"}, client_host="127.0.0.1", **base).status == 409
    assert started == []
    monkeypatch.setattr("core.web.api.runtime.ollama_base_url", lambda env=None: "http://127.0.0.1:11434")
    ok = dispatch_post(body={"model_name": "qwen3:8b"}, client_host="127.0.0.1", **base)
    assert ok.status == 202
    assert started == ["qwen3:8b"]


def test_the_widget_speaks_plainly_and_every_word_is_in_every_catalog() -> None:
    page = (ROOT / "core" / "vool_settings_page.py").read_text(encoding="utf-8")
    widget = page[page.index("function widgetLocalModels"):]
    widget = widget[: widget.index("\n}\n")]
    code = re.sub(r"/\*.*?\*/", "", widget, flags=re.S)
    literals = re.findall(r"'((?:[^'\\]|\\.)*)'", code)
    visible = " ".join(text for text in literals if " " in text or text[:1].isupper()).lower()
    for word in ("certif", "probe", "degraded", "register", "sealed"):
        assert word not in visible, word
    keys = {"settings.local_models." + k for k in re.findall(r"LM\('([a-z_.]+)'", widget)}
    assert len(keys) > 20
    catalogs = sorted((ROOT / "core" / "i18n" / "catalogs").glob("*.json"))
    assert len(catalogs) == 17
    for path in catalogs:
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        missing = sorted(k for k in keys if k not in messages)
        assert not missing, (path.name, missing)
        for k in keys:
            assert "certif" not in messages[k].lower(), (path.name, k)
