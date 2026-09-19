"""Served Checkpoint B: a detailed comparison is planned per facet, read from dated fixtures, and
published exactly as far as the sources carry it.

Every test runs its own scratch daemon (own home, own port, scripted local model certified by the
production probe) with the test-only fixture transport first on PYTHONPATH: dated synthetic
search results and pages answer the real governed door, every other host is refused, no headless
browser search can run. Assertions are environmental: which queries the runtime issued, which
pages it fetched, and the publication gate's own verdict -- never the prose.

The scripted model restates the fixture facts and, as a negative control, always adds ONE sentence
no fixture supports; that sentence must be withheld on every path.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

import pytest

import tests._reader_served_rig as rig

ROOT = Path(__file__).resolve().parents[1]
# Canonical, relocatable fixture set (synthetic dated sources; body_file names are
# relative to the manifest — no builder-machine paths). Relocated here from the
# excluded internal validation-logs lane so the suite is self-contained.
FIX = ROOT / "tests" / "fixtures" / "comparison_coverage"
SHIM = ROOT / "tests" / "fixture_transport"

CARS = "Now compare the VW Passat and the VW Golf in detail: production periods, sales, regions, engines and prices."
CARS_FACETS = ("production periods", "sales", "regions", "engines", "prices")
FABRICATED = "The Golf was assembled in Australia until 1999."
CARS_REPLY = (
    "Golf production began in 1974 and Passat production in 1973. "
    "Sales: Golf about 37 million units by end of 2024, Passat about 34 million. "
    "Regions: Golf mainly Europe; Passat mainly China and Europe. "
    "Engines: Passat B9 offers a 2.0 TSI with 265 PS; Golf R has 333 PS. "
    "Prices in Germany, August 2026: Golf from 27,180 EUR, Passat Variant from 41,155 EUR. "
    + FABRICATED
)
CARS_CONFLICT_REPLY = (
    "Sales: one source puts cumulative Golf sales at about 37 million units by end of 2024, "
    "another at about 35.5 million by end of 2024. Passat sales are about 34 million units. " + FABRICATED
)
TABLE_REPLY = ("| Facet | VW Golf | VW Passat |\n|---|---|---|\n| Production | since 1974 | since 1973 |\n"
               "| Sales | about 37 million units by end of 2024 | about 34 million units by end of 2024 |\n"
               "| Regions | mainly Europe | mainly China and Europe |")
SHORT_REPLY = "Golf: produced since 1974, about 37 million sold. Passat: produced since 1973, about 34 million sold. " + FABRICATED
CITIES = "Compare Vilnius and Riga: population, area, founding year and airport passengers."
CITIES_REPLY = ("Vilnius had 605,270 people in 2025 and Riga 605,802. Vilnius covers 401 square kilometres, Riga 307. "
                "Vilnius was first mentioned in 1323 and Riga was founded in 1201. Vilnius Airport handled 7.1 million passengers in 2025, "
                "Riga Airport 8.4 million. Riga has a metro line since 2019.")


def _reply_fn(default: str):
    def fn(body):
        msgs = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        last = " ".join(str(m.get("content") or "") for m in msgs[-2:]).lower()
        if "table" in last:
            return TABLE_REPLY
        if "shorter" in last or "briefer" in last:
            return SHORT_REPLY
        return default
    return fn


def _body(answer: str) -> str:
    """The published body: everything before the withheld notice and the closure section, which
    legitimately quote withheld sentences and unserved slots verbatim."""
    text = str(answer or "")
    for marker in ("Withheld from this answer:", "Also set aside:", "Could not be answered:"):
        index = text.find(marker)
        if index >= 0:
            text = text[:index]
    return text


class _Drive:
    def __init__(self, tmp_path: Path, manifest: Path, reply: str):
        self.transport_log = tmp_path / "transport.log"
        self.gate_log = tmp_path / "gate.log"
        env_extra = {
            "PYTHONPATH": os.pathsep.join([str(SHIM), str(rig.REPO_ROOT), str(rig.READER_DEPS)]),
            "VOOL_FIXTURE_TRANSPORT_MANIFEST": str(manifest),
            "VOOL_FIXTURE_TRANSPORT_LOG": str(self.transport_log),
            "VOOL_FIXTURE_GATE_LOG": str(self.gate_log),
            "ALLOW_BROWSER_FALLBACK": "0",
            "WEB_SEARCH_PROVIDER_ORDER": "google_html",
        }
        self.provider = rig.CapturingProvider(default=reply, reply_fn=_reply_fn(reply))
        self.provider.__enter__()
        self.daemon = rig.ServedDaemon(tmp_path / "home", provider=self.provider, env_extra=env_extra)
        self.daemon.register_provider()
        self.daemon.start()
        cert = self.daemon.certify()
        assert cert.get("state") == "verified", cert
        self.session = rig.canonical_session("b-served-" + uuid.uuid4().hex[:8])
        self._log_pos = 0
        self._gate_pos = 0

    def close(self) -> None:
        self.daemon.stop()
        self.provider.__exit__(None, None, None)

    def turn(self, text: str) -> dict:
        body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True,
                "session_id": self.session, "turn_id": uuid.uuid4().hex, "model": self.daemon.model,
                "model_selection": "sticky", "mode": "ask"}
        req = Request(f"{self.daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        answer = []
        with urlopen(req, timeout=900) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message")
                if isinstance(msg, dict) and msg.get("content"):
                    answer.append(str(msg["content"]))
        lines = self.transport_log.read_text().splitlines() if self.transport_log.exists() else []
        rows = [json.loads(l) for l in lines[self._log_pos:]]
        self._log_pos = len(lines)
        queries, pages = [], []
        for row in rows:
            u = urlparse(row["url"])
            host = u.hostname or ""
            if host.startswith("search."):
                q = parse_qs(u.query)
                queries.append(unquote((q.get("p") or q.get("q") or [""])[0]).lower())
            elif host.endswith("-fixture.org"):
                pages.append(u.path)
        gate_lines = self.gate_log.read_text().splitlines() if self.gate_log.exists() else []
        gates = [json.loads(l) for l in gate_lines[self._gate_pos:]]
        self._gate_pos = len(gate_lines)
        verdict = gates[-1] if gates else {}
        return {"answer": "".join(answer), "queries": queries, "pages": pages, "gate": verdict.get("publication") or {},
                "gate_input": verdict.get("content", ""), "transport": [(r["rule"], r["outcome"]) for r in rows]}


def test_complete_evidence_plans_every_facet_and_publishes_what_the_sources_carry(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_complete.json", CARS_REPLY)
    try:
        out = drive.turn(CARS)
    finally:
        drive.close()
    for facet in CARS_FACETS:
        assert any(facet in q and "passat" in q and "golf" in q for q in out["queries"]), (facet, out["queries"])
    assert not any("topic comparison" in q for q in out["queries"]), out["queries"]
    hosts = {p.split("/")[1] for p in out["pages"] if p}
    assert {"passat-production", "golf-production", "vw-model-sales", "vw-regions", "vw-germany-prices-2026"} <= set(p.strip("/") for p in out["pages"]), out["pages"]
    gate = out["gate"]
    assert gate.get("state") in {"partial", "published"}, gate
    assert int(gate.get("supported_claim_count") or 0) >= 5, gate
    assert any(FABRICATED[:25] in w for w in (gate.get("withheld_claims") or [])), gate
    assert FABRICATED not in _body(out["answer"]), _body(out["answer"])[:300]
    assert "37 million" in _body(out["answer"]) and "1974" in _body(out["answer"]), out["answer"][:300]
    # Closure accounting: the comparison shipped in part, so it is not "Could not be answered".
    assert "Could not be answered:" not in out["answer"], out["answer"][-600:]
    assert "Answered in part:" in out["answer"], out["answer"][-600:]


def test_partial_evidence_withholds_the_facet_no_source_carries(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_partial.json", CARS_REPLY)
    try:
        out = drive.turn(CARS)
    finally:
        drive.close()
    assert any("engines" in q for q in out["queries"]), out["queries"]
    assert not any("engines" in p for p in out["pages"]), out["pages"]
    gate = out["gate"]
    assert gate.get("state") in {"partial", "published"}, gate
    withheld = " ".join(gate.get("withheld_claims") or [])
    assert "265 PS" in withheld or "333 PS" in withheld, gate
    assert "265 PS" not in _body(out["answer"]), _body(out["answer"])[:400]
    assert "37 million" in _body(out["answer"]), out["answer"][:300]


def test_conflicting_sources_both_survive_and_the_fabrication_does_not(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_conflict.json", CARS_CONFLICT_REPLY)
    try:
        out = drive.turn("Compare the VW Passat and the VW Golf in detail: sales and production periods.")
    finally:
        drive.close()
    assert any("golf-sales-total" in p for p in out["pages"]), out["pages"]
    gate = out["gate"]
    assert gate.get("state") in {"partial", "published"}, gate
    body = _body(out["answer"])
    assert "37 million" in body and "35.5 million" in body, out["answer"][:400]
    assert FABRICATED not in body


def test_unavailable_search_yields_no_fabricated_comparison(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_unavailable.json", CARS_REPLY)
    try:
        out = drive.turn(CARS)
    finally:
        drive.close()
    assert not out["pages"], out["pages"]
    assert "37 million" not in out["answer"] and "1974" not in out["answer"], out["answer"][:400]
    lowered = out["answer"].lower()
    assert any(word in lowered for word in ("retriev", "source", "search", "couldn't", "can't", "could not obtain")), out["answer"][:300]


def test_table_and_shorter_follow_ups_inherit_the_published_support(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_complete.json", CARS_REPLY)
    try:
        first = drive.turn(CARS)
        table = drive.turn("put that in a table")
        shorter = drive.turn("shorter please")
    finally:
        drive.close()
    assert first["gate"].get("state") in {"partial", "published"}, first["gate"]
    assert not table["queries"] and not shorter["queries"], "a re-presentation retrieves nothing new"
    assert table["gate"].get("state") in {"partial", "published"}, table["gate"]
    assert "| Production | since 1974 | since 1973 |" in table["answer"], table["answer"][:400]
    assert shorter["gate"].get("state") in {"partial", "published"}, shorter["gate"]
    assert "37 million" in _body(shorter["answer"]), shorter["answer"][:300]
    assert FABRICATED not in _body(shorter["answer"]), shorter["answer"][:300]
    assert any(FABRICATED[:25] in w for w in (shorter["gate"].get("withheld_claims") or [])), shorter["gate"]


def test_a_structurally_different_comparison_is_planned_and_gated_the_same_way(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_cities_complete.json", CITIES_REPLY)
    try:
        out = drive.turn(CITIES)
    finally:
        drive.close()
    for facet in ("population", "area", "founding year", "airport passengers"):
        assert any(facet in q and "vilnius" in q and "riga" in q for q in out["queries"]), (facet, out["queries"])
    gate = out["gate"]
    assert gate.get("state") in {"partial", "published"}, gate
    assert int(gate.get("supported_claim_count") or 0) >= 3, gate
    assert "metro" not in _body(out["answer"]).lower(), out["answer"][:400]
    assert "605,270" in _body(out["answer"]) or "605270" in _body(out["answer"]), out["answer"][:400]
