"""Scenario runner: drives the REAL Auto selection + execution seams.

Per scenario it:
  1. reconfigures a disposable VOOL_HOME (asserted under the job profile root),
  2. wipes the runtime routing tables (same mechanics as tests/conftest.py),
  3. writes the scenario's openrouter catalog cache rows,
  4. registers the synthetic manifests through ModelRegistry.register_manifest,
  5. arms the guard transport for exactly those manifests' endpoints,
  6. stamps mode binding with the REAL ingress seams (auto_local_only_mode.bind_turn),
  7. calls MemoryFirstRouter._execute_provider_task -- the real selector, real
     plan mint, real ranking, real _invoke_manifest gates, real output guards,
     real usage accounting -- with only the two canonical determinism patches
     the production tests themselves use (free-VRAM probe, health probe),
  8. records the decision, transport calls, runtime events, usage rows,
     routing-decision log, and the guard's violations.

It never patches ranking, eligibility, or the provider gates: the audit is of
the shipped path, at the shipped seams.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from . import synthetic as syn
from .guard_transport import AuditNetworkGuardViolation, active_guard, install

_IMPORT_ORDER_NOTE = "core.agent_runtime must import before core.memory_first_router (cycle)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_imports() -> None:
    import core.agent_runtime
    import core.memory_first_router  # noqa: F401


class ScenarioFailure(Exception):
    pass


def _decision_to_dict(decision: Any) -> dict[str, Any]:
    if decision is None:
        return {"present": False}
    out: dict[str, Any] = {"present": True}
    if is_dataclass(decision):
        try:
            import dataclasses

            raw = dataclasses.asdict(decision)
        except Exception:
            raw = {k: getattr(decision, k, None) for k in getattr(decision, "__dataclass_fields__", {})}
    else:
        raw = {k: getattr(decision, k, None) for k in dir(decision) if not k.startswith("_")}
    for key, value in raw.items():
        text = value if isinstance(value, (int, float, bool, type(None))) else str(value)
        if isinstance(text, str) and len(text) > 4000:
            text = text[:2000] + f"...[{len(text)} chars]"
        out[key] = text
    return out


def _classify_outcome(decision: Any, guard_violations: list[dict[str, Any]],
                      trace_calls: list[dict[str, Any]] | None = None) -> str:
    """Map the decision + guard state onto the audit's observable outcome classes.

    completed          a model actually served a usable answer (used_model truth)
    refused            no model ran and the router said why (explicit refusal)
    infra_or_unresolved no model ran for any other reason (exhausted, errors, ...)
    guard_violation    the harness guard refused an out-of-scenario dispatch
    """
    if guard_violations:
        return "guard_violation"
    if decision is None:
        return "infra_or_unresolved"
    if bool(getattr(decision, "used_model", False)):
        return "completed"
    posts = [c for c in trace_calls if str(c.get("method")) == "POST"] if trace_calls is not None else None
    if posts is not None and not posts:
        # nothing was dispatched and the router said why: a policy/plan refusal
        # (pin unresolved, not_owner_local, authorship block) is a refusal, not
        # an infrastructure failure
        return "refused"
    text = str(getattr(decision, "output_text", "") or "")
    details = str(getattr(decision, "details", "") or "")
    source = str(getattr(decision, "source", "") or "")
    refusal_markers = (
        "local only mode",
        "can't complete",
        "cannot complete",
        "unavailable",
        "not permitted",
        "no eligible",
        "unable to",
        "requires a cloud",
        "not_owner_local",
        "selected_model_blocked",
        "model_unavailable",
        "paid_call_not_authorized",
    )
    haystack = " ".join([text, details, source]).lower()
    if any(marker in haystack for marker in refusal_markers):
        return "refused"
    return "infra_or_unresolved"


def require_disposable_profile_root(profile_root: Path) -> Path:
    """The root every disposable VOOL_HOME is created under. It must be a dedicated directory:
    never the user's home and never inside a real VOOL/NULLA home, because each scenario wipes and
    rebuilds the profiles below it."""
    root = Path(profile_root).expanduser().resolve()
    home = Path.home().resolve()
    real_homes = [home / ".vool", home / ".nulla"]
    for env in ("VOOL_HOME", "NULLA_HOME"):
        if os.environ.get(env):
            real_homes.append(Path(os.environ[env]).expanduser().resolve())
    if root == home or root == Path(root.anchor):
        raise ValueError(f"profile root {root} must be a dedicated directory, not the home or filesystem root")
    for real in real_homes:
        if root == real or real in root.parents:
            raise ValueError(f"profile root {root} is inside a real VOOL home ({real}); use a dedicated directory")
    return root


class AuditRunner:
    def __init__(self, *, profile_root: Path, repo_root: Path) -> None:
        self.profile_root = require_disposable_profile_root(profile_root)
        self.repo_root = repo_root.resolve()
        self._ensure_storage()

    # ------------------------------------------------------------ environment
    def _ensure_storage(self) -> None:
        _ensure_imports()
        os.environ.setdefault("VOOL_KEY_STORAGE_MODE", "file")
        os.environ.setdefault("VOOL_CREDENTIAL_STORE", "vault")
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.run_home = (self.profile_root / f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{os.getpid()}")
        self.run_home.mkdir(parents=True, exist_ok=True)
        self._rehome(self.run_home)

    def _rehome(self, home: Path) -> None:
        from core.runtime_paths import configure_runtime_home
        from storage.db import (
            active_default_db_path,
            configure_default_db_path,
            reset_default_connection,
        )

        os.environ["VOOL_HOME"] = str(home)
        configure_runtime_home(home)
        configure_default_db_path(home / "data" / "audit.db")
        reset_default_connection()
        try:  # continuity DB follows the active default, same as tests/conftest
            from core.runtime_continuity import configure_runtime_continuity_db_path

            configure_runtime_continuity_db_path(active_default_db_path())
        except Exception:
            pass
        (home / "data").mkdir(parents=True, exist_ok=True)

    _WIPE_TABLES = (
        "model_provider_manifests",
        "token_usage",
    )

    def _wipe_runtime_tables(self) -> None:
        import storage.db as db

        conn = db.get_connection()
        try:
            for table in self._WIPE_TABLES:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            pass
        from core.model_health import reset_provider_health

        reset_provider_health()

    def _write_catalog(self, row_keys: list[str]) -> None:
        from core.runtime_paths import active_data_dir

        path = active_data_dir() / "openrouter_models_cache.json"
        if not row_keys:
            path.unlink(missing_ok=True)
            return
        path.write_text(json.dumps(syn.catalog_payload(row_keys)), encoding="utf-8")

    def _register_manifests(self, keys: list[str]) -> dict[str, str]:
        from core.model_registry import ModelRegistry

        registry = ModelRegistry()
        ids = {}
        for key in keys:
            manifest = registry.register_manifest(syn.MANIFESTS[key])
            ids[key] = manifest.provider_id
            self._certify_local_authorship(manifest)
        return ids

    def _certify_local_authorship(self, manifest: Any) -> None:
        """Satisfy the real authorship authority the way an operator deployment would.

        ``core.final_answer_authorship`` refuses an UNCERTIFIED loopback model as a
        final-answer author before the adapter is built (measured:
        ``selected_model_blocked``). The product path to a certified local author is a
        completed tool-certification run, so write one through the certification
        store's own public writers at the fingerprint the runtime reads back under —
        the same approach as tests/_authorship_certification.py (nothing bypasses the
        authority; it satisfies it).
        """
        try:
            from core.local_model_tool_certification import (
                certification_applies,
                certification_fingerprint,
                certification_fingerprint_payload,
                certification_status,
            )
            from storage.model_tool_certification_store import (
                begin_certification_run,
                complete_certification_run,
            )
        except Exception:
            return
        if not certification_applies(manifest):
            return
        import uuid as _uuid

        # No identity mocking: certification_status resolves the fingerprint via
        # a live (canned) identity read, and the invoke-time fence resolves the
        # same read — identical inputs on both sides make the fingerprints agree.
        status = certification_status(manifest)
        fingerprint = str(status.get("current_fingerprint") or status.get("fingerprint") or "")
        payload = dict(status.get("current_fingerprint_components") or status.get("fingerprint_components") or {})
        if not fingerprint:
            payload = certification_fingerprint_payload(
                manifest,
                backend_version="unknown",
                runtime_identity={},
            )
            fingerprint = certification_fingerprint(payload)
        run_id = f"tool-cert-audit-{_uuid.uuid4().hex}"
        begin_certification_run(
            run_id=run_id,
            fingerprint=fingerprint,
            fingerprint_payload=payload,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            adapter_type=str(manifest.adapter_type or "openai_compatible"),
        )
        complete_certification_run(
            run_id=run_id,
            fingerprint=fingerprint,
            state="verified",
            successful=True,
            stages={"transport_acceptance": {"state": "passed"}},
            evidence={"exchange_count": 4},
            latency_ms=12.0,
        )

    # ------------------------------------------------------------ one scenario
    def run_scenario(self, scenario: dict[str, Any]) -> dict[str, Any]:
        sid = scenario["id"]
        guard = active_guard() or install()
        env = scenario.get("env", {})
        manifest_keys = list(env.get("manifests", []))
        transport_cfg = scenario.get("transport", {})
        trace: dict[str, Any] = {
            "scenario_id": sid,
            "split": scenario.get("split"),
            "category": scenario.get("category"),
            "started_at": _now_iso(),
            "base_commit": BASE_COMMIT,
        }
        # -- scenario-local profile: sequence scenarios share one home so
        # cross-turn leakage is observable instead of erased
        seq_home = self.run_home / sid
        seq_home.mkdir(parents=True, exist_ok=True)
        self._rehome(seq_home)
        import storage.migrations as migrations

        migrations.run_migrations()
        self._wipe_runtime_tables()
        self._write_catalog(list(env.get("catalog", [])))
        try:  # module-level catalog memo must not leak a row across scenario homes
            import core.openrouter_catalog as _catalog

            _catalog._CACHED_ROW_MEMO.clear()
        except Exception:
            pass
        manifest_ids = self._register_manifests(manifest_keys)
        trace["registered_manifests"] = manifest_ids

        # -- escalation policy (paid authorization ceiling), real seam
        if env.get("cloud_escalation"):
            from dataclasses import replace as _replace

            import core.cloud_escalation_policy as cep

            cep.save_policy(_replace(cep.load_policy(), **env["cloud_escalation"]))

        # -- pre-open circuits, real seam
        for provider_id in env.get("circuit_open", []):
            from core.model_health import record_provider_failure

            for _ in range(5):
                record_provider_failure(provider_id, error="preset_circuit_open")

        # -- guard: exactly this scenario's synthetic endpoints
        allowlist = syn.manifest_allowlist(manifest_keys)
        scripts = {
            syn.MANIFESTS[key]["runtime_config"]["base_url"]: transport_cfg["by_manifest"][key]
            for key in transport_cfg.get("by_manifest", {})
            if key in syn.MANIFESTS
        }
        guard.configure(
            allowlist=allowlist,
            scripts=scripts,
            default=transport_cfg.get("default", "ok_text:synthetic answer"),
        )

        # -- runtime event sink (paid receipts, model_routing events)
        from core.runtime_task_events import (
            new_runtime_event_stream_id,
            register_runtime_event_sink,
            unregister_runtime_event_sink,
        )

        events: list[dict[str, Any]] = []
        stream_id = new_runtime_event_stream_id()
        register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))

        # -- mode binding through the REAL ingress seam (per-turn overrides land
        # inside the turn loop below; this is the scenario-level default)
        mode = scenario.get("mode", {})
        source_context: dict[str, Any] = {
            "turn_id": f"{sid}-turn",
            "session_id": f"{sid}-session",
            "runtime_session_id": f"{sid}-rt",
            "runtime_event_stream_id": stream_id,
        }
        # ALWAYS stamp _owner_local explicitly: request_is_owner_local falls back
        # to surface heuristics when the key is absent, and the fallback treats a
        # CLI surface as owner — an absent key would silently re-authorize the
        # paid lane the scenario meant to refuse (measured on the first run).
        source_context["_owner_local"] = bool(mode.get("owner_local", True))
        requested = None
        if mode.get("pin_manifest"):
            requested = manifest_ids[mode["pin_manifest"]]
            source_context["requested_model"] = requested
        elif mode.get("pin_manifest_absent"):
            # a pin naming a model that is NOT registered (fail-closed probe)
            requested = str(mode["pin_manifest_absent"])
            source_context["requested_model"] = requested
        turn_mode = {"local_only": mode.get("type") == "local_only"}
        trace["mode"] = {"type": mode.get("type", "auto"), "pinned": requested}

        # -- observation pass: the same rank call the router makes, same fresh
        # registry state. NOT consumed by the router; recorded as context for
        # "what was eligible at decision time" (pre plan-fence, pre pin-narrow).
        observed_eligible: list[str] = []
        try:
            from core.provider_routing import rank_provider_candidates

            ranked = rank_provider_candidates(
                self._fresh_registry(),
                task_kind=scenario["turns"][0].get("task_kind", "qa_short"),
                output_mode=scenario["turns"][0].get("output_mode", "plain_text"),
                role="queen",
                preferred_provider=None,
                preferred_model=None,
                allow_paid_fallback=False,
                swarm_size=4,
                min_trust=0.45,
                enforce_hardware_fit=True,
                local_only=bool(turn_mode["local_only"]),
            )
            observed_eligible = [m.provider_id for m in ranked]
        except Exception as exc:  # observation must never fail the scenario
            observed_eligible = [f"observation_error:{exc}"]
        trace["observed_eligible_pre_fence"] = observed_eligible

        # -- the real turns
        from core.auto_local_only_mode import bind_turn
        from core.memory_first_router import MemoryFirstRouter

        turn_results: list[dict[str, Any]] = []
        local_only_in_force = False
        for index, turn in enumerate(scenario["turns"]):
            turn_ctx = dict(source_context)
            turn_ctx["turn_id"] = f"{sid}-turn{index}"
            # per-turn mode overrides (sequence scenarios)
            turn_local_only = bool(turn_mode["local_only"]) or bool(turn.get("local_only"))
            pin_key = turn.get("pin_manifest") or mode.get("pin_manifest")
            turn_requested = manifest_ids[pin_key] if pin_key else None
            if turn.get("pin_manifest"):
                turn_ctx["requested_model"] = turn_requested
            if "owner_local" in turn:
                turn_ctx["_owner_local"] = bool(turn["owner_local"])
            if turn_local_only and not turn_requested:
                turn_ctx["requested_model"] = "vool-local-only"
            local_only_in_force = bool(bind_turn(turn_ctx, turn_ctx.get("requested_model"))) or turn_local_only
            user_text = str(turn.get("user_text", "audit turn"))
            long_fill = int(turn.get("long_fill_chars", 0) or 0)
            if long_fill and "$TEXT" in user_text:
                filler = ("audit paragraph with ordinary words. " * (long_fill // 30 + 1))[:long_fill]
                user_text = user_text.replace("$TEXT", filler)
            turn["user_text_expanded"] = user_text
            call_start = len(guard.calls)
            turn_result = self._run_turn(
                router_cls=MemoryFirstRouter,
                turn=turn,
                index=index,
                sid=sid,
                source_context=turn_ctx,
                guard=guard,
            )
            turn_result["local_only_bound"] = local_only_in_force
            turn_result["transport_call_window"] = [call_start, len(guard.calls)]
            turn_results.append(turn_result)
        trace["long_fill_chars"] = max(int(t.get("long_fill_chars", 0) or 0) for t in scenario["turns"])

        unregister_runtime_event_sink(stream_id)

        all_violations = list(guard.violations)
        # Split: an attempted dispatch toward a MODEL endpoint outside the
        # scenario allowlist is scenario-fatal. A blocked AMBIENT egress (e.g.
        # the cache-cold coingecko ticker-index refresh inside requirement
        # analysis) is recorded as evidence of routing-time network behavior —
        # the guard still refused it, no live call happened, and the model lane's
        # own outcome decides the scenario.
        hosts = syn.manifest_hosts()
        fatal = [v for v in all_violations if str(v.get("url", "")).startswith(tuple(hosts))]
        ambient = [v for v in all_violations if v not in fatal]
        outcome = _classify_outcome(
            turn_results[-1]["decision_obj"] if turn_results else None,
            fatal,
            trace_calls=guard.calls_to_dict(),
        )
        # -- post-state reads
        usage_rows = self._usage_rows()
        decision_rows = self._decision_log_rows()
        paid_events = [
            {k: e.get(k) for k in ("event_type", "provider_id", "model_id", "reserved_usd", "actual_usd", "held_usd", "call_role")}
            for e in events
            if str(e.get("event_type", "")).startswith("paid_call.")
        ]
        model_events = [
            {k: e.get(k) for k in ("event_type", "provider_id", "model_id", "attempt", "reason")}
            for e in events
            if "model" in str(e.get("event_type", "")).lower()
        ]
        trace.update(
            {
                "finished_at": _now_iso(),
                "turns": [self._public_turn(t) for t in turn_results],
                "outcome_class": outcome,
                "guard_violations": fatal,
                "ambient_live_fetches_blocked": ambient,
                "transport_calls": guard.calls_to_dict(),
                "paid_call_events": paid_events,
                "model_events": model_events,
                "usage_rows": usage_rows,
                "routing_decision_log_rows": decision_rows,
            }
        )
        return trace

    def _fresh_registry(self):
        from core.model_registry import ModelRegistry

        return ModelRegistry()

    def _run_turn(self, *, router_cls, turn: dict[str, Any], index: int, sid: str,
                  source_context: dict[str, Any], guard) -> dict[str, Any]:
        user_text = str(turn.get("user_text_expanded") or turn.get("user_text", "audit turn"))
        interpretation = SimpleNamespace(reconstructed_text=user_text)
        report = SimpleNamespace(
            retrieval_confidence=0.2,
            to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
        )
        context_result = SimpleNamespace(
            retrieval_confidence_score=0.2,
            report=report,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
        )
        task = SimpleNamespace(task_id=f"{sid}-t{index}", task_summary=user_text[:200])
        started = time.monotonic()
        decision = None
        error = None
        with mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None), mock.patch(
            "core.memory_first_router.should_probe_health", return_value=False
        ):
            try:
                router = router_cls()
                decision = router._execute_provider_task(
                    task=task,
                    classification={"task_class": turn.get("task_class", "chat_conversation")},
                    interpretation=interpretation,
                    context_result=context_result,
                    persona=SimpleNamespace(tone="neutral"),
                    task_hash=f"{sid}-hash-{index}",
                    task_kind=turn.get("task_kind", "qa_short"),
                    output_mode=turn.get("output_mode", "plain_text"),
                    allow_paid_fallback=bool(turn.get("allow_paid_fallback", False)),
                    provider_role="queen",
                    surface="cli",
                    source_context=source_context,
                )
            except AuditNetworkGuardViolation as exc:
                error = {"kind": "guard_violation", "message": str(exc)}
            except Exception as exc:
                error = {"kind": type(exc).__name__, "message": str(exc)[:500]}
        duration = round(time.monotonic() - started, 4)
        return {
            "index": index,
            "task_kind": turn.get("task_kind"),
            "output_mode": turn.get("output_mode"),
            "decision": _decision_to_dict(decision),
            "decision_obj": decision,
            "error": error,
            "duration_seconds": duration,
        }

    def _public_turn(self, t: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in t.items() if k != "decision_obj"}
        return out

    # ------------------------------------------------------------ post reads
    def _usage_rows(self) -> list[dict[str, Any]]:
        import storage.db as db

        try:
            conn = db.get_connection()
            rows = conn.execute(
                "SELECT entry_id, provider_id, model_id, cost_class, prompt_tokens, "
                "output_tokens, usd_actual, created_at FROM token_usage ORDER BY created_ts"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            return [{"read_error": str(exc)}]

    def _decision_log_rows(self) -> list[dict[str, Any]]:
        from core.routing_decision_log import decisions_path

        path = decisions_path()
        if not path.is_file():
            return []
        try:
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            return []


BASE_COMMIT = os.environ.get("VOOL_AUDIT_BASE_COMMIT", "dca4679fccd777554c4ad9f9653f0a3e8406b0ef")


def run_scenarios(scenarios: list[dict[str, Any]], *, profile_root: Path, repo_root: Path,
                  on_scenario=None) -> list[dict[str, Any]]:
    from .guard_transport import install, uninstall

    install()
    runner = AuditRunner(profile_root=profile_root, repo_root=repo_root)
    traces: list[dict[str, Any]] = []
    try:
        for scenario in scenarios:
            try:
                trace = runner.run_scenario(scenario)
            except Exception as exc:
                trace = {
                    "scenario_id": scenario.get("id"),
                    "split": scenario.get("split"),
                    "harness_error": {"kind": type(exc).__name__, "message": str(exc)[:500],
                                      "traceback": traceback.format_exc()[-2000:]},
                    "outcome_class": "harness_error",
                }
            traces.append(trace)
            if on_scenario:
                on_scenario(trace)
    finally:
        uninstall()
    return traces
