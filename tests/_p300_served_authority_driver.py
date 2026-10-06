"""Offline served-path driver for the paired300 authority-repair served proof.

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
    python tests/_p300_served_authority_driver.py \
        --home <disposable-profile> --out <result.json> --repo-root <repo> \
        [--turns-json <turns.json>] [--skip-seed] [--flag-off] [--delete-token T]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys


def _epoch(date: str) -> float:
    return _dt.datetime.fromisoformat(date).replace(
        tzinfo=_dt.timezone.utc
    ).timestamp()


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
    args = parser.parse_args()

    repo = args.repo_root
    if repo not in sys.path:
        sys.path.insert(0, repo)
    os.environ["VOOL_HOME"] = os.path.abspath(args.home)
    os.environ["VOOL_HOME"] = os.path.abspath(args.home)
    # keep installed ollama CHAT models out of the provider universe so the
    # only selectable provider is the scripted one registered below (the local
    # EMBEDDING service is a separate endpoint and stays reachable)
    os.environ["VOOL_REGISTER_INSTALLED_OLLAMA_MODELS"] = "0"
    if args.flag_off:
        os.environ.pop("VOOL_CONTEXT_CAPSULE_V2", None)
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
    import requests

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

    def _sink_post(url, **kwargs):
        payload = kwargs.get("json")
        if str(url).startswith(_LOCAL_EMBEDDING_PREFIX):
            # the local embedding backend is an ALLOWED local service: recorded,
            # then passed through untouched
            calls.append(
                {
                    "method": "POST",
                    "url": str(url),
                    "payload": None,
                    "payload_bytes": "",
                    "scripted_choice": "",
                    "local_passthrough": True,
                }
            )
            return _REAL_POST(url, **kwargs)
        _systems = [str(m.get("content") or "") for m in (payload or {}).get("messages", []) if m.get("role") == "system"]
        if any(x.lstrip().startswith("You decide whether one user question turns on an entity") for x in _systems):
            return _ScriptedResponse({"choices": [{"message": {"content": json.dumps({"ambiguous": False, "referents": [], "clarification": ""})}, "finish_reason": "stop"}], "model": PROVIDER_MODEL, "usage": {"prompt_tokens": 0, "completion_tokens": 0}})
        calls.append(
            {
                "method": "POST",
                "url": str(url),
                "payload": payload,
                "payload_bytes": json.dumps(payload) if payload is not None else "",
                "scripted_choice": state["scripted"],
            }
        )
        return _ScriptedResponse(
            {
                "choices": [
                    {"message": {"content": state["scripted"]}, "finish_reason": "stop"}
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
                    "local_passthrough": True,
                }
            )
            return _REAL_GET(url, **kwargs)
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
    import core.embedding_service as embedding_service
    import core.context_retrieval as context_retrieval

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
    MAIN_CHAT = "allotment-log"
    FOREIGN_CHAT = "neighbor-notes"
    seed_receipts: list[dict] = []
    if not args.skip_seed:
        from core.context_namespace import ensure_chat_namespace
        from core.context_retrieval import store_turn
        from core.memory.entries import resolve_memory_access_policy

        for chat in (MAIN_CHAT, FOREIGN_CHAT):
            ensure_chat_namespace(chat, grant_current_receipts=False)
        policy = resolve_memory_access_policy(chat_id=MAIN_CHAT)
        turns = json.loads(open(args.seed_json).read()) if args.seed_json else []
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
    manifest = registry.register_manifest(
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
    certify_for_authorship(manifest)

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

    turns_doc = json.loads(open(args.turns_json).read())
    turns = turns_doc["turns"] if isinstance(turns_doc, dict) else turns_doc
    from core.bootstrap_context import admitted_capsule_evidence_text
    from core.context_retrieval import get_last_retrieval_telemetry

    turn_records = []
    for turn in turns:
        calls.clear()
        receipt_start = len(embedding_receipts)
        state["scripted"] = turn["scripted"]
        source_context: dict = {
            "surface": "openclaw",
            "platform": "openclaw",
            "allow_remote_fetch": False,
            "chat_id": MAIN_CHAT,
            "runtime_home": home,
            "requested_model": PROVIDER_MODEL,
            "conversation_history": [{"role": "user", "content": turn["question"]}],
            "client_conversation_history": [
                {"role": "user", "content": turn["question"]}
            ],
        }
        result = run_agent(
            runtime,
            turn["question"],
            session_id=MAIN_CHAT,
            source_context=source_context,
            workspace_root_provider=lambda: home,
        )
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
                "delivered": result.get("response"),
                "model_calls": result.get("model_calls"),
                "result_keys": sorted(str(k) for k in result),
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
            "NOT live-model accuracy"
        ),
        "source_head": head,
        "embedding_backend": ", ".join(sorted({r["backend"] for r in embedding_receipts})) or "not-observed",
        "embedding_receipts": embedding_receipts,
        "provider_model": PROVIDER_MODEL,
        "flag_off": bool(args.flag_off),
        "deletion": deletion,
        "seed_receipts": seed_receipts,
        "turns": turn_records,
    }
    with open(args.out, "w") as handle:
        json.dump(document, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
