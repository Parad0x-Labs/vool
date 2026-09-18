"""Offline self-tests for the served-reality bench itself.

These prove the bench's own instruments before any product run: schema
fidelity, wire-byte retention, stub determinism, failure classification,
skip-is-failure semantics, mutation anchors, and the CAS verifier. They need
no daemon and no network (loopback only).

Run:
    python -m ops.served_reality.selftest           # all checks, exit 0/1
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_checks: list[tuple[str, callable]] = []


def check(name: str):
    def _wrap(fn):
        _checks.append((name, fn))
        return fn

    return _wrap


# ---------------------------------------------------------------------------
# schema fidelity
# ---------------------------------------------------------------------------

@check("case-result schema document parses and enumerates the contract")
def _schema_docs_parse() -> None:
    for name in ("case_result.schema.json", "run_manifest.schema.json"):
        doc = json.loads((REPO_ROOT / "ops" / "served_reality" / "schemas" / name).read_text())
        assert doc.get("$schema", "").startswith("https://json-schema.org/")
        assert doc.get("required"), name


@check("CaseResult serializes with the identity/verdict/failure contract")
def _case_result_contract() -> None:
    from ops.served_reality.schema import (
        FAILURE_CLASSES,
        VERDICT_FAIL,
        VERDICT_PASS,
        VERDICT_SKIP_EXPECTED,
        VERDICT_SKIP_UNEXPECTED,
        CaseResult,
        TurnIdentity,
    )

    result = CaseResult(
        case_id="selftest",
        title="selftest",
        required=True,
        verdict=VERDICT_PASS,
    )
    result.identities.append(TurnIdentity(request_id="req:1", session_id="s", model_lane="vllm-local:x"))
    result.assertion("demo", True, expected=True, observed=True)
    payload = result.to_dict()
    for key in ("case_id", "verdict", "is_failure", "assertions", "identities", "wire_refs", "schema_version"):
        assert key in payload, key
    assert payload["is_failure"] is False
    assert set(FAILURE_CLASSES) == {"VOOL", "PROVIDER", "MODEL", "HARNESS"}


@check("a skipped required case or unexpected skip is a FAILURE")
def _skip_semantics() -> None:
    from ops.served_reality.schema import (
        VERDICT_SKIP_EXPECTED,
        VERDICT_SKIP_UNEXPECTED,
        CaseResult,
    )

    unexpected = CaseResult(case_id="x", title="x", required=True, verdict=VERDICT_SKIP_UNEXPECTED)
    assert unexpected.is_failure is True
    required_skip = CaseResult(case_id="x", title="x", required=True, verdict=VERDICT_SKIP_EXPECTED, reason="live_provider_not_enabled")
    assert required_skip.is_failure is True, "a required case can never skip"
    optional_skip = CaseResult(case_id="x", title="x", required=False, verdict=VERDICT_SKIP_EXPECTED, reason="live_provider_not_enabled")
    assert optional_skip.is_failure is False


# ---------------------------------------------------------------------------
# classification truth table
# ---------------------------------------------------------------------------

@check("failure classification names exactly one owner per failure type")
def _classification() -> None:
    from ops.served_reality.classify import (
        BenchError,
        ModelExpectationError,
        ProductAssertionError,
        ProviderExpectationError,
        classify_exception,
    )
    from ops.served_reality.schema import (
        FAILURE_CLASS_HARNESS,
        FAILURE_CLASS_MODEL,
        FAILURE_CLASS_PROVIDER,
        FAILURE_CLASS_VOOL,
    )

    assert classify_exception(BenchError("boom")) == FAILURE_CLASS_HARNESS
    assert classify_exception(ProductAssertionError("boom")) == FAILURE_CLASS_VOOL
    assert classify_exception(ProviderExpectationError("boom")) == FAILURE_CLASS_PROVIDER
    assert classify_exception(ModelExpectationError("boom")) == FAILURE_CLASS_MODEL
    assert classify_exception(ValueError("unattributable")) == FAILURE_CLASS_HARNESS, (
        "an unattributable failure is a bench defect, visible as one"
    )


# ---------------------------------------------------------------------------
# wire fidelity (loopback only)
# ---------------------------------------------------------------------------

@check("wire client retains exact bytes and redacts credential values")
def _wire_fidelity() -> None:
    from ops.served_reality.provider_stub import ProviderStub, StubPlan
    from ops.served_reality.wire import WireClient, WireLog

    with tempfile.TemporaryDirectory(prefix="sr_selftest_") as tmp:
        stub = ProviderStub(model_id="selftest-model")
        port = stub.start()
        StubPlan().answer("HELLO-EXACT-BYTES-42").apply(stub)
        try:
            log = WireLog(Path(tmp), "selftest")
            client = WireClient(f"http://127.0.0.1:{port}", log, default_timeout_s=10.0)
            exchange = client.request(
                "POST",
                "/v1/chat/completions",
                body={"messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer SECRET-NEVER-RETAINED"},
            )
            assert exchange.status == 200, exchange.error
            assert exchange.response_body is not None
            assert b"HELLO-EXACT-BYTES-42" in exchange.response_body
            public = exchange.to_public_dict()
            assert "SECRET-NEVER-RETAINED" not in json.dumps(public), "credential values must never survive in wire records"
            assert public["request_headers"]["authorization"].startswith("<redacted")
            assert "HELLO-EXACT-BYTES-42" in public["response_body"]
            log.close()
        finally:
            stub.stop()


@check("provider stub is deterministic and injects exactly the scripted failure")
def _stub_determinism() -> None:
    from ops.served_reality.provider_stub import ProviderStub, StubPlan

    stub = ProviderStub(model_id="det-model")
    port = stub.start()
    StubPlan().fail(429, "quota exhausted", match="EXHAUST").answer("OK-DEFAULT").apply(stub)
    try:

        def _call(text: str):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps({"messages": [{"role": "user", "content": text}]}).encode(),
                headers={"content-type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        for _ in range(3):
            status, body = _call("EXHAUST me")
            assert status == 429, status
            assert b"quota exhausted" in body
        status, body = _call("ordinary")
        assert status == 200 and b"OK-DEFAULT" in body
        # ollama protocol too
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/chat",
            data=json.dumps({"messages": [{"role": "user", "content": "ordinary"}]}).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())
            assert payload["message"]["content"] == "OK-DEFAULT"
            assert payload["done"] is True
    finally:
        stub.stop()


# ---------------------------------------------------------------------------
# mutation anchors (loud staleness detection)
# ---------------------------------------------------------------------------

@check("app-patch mutation anchors still apply to the product sources")
def _mutation_anchors() -> None:
    from ops.served_reality.mutations import MUTATION_CONTROLS, _M2_ANCHOR, _M2_REPLACEMENT, _M3_ANCHOR, _M3_REPLACEMENT, anchor_path_for

    for mutation_id, anchor, replacement in (
        ("m2-bypassed-permission", _M2_ANCHOR, _M2_REPLACEMENT),
        ("m3-fabricated-tool-success", _M3_ANCHOR, _M3_REPLACEMENT),
    ):
        path = REPO_ROOT / anchor_path_for(mutation_id)
        text = path.read_text(encoding="utf-8")
        count = text.count(anchor)
        assert count == 1, (
            f"{mutation_id}: anchor found {count}x in {path.name} — the patch site is stale; "
            "the control would fail loudly rather than silently pass"
        )
        # and the replacement is a superset edit (anchor text preserved inside)
        assert anchor.splitlines()[0] in replacement
    assert len(MUTATION_CONTROLS) == 6, "six mutation controls are the contract"


# ---------------------------------------------------------------------------
# CAS verifier both directions
# ---------------------------------------------------------------------------

@check("cas verifier flags corrupted chunks and clears clean stores")
def _cas_verifier() -> None:
    from ops.served_reality.bench import BenchRig

    with tempfile.TemporaryDirectory(prefix="sr_cas_") as tmp:
        rig = BenchRig.__new__(BenchRig)  # no daemon; only the home path is used
        rig.home = Path(tmp)
        assert rig.cas_verify_all() == [], "empty store is clean"
        chunk_root = rig.cas_chunk_root() / "ab" / "cd"
        chunk_root.mkdir(parents=True)
        good = b"good bytes"
        (chunk_root / hashlib.sha256(good).hexdigest()).write_bytes(good)
        assert rig.cas_verify_all() == [], "content-addressed file is clean"
        (chunk_root / ("0" * 64)).write_bytes(b"corrupted bytes")
        mismatches = rig.cas_verify_all()
        assert len(mismatches) == 1 and mismatches[0]["named"] == "0" * 64


# ---------------------------------------------------------------------------
# runner truth: results/manifest persistence
# ---------------------------------------------------------------------------

@check("run artifacts persist machine-readable results and manifest")
def _persistence() -> None:
    from ops.served_reality.schema import (
        VERDICT_FAIL,
        CaseResult,
        RunManifest,
        new_run_id,
        write_results,
    )

    with tempfile.TemporaryDirectory(prefix="sr_persist_") as tmp:
        run_dir = Path(tmp)
        manifest = RunManifest(run_id=new_run_id(), created_at=0.0, product_sha="a" * 40, outcome="fail")
        results = [
            CaseResult(case_id="one", title="one", required=True, verdict=VERDICT_FAIL, failure_class="VOOL", reason="x"),
        ]
        paths = write_results(run_dir, manifest, results)
        rows = [json.loads(line) for line in paths["results"].read_text().splitlines()]
        assert rows[0]["case_id"] == "one" and rows[0]["is_failure"] is True
        persisted = json.loads(paths["manifest"].read_text())
        assert persisted["product_sha"] == "a" * 40


def run_all() -> int:
    failed = 0
    for name, fn in _checks:
        try:
            fn()
            print(f"[SELFTEST-PASS] {name}", flush=True)
        except Exception as exc:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"[SELFTEST-FAIL] {name}: {type(exc).__name__}: {exc}", flush=True)
    print(f"selftest: {len(_checks) - failed}/{len(_checks)} passed", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_all())
