"""The model-market feed: typed events from REAL catalog diffs, never invention.

Drives the real module under an isolated store; catalog rows are stubbed model objects (the
same attribute surface `_rows_snapshot` reads), watched-set sources are stubbed at their seams.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        return out

    monkeypatch.setattr("core.model_market_feed.data_path", _patched)
    return tmp_path


def _model(model_id: str, prompt: float, completion: float, free: bool,
           name: str = "", context: int = 0, tools: bool = False, images: bool = False):
    return SimpleNamespace(
        model_id=model_id,
        prompt_usd_per_token=prompt / 1_000_000,
        completion_usd_per_token=completion / 1_000_000,
        _free=free,
        name=name,
        context_length=context,
        supported_parameters=("tools",) if tools else (),
        input_modalities=("text", "image") if images else ("text",),
    )


@pytest.fixture()
def feed(isolated_home, monkeypatch):
    from core import model_market_feed as mmf

    monkeypatch.setattr("core.openrouter_catalog.model_is_free", lambda m: bool(m._free))
    monkeypatch.setattr(mmf, "_watched_ids", lambda: {"openai/gpt-4.1-mini", "vendor/gone"})
    return mmf


def test_first_refresh_snapshots_but_emits_nothing(feed) -> None:
    events = feed.record_catalog_refresh([_model("openai/gpt-4.1-mini", 0.4, 1.6, False)])
    assert events == [], "no prior snapshot means nothing to diff — a fresh feed stays silent"
    assert feed.read_events() == []


def test_rise_fall_freepaid_delist_and_new_free(feed) -> None:
    feed.record_catalog_refresh(
        [
            _model("openai/gpt-4.1-mini", 0.4, 1.6, False),
            _model("vendor/gone", 1.0, 2.0, False),
            _model("vendor/was-paid", 3.0, 9.0, False, name="Was Paid Pro", context=131072, tools=True),
        ]
    )
    events = feed.record_catalog_refresh(
        [
            _model("openai/gpt-4.1-mini", 1.6, 6.4, False),   # watched rise
            _model("vendor/was-paid", 0.0, 0.0, True, name="Was Paid Pro", context=131072, tools=True),  # newly free
            _model("vendor/brand-new", 0.0, 0.0, True, name="Brand New", context=8192),  # brand new free
        ]
    )
    kinds = sorted(e["type"] for e in events)
    # The aggregate count event is gone: ONE fully-identified event per newly-free model
    # (product/desktop-usability-20260917 — "N new free models" named nothing).
    assert kinds == ["model_delisted", "new_free_model", "new_free_model", "price_increased"]
    rise = next(e for e in events if e["type"] == "price_increased")
    assert rise["model"] == "openai/gpt-4.1-mini"
    assert rise["before"]["prompt_usd_per_m"] == 0.4 and rise["after"]["prompt_usd_per_m"] == 1.6
    fresh = {e["model"]: e for e in events if e["type"] == "new_free_model"}
    assert set(fresh) == {"vendor/was-paid", "vendor/brand-new"}
    identified = fresh["vendor/was-paid"]
    assert identified["display_name"] == "Was Paid Pro"
    assert identified["provider_id"] == "openrouter"
    assert identified["prices"] == {"input_usd_per_m": 0.0, "output_usd_per_m": 0.0}
    assert identified["free_basis"] == "all_published_prices_zero"
    assert identified["context_length"] == 131072
    assert identified["supports_tools"] is True
    assert identified["observed_at"] and identified["evidence_url"].startswith("https://openrouter.ai")
    unnamed = fresh["vendor/brand-new"]
    assert unnamed["display_name"] == "Brand New" and unnamed["context_length"] == 8192
    stored = feed.read_events()
    assert [e["type"] for e in stored] == [e["type"] for e in events]
    assert all(e["seq"] >= 1 for e in stored)
    # Cursor semantics: after the last seq, nothing returns.
    assert feed.read_events(after=stored[-1]["seq"]) == []


def test_unwatched_models_never_emit_per_row_events(feed) -> None:
    feed.record_catalog_refresh([_model("noise/model", 1.0, 1.0, False)])
    events = feed.record_catalog_refresh([_model("noise/model", 9.0, 9.0, False)])
    assert events == [], "an unwatched model's price change is not the operator's alarm"


def test_price_history_appends_only_on_change(feed) -> None:
    feed.record_catalog_refresh([_model("openai/gpt-4.1-mini", 0.4, 1.6, False)])
    feed.record_catalog_refresh([_model("openai/gpt-4.1-mini", 0.4, 1.6, False)])
    feed.record_catalog_refresh([_model("openai/gpt-4.1-mini", 1.6, 6.4, False)])
    rows = feed.price_history("openai/gpt-4.1-mini")
    assert [r["prompt_usd_per_m"] for r in rows] == [0.4, 1.6]


def test_feed_failure_never_breaks_the_refresh(feed, monkeypatch) -> None:
    monkeypatch.setattr(feed, "_rows_snapshot", lambda models: (_ for _ in ()).throw(RuntimeError("boom")))
    assert feed.record_catalog_refresh([_model("openai/gpt-4.1-mini", 0.4, 1.6, False)]) == []


def test_market_endpoints_are_owner_local(isolated_home) -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    def _get(path, host, query=None):
        res = dispatch_get(
            path=path, query=query or {}, runtime=RuntimeServices(display_name="N"),
            model_name="vool", client_host=host,
        )
        return res.status, json.loads(res.body.decode("utf-8"))

    for path in ("/api/cloud/market-events", "/api/cloud/price-history"):
        status, payload = _get(path, "203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required"
    status, payload = _get("/api/cloud/market-events", "127.0.0.1")
    assert status == 200 and payload["ok"] is True and payload["events"] == []
    status2, payload2 = _get("/api/cloud/price-history", "127.0.0.1", {"model": ["x/y"]})
    assert status2 == 200 and payload2["history"] == []


def test_memory_endpoints_owner_local_and_id_scoped(tmp_path, monkeypatch) -> None:
    """GET lists real rows; POST forgets exactly one record; both owner-local."""
    import json as _json

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    def _get(path, host, query=None):
        res = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="N"),
                           model_name="vool", client_host=host)
        return res.status, _json.loads(res.body.decode("utf-8"))

    def _post(path, body, host):
        res = dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                            runtime=RuntimeServices(display_name="N"), model_name="vool",
                            workspace_root_provider=lambda: "/tmp", client_host=host)
        return res.status, _json.loads(res.body.decode("utf-8"))

    status, payload = _get("/api/memory/entries", "203.0.113.9")
    assert status == 403 and payload.get("error") == "owner_local_required"
    status, payload = _post("/api/memory/forget", {"record_id": "x"}, "203.0.113.9")
    assert status == 403

    rows = [
        {"record_id": "keep-1", "fact": "keeps"},
        {"record_id": "drop-1", "fact": "drops"},
    ]
    # Memory reads are governed per chat namespace; a caller with no chat is answered from every
    # ACTIVE namespace, each read under its own policy. Give the route one to read under.
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace("openclaw:" + "a" * 20)
    monkeypatch.setattr("core.memory.entries.list_memory_entries", lambda **_k: rows)
    status, payload = _get("/api/memory/entries", "127.0.0.1")
    assert status == 200 and [e["record_id"] for e in payload["entries"]] == ["keep-1", "drop-1"]

    calls = []
    monkeypatch.setattr("core.memory.entries.forget_memory_record",
                        lambda record_id: calls.append(record_id) or record_id == "drop-1")
    status, payload = _post("/api/memory/forget", {"record_id": "drop-1"}, "127.0.0.1")
    assert status == 200 and payload["removed"] is True and calls == ["drop-1"]
    status, payload = _post("/api/memory/forget", {"record_id": "ghost"}, "127.0.0.1")
    assert status == 200 and payload["removed"] is False
    status, payload = _post("/api/memory/forget", {"record_id": "x", "extra": 1}, "127.0.0.1")
    assert status == 400


def test_forget_memory_record_is_id_scoped(tmp_path, monkeypatch) -> None:
    """The real remover deletes exactly one row and leaves twins with similar text intact."""
    import core.memory.entries as entries

    store = tmp_path / "memory_entries.jsonl"
    rows = [
        {"record_id": "a1", "text": "likes dark UIs"},
        {"record_id": "a2", "text": "likes dark UIs"},
    ]
    store.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(entries, "memory_entries_path", lambda: store)
    monkeypatch.setattr(entries, "_ensure_memory_files", lambda: None)
    assert entries.forget_memory_record("a1") is True
    kept = [__import__("json").loads(line) for line in store.read_text().splitlines()]
    # The P0 erasure law keeps the erased row as a durable TOMBSTONE (status "erased", salted digest);
    # what the reader sees is every live row. The twin with identical text must be among them.
    live = [r["record_id"] for r in kept if r.get("status") != "erased"]
    assert live == ["a2"], "the twin with identical text must survive"
    assert [r["record_id"] for r in kept if r.get("status") == "erased"] == ["a1"], "the erased row is a tombstone, not a hole"
    assert entries.forget_memory_record("missing") is False
