"""Round-018: NOVA-71 false-grounding — compound-identifier mapping fail-closed.

MUST FIX #1: notation repair must not bypass the referent-gap guard.

Architecture ruling C: an observed web_lookup claim that maps an opaque
compound identifier (NOVA-71, AURORA-418) to a locator URL is FAIL-CLOSED
when supported only by free-prose world receipts. No structured WorldBinding
contract exists yet (deferred). Free-prose co-occurrence cannot prove the
(referent, relation, value) tuple.

Normal web facts, user stipulation, arithmetic, and compose are unaffected.
"""
from __future__ import annotations

from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(extract_rows, synth_claims=None, verify_response=None):
    """Stand-in for _model_json; verifier defaults to all-true (sabotage-proof)."""
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims or []}
        if effect_id == "model.verify":
            if verify_response is not None:
                return verify_response
            n = user.count("CLAIM ")
            return {"verdicts": [{"claim": i, "bound": True, "answers_the_ask": True,
                                  "magnitude_sane": True, "entity_supported": True}
                                 for i in range(1, n + 1)],
                    "all_parts_answered": True, "missing": ""}
        raise AssertionError(f"unexpected effect {effect_id}")
    return fake


def _row(desc, lane, query="", fmt=""):
    return {"description": desc, "lane": lane, "query": query, "format": fmt,
            "source_offset": 0, "resolves_carryover": ""}


# ── Category A: referent-gap guard (MUST FIX #1) ──────────────────────────

def test_nova_71_rejected_no_world_evidence(monkeypatch):
    """N1: NOVA-71 with unrelated Nova/project-nova results must not ground."""
    judge = _judge(
        [_row("find the latest deployment URL", "web_lookup",
              "latest deployment URL for project NOVA-71")],
        [{"obligation_id": "ob1",
          "text": "The latest deployment URL for project NOVA-71 can be found "
                  "on GitHub at https://github.com/dujonwalker/project-nova.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the latest deployment URL for project NOVA-71.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "OpenStack Compute (nova) docs",
                    "snippet": "nova-api on wsgi deployment guide version 33",
                    "url": "https://docs.openstack.org/nova/latest/"},
                   {"title": "GitHub - dujonwalker/project-nova",
                    "snippet": ("Project NOVA routes requests to domain-specific "
                                "experts; 25+ specialized agents through n8n"),
                    "url": "https://github.com/dujonwalker/project-nova"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "The latest deployment URL for project NOVA-71" not in ans, \
        f"N1: fabricated claim must not ship: {ans!r}"


def test_stale_variable_bypass_prevented(monkeypatch):
    """Verifier all-true does NOT resurrect a notation-repaired user-only claim."""
    judge = _judge(
        [_row("find the latest deployment URL", "web_lookup",
              "latest deployment URL for project NOVA-71")],
        [{"obligation_id": "ob1",
          "text": "The latest deployment URL for project NOVA-71 can be found "
                  "on GitHub at https://github.com/dujonwalker/project-nova.",
          "type": "observed"}],
        verify_response={"verdicts": [{"claim": 1, "bound": True,
                                       "answers_the_ask": True,
                                       "magnitude_sane": True,
                                       "entity_supported": True}],
                         "all_parts_answered": True, "missing": ""})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the latest deployment URL for project NOVA-71.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "OpenStack Compute (nova) docs",
                    "snippet": "nova-api version 33 deployment",
                    "url": "https://docs.openstack.org/nova/latest/"},
                   {"title": "GitHub - dujonwalker/project-nova",
                    "snippet": "Project NOVA routes requests; 25+ agents",
                    "url": "https://github.com/dujonwalker/project-nova"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "The latest deployment URL for project NOVA-71" not in ans, \
        f"Stale fix: verifier all-true must not resurrect: {ans!r}"


# ── Category B: compound-identifier mapping fail-closed (ruling C) ──────────

def test_aurora_418_rejected(monkeypatch):
    """N2: AURORA-418 with unrelated Aurora results must not ground."""
    judge = _judge(
        [_row("find deployment URL for AURORA-418", "web_lookup",
              "AURORA-418 deployment URL")],
        [{"obligation_id": "ob1",
          "text": "AURORA-418 deployment URL is https://aurora.example.com "
                  "using {n1} environments.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the deployment URL for project AURORA-418.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Aurora DB docs",
                    "snippet": "Aurora Serverless deployment v2 standard",
                    "url": "https://aws.amazon.com/rds/aurora/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "aurora.example.com" not in ans, \
        f"N2: AURORA-418 must not ground: {ans!r}"


def test_mercury_77_rejected(monkeypatch):
    """N3: MERCURY-77 with unrelated Mercury results must not ground."""
    judge = _judge(
        [_row("find status page for MERCURY-77", "web_lookup",
              "MERCURY-77 status page")],
        [{"obligation_id": "ob1",
          "text": "MERCURY-77 status page is at https://mercury77.example.com "
                  "with {n1} incidents.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the status page for service MERCURY-77.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Mercury Messenger",
                    "snippet": "Mercury cross-platform messaging app 2.0",
                    "url": "https://mercury.com/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "mercury77.example.com" not in ans, \
        f"N3: MERCURY-77 must not ground: {ans!r}"


def test_atlas_009_rejected(monkeypatch):
    """N4: ATLAS-009 with unrelated Atlas repos must not ground."""
    judge = _judge(
        [_row("find repository for ATLAS-009", "web_lookup",
              "ATLAS-009 repository")],
        [{"obligation_id": "ob1",
          "text": "ATLAS-009 repo is at https://github.com/atlas-009 with "
                  "{n1} stars.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the repository for internal project ATLAS-009.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "atlas/atlas",
                    "snippet": "Atlas: A Python library for data processing v3",
                    "url": "https://github.com/atlas/atlas"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "github.com/atlas-009" not in ans, \
        f"N4: ATLAS-009 must not ground: {ans!r}"


def test_node_73b_rejected(monkeypatch):
    """N5: NODE-73B with generic Node pages must not ground."""
    judge = _judge(
        [_row("find latest build for NODE-73B", "web_lookup",
              "NODE-73B latest build")],
        [{"obligation_id": "ob1",
          "text": "NODE-73B build is at https://build.example/node-73b with "
                  "version {n1}.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the latest build for NODE-73B.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Node.js",
                    "snippet": "Node.js v73 release notes and downloads",
                    "url": "https://nodejs.org/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "build.example/node-73b" not in ans, \
        f"N5: NODE-73B must not ground: {ans!r}"


# ── FALSE BINDING TESTS ───────────────────────────────────────────────────

def test_f1_issue_tracker_rejected(monkeypatch):
    """F1: issue-tracker receipt must not ground deployment URL."""
    judge = _judge(
        [_row("find deployment URL for AURORA-418", "web_lookup",
              "AURORA-418 deployment URL")],
        [{"obligation_id": "ob1",
          "text": "AURORA-418 deployment URL is https://example.test/deploy "
                  "with version {n1}.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the deployment URL for project AURORA-418.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "AURORA-418 tracker",
                    "snippet": "AURORA-418 deployment issue tracker: "
                               "https://example.test/deploy with 42",
                    "url": "https://aurora-418.example.com/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "AURORA-418 deployment URL is" not in ans, \
        f"F1: issue-tracker must not ground deployment URL: {ans!r}"


def test_f2_debug_log_rejected(monkeypatch):
    """F2: debug-log receipt must not ground deployment URL."""
    judge = _judge(
        [_row("find deployment URL for AURORA-418", "web_lookup",
              "AURORA-418 deployment URL")],
        [{"obligation_id": "ob1",
          "text": "AURORA-418 deployment URL is https://example.test/deploy "
                  "with version {n1}.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the deployment URL for project AURORA-418.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "AURORA-418 log",
                    "snippet": "AURORA-418 deployment failed. "
                               "Debug log: https://example.test/deploy 42",
                    "url": "https://aurora-418.example.com/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "AURORA-418 deployment URL is" not in ans, \
        f"F2: debug-log must not ground deployment URL: {ans!r}"


def test_f3_deploy_without_identifier_rejected(monkeypatch):
    """F3: deploy receipt without identifier must not ground."""
    judge = _judge(
        [_row("find deployment URL for AURORA-418", "web_lookup",
              "AURORA-418 deployment URL")],
        [{"obligation_id": "ob1",
          "text": "AURORA-418 deployment URL is https://example.test/deploy "
                  "with version {n1}.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the deployment URL for project AURORA-418.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Deploy docs",
                    "snippet": "Deploy applications at https://example.test/deploy",
                    "url": "https://example.test/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "AURORA-418 deployment URL is" not in ans, \
        f"F3: deploy-without-identifier must not ground: {ans!r}"


# ── FREE-PROSE EXPLICIT BINDING (deliberate false negative under ruling C) ─

def test_free_prose_explicit_binding_fails_closed(monkeypatch):
    """Under ruling C, free-prose explicit binding is a deliberate false negative."""
    judge = _judge(
        [_row("find deployment URL for AURORA-418", "web_lookup",
              "AURORA-418 deployment URL")],
        [{"obligation_id": "ob1",
          "text": "AURORA-418 deployment URL is https://example.test/deploy "
                  "with version {n1}.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the deployment URL for project AURORA-418.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "AURORA-418 deploy",
                    "snippet": "Project AURORA-418 deployment URL: "
                               "https://example.test/deploy version 2.1",
                    "url": "https://aurora-418.example.com/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    # Under ruling C, even an explicit free-prose binding cannot ground
    # the mapping until a structured WorldBinding contract exists.
    assert "AURORA-418 deployment URL is" not in ans, \
        f"Free-prose explicit binding must fail-closed under ruling C: {ans!r}"


# ── VERIFIER ALL-TRUE SABOTAGE ────────────────────────────────────────────

def test_verifier_all_true_fail_closed_still_rejects(monkeypatch):
    """S3: verifier always-true cannot resurrect fail-closed mapping."""
    judge = _judge(
        [_row("find deployment URL for AURORA-418", "web_lookup",
              "AURORA-418 deployment URL")],
        [{"obligation_id": "ob1",
          "text": "AURORA-418 deployment URL is https://aurora-bad.example.com "
                  "with version {n1}.",
          "type": "observed"}],
        verify_response={"verdicts": [{"claim": 1, "bound": True,
                                       "answers_the_ask": True,
                                       "magnitude_sane": True,
                                       "entity_supported": True}],
                         "all_parts_answered": True, "missing": ""})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Find the deployment URL for project AURORA-418.",
        EffectRunner(mode="record"),
        lambda q: [{"title": "Aurora DB docs",
                    "snippet": "Aurora Serverless deployment v2",
                    "url": "https://aws.amazon.com/rds/aurora/"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "aurora-bad.example.com" not in ans, \
        f"S3: verifier all-true must not resurrect: {ans!r}"


# ── POSITIVE CONTROLS ─────────────────────────────────────────────────────

def test_normal_web_fact_unaffected(monkeypatch):
    """P2: ordinary web fact without compound identifier unaffected."""
    judge = _judge(
        [_row("current temperature in London", "web_lookup",
              "London current temperature celsius")],
        [{"obligation_id": "ob1",
          "text": "London temperature is {n1}°C.",
          "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "What is the current temperature in London?",
        EffectRunner(mode="record"),
        lambda q: [{"title": "London weather",
                    "snippet": "London current temperature is 18 celsius",
                    "url": "https://weather.com/london"}],
        "test")
    ans = repl._extract_answer(transcript) or ""
    assert "18" in ans, \
        f"P2: normal web fact must remain answerable: {ans!r}"


def test_user_stipulated_passes(monkeypatch):
    """P3: user-stipulated fact must survive (chat lane, conversational type)."""
    judge = _judge(
        [_row("repeat temperature", "chat", "")],
        [{"obligation_id": "ob1",
          "text": "The user said the current temperature is 22 degrees.",
          "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "The current temperature is 22 degrees. Repeat that back to me.",
        EffectRunner(mode="record"),
        lambda q: [], "test")
    ans = repl._extract_answer(transcript) or ""
    assert any(word in ans for word in ("22", "temperature")), \
        f"P3: user-stipulated must survive: {ans!r}"


def test_arithmetic_unaffected(monkeypatch):
    """P4: arithmetic lane bypasses compound-identifier guard (no web_lookup)."""
    judge = _judge(
        [_row("calculate 10 plus 20", "arithmetic", "10 + 20")],
        [{"obligation_id": "ob1",
          "text": "10 + 20 = {n1}.",
          "type": "observed"}],
        verify_response={"verdicts": [{"claim": 1, "bound": True,
                                       "answers_the_ask": True,
                                       "magnitude_sane": True,
                                       "entity_supported": True}],
                         "all_parts_answered": True, "missing": ""})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Calculate 10 plus 20.",
        EffectRunner(mode="record"),
        lambda q: [], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "30" in ans, \
        f"P4: arithmetic must remain answerable: {ans!r}"


def test_compose_unaffected(monkeypatch):
    """P5: compose lane bypasses compound-identifier guard (no web_lookup)."""
    judge = _judge(
        [_row("draft greeting", "compose", "")],
        [{"obligation_id": "ob1",
          "text": "Hello, welcome to AURORA-418 services.",
          "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, still_open, _ = repl.run_turn(
        "Draft a welcome greeting for AURORA-418 services.",
        EffectRunner(mode="record"),
        lambda q: [], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "AURORA-418" in ans, \
        f"P5: compose must not be affected: {ans!r}"
