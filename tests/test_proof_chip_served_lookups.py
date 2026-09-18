"""Served proof: a live-quote turn's chip counts its lookups, its unique sources, and its failures.

A scratch daemon (own home/port, scripted local model) with the test-only urlopen fixture shim
first on its PYTHONPATH: dated synthetic CoinGecko and Yahoo bodies, every other host refused. The
turn runs through the real front door, the real live-data/conductor lanes, the real governed
network door (`core.remote_fetch_policy.open_remote`) and the real proof route.

Three shapes the delivered build rendered identically as `0 actions · 0 sources · VERIFIED`:
* two markets from two organisations -> `2 sources`, `2/2 lookups`;
* one market's source down (HTTP 429) -> the label names the failure, the down source is not a source;
* two coins from ONE organisation -> `1 source`, `2/2 lookups`.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

import tests._reader_served_rig as rig

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "validation-logs" / "comparison-truth-overnight-20260906" / "fixtures" / "quotes_manifest.json"
SHIM = ROOT / "tests" / "fixture_transport"


def _daemon(tmp_path: Path, provider, manifest: Path):
    log = tmp_path / "fixture_transport.log"
    env_extra = {
        "PYTHONPATH": os.pathsep.join([str(SHIM), str(rig.REPO_ROOT), str(rig.READER_DEPS)]),
        "VOOL_FIXTURE_TRANSPORT_MANIFEST": str(manifest),
        "VOOL_FIXTURE_TRANSPORT_LOG": str(log),
    }
    daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra=env_extra)
    daemon.register_provider()
    daemon.start()
    return daemon, log


def _turn(daemon, question: str) -> tuple[str, dict, str]:
    session = rig.canonical_session("served-lookups-" + uuid.uuid4().hex[:8])
    body = {
        "messages": [{"role": "user", "content": question}], "stream": True, "stream_task_events": True,
        "session_id": session, "turn_id": uuid.uuid4().hex, "model": "reader-drive:stub",
        "model_selection": "sticky", "mode": "ask",
    }
    req = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    request_id, text = None, []
    with urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            commit = obj.get("vool_response_commit")
            if isinstance(commit, dict) and commit.get("request_id"):
                request_id = str(commit["request_id"])
            msg = obj.get("message")
            if isinstance(msg, dict) and msg.get("content"):
                text.append(str(msg["content"]))
    assert request_id, "the served turn committed no request id"
    with urlopen(f"{daemon.base_url}/api/chat/proof?{urlencode({'session': session, 'request_id': request_id})}", timeout=60) as r:
        proof = json.loads(r.read().decode())
    return request_id, proof, "".join(text)


def _transport(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _faulted_manifest(tmp_path: Path, rule_name: str, status: int) -> Path:
    manifest = json.loads(MANIFEST.read_text())
    for rule in manifest["rules"]:
        if rule["name"] == rule_name:
            rule.pop("json", None)
            rule["status"] = status
            rule["reason"] = "Too Many Requests"
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest))
    return target


@pytest.mark.skipif(not MANIFEST.exists(), reason="fixture manifest missing")
def test_two_markets_two_organisations_count_two_sources_and_two_lookups(tmp_path):
    with rig.CapturingProvider(default="I read the quotes above.") as provider:
        daemon, log = _daemon(tmp_path, provider, MANIFEST)
        try:
            _, proof, answer = _turn(daemon, "What is the bitcoin price in USD and the gold price per ounce right now?")
        finally:
            daemon.stop()
    fetched = [(row["rule"], row["outcome"]) for row in _transport(log)]
    assert ("yahoo-gold", "http_200") in fetched and ("coingecko-simple-price", "http_200") in fetched, fetched
    assert all(row["outcome"] != "refused_unknown_host" or "127.0.0.1" in row["url"] for row in _transport(log)), fetched
    compact = proof["compact"]
    assert compact["sources"] == 2, proof["expanded"]["sources"]
    assert compact["coverage"]["label"] == "2/2 lookups", compact["coverage"]
    assert compact["coverage"]["observations"] == {"total": 2, "succeeded": 2, "failed": 0, "cached": 0, "pending": 0}
    # Every action is counted once: the fact ledger's rows and the node completions are unioned by id.
    assert compact["actions"] == len(proof["expanded"]["actions"]), (compact, proof["expanded"]["actions"])
    hosts = sorted({row["host"] for row in proof["expanded"]["sources"] if row["host"]})
    assert any("yahoo" in h for h in hosts) and any("coingecko" in h for h in hosts), hosts
    assert "4,476.60" in answer and "63,250.00" in answer, answer[:400]


@pytest.mark.skipif(not MANIFEST.exists(), reason="fixture manifest missing")
def test_one_source_down_is_named_and_is_not_a_source(tmp_path):
    with rig.CapturingProvider(default="I read the quotes above.") as provider:
        daemon, log = _daemon(tmp_path, provider, _faulted_manifest(tmp_path, "yahoo-gold", 429))
        try:
            _, proof, answer = _turn(daemon, "What is the bitcoin price in USD and the gold price per ounce right now?")
        finally:
            daemon.stop()
    fetched = [(row["rule"], row["outcome"]) for row in _transport(log)]
    assert ("yahoo-gold", "http_429") in fetched, fetched
    compact = proof["compact"]
    assert compact["coverage"]["observations"]["failed"] == 1, compact["coverage"]
    assert compact["coverage"]["label"].startswith("1 of 2 lookups failed"), compact["coverage"]
    assert compact["sources"] == 1, proof["expanded"]["sources"]
    failed = [row for row in proof["expanded"]["observations"] if not row["ok"]]
    assert failed and failed[0]["operation"] == "market_quote", failed


@pytest.mark.skipif(not MANIFEST.exists(), reason="fixture manifest missing")
def test_two_coins_from_one_organisation_count_one_source(tmp_path):
    with rig.CapturingProvider(default="I read the quotes above.") as provider:
        daemon, log = _daemon(tmp_path, provider, MANIFEST)
        try:
            _, proof, _answer = _turn(daemon, "What are the bitcoin and ethereum prices in USD right now?")
        finally:
            daemon.stop()
    compact = proof["compact"]
    assert compact["coverage"]["observations"]["succeeded"] == 2, compact["coverage"]
    assert compact["sources"] == 1, proof["expanded"]["sources"]
    assert compact["coverage"]["label"] == "2/2 lookups", compact["coverage"]
