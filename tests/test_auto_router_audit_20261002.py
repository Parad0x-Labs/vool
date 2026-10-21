"""Auto router audit harness tests (mission auto-router-audit-20261002).

These tests pin the HARNESS, not the product: corpus integrity, the
no-live-network guard (including that the guard itself is load-bearing and
correctly self-validating), and a deterministic fixture run of a compact
scenario subset through the real routing seams at the frozen public base
dca4679fccd777554c4ad9f9653f0a3e8406b0ef.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_PKG = REPO_ROOT / "tools" / "auto_router_audit_20261002"
for entry in (str(REPO_ROOT), str(TOOLS_PKG)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from audit_lib import guard_transport, report, synthetic
from audit_lib import runner as audit_runner

CORPUS = json.loads((TOOLS_PKG / "corpus.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- corpus integrity
def test_corpus_ids_unique_and_splits_declared_before_run() -> None:
    ids = [s["id"] for s in CORPUS["scenarios"]]
    assert len(ids) == len(set(ids))
    declaration = CORPUS["split_declaration"]
    assert declaration["declared_before_any_run"] is True
    declared = set(declaration["dev"]) | set(declaration["heldout"]) | set(declaration["control"])
    assert declared == set(ids), "every scenario must belong to exactly one declared split"
    for split in ("dev", "heldout", "control"):
        for sid in declaration[split]:
            scenario = next(s for s in CORPUS["scenarios"] if s["id"] == sid)
            assert scenario["split"] == split


@pytest.mark.parametrize("scenario", CORPUS["scenarios"], ids=lambda s: s["id"])
def test_scenario_schema(scenario: dict) -> None:
    env = scenario.get("env", {})
    for key in env.get("manifests", []):
        assert key in synthetic.MANIFESTS, f"unknown manifest key {key}"
    for key in env.get("catalog", []):
        assert key in synthetic.CATALOG_ROWS, f"unknown catalog row {key}"
    for key in scenario.get("transport", {}).get("by_manifest", {}):
        assert key in synthetic.MANIFESTS
    for manifest_key in scenario.get("expect", {}).get("no_dispatch_to", []):
        assert manifest_key in synthetic.MANIFESTS
    assert scenario["turns"], "every scenario has at least one turn"
    for turn in scenario["turns"]:
        assert "user_text" in turn and "task_kind" in turn and "output_mode" in turn


def test_no_scenario_endpoint_is_a_real_host() -> None:
    # every endpoint is synthetic EXCEPT the one guard-control manifest whose
    # entire purpose is to point at the real openrouter.ai host
    for key, spec in synthetic.MANIFESTS.items():
        url = str(spec["runtime_config"]["base_url"])
        loopback = url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
        if key == "guard_probe_external":
            assert "openrouter.ai" in url
            continue
        assert ".invalid" in url or loopback, f"non-synthetic endpoint would be reachable: {url}"
    # and that real-world host must NEVER be allowlisted by any scenario
    allowlists = [synthetic.manifest_allowlist([key]) for key in synthetic.MANIFESTS]
    flat = [prefix for sub in allowlists for prefix in sub]
    assert not any("openrouter.ai" in p for p in flat)


# ---------------------------------------------------------------- guard validation
def test_guard_self_test_external_dispatch_refused() -> None:
    results = guard_transport.self_test()
    assert results == {
        "external_post_refused": True,
        "external_get_refused": True,
        "allowlisted_post_served": True,
        "violations_recorded": True,
    }


def test_guard_install_and_uninstall_restore_real_transports() -> None:
    import requests

    real_post = requests.post
    guard = guard_transport.install()
    try:
        assert guard_transport.active_guard() is guard
        assert requests.post.__self__ is guard  # bound-method identity
    finally:
        guard_transport.uninstall()
    assert requests.post == real_post
    assert guard_transport.active_guard() is None


def test_guard_refuses_external_dispatch_with_violation_record() -> None:
    guard = guard_transport.GuardTransport()
    guard.configure(
        allowlist=["https://synthetic.example.invalid"],
        scripts={},
        default="ok_text:x",
    )
    with pytest.raises(guard_transport.AuditNetworkGuardViolation):
        guard.post("https://api.openai.com/v1/chat/completions", json={"model": "gpt"})
    assert len(guard.violations) == 1
    assert guard.violations[0]["url"] == "https://api.openai.com/v1/chat/completions"


def test_guard_sequential_script_advances_per_call() -> None:
    guard = guard_transport.GuardTransport()
    guard.configure(
        allowlist=["http://127.0.0.1:18999"],
        scripts={"http://127.0.0.1:18999": "empty|ok_text:recovered"},
        default="ok_text:x",
    )
    first = guard.post("http://127.0.0.1:18999/v1/chat/completions", json={"model": "m"})
    second = guard.post("http://127.0.0.1:18999/v1/chat/completions", json={"model": "m"})
    assert first.json()["choices"][0]["message"]["content"] == ""
    assert second.json()["choices"][0]["message"]["content"] == "recovered"


@pytest.mark.parametrize("inside", ["", ".vool", ".vool/profiles", ".nulla/x", "custom-home/sub"])
def test_runner_refuses_a_profile_root_that_is_a_real_home(tmp_path: Path, monkeypatch, inside: str) -> None:
    home = tmp_path / "home"
    (home / "custom-home").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home / "custom-home"))
    with pytest.raises(ValueError):
        audit_runner.AuditRunner(profile_root=home / inside, repo_root=REPO_ROOT)


def test_runner_accepts_a_dedicated_profile_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("VOOL_HOME", raising=False)
    monkeypatch.delenv("NULLA_HOME", raising=False)
    root = tmp_path / "scratch" / "profiles"
    assert audit_runner.require_disposable_profile_root(root) == root.resolve()


# ---------------------------------------------------------------- deterministic fixture run
_FIXTURE_IDS = [
    "auto-easy-chat",
    "local-only-no-cloud-egress",
    "paid-missing-approval-no-dispatch",
    "c-guard-external-host",
]


def test_deterministic_fixture_run_at_pinned_base(tmp_path: Path) -> None:
    """A compact rerunnable subset: anyone can reproduce these four verdicts."""
    scenarios = [s for s in CORPUS["scenarios"] if s["id"] in _FIXTURE_IDS]
    assert len(scenarios) == len(_FIXTURE_IDS)
    traces = audit_runner.run_scenarios(
        scenarios, profile_root=tmp_path / "profiles", repo_root=REPO_ROOT
    )
    by_id = {t["scenario_id"]: t for t in traces}

    easy = by_id["auto-easy-chat"]
    assert easy["outcome_class"] == "completed"
    assert easy["usage_rows"] and all(
        r["cost_class"] in ("free_local", "free_cloud") for r in easy["usage_rows"]
    )

    local_only = by_id["local-only-no-cloud-egress"]
    cloud_hosts = (
        report._manifest_host("free_cloud_verified"),
        report._manifest_host("paid_cloud"),
    )
    assert not any(
        str(c["url"]).startswith(cloud_hosts) for c in local_only["transport_calls"]
    ), "a Local Only turn must never egress to a cloud endpoint"

    paid_missing = by_id["paid-missing-approval-no-dispatch"]
    assert paid_missing["outcome_class"] == "refused"
    assert not any(c.get("method") == "POST" for c in paid_missing["transport_calls"])
    assert paid_missing["paid_call_events"] == []

    control = by_id["c-guard-external-host"]
    assert control["outcome_class"] == "guard_violation"
    assert any("openrouter.ai" in v["url"] for v in control["guard_violations"])


def test_baseline_report_builder_verdicts() -> None:
    by_id = {s["id"]: s for s in CORPUS["scenarios"]}
    completed_trace = {
        "scenario_id": "auto-easy-chat",
        "split": "dev",
        "outcome_class": "completed",
        "transport_calls": [{"method": "POST", "url": report._manifest_host("local_big") + "/v1/chat/completions"}],
        "turns": [{"decision": {"provider_id": "synthetic-local:workhorse-8b", "used_model": True}}],
        "usage_rows": [{"cost_class": "free_local"}],
        "paid_call_events": [],
        "guard_violations": [],
    }
    verdict = report.scenario_verdict(by_id["auto-easy-chat"], completed_trace)
    assert verdict == "pass"
    baseline = report.build_baseline(
        [completed_trace], CORPUS, repo_head="test", guard_self_test={"ok": True}, command="test"
    )
    assert baseline["summary"]["pass"] == 1


# ---------------------------------------------------------------- production untouched
def test_no_production_module_reaches_the_harness() -> None:
    """The harness drives production seams; production never imports or names the harness.

    Read from the source itself rather than from the checkout's working-tree state, which any
    unrelated uncommitted edit (or a committed harness, whose files are no longer untracked)
    would turn into a verdict about the environment instead of about the code.
    """
    production = [REPO_ROOT / name for name in ("core", "apps", "adapters", "storage", "network", "retrieval", "sandbox")]
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for root in production
        if root.is_dir()
        for path in root.rglob("*.py")
        if "auto_router_audit" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == [], f"production code reaches the audit harness: {offenders}"
