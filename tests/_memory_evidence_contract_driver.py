"""Offline native evidence-contract acceptance driver.

Fresh source worlds; real served decisions; no network or model execution.
The deterministic reader only emits the authored answer when all required
source fragments occur in the serialized payload. This is pipeline acceptance,
not live reader quality.

Adapted from the existing paired300 authority served-path driver.

Runs the REAL served turn — `VoolAgent.run_once` with only the bare question —
in a fresh interpreter over a disposable runtime home, with normal ingestion
(`store_turn`) and the real routing / retrieval / context-assembly / normalizer /
invocation-sealing / guard chain untouched.

The ONLY scripted element is the external transport boundary: `requests.post`
(the model endpoint) and `requests.get` (the provider health probe) are replaced
by a recording sink that answers with a per-turn SCRIPTED CHOICE. Every replaced
call is recorded (method, URL, payload) and reported, so no paid or remote
service can be reached and no provider call can pass unlabelled. The scripted
reply is a transport-boundary choice, NOT live-model accuracy.

Output: one JSON document with, per turn — the question, the scripted raw reply,
the captured serialized request payload, the selected evidence the request
carried, model-call count, and the final delivered text — plus the embedding
backend receipt and the source head.

Usage:
    python tests/_memory_evidence_contract_driver.py \
        --home <disposable-profile> --out <result.json> --repo-root <repo> \
        [--turns-json <turns.json>] [--skip-seed] [--flag-off] [--delete-token T]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import time
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path


def _epoch(date: str) -> float:
    return _dt.datetime.fromisoformat(date).replace(
        tzinfo=_dt.timezone.utc
    ).timestamp()


def install_fixture_reference_clock(reference_date: str) -> None:
    """Install one process-local reference before any product datetime imports."""
    if not reference_date:
        return
    if getattr(_dt.datetime, "_vool_fixture_reference_date", None) == reference_date:
        return
    original_datetime = _dt.datetime
    fixed = original_datetime.fromisoformat(reference_date).replace(hour=12, tzinfo=_dt.timezone.utc)
    class FixtureDateTime(original_datetime):
        _vool_fixture_reference_date = reference_date
        @classmethod
        def now(cls, tz=None):
            value = fixed if tz is None else fixed.astimezone(tz)
            return cls.fromtimestamp(value.timestamp(), tz) if tz is not None else cls.fromtimestamp(value.timestamp(), _dt.timezone.utc).replace(tzinfo=None)
        @classmethod
        def utcnow(cls):
            return cls.fromtimestamp(fixed.timestamp(), _dt.timezone.utc).replace(tzinfo=None)
    _dt.datetime = FixtureDateTime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--turns-json", required=True)
    parser.add_argument("--seed-json", default="")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--flag-off", action="store_true")
    parser.add_argument("--delete-token", default="")
    parser.add_argument("--control-dir", default="", help="Parent-only reply controls through file IPC; worker turns contain no gold")
    parser.add_argument("--app-default", action="store_true")
    parser.add_argument("--library-unset", action="store_true")
    parser.add_argument("--gold-canary", default="")
    parser.add_argument("--reference-date", default="")
    parser.add_argument("--chat-id", default="allotment-log")
    parser.add_argument("--no-event-capture", action="store_true")
    parser.add_argument("--commit", action="store_true", help="Also run the served door's response commit (finalization and publication gate) and record it")
    args = parser.parse_args()

    install_fixture_reference_clock(args.reference_date)
    gold_canary_denied = None
    if args.gold_canary:
        try:
            Path(args.gold_canary).read_bytes()
        except PermissionError:
            gold_canary_denied = True
        else:
            raise RuntimeError("Native worker can read evaluator-only control: refusing unblinded run")
    repo = args.repo_root
    if repo not in sys.path:
        sys.path.insert(0, repo)
    os.environ["VOOL_HOME"] = os.path.abspath(args.home)
    os.environ["VOOL_HOME"] = os.path.abspath(args.home)
    # keep installed ollama CHAT models out of the provider universe so the
    # only selectable provider is the scripted one registered below (the local
    # EMBEDDING service is a separate endpoint and stays reachable)
    os.environ["VOOL_REGISTER_INSTALLED_OLLAMA_MODELS"] = "0"
    from _memory_contract_transport import launcher_context_defaults
    if sum((args.app_default, args.flag_off, args.library_unset)) > 1:
        raise ValueError("Startup configuration controls are mutually exclusive")
    configuration_receipt = {"configuration_kind": "explicit_capsule_control", "resolved": {"VOOL_CONTEXT_CAPSULE_V2": "1"}}
    if args.app_default:
        configuration_receipt = launcher_context_defaults(Path(repo), dict(os.environ))
        os.environ.update(configuration_receipt["resolved"])
    elif args.library_unset:
        os.environ.pop("VOOL_CONTEXT_CAPSULE_V2", None)
        configuration_receipt = {"configuration_kind": "raw_library_capsule_unset", "resolved": {"VOOL_CONTEXT_CAPSULE_V2": None}}
    elif args.flag_off:
        os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "0"
        configuration_receipt = {"configuration_kind": "explicit_capsule_opt_out", "resolved": {"VOOL_CONTEXT_CAPSULE_V2": "0"}}
    else:
        os.environ["VOOL_CONTEXT_CAPSULE_V2"] = "1"

    # --- runtime home BEFORE storage imports ---------------------------------
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    home = os.path.abspath(args.home)
    configure_runtime_home(home)
    configure_default_db_path(os.path.join(home, "data", "vool_web0_v2.db"))

    from storage.migrations import run_migrations

    run_migrations()

    # --- the offline scripted transport sink (THE boundary) ------------------
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    import requests

    blocked_network: list[dict] = []

    def _no_urlopen(request, *a, **kw):
        url = str(getattr(request, "full_url", request))
        blocked_network.append({"transport": "urllib", "url": url})
        raise urllib.error.URLError("Offline acceptance: network disabled")

    def _no_request(*a, **kw):
        raise requests.ConnectionError("Offline acceptance: network disabled")

    urllib.request.urlopen = _no_urlopen
    requests.sessions.Session.request = _no_request

    calls: list[dict] = []
    state: dict = {"scripted": ""}
    PROVIDER_MODEL = os.environ.get("P300_SCRIPTED_MODEL", "p300-served-scripted")
    _REAL_POST, _REAL_GET = requests.post, requests.get
    _LOCAL_EMBEDDING_PREFIX = "http://127.0.0.1:11434"

    class _ScriptedResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

        @property
        def text(self):
            return json.dumps(self._payload)

    from _memory_contract_transport import ReplyControl, reply_for

    def _sink_post(url, **kwargs):
        payload = kwargs.get("json")
        if str(url).startswith(_LOCAL_EMBEDDING_PREFIX):
            # local embeddings are denied, exercising the real fallback
            calls.append(
                {
                    "method": "POST",
                    "url": str(url),
                    "payload": None,
                    "payload_bytes": "",
                    "scripted_choice": "",
                    "local_blocked": True,
                }
            )
            raise requests.ConnectionError("Offline acceptance: local embeddings disabled")
        if urlsplit(str(url)).hostname not in {"127.0.0.1", "localhost"} or urlsplit(str(url)).port != 9:
            raise RuntimeError("Unexpected provider URL in offline acceptance: " + str(url))
        prepared = requests.Request("POST", str(url), json=payload).prepare()
        prepared_body = prepared.body or b""
        if isinstance(prepared_body, str): prepared_body = prepared_body.encode()
        wire = prepared_body.decode()
        assert json.loads(prepared_body) == payload
        required = state.get("reader_require") or []
        if args.control_dir:
            bridge = Path(args.control_dir).resolve()
            request_id = hashlib.sha256((str(state["id"]) + ":" + str(len(calls)) + ":" + wire).encode()).hexdigest()
            request = bridge / "requests" / (request_id + ".json")
            response = bridge / "responses" / (request_id + ".json")
            request.write_text(json.dumps({"request_id": request_id, "question_id": state["id"], "payload": payload, "request_body_b64": base64.b64encode(prepared_body).decode(), "request_bytes_sha256": hashlib.sha256(prepared_body).hexdigest(), "capture_boundary": "requests.PreparedRequest.body before synthetic exchange"}))
            deadline = time.monotonic() + 120
            while not response.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Offline parent reply control timeout")
                time.sleep(0.01)
            decision = json.loads(response.read_text())
        else:
            decision = reply_for(payload, ReplyControl(
                state["scripted"], tuple(required),
                malformed_auxiliary=bool(state.get("malformed_auxiliary")),
                malformed_final=bool(state.get("malformed_final")),
                finish_reason=str(state.get("finish_reason") or "stop"),
            ))
        reader_sufficient = decision.get("source_present")
        raw = decision["content"]
        calls.append(
            {
                "method": "POST",
                "url": str(url),
                "payload": payload,
                "raw_provider_reply": raw,
                "reader_required": required,
                "reader_sufficient": reader_sufficient,
                "payload_bytes": wire,
                "request_body_sha256": hashlib.sha256(prepared_body).hexdigest(),
                "request_body_b64": base64.b64encode(prepared_body).decode(),
                "capture_boundary": "requests.PreparedRequest.body before synthetic exchange",
                "scripted_choice": state["scripted"] if not args.control_dir else "parent_only_not_in_worker",
                "call_purpose": decision["purpose"],
                "output_contract": decision["output_contract"],
                "source_required_at_this_call": decision["source_required_at_this_call"],
                "usage_measurement": "synthetic_not_measured",
                "max_output_tokens_wire": payload.get("max_tokens"),
            }
        )
        return _ScriptedResponse(
            {
                "choices": [
                    {"message": {"content": raw}, "finish_reason": decision["finish_reason"]}
                ],
                "model": PROVIDER_MODEL,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }
        )

    def _sink_get(url, **kwargs):
        if str(url).startswith(_LOCAL_EMBEDDING_PREFIX):
            calls.append(
                {
                    "method": "GET",
                    "url": str(url),
                    "payload": None,
                    "local_blocked": True,
                }
            )
            raise requests.ConnectionError("Offline acceptance: local embeddings disabled")
        if urlsplit(str(url)).hostname not in {"127.0.0.1", "localhost"} or urlsplit(str(url)).port != 9:
            raise RuntimeError("Unexpected provider URL in offline acceptance: " + str(url))
        calls.append({"method": "GET", "url": str(url), "payload": None})
        # health probe disclosure: an answered probe, never a real endpoint
        return _ScriptedResponse({"data": [{"id": PROVIDER_MODEL}]})

    requests.post = _sink_post  # type: ignore[assignment]
    requests.get = _sink_get  # type: ignore[assignment]
    import core.provider_http as _provider_http
    _provider_http.post = _sink_post
    _provider_http.get = _sink_get

    # Observe the returned vector-space stamp, rather than guessing the model
    # from an open port. The wrapper returns the original vector unchanged.
    import core.context_retrieval as context_retrieval
    import core.embedding_service as embedding_service

    from core.ollama_endpoint import ollama_base_url as _obu; assert _obu() == "http://127.0.0.1:11434", _obu()
    embedding_receipts: list[dict] = []
    original_embed_stamped = embedding_service.embed_stamped

    def observe_embed_stamped(text):
        vector, backend = original_embed_stamped(text)
        embedding_receipts.append({"backend": backend, "dimensions": len(vector)})
        return vector, backend

    embedding_service.embed_stamped = observe_embed_stamped
    context_retrieval.embed_stamped = observe_embed_stamped

    # --- normal ingestion into the disposable home ---------------------------
    MAIN_CHAT = args.chat_id
    FOREIGN_CHAT = "neighbor-notes"
    seed_receipts: list[dict] = []
    if not args.skip_seed:
        from core.context_namespace import ensure_chat_namespace
        from core.context_retrieval import store_turn
        from core.memory.entries import resolve_memory_access_policy

        turns = json.loads(Path(args.seed_json).read_text()) if args.seed_json else []
        for chat in sorted({MAIN_CHAT, FOREIGN_CHAT, *(entry["chat"] for entry in turns)}):
            ensure_chat_namespace(chat, grant_current_receipts=False)
        for entry in turns:
            result = store_turn(
                entry["chat"],
                entry["user"],
                entry["assistant"],
                access_policy=resolve_memory_access_policy(chat_id=entry["chat"]),
                source_context={
                    "chat_id": entry["chat"],
                    "runtime_home": home,
                    "statement_at": _epoch(entry["stated"]),
                },
            )
            seed_receipts.append(
                {"chat": entry["chat"], "status": result.get("status")}
            )

    # --- the scripted provider manifest (pinning the served route) -----------
    from core.model_registry import ModelRegistry

    registry = ModelRegistry()
    _manifest = registry.register_manifest(
        {
            "provider_name": PROVIDER_MODEL,
            "model_name": PROVIDER_MODEL,
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": [
                "summarize",
                "classify",
                "structured_json",
                "format",
                "extract",
                "tool_intent",
                "long_context",
                "code_basic",
            ],
            "runtime_config": {"base_url": "http://127.0.0.1:9"},
            "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    from tests._authorship_certification import certify_for_authorship
    certify_for_authorship(_manifest)

    # --- the agent under its real served path --------------------------------
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(
        backend_name="test-served", device="served-authority-test", persona_id="default"
    )
    agent._sync_public_presence = lambda *a, **k: None  # no public presence I/O
    agent.start()

    # the production /api/chat door: run_agent funnels into VoolAgent.run_once
    # with the full ingress context (surface, histories, runtime home, workspace)
    from core.web.api.runtime import RuntimeServices, run_agent

    runtime = RuntimeServices(
        agent=agent, runtime_home=home, runtime_model_tag=PROVIDER_MODEL
    )

    deletion = None
    if args.delete_token:
        from core.context_retrieval import _AGENT_ID
        from core.vool_memory import VoolMemory

        mem = VoolMemory(runtime_home=home, agent_id=_AGENT_ID)
        try:
            node_hits = mem.node_invalidate_matching(
                args.delete_token, session_id=MAIN_CHAT
            )
            occ_hits = mem.occurrence_invalidate_matching(
                args.delete_token, chat_scope=MAIN_CHAT
            )
        finally:
            mem.close()
        deletion = {"token": args.delete_token, "nodes": node_hits, "occurrences": occ_hits}

    turns_doc = json.loads(Path(args.turns_json).read_text())
    turns = turns_doc["turns"] if isinstance(turns_doc, dict) else turns_doc
    from core.context_retrieval import get_last_retrieval_telemetry

    turn_records = []
    for turn in turns:
        calls.clear()
        receipt_start = len(embedding_receipts)
        state["id"] = turn["id"]
        state["scripted"] = turn.get("scripted", "")
        state["malformed_auxiliary"] = turn.get("malformed_auxiliary", False)
        state["malformed_final"] = turn.get("malformed_final", False)
        state["finish_reason"] = turn.get("finish_reason", "stop")
        state["reader_require"] = turn.get("reader_require") or []
        source_context: dict = {
            "surface": str(turn.get("surface") or "openclaw"),
            "platform": str(turn.get("surface") or "openclaw"),
            "allow_remote_fetch": False,
            "chat_id": MAIN_CHAT,
            "runtime_home": home,
            "requested_model": PROVIDER_MODEL,
            "conversation_history": [{"role": "user", "content": turn["question"]}],
            "request_id": "offline-" + turn["id"],
            "cancel_turn_id": "offline-" + turn["id"],
            "turn_id": "offline-" + turn["id"],
            "client_conversation_history": [
                {"role": "user", "content": turn["question"]}
            ],
        }
        from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink
        stream_id = "offline-stream-" + turn["id"]
        source_context["runtime_event_stream_id"] = stream_id
        observed_events = []
        def capture_event(event):
            if event.get("event_type") == "model_output_chunk":
                return
            # Preserve each decision once, replacing only large repeated text with
            # its measured hash/length. Full authorized source remains in wire/body.
            def compact(value):
                if isinstance(value, str) and len(value) > 2048:
                    return {"text_sha256": hashlib.sha256(value.encode()).hexdigest(), "characters": len(value), "capture": "hashed_large_text"}
                if isinstance(value, dict): return {k: compact(v) for k,v in value.items()}
                if isinstance(value, list): return [compact(v) for v in value]
                return value
            observed_events.append(compact(event))
        if not args.no_event_capture:
            register_runtime_event_sink(stream_id, capture_event)
        result = run_agent(
            runtime,
            turn["question"],
            session_id=MAIN_CHAT,
            source_context=source_context,
            workspace_root_provider=lambda: home,
        )
        unregister_runtime_event_sink(stream_id)
        # The served door commits the result (core.web.api.runtime._response_commit): finalization and the
        # grounding publication gate run there, after run_agent returns. Opt-in so earlier callers keep
        # their recorded shape.
        commit_record = None
        if args.commit:
            from core.web.api.runtime import _response_commit

            commit = _response_commit(result, source_context=result.get("source_context") or source_context)
            lifecycle = commit.get("grounding_lifecycle") if isinstance(commit.get("grounding_lifecycle"), dict) else {}
            commit_record = {
                "canonical_content": commit.get("canonical_content"),
                "status": commit.get("status"),
                "grounding_stages": lifecycle.get("stages"),
                "grounding_reason_codes": lifecycle.get("reason_codes"),
                "grounding_publication": lifecycle.get("publication"),
            }
        # The driver's own source_context dict NEVER receives the admitted
        # capsule record: run_agent merges it into a fresh base_context, and
        # run_once returns a copy of THAT context. The guard-visible record is
        # read from the two channels the guards themselves read
        # (admitted_capsule_evidence_text): the returned runtime context first,
        # the turn retrieval telemetry (last_admitted_capsule) second.
        result_context = (
            result.get("source_context")
            if isinstance(result.get("source_context"), dict)
            else {}
        )
        context_record = result_context.get("admitted_capsule_evidence")
        telemetry = dict(get_last_retrieval_telemetry() or {})
        telemetry_record = telemetry.get("last_admitted_capsule")
        capsule_record = (
            context_record
            if isinstance(context_record, dict)
            and str(context_record.get("text") or "").strip()
            else telemetry_record
        ) or {}
        turn_records.append(
            {
                "id": turn["id"],
                "question": turn["question"],
                "scripted_raw": state["scripted"],
                "raw_provider_replies": [c["raw_provider_reply"] for c in calls if "raw_provider_reply" in c],
                "delivered": result.get("response"),
                "committed": commit_record,
                "model_calls": result.get("model_calls"),
                "result_keys": sorted(str(k) for k in result),
                "mode": result.get("mode"),
                "action_details": result.get("details"),
                "runtime_events": observed_events,
                "request_identity": {k: source_context.get(k) for k in ("request_id", "cancel_turn_id", "turn_id")},
                "fulfillment_outcome": result.get("fulfillment_outcome"),
                "response_control": result.get("response_control"),
                "model_execution_decision": result.get("model_execution_decision"),
                "diagnostic_decisions": {key: result_context.get(key) for key in (
                    "past_time_support_decision", "execution_requirements", "current_information_required",
                    "requested_answer_contract", "raw_output_contract", "claim_conviction", "evidence_verification")},
                "diagnostic_capture_status": "null means absent from returned runtime context; not reconstructed",

                "routes": {
                    key: result.get(key)
                    for key in (
                        "route",
                        "route_reason",
                        "route_skips",
                        "model_selected",
                        "backend",
                        "model_residency",
                        "model_execution",
                        "fast_path_hit",
                        "capsule_mode",
                        "web_calls",
                        "prompt_eval_count",
                    )
                    if key in result
                },
                "selected_facts": telemetry.get("selected_facts") or [],
                "admitted_capsule": capsule_record.get("text") or "",
                "admitted_request_evidence_identity": {key: value for key,value in capsule_record.items() if key not in {"text", "evidence_texts"}},
                "actual_runtime_identities": {key: result_context.get(key) for key in ("_canonical_user_turn_id", "current_turn_id", "request_id", "cancel_turn_id", "turn_id")},
                "admitted_capsule_source": (
                    "result_context"
                    if isinstance(context_record, dict)
                    and str(context_record.get("text") or "").strip()
                    else (
                        "telemetry"
                        if isinstance(telemetry_record, dict)
                        and str(telemetry_record.get("text") or "").strip()
                        else "none"
                    )
                ),
                "request_calls": [dict(c) for c in calls],
                "embedding_receipts": [dict(r) for r in embedding_receipts[receipt_start:]],
                "evidence_semantic_backend": telemetry.get("evidence_semantic_backend"),
            }
        )

    # the synthetic manifest is this driver's own registration; remove it so no
    # other process can ever resolve the scripted provider
    from storage.db import get_connection

    connection = get_connection()
    try:
        connection.execute(
            "DELETE FROM model_provider_manifests WHERE provider_name = ?",
            (PROVIDER_MODEL,),
        )
        connection.commit()
    finally:
        connection.close()

    head = ""
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
    except Exception:
        head = ""

    document = {
        "proof_level": (
            "REAL VoolAgent.run_once served path; scripted choice at the external "
            "transport boundary only (requests.post/get sink), disclosed per call; "
            "normal ingestion, real retrieval/assembly/normalizer/sealing/guards; "
            "all network disabled, production hash fallback; "
            "NOT live-model accuracy"
        ),
        "source_head": head,
        "network_blocked": True,
        "event_observation_enabled": not args.no_event_capture,
        "blocked_network_attempts": blocked_network,
        "reader_contract": "Deterministic fixture gated by exact supplied source fragments; NOT live model reasoning",
        "embedding_backend": ", ".join(sorted({r["backend"] for r in embedding_receipts})) or "not-observed",
        "embedding_receipts": embedding_receipts,
        "provider_model": PROVIDER_MODEL,
        "flag_off": bool(args.flag_off),
        "app_default_configuration": bool(args.app_default),
        "library_unset_configuration": bool(args.library_unset),
        "configuration_receipt": configuration_receipt,
        "controlled_reference_date": args.reference_date or None,
        "parent_only_reply_control": bool(args.control_dir),
        "gold_canary_read_denied": gold_canary_denied,
        "deletion": deletion,
        "seed_receipts": seed_receipts,
        "turns": turn_records,
    }
    # Source completeness/provenance control reads the actual retained rows.
    import sqlite3
    memory_path = os.path.join(home, "data", "memory", "vool_memory.db")
    if os.path.exists(memory_path):
        con = sqlite3.connect("file:" + memory_path + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            document["retained_sources"] = [dict(row) for row in con.execute(
                "SELECT occurrence_id, chat_scope, role, authority, body, statement_at, status FROM source_occurrences ORDER BY rowid"
            )]
            document["indexed_roles"] = [dict(row) for row in con.execute(
                "SELECT n.content, o.role FROM memory_nodes n LEFT JOIN source_occurrences o ON o.occurrence_id=n.source_occurrence_id"
            )]
        finally:
            con.close()
    # A source-complete capability control for the deterministic fixture.
    # This is an explicit hypothetical transport input, never injected into
    # serving, and does not claim live reader accuracy or retrieval success.
    complete_text = "\n".join(
        f"- {row['role']} said: {row['body']}" for row in document.get("retained_sources", [])
        if row["chat_scope"] == MAIN_CHAT and row["status"] == "active"
    )
    document["source_complete_reader_control"] = [
        {"id": turn["id"],
         "proof_level": "Fixture only over complete active same-chat sources; not native retrieval or live inference",
         "sufficient": all(fragment in complete_text for fragment in turn.get("reader_require", [])),
         "raw_reply": turn.get("scripted", "")}
        for turn in turns if turn.get("reader_require")
    ]
    with open(args.out, "w") as handle:
        json.dump(document, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
