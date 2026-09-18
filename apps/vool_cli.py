from __future__ import annotations
import argparse
import getpass
import json
import os

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import shutil
import sys as _bootstrap_sys
from pathlib import Path

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
from apps.vool_agent import VoolAgent
from apps.vool_daemon import DaemonConfig, VoolDaemon
from core import policy_engine
from core.adaptation_autopilot import (
    get_adaptation_autopilot_status,
    schedule_adaptation_autopilot_tick,
    score_adaptation_corpus,
)
from core.adaptation_dataset import build_adaptation_corpus
from core.control_plane_workspace import sync_control_plane_workspace
from core.credit_ledger import reconcile_ledger
from core.dna_wallet_manager import DNAWalletManager
from core.identity_lifecycle import identity_lifecycle_snapshot
from core.identity_manager import load_active_persona
from core.install_recommendations import build_install_recommendation_truth
from core.local_worker_pool import resolve_local_worker_capacity
from core.lora_training_pipeline import promote_adaptation_job, run_adaptation_job
from core.null_protocol import NullProtocolError, resolve_null_request
from core.null_resolver import NullDomainRecord, resolve_null_domain
from core.release_channel import release_manifest_snapshot
from core.runtime_backbone import build_provider_registry_snapshot, build_runtime_backbone
from core.runtime_bootstrap import (
    bootstrap_storage_environment,
)
from core.runtime_context import build_runtime_context
from core.runtime_install_profiles import (
    INSTALL_PROFILE_CHOICES,
    PUBLIC_INSTALL_PROFILE_CHOICES,
    active_install_profile_id,
    build_install_profile_truth,
    format_install_profile_id,
    install_profile_display_choices,
    installed_profile_id,
    normalize_install_profile_id,
    persist_install_profile_record,
)
from core.runtime_paths import data_path
from core.trainable_base_manager import stage_trainable_base, trainable_base_status
from core.web0_capability_broadcast import build_manifest_from_env
from network.signer import get_local_peer_id
from storage.adaptation_store import (
    create_adaptation_corpus,
    create_adaptation_job,
    get_adaptation_corpus,
    list_adaptation_eval_runs,
    list_adaptation_job_events,
    list_adaptation_jobs,
    update_corpus_build,
)


def _bootstrap_cli_storage() -> None:
    bootstrap_storage_environment(context=build_runtime_context(mode="cli_storage"))


def cmd_up() -> int:
    # 1. Boot the canonical runtime environment first.
    try:
        backbone = build_runtime_backbone(
            mode="cli_up",
            resolve_backend=True,
        )
    except RuntimeError as exc:
        print(f"Vool could not start: {exc}")
        return 1

    # 2. Surface provider warnings after policy/bootstrap is loaded.
    provider_warnings = list(backbone.provider_snapshot.warnings)
    if provider_warnings:
        print("Model provider warnings:")
        for warning in provider_warnings:
            print(f" - {warning}")

    # 3. Load local-only persona
    persona = load_active_persona("default")

    # 4. Ensure node identity exists
    peer_id = get_local_peer_id()

    # 5. Auto-detect backend
    selection = backbone.boot.backend_selection
    if selection is None:
        print("Vool could not start: no supported backend found.")
        print("Install at least one supported runtime: mlx, torch, or onnxruntime.")
        return 1
    hw = selection.hardware
    if selection.backend_name == "remote_only":
        print("No local model backend found. Running in remote-only mode.")

    # 6. Start local agent + local node
    agent = VoolAgent(
        backend_name=selection.backend_name,
        device=selection.device,
        persona_id=persona.persona_id,
    )
    agent_runtime = agent.start()

    pool_hard_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    policy_target = int(policy_engine.get("orchestration.local_worker_pool_target", 0) or 0)
    env_override_raw = str(os.environ.get("VOOL_DAEMON_CAPACITY", "")).strip()
    requested_capacity: int | None = None
    if env_override_raw:
        try:
            requested_capacity = max(1, int(env_override_raw))
        except Exception:
            print(f"Invalid VOOL_DAEMON_CAPACITY='{env_override_raw}', ignoring override.")
            requested_capacity = None
    elif policy_target > 0:
        requested_capacity = policy_target

    daemon_capacity, recommended_capacity = resolve_local_worker_capacity(
        requested=requested_capacity if requested_capacity is not None else None,
        hard_cap=pool_hard_cap,
    )
    if daemon_capacity > recommended_capacity:
        print(
            "WARNING: Local helper capacity override is above recommended "
            f"({daemon_capacity} > {recommended_capacity}). This can degrade stability."
        )

    daemon = VoolDaemon(
        DaemonConfig(
            capacity=int(daemon_capacity),
            local_worker_threads=max(2, int(daemon_capacity) * 2),
        )
    )
    node_runtime = daemon.start()

    print("======================================")
    print("Vool is yours and running.")
    print("======================================")
    print(f"OS:            {hw.os_name}")
    print(f"Machine:       {hw.machine}")
    print(f"Backend:       {agent_runtime.backend_name}")
    print(f"Device:        {agent_runtime.device}")
    print(f"Persona:       {persona.display_name} ({persona.persona_id})")
    print(f"Tone:          {persona.tone}")
    print(f"Spirit lock:   {'enabled' if persona.personality_locked else 'disabled'}")
    print(f"Swarm:         {'enabled' if agent_runtime.swarm_enabled else 'disabled'}")
    print(f"Node:          {node_runtime.host}:{node_runtime.port}")
    print(f"Public Node:   {node_runtime.public_host}:{node_runtime.public_port}")
    print(f"Helper Pool:   {daemon_capacity} (recommended: {recommended_capacity})")
    print(f"Peer ID:       {peer_id[:24]}...")
    print(f"Reason:        {selection.reason}")
    print(f"Safety mode:   {policy_engine.get('execution.default_mode')}")
    print("Identity:      local-only / never synced")
    print("Mode:          standalone-capable / optional sidecars")
    return 0


def cmd_summary(json_mode: bool = False, limit: int = 5) -> int:
    _bootstrap_cli_storage()
    # GENERATED_ADAPTER: the command registry owns this action (status.show).
    from core.command_registry.execute import ExecutionContext, execute_command
    from core.vool_user_summary import render_user_summary

    envelope = execute_command("status.show", {}, context=ExecutionContext(projection="cli"))
    if not envelope.ok:
        print(envelope.summary)
        return 1
    if json_mode:
        _emit_json(envelope.data)
        return 0
    print(render_user_summary(envelope.data))
    return 0

def _strip_null_suffix(name: str) -> str:
    """Normalize a .null name: trim whitespace and a single trailing '.null'."""
    value = str(name or "").strip()
    if value.lower().endswith(".null"):
        value = value[: -len(".null")]
    return value.strip()


def _explorer_tx_link(signature: str) -> str:
    """Copy-pasteable mainnet explorer link for a Solana tx signature."""
    return f"https://explorer.solana.com/tx/{signature}"


def resolve_record_payload(name: str, record: NullDomainRecord | None) -> dict[str, object]:
    """Pure: shape a resolved .null record (or a miss) into a serializable payload."""
    if record is None:
        return {"name": name, "resolved": False}
    return {
        "name": name,
        "resolved": True,
        "owner": record.owner,
        "arweave_txid": record.arweave_txid,
        "x402_endpoint": record.x402_endpoint or "",
        "passport_present": record.passport_hash is not None,
    }


def render_resolve_lines(payload: dict[str, object]) -> list[str]:
    """Pure: human-readable lines for a resolve payload."""
    name = str(payload.get("name") or "")
    lines = [
        "VOOL .null resolution",
        "======================",
        f"Name:           {name}.null",
    ]
    if not payload.get("resolved"):
        lines.append("Status:         unresolved (no on-chain record)")
        return lines
    arweave = payload.get("arweave_txid")
    endpoint = str(payload.get("x402_endpoint") or "")
    lines.extend(
        [
            f"Owner:          {payload.get('owner')}",
            f"Arweave txid:   {arweave if arweave else 'none'}",
            f"x402 endpoint:  {endpoint if endpoint else 'unset'}",
            f"Passport:       {'present' if payload.get('passport_present') else 'none'}",
        ]
    )
    return lines


def cmd_resolve(name: str, *, json_mode: bool = False) -> int:
    clean = _strip_null_suffix(name)
    if not clean:
        print("usage: vool resolve <name>.null")
        return 2
    record = resolve_null_domain(clean)
    payload = resolve_record_payload(clean, record)
    if json_mode:
        _emit_json(payload)
        return 0 if payload.get("resolved") else 1
    print("\n".join(render_resolve_lines(payload)))
    return 0 if payload.get("resolved") else 1


WEB_DISABLED_MESSAGE = "Web access is off. Enable it with VOOL_ENABLE_WEB=1 (web is opt-in and off by default)."


def _run_web_intent(intent: str, arguments: dict[str, object]) -> object:
    """Dispatch a single web tool intent through the canonical executor.

    Only reached when web is enabled; the caller gates on the policy flag so no
    network call happens while web is off.
    """
    from core.hive_activity_tracker import HiveActivityTracker
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent(
        {"intent": intent, "arguments": dict(arguments)},
        task_id="cli-web",
        session_id="cli-web",
        source_context={"surface": "cli", "platform": "vool-cli"},
        hive_activity_tracker=HiveActivityTracker(),
    )


def cmd_web(
    *,
    query: str = "",
    fetch_url: str = "",
    render_url: str = "",
    limit: int = 5,
    json_mode: bool = False,
) -> int:
    """Run a live web search/fetch/browse, only when web access is opted in."""
    if fetch_url:
        intent, arguments, requested = "web.fetch", {"url": fetch_url}, fetch_url
    elif render_url:
        intent, arguments, requested = "browser.render", {"url": render_url}, render_url
    else:
        clean_query = str(query or "").strip()
        if not clean_query:
            print("usage: vool web <query> | --fetch <url> | --browse <url>")
            return 2
        intent, arguments, requested = (
            "web.search",
            {"query": clean_query, "limit": max(1, min(int(limit or 5), 5))},
            clean_query,
        )

    if not policy_engine.allow_web_fallback():
        if json_mode:
            _emit_json({"intent": intent, "enabled": False, "message": WEB_DISABLED_MESSAGE})
        else:
            print(WEB_DISABLED_MESSAGE)
        return 2

    _bootstrap_cli_storage()
    execution = _run_web_intent(intent, arguments)
    response_text = str(getattr(execution, "response_text", "") or "").strip()
    ok = bool(getattr(execution, "ok", False))
    status = str(getattr(execution, "status", "") or "").strip()
    if json_mode:
        _emit_json(
            {
                "intent": intent,
                "enabled": True,
                "requested": requested,
                "ok": ok,
                "status": status,
                "response_text": response_text,
            }
        )
        return 0 if ok else 1
    print(response_text or f"web {intent} returned no output (status={status or 'unknown'}).")
    return 0 if ok else 1


_RECEIPT_EVENT_TYPES = ("solana_proof_anchored", "parent_output_finalized")


def _load_receipt_rows(limit: int = 50) -> list[dict[str, object]]:
    """Local SQLite read of anchored / finalized proof events from audit_log."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT event_type, target_id, details_json, created_at
                FROM audit_log
                WHERE event_type IN (?, ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (_RECEIPT_EVENT_TYPES[0], _RECEIPT_EVENT_TYPES[1], max(1, int(limit))),
            ).fetchall()
        ]
    finally:
        conn.close()
    return rows


def format_receipt_row(row: dict[str, object]) -> dict[str, object]:
    """Pure: shape one audit_log row into a receipt entry with an explorer link."""
    details = row.get("details") if isinstance(row.get("details"), dict) else None
    if details is None:
        raw = row.get("details_json")
        try:
            details = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            details = {}
    signature = str(details.get("signature") or "")
    entry: dict[str, object] = {
        "event_type": str(row.get("event_type") or ""),
        "task_id": str(row.get("target_id") or ""),
        "confidence": float(details.get("confidence") or 0.0),
        "signature": signature,
        "created_at": str(row.get("created_at") or ""),
    }
    entry["explorer_link"] = _explorer_tx_link(signature) if signature else ""
    return entry


def render_receipt_lines(entries: list[dict[str, object]]) -> list[str]:
    """Pure: human-readable receipt block."""
    lines = ["VOOL receipts", "=============="]
    if not entries:
        lines.append("No anchored or finalized proofs recorded yet.")
        return lines
    for entry in entries:
        lines.append(f"{entry['event_type']}")
        lines.append(f"  Task:      {entry['task_id'] or 'unknown'}")
        lines.append(f"  Confidence:{' '}{float(entry['confidence']):.2f}")
        if entry.get("signature"):
            lines.append(f"  Signature: {entry['signature']}")
            lines.append(f"  Explorer:  {entry['explorer_link']}")
        else:
            lines.append("  Signature: none (local finalize, not yet anchored)")
        lines.append(f"  When:      {entry['created_at']}")
    return lines


def cmd_receipts(*, limit: int = 20, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = _load_receipt_rows(limit=max(1, min(int(limit), 200)))
    entries = [format_receipt_row(row) for row in rows]
    if json_mode:
        _emit_json(entries)
        return 0
    print("\n".join(render_receipt_lines(entries)))
    return 0


def cmd_session_bundle(args) -> int:
    """Export / inspect / import one conversation bundle. A thin shell over the ONE seam
    (core.session_portability.api); every typed refusal prints its stable code and exits 1."""
    from core.session_portability import api

    command = getattr(args, "bundle_command", "")
    try:
        if command == "preview":
            preview = api.preview_export(str(getattr(args, "session_id", "") or ""))
            print(f"Session:        {preview['session_id']}")
            print(f"Schema:         {preview['schema']} v{preview['schema_version']}")
            print(f"Turns:          {preview['counts']['turns']}")
            print(f"Summaries:      {preview['counts']['summaries']}")
            print(f"Obligation sets:{preview['counts']['obligation_sets']}")
            print(f"Tool receipts:  {preview['counts']['tool_receipts']}")
            print(f"Session events: {preview['counts']['session_events']}")
            print(f"Evidence refs:  {preview['counts']['evidence_refs']}")
            print(f"Profile items:  {preview['counts']['profile_items']}")
            print(f"Redactions:     {preview['redactions']}")
            for entry in preview["embedded_files"]:
                print(f"  file: {entry['path']} ({entry['size_bytes']} bytes)")
            print(f"Scope:          {preview['scope_note']}")
            return 0
        if command == "export":
            receipt = api.export_session(
                str(getattr(args, "session_id", "") or ""),
                str(getattr(args, "out", "") or ""),
                passphrase=str(getattr(args, "passphrase", "") or ""),
            )
            print(f"Exported:       {receipt['path']}")
            print(f"Bundle id:      {receipt['bundle_id']}")
            print(f"Encrypted:      {receipt['encrypted']}")
            print(f"Turns:          {receipt['counts']['turns']}")
            print(f"Redactions:     {receipt['redactions']}")
            return 0
        if command == "inspect":
            summary = api.inspect_bundle(
                str(getattr(args, "path", "") or ""),
                passphrase=str(getattr(args, "passphrase", "") or ""),
            )
            print(f"Session:        {(summary.get('session') or {}).get('session_id', '')}")
            print(f"Schema:         {summary.get('schema')} v{summary.get('schema_version')}")
            print(f"Bundle id:      {summary.get('bundle_id')}")
            print(f"Encrypted:      {summary.get('encrypted')}")
            counts = summary.get("counts") or {}
            print(f"Turns:          {counts.get('turns')}")
            print(f"Tool receipts:  {counts.get('tool_receipts')}")
            print(f"Manifest:       {summary.get('manifest', {}).get('algorithm')} over "
                  f"{len(summary.get('manifest', {}).get('entries') or [])} entries")
            return 0
        if command == "import":
            receipt = api.import_bundle(
                str(getattr(args, "path", "") or ""),
                passphrase=str(getattr(args, "passphrase", "") or ""),
                home=(str(getattr(args, "home", "") or "") or None),
            )
            print(f"Imported:       {receipt['imported_session_id']}")
            print(f"Source session: {receipt['source_session_id']}")
            print(f"Collision:      {receipt['collision']}")
            print(f"Turns:          {receipt['counts']['turns']}")
            print(f"Stale evidence: {receipt['evidence_marked_stale']} item(s) marked history")
            return 0
        print(f"Unknown session-bundle command: {command}")
        return 2
    except api.PortabilityRefused as exc:
        print(f"refused [{exc.code}] {exc.message}")
        return 1


def render_manifest_lines(manifest_dict: dict[str, object]) -> list[str]:
    """Pure: human-readable capability+price card for this node's manifest."""
    provider_ids = list(manifest_dict.get("provider_ids") or [])
    tools = list(manifest_dict.get("tools") or [])
    return [
        "VOOL capability manifest",
        "=========================",
        f"Worker ID:      {manifest_dict.get('worker_id')}",
        f"Top tier:       {manifest_dict.get('top_tier')}",
        f"Top TPS:        {float(manifest_dict.get('top_tps') or 0.0):.2f}",
        f"Context window: {int(manifest_dict.get('context_window') or 0)}",
        f"Providers:      {', '.join(provider_ids) if provider_ids else 'none registered'}",
        f"Tools:          {', '.join(tools) if tools else 'none'}",
        f"Price/token:    {float(manifest_dict.get('price_per_token_usdc') or 0.0):.8f} USDC",
        f"Privacy mode:   {manifest_dict.get('privacy_mode')}",
    ]


def cmd_manifest(*, json_mode: bool = False) -> int:
    manifest = build_manifest_from_env()
    payload = manifest.to_dict()
    if json_mode:
        _emit_json(payload)
        return 0
    print("\n".join(render_manifest_lines(payload)))
    return 0


def _quote_target_to_uri(target: str) -> str:
    """Map a `null://...` URI or a `<name>.null` into a null:// task URI."""
    value = str(target or "").strip()
    if value.lower().startswith("null://"):
        return value
    name = _strip_null_suffix(value)
    return f"null://task/{name}" if name else ""


def render_quote_lines(payload: dict[str, object]) -> list[str]:
    """Pure: invoice preview for a resolved null:// request."""
    quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else {}
    quote = quote or {}
    return [
        "VOOL sell-quote preview",
        "========================",
        f"URI:            {payload.get('uri')}",
        f"Service:        {payload.get('service')}",
        f"Path:           {payload.get('path') or '(none)'}",
        f"Session ID:     {payload.get('session_id')}",
        f"Amount:         {float(quote.get('amount_usdc') or 0.0):.8f} USDC",
        f"Recipient:      {quote.get('recipient_wallet') or 'unset'}",
        f"USDC mint:      {quote.get('usdc_mint') or 'unset'}",
        f"Quote hash:     {quote.get('quote_hash') or 'unset'}",
    ]


def cmd_sell_quote(target: str, *, json_mode: bool = False) -> int:
    uri = _quote_target_to_uri(target)
    if not uri:
        print("usage: vool sell-quote [null://service/path | <name>.null]")
        return 2
    try:
        request = resolve_null_request(uri)
    except NullProtocolError as exc:
        print(f"Invalid null:// request: {exc}")
        return 2
    quote = request.quote
    payload: dict[str, object] = {
        "uri": request.uri.raw,
        "service": request.uri.service,
        "path": request.uri.path,
        "session_id": request.session_id,
        "quote": None
        if quote is None
        else {
            "amount_usdc": quote.amount_usdc,
            "recipient_wallet": quote.recipient_wallet,
            "usdc_mint": quote.usdc_mint,
            "quote_hash": quote.quote_hash,
        },
    }
    if json_mode:
        _emit_json(payload)
        return 0
    print("\n".join(render_quote_lines(payload)))
    return 0


DIAL_DISABLED_MESSAGE = (
    "Remote dial is opt-in; enable with VOOL_ENABLE_NULL_DIAL=1 "
    "(remote dial is off by default). Payment is separately gated by "
    "--allow-spend within a cap."
)


def cmd_dial(
    name: str,
    task: str,
    *,
    allow_spend: bool = False,
    max_spend_usdc: float = 1.0,
    json_mode: bool = False,
) -> int:
    """Reach a named .null agent's endpoint and return its result.

    Gated on the null-dial policy flag: when off, this prints how to enable it and
    makes ZERO network calls.
    """
    if not policy_engine.null_dial_enabled():
        if json_mode:
            _emit_json({"enabled": False, "message": DIAL_DISABLED_MESSAGE})
        else:
            print(DIAL_DISABLED_MESSAGE)
        return 2

    clean = _strip_null_suffix(name)
    task_text = str(task or "").strip()
    if not clean or not task_text:
        print('usage: vool dial <name>.null "<task>" [--allow-spend --max-spend <usdc>]')
        return 2

    from core.null_dial import try_dial

    record = resolve_null_domain(clean)
    if record is None:
        payload = {"name": clean, "resolved": False, "dialed": False}
        if json_mode:
            _emit_json(payload)
        else:
            print(f"{clean}.null did not resolve to an on-chain record.")
        return 1
    if not record.x402_endpoint:
        payload = {"name": clean, "resolved": True, "dialed": False, "reason": "no_endpoint"}
        if json_mode:
            _emit_json(payload)
        else:
            print(f"{clean}.null has no x402 endpoint set; nothing to dial.")
        return 1

    spend_refusal: dict | None = None
    if allow_spend:
        # Paying a 402 from the terminal is a retired money surface: the only payer is core.wallet
        # (wallet.propose / POST /api/wallet/x402/fetch). Refuse it typed and receipted, then dial
        # WITHOUT spending so the operator still gets the reach result or the payment preview.
        from core.wallet.authority import refuse_legacy

        fault = refuse_legacy("cli.dial-allow-spend")
        spend_refusal = {"error": fault.code, "fault_id": fault.fault_id, "detail": fault.user_message}
        if not json_mode:
            print(f"{fault.code}: {fault.user_message}")
            print(f"  receipt: {fault.fault_id}")

    uri = _quote_target_to_uri(clean)
    result = try_dial(
        uri,
        task_text,
        record=record,
        wallet=None,
        allow_spend=False,
        owner_local=True,  # the machine owner's own terminal session
        max_spend_usdc=float(max_spend_usdc),
    )
    if result is None:
        payload = {"name": clean, "resolved": True, "dialed": False, "reason": "no_remote_result"}
        if spend_refusal:
            payload["spend"] = spend_refusal
        if json_mode:
            _emit_json(payload)
        else:
            print(f"{clean}.null could not be reached; run it locally instead.")
        return 1

    payload = {"name": clean, "resolved": True, "dialed": True, "endpoint": record.x402_endpoint, "result": result}
    if spend_refusal:
        payload["spend"] = spend_refusal
    if json_mode:
        _emit_json(payload)
    else:
        print(f"VOOL dial -> {clean}.null ({record.x402_endpoint})")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_x402_pay(
    amount_usdc: float,
    recipient: str,
    *,
    keypair_path: str = "",
    mainnet: bool = False,
    asset_mint: str = "",
    memo: str = "",
    allow_spend: bool = False,
    json_mode: bool = False,
) -> int:
    """RETIRED: the CLI never signs or settles a payment; core.wallet is the one money authority."""
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("cli.x402-pay")
    if json_mode:
        _emit_json({"ok": False, "error": fault.code, "fault_id": fault.fault_id, "message": fault.user_message})
    else:
        print(f"{fault.code}: {fault.user_message}")
        print("Payments are proposed and approved in the app (wallet.propose / the Wallet section); receipt " + fault.fault_id)
    return 2


def _nullpass_rpc(mode: str):
    """An RPC caller routed to the right cluster for an on-chain confirm."""
    from core.remote_fetch_policy import open_remote_url

    rpc_url = "https://api.devnet.solana.com" if mode == "devnet" else "https://solana-rpc.publicnode.com"

    def rpc(method: str, params: list):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        with open_remote_url(rpc_url, data=body, method="POST", timeout=15,
                             headers={"Content-Type": "application/json",
                                      "User-Agent": "vool/1.0"}) as r:
            return json.loads(r.read().decode()).get("result")
    return rpc


def cmd_nullpass(
    action: str,
    target: str,
    *,
    result: str = "",
    out: str = "",
    confirm_onchain: bool = False,
    json_mode: bool = False,
) -> int:
    """Issue or verify a portable .nullpass work credential."""
    from core.nullpass import verify_nullpass

    if action == "verify":
        if not target:
            print("usage: vool nullpass verify <file.nullpass> [--confirm-onchain]")
            return 2
        try:
            with open(target) as f:
                bundle = json.load(f)
        except Exception as exc:
            print(f"cannot read {target}: {exc}")
            return 2
        rpc = None
        if confirm_onchain:
            mode = str(((bundle.get("receipt") or {}).get("payment") or {}).get("mode", ""))
            rpc = _nullpass_rpc(mode)
        verdict = verify_nullpass(bundle, confirm_onchain=confirm_onchain, rpc_call=rpc)
        if json_mode:
            _emit_json(verdict)
        else:
            print(f"{'VALID' if verdict['valid'] else 'INVALID'} .nullpass  "
                  f"receipt={verdict.get('receipt_id')}  issuer={verdict.get('issuer')}")
            for name, ok in (verdict.get("checks") or {}).items():
                print(f"  [{'ok' if ok else 'FAIL'}] {name}")
        return 0 if verdict["valid"] else 1

    if action == "issue":
        if not target:
            print('usage: vool nullpass issue <task_id> --result "<text>" [--out file]')
            return 2
        from core.wallet.authority import refuse_legacy

        fault = refuse_legacy("cli.nullpass-issue")
        print(f"{fault.code}: {fault.user_message} (receipt {fault.fault_id})")
        return 2
        bundle = None  # unreachable: kept so the rest of the handler reads unchanged
        if out:
            with open(out, "w") as f:
                json.dump(bundle, f, indent=2)
            print(f"wrote {out}")
        if json_mode:
            _emit_json(bundle)
        elif not out:
            print(json.dumps(bundle, indent=2))
        return 0

    print("usage: vool nullpass <verify|issue> …")
    return 2


def cmd_providers(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    context = build_runtime_context(mode="cli_storage")
    snapshot = build_provider_registry_snapshot(
        runtime_home=str(context.paths.runtime_home),
        honor_install_profile=True,
    )
    rows = list(snapshot.audit_rows)
    if json_mode:
        import json

        print(
            json.dumps(
                [
                    {
                        "provider_id": row.provider_id,
                        "source_type": row.source_type,
                        "license_name": row.license_name,
                        "weight_location": row.weight_location,
                        "redistribution_allowed": row.redistribution_allowed,
                        "warnings": row.warnings,
                    }
                    for row in rows
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not rows:
        print("No model providers are registered.")
        return 0

    print("VOOL model providers")
    print("=====================")
    for row in rows:
        print(f"{row.provider_id}")
        print(f"  Source:          {row.source_type}")
        print(f"  License:         {row.license_name or 'MISSING'}")
        print(f"  License ref:     {row.license_reference or 'MISSING'}")
        print(f"  Runtime dep:     {row.runtime_dependency or 'MISSING'}")
        print(f"  Weight location: {row.weight_location}")
        print(f"  Weights bundled: {row.weights_bundled}")
        print(f"  Redistribution:  {row.redistribution_allowed if row.redistribution_allowed is not None else 'unknown'}")
        if row.warnings:
            print(f"  Warnings:        {'; '.join(row.warnings)}")
        else:
            print("  Warnings:        none")
    return 0


def cmd_install_profile(*, set_profile: str = "", json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    context = build_runtime_context(mode="cli_install_profile")
    runtime_home = str(context.paths.runtime_home)
    requested_profile = str(set_profile or "").strip().lower()
    requested_profile_normalized = normalize_install_profile_id(requested_profile, allow_auto=True)
    stored_profile = installed_profile_id(runtime_home)
    provider_snapshot = build_provider_registry_snapshot(
        runtime_home=runtime_home,
        requested_profile=requested_profile_normalized or requested_profile or None,
        honor_install_profile=False,
    )
    active_profile = active_install_profile_id(runtime_home=runtime_home, allow_auto=True)
    install_recommendation = build_install_recommendation_truth(runtime_home=runtime_home)

    if requested_profile:
        profile = build_install_profile_truth(
            requested_profile=requested_profile_normalized or requested_profile,
            runtime_home=runtime_home,
            provider_capability_truth=provider_snapshot.capability_truth,
        )
        if not profile.ready:
            message = "; ".join(profile.reasons) or "selected install profile is not ready on this machine"
            if json_mode:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "requested_profile": requested_profile,
                            "install_recommendation": install_recommendation.to_dict(),
                            "resolved_profile": profile.to_dict(),
                            "error": message,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                print(f"Install profile switch blocked: {requested_profile}")
                print(message)
            return 2
        saved_profile = profile.profile_id
        record_kwargs = {
            "selected_model": profile.selected_model,
        }
        if profile.selected_models:
            record_kwargs["selected_models"] = profile.selected_models
        if profile.bundle_id:
            record_kwargs["bundle_id"] = profile.bundle_id
        if profile.bundle_kind:
            record_kwargs["bundle_kind"] = profile.bundle_kind
        record_path = persist_install_profile_record(
            runtime_home,
            saved_profile,
            **record_kwargs,
        )
        if json_mode:
            print(
                json.dumps(
                        {
                            "ok": True,
                            "record_path": str(record_path),
                            "requested_profile": requested_profile,
                            "saved_profile": saved_profile,
                            "install_recommendation": install_recommendation.to_dict(),
                            "resolved_profile": profile.to_dict(),
                            "next_step": "Restart VOOL to apply the new provider mix.",
                        },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Install profile saved: {format_install_profile_id(saved_profile, allow_auto=False)}")
            print(f"Resolved profile:     {format_install_profile_id(profile.profile_id, allow_auto=False)} ({profile.label})")
            print(f"Summary:              {profile.summary}")
            if profile.selected_models:
                print(f"Bundle models:        {', '.join(profile.selected_models)}")
            print(f"Record:               {record_path}")
            print("Next step:            Restart VOOL to apply the new provider mix.")
        return 0

    profile = build_install_profile_truth(
        requested_profile=None,
        runtime_home=runtime_home,
        provider_capability_truth=provider_snapshot.capability_truth,
    )
    if json_mode:
        print(
            json.dumps(
                {
                    "runtime_home": runtime_home,
                    "stored_profile_id": stored_profile or "",
                    "requested_profile_id": active_profile or "",
                    "available_profiles": list(PUBLIC_INSTALL_PROFILE_CHOICES),
                    "all_profiles": list(INSTALL_PROFILE_CHOICES),
                    "install_recommendation": install_recommendation.to_dict(),
                    "resolved_profile": profile.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print("VOOL install profile")
    print("=====================")
    print(f"Runtime home:    {runtime_home}")
    print(f"Stored profile:  {format_install_profile_id(stored_profile, allow_auto=False) if stored_profile else 'none'}")
    resolved_profile_display = format_install_profile_id(profile.profile_id, allow_auto=False)
    print(f"Resolved profile:{' ' if resolved_profile_display else ''}{resolved_profile_display} ({profile.label})")
    print(
        f"Recommended default: {format_install_profile_id(install_recommendation.recommended_default_profile, allow_auto=False)}"
    )
    bundle_models = ", ".join(install_recommendation.recommended_bundle_models)
    if bundle_models:
        print(
            f"Recommended bundle:  {install_recommendation.recommended_bundle_id} "
            f"({install_recommendation.recommended_bundle_kind}) -> {bundle_models}"
        )
    fallback_models = ", ".join(install_recommendation.fallback_bundle_models)
    if fallback_models:
        print(f"Lighter fallback:    {install_recommendation.fallback_bundle_id} -> {fallback_models}")
    optional_profile_display = format_install_profile_id(
        install_recommendation.recommended_optional_profile,
        allow_auto=False,
    )
    if optional_profile_display:
        print(f"Optional stronger:   {optional_profile_display} via {install_recommendation.secondary_local_backend}")
    print(f"Summary:         {profile.summary}")
    print("Available:       " + ", ".join(install_profile_display_choices()))
    if profile.reasons:
        print("Notes:")
        for reason in profile.reasons:
            print(f" - {reason}")
    return 0


def cmd_identity_report(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    report = identity_lifecycle_snapshot()
    import json

    if json_mode:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print("VOOL identity lifecycle")
    print("======================")
    print(f"Active peer: {report['active_local_peer_id']}")
    print(f"Key path:    {report['key_path']}")
    print(f"Revocations: {len(report['revocations'])}")
    print(f"Key history: {len(report['key_history'])}")
    return 0


def cmd_release_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    report = release_manifest_snapshot()
    import json

    if json_mode:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print("VOOL release status")
    print("====================")
    print(f"Channel:       {report['channel_name']}")
    print(f"Version:       {report['release_version']}")
    print(f"Protocol:      {report['protocol_version']}")
    print(f"Schema gen:    {report['schema_generation']}")
    print(f"Min compat:    {report['minimum_compatible_release']}")
    print(f"Rollout stage: {report['rollout_stage']}")
    warnings = list(report.get("warnings") or [])
    print(f"Warnings:      {len(warnings)}")
    for warning in warnings:
        print(f" - {warning}")
    return 0


def _resolve_secret(value: str | None, *, prompt: str) -> str:
    raw = str(value or "").strip()
    if raw:
        return raw
    return getpass.getpass(prompt)


def _emit_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_credits(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    peer_id = get_local_peer_id()
    recon = reconcile_ledger(peer_id)
    if json_mode:
        import json
        print(json.dumps({
            "peer_id": recon.peer_id,
            "balance": recon.balance,
            "entries": recon.entries,
            "mode": recon.mode,
        }, indent=2))
        return 0
    print("VOOL compute credits")
    print("=====================")
    print(f"Peer ID:     {peer_id[:24]}...")
    print(f"Balance:     {recon.balance:.2f} credits")
    print(f"Ledger rows: {recon.entries}")
    print(f"Mode:        {recon.mode}")
    return 0


def cmd_adaptation_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = get_adaptation_autopilot_status()
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL adaptation status")
    print("======================")
    print(f"Deps ok:      {payload['dependency_status']['ok']}")
    print(f"Device:       {payload['dependency_status']['device']}")
    print(f"Modules:      {payload['dependency_status']['modules']}")
    print(f"Loop status:  {(payload.get('loop_state') or {}).get('status') or 'idle'!s}")
    print(f"Decision:     {(payload.get('loop_state') or {}).get('last_decision') or ''!s}")
    print(f"Reason:       {(payload.get('loop_state') or {}).get('last_reason') or ''!s}")
    print(f"Corpora:      {len(payload['recent_corpora'])}")
    print(f"Jobs:         {len(payload['recent_jobs'])}")
    print(f"Evals:        {len(payload['recent_evals'])}")
    print(f"Worker:       {'running' if payload.get('worker_running') else 'idle'}")
    return 0


def cmd_adaptation_corpus(
    *,
    corpus_id: str,
    label: str,
    include_conversations: bool,
    include_final_responses: bool,
    include_hive_posts: bool,
    limit_per_source: int,
    json_mode: bool = False,
) -> int:
    _bootstrap_cli_storage()
    if str(corpus_id or "").strip():
        result = build_adaptation_corpus(str(corpus_id).strip())
        payload = {
            "corpus_id": result.corpus_id,
            "output_path": result.output_path,
            "example_count": result.example_count,
            "source_stats": result.source_stats,
        }
    else:
        corpus = create_adaptation_corpus(
            label=str(label or "").strip() or "default-corpus",
            source_config={
                "include_conversations": bool(include_conversations),
                "include_final_responses": bool(include_final_responses),
                "include_hive_posts": bool(include_hive_posts),
                "limit_per_source": max(1, int(limit_per_source)),
            },
        )
        result = build_adaptation_corpus(str(corpus["corpus_id"]))
        payload = {
            "corpus_id": result.corpus_id,
            "output_path": result.output_path,
            "example_count": result.example_count,
            "source_stats": result.source_stats,
        }
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL adaptation corpus")
    print("=======================")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Output:       {payload['output_path']}")
    print(f"Examples:     {payload['example_count']}")
    print(f"Sources:      {payload['source_stats']}")
    return 0


def cmd_adaptation_corpus_import(*, input_path: str, label: str, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    source = Path(str(input_path or "").strip()).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Corpus input does not exist: {source}")
    corpus = create_adaptation_corpus(
        label=str(label or "").strip() or source.stem,
        source_config={"imported": True, "source_path": str(source)},
    )
    dest = data_path("adaptation", "corpora", f"{corpus['corpus_id']}.jsonl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    example_count = sum(1 for line in dest.read_text(encoding="utf-8").splitlines() if line.strip())
    update_corpus_build(corpus["corpus_id"], output_path=str(dest), example_count=example_count, source_stats={"imported": example_count})
    score = score_adaptation_corpus(corpus["corpus_id"], str(dest))
    payload = {
        "corpus_id": corpus["corpus_id"],
        "output_path": str(dest),
        "example_count": example_count,
        "quality_score": score.quality_score,
        "content_hash": score.content_hash,
    }
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL imported adaptation corpus")
    print("===============================")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Output:       {payload['output_path']}")
    print(f"Examples:     {payload['example_count']}")
    print(f"Quality:      {payload['quality_score']:.4f}")
    return 0


def cmd_adaptation_corpus_export(*, corpus_id: str, output_path: str, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    corpus = get_adaptation_corpus(str(corpus_id or "").strip())
    if not corpus:
        raise ValueError(f"Unknown corpus: {corpus_id}")
    source = Path(str(corpus.get("output_path") or "")).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Corpus file does not exist: {source}")
    target = Path(str(output_path or "").strip()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    payload = {
        "corpus_id": corpus["corpus_id"],
        "source_path": str(source),
        "output_path": str(target),
        "example_count": int(corpus.get("example_count") or 0),
    }
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL exported adaptation corpus")
    print("================================")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Source:       {payload['source_path']}")
    print(f"Output:       {payload['output_path']}")
    print(f"Examples:     {payload['example_count']}")
    return 0


def cmd_adaptation_job_create(
    *,
    corpus_id: str,
    base_model_ref: str,
    base_provider_name: str,
    base_model_name: str,
    adapter_provider_name: str,
    adapter_model_name: str,
    license_name: str,
    license_reference: str,
    capabilities: list[str],
    target_modules: list[str],
    epochs: int,
    max_steps: int,
    batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    cutoff_len: int,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    promote: bool = False,
    json_mode: bool = False,
) -> int:
    _bootstrap_cli_storage()
    payload = create_adaptation_job(
        corpus_id=str(corpus_id or "").strip(),
        base_model_ref=str(base_model_ref or "").strip(),
        base_provider_name=str(base_provider_name or "").strip(),
        base_model_name=str(base_model_name or "").strip(),
        adapter_provider_name=str(adapter_provider_name or "").strip(),
        adapter_model_name=str(adapter_model_name or "").strip(),
        training_config={
            "license_name": str(license_name or "").strip(),
            "license_reference": str(license_reference or "").strip(),
            "capabilities": list(capabilities or []),
            "target_modules": list(target_modules or []),
            "epochs": max(1, int(epochs)),
            "max_steps": max(1, int(max_steps)),
            "batch_size": max(1, int(batch_size)),
            "gradient_accumulation_steps": max(1, int(gradient_accumulation_steps)),
            "learning_rate": float(learning_rate),
            "cutoff_len": max(128, int(cutoff_len)),
            "lora_r": max(1, int(lora_r)),
            "lora_alpha": max(1, int(lora_alpha)),
            "lora_dropout": max(0.0, float(lora_dropout)),
        },
    )
    if promote:
        payload["promote_requested"] = True
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL adaptation job")
    print("====================")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Corpus ID:    {payload['corpus_id']}")
    print(f"Base model:   {payload['base_model_ref']}")
    print(f"Status:       {payload['status']}")
    return 0


def cmd_adaptation_jobs(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = list_adaptation_jobs(limit=100)
    if json_mode:
        _emit_json(rows)
        return 0
    if not rows:
        print("No adaptation jobs exist.")
        return 0
    print("VOOL adaptation jobs")
    print("=====================")
    for row in rows:
        print(f"{row['job_id']}")
        print(f"  Corpus:   {row['corpus_id']}")
        print(f"  Base:     {row['base_model_ref']}")
        print(f"  Status:   {row['status']}")
        print(f"  Device:   {row['device'] or 'pending'}")
        if row.get("output_dir"):
            print(f"  Output:   {row['output_dir']}")
        if row.get("error_text"):
            print(f"  Error:    {row['error_text']}")
    return 0


def cmd_adaptation_eval_runs(*, job_id: str = "", json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = list_adaptation_eval_runs(job_id=str(job_id or "").strip() or None, limit=100)
    if json_mode:
        _emit_json(rows)
        return 0
    if not rows:
        print("No adaptation eval runs exist.")
        return 0
    print("VOOL adaptation eval runs")
    print("=========================")
    for row in rows:
        print(f"{row['eval_id']}")
        print(f"  Job:      {row['job_id']}")
        print(f"  Kind:     {row['eval_kind']}")
        print(f"  Status:   {row['status']}")
        print(f"  Delta:    {row['score_delta']:.4f}")
        print(f"  Decision: {row['decision']}")
    return 0


def cmd_adaptation_job_events(job_id: str, *, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    rows = list_adaptation_job_events(str(job_id or "").strip(), limit=500)
    if json_mode:
        _emit_json(rows)
        return 0
    if not rows:
        print("No adaptation job events found.")
        return 0
    print("VOOL adaptation job events")
    print("===========================")
    for row in rows:
        print(f"[{row['seq']:03d}] {row['event_type']}: {row['message']}")
    return 0


def cmd_adaptation_job_run(job_id: str, *, promote: bool = False, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = run_adaptation_job(str(job_id or "").strip(), promote=bool(promote))
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL adaptation run")
    print("====================")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Status:       {payload['status']}")
    print(f"Device:       {payload.get('device') or 'unknown'}")
    if payload.get("output_dir"):
        print(f"Output:       {payload['output_dir']}")
    if payload.get("error_text"):
        print(f"Error:        {payload['error_text']}")
        return 1
    return 0


def cmd_adaptation_job_promote(job_id: str, *, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = promote_adaptation_job(str(job_id or "").strip())
    if json_mode:
        _emit_json(payload)
        return 0
    manifest = payload.get("registered_manifest") or {}
    print("VOOL adaptation promotion")
    print("==========================")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Status:       {payload['status']}")
    print(f"Provider:     {manifest.get('provider_name', '')}:{manifest.get('model_name', '')}")
    return 0


def cmd_adaptation_loop_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = get_adaptation_autopilot_status()
    if json_mode:
        _emit_json(payload)
        return 0
    loop_state = dict(payload.get("loop_state") or {})
    print("VOOL adaptation loop")
    print("=====================")
    print(f"Status:       {loop_state.get('status', 'idle')}")
    print(f"Decision:     {loop_state.get('last_decision', '')}")
    print(f"Reason:       {loop_state.get('last_reason', '')}")
    print(f"Active job:   {loop_state.get('active_job_id', '')}")
    print(f"Active model: {loop_state.get('active_provider_name', '')}:{loop_state.get('active_model_name', '')}")
    print(f"Last eval:    {loop_state.get('last_eval_id', '')}")
    print(f"Last canary:  {loop_state.get('last_canary_eval_id', '')}")
    return 0


def cmd_adaptation_loop_tick(*, force: bool = False, json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    payload = schedule_adaptation_autopilot_tick(force=bool(force), wait=True)
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL adaptation loop tick")
    print("==========================")
    print(f"Status:       {payload.get('status', '')}")
    print(f"Decision:     {payload.get('last_decision', '')}")
    print(f"Reason:       {payload.get('last_reason', '')}")
    return 0


def cmd_control_plane_sync(*, json_mode: bool = False) -> int:
    payload = sync_control_plane_workspace()
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL control-plane workspace sync")
    print("=================================")
    print(f"Workspace:    {payload['workspace_root']}")
    print(f"Control root: {payload['control_root']}")
    print(f"Templates:    {payload['templates_root']}")
    print(f"Writes:       {payload['writes']}")
    print(f"Open tasks:   {payload['open_task_count']}")
    print(f"Runs:         {payload['runtime_session_count']}")
    print(f"Approvals:    {payload['pending_approval_count']} backlog")
    print(f"Runtime wait: {payload.get('runtime_pending_approval_count', 0)} session(s)")
    return 0


def cmd_trainable_base_status(*, json_mode: bool = False) -> int:
    payload = trainable_base_status()
    if json_mode:
        _emit_json(payload)
        return 0
    active = dict(payload.get("active_policy") or {})
    print("VOOL trainable base status")
    print("===========================")
    print(f"Active base:  {active.get('base_model_name', '')}")
    print(f"Base ref:     {active.get('base_model_ref', '')}")
    print(f"Provider:     {active.get('base_provider_name', '')}")
    print(f"Staged bases: {len(payload.get('staged_bases') or [])}")
    return 0


def cmd_stage_trainable_base(
    *,
    model_ref: str,
    activate: bool,
    verify_load: bool,
    force_download: bool,
    license_name: str,
    license_reference: str,
    trust_remote_code: bool,
    json_mode: bool = False,
) -> int:
    payload = stage_trainable_base(
        model_ref=str(model_ref or "").strip() or "qwen-0.5b",
        activate=bool(activate),
        verify_load=bool(verify_load),
        force_download=bool(force_download),
        license_name=str(license_name or "").strip(),
        license_reference=str(license_reference or "").strip(),
        trust_remote_code=bool(trust_remote_code),
    )
    if json_mode:
        _emit_json(payload)
        return 0
    print("VOOL trainable base")
    print("====================")
    print(f"Model:        {payload['model_name']}")
    print(f"Repo:         {payload['model_id']}")
    print(f"Path:         {payload['local_path']}")
    print(f"Activated:    {payload['activated']}")
    verification = dict(payload.get("verification") or {})
    if verification:
        print(f"Tokenizer:    {verification.get('tokenizer_class', '')}")
        print(f"Parameters:   {verification.get('parameter_count', 0)}")
    return 0


def cmd_adaptation_autopilot(
    *,
    label: str,
    base_model_ref: str,
    base_provider_name: str,
    base_model_name: str,
    adapter_provider_name: str,
    adapter_model_name: str,
    limit_per_source: int,
    epochs: int,
    max_steps: int,
    batch_size: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    cutoff_len: int,
    lora_r: int,
    lora_alpha: int,
    lora_dropout: float,
    license_name: str,
    license_reference: str,
    capabilities: list[str],
    target_modules: list[str],
    promote: bool = False,
    json_mode: bool = False,
) -> int:
    _bootstrap_cli_storage()
    corpus = create_adaptation_corpus(
        label=str(label or "").strip() or "autopilot-corpus",
        source_config={
            "include_conversations": True,
            "include_final_responses": True,
            "include_hive_posts": True,
            "limit_per_source": max(1, int(limit_per_source)),
        },
    )
    built = build_adaptation_corpus(str(corpus["corpus_id"]))
    job = create_adaptation_job(
        corpus_id=built.corpus_id,
        base_model_ref=str(base_model_ref or "").strip(),
        base_provider_name=str(base_provider_name or "").strip(),
        base_model_name=str(base_model_name or "").strip(),
        adapter_provider_name=str(adapter_provider_name or "").strip(),
        adapter_model_name=str(adapter_model_name or "").strip(),
        training_config={
            "license_name": str(license_name or "").strip(),
            "license_reference": str(license_reference or "").strip(),
            "capabilities": list(capabilities or []),
            "target_modules": list(target_modules or []),
            "epochs": max(1, int(epochs)),
            "max_steps": max(1, int(max_steps)),
            "batch_size": max(1, int(batch_size)),
            "gradient_accumulation_steps": max(1, int(gradient_accumulation_steps)),
            "learning_rate": float(learning_rate),
            "cutoff_len": max(128, int(cutoff_len)),
            "lora_r": max(1, int(lora_r)),
            "lora_alpha": max(1, int(lora_alpha)),
            "lora_dropout": max(0.0, float(lora_dropout)),
        },
    )
    payload = run_adaptation_job(str(job["job_id"]), promote=bool(promote))
    result = {
        "corpus_id": built.corpus_id,
        "corpus_output_path": built.output_path,
        "corpus_example_count": built.example_count,
        "job": payload,
    }
    if json_mode:
        _emit_json(result)
        return 0
    print("VOOL adaptation autopilot")
    print("==========================")
    print(f"Corpus ID:    {built.corpus_id}")
    print(f"Examples:     {built.example_count}")
    print(f"Job ID:       {payload['job_id']}")
    print(f"Status:       {payload['status']}")
    if payload.get("output_dir"):
        print(f"Output:       {payload['output_dir']}")
    if payload.get("error_text"):
        print(f"Error:        {payload['error_text']}")
        return 1
    return 0


def cmd_wallet_init(*, hot_address: str, cold_address: str, cold_secret: str | None, hot_usdc: float, cold_usdc: float, json_mode: bool = False) -> int:
    """RETIRED simulated custody: the only wallet is core.wallet (set up in the app)."""
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("cli.wallet-init")
    if json_mode:
        _emit_json({"ok": False, "error": fault.code, "fault_id": fault.fault_id, "message": fault.user_message})
    else:
        print(f"{fault.code}: {fault.user_message}")
        print("Payments are proposed and approved in the app (wallet.propose / the Wallet section); receipt " + fault.fault_id)
    return 2


def cmd_wallet_status(json_mode: bool = False) -> int:
    _bootstrap_cli_storage()
    manager = DNAWalletManager()
    status = manager.get_status()
    if status is None:
        print("Wallet profile is not configured.")
        return 1
    if json_mode:
        import json

        print(json.dumps(status.to_dict(), indent=2, sort_keys=True))
        return 0
    print("DNA wallet status")
    print("=================")
    print(f"Hot wallet:   {status.hot_wallet_address}")
    print(f"Cold wallet:  {status.cold_wallet_address}")
    print(f"Hot USDC:     {status.hot_balance_usdc:.6f}")
    print(f"Cold USDC:    {status.cold_balance_usdc:.6f}")
    print(f"Hot auto use: {'enabled' if status.hot_auto_spend_enabled else 'disabled'}")
    return 0


def cmd_wallet_topup_hot(*, usdc: float, cold_secret: str | None, json_mode: bool = False) -> int:
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("cli.wallet-topup-hot")
    if json_mode:
        _emit_json({"ok": False, "error": fault.code, "fault_id": fault.fault_id, "message": fault.user_message})
    else:
        print(f"{fault.code}: {fault.user_message}")
        print("Payments are proposed and approved in the app (wallet.propose / the Wallet section); receipt " + fault.fault_id)
    return 2


def cmd_wallet_move_to_cold(*, usdc: float, cold_secret: str | None, json_mode: bool = False) -> int:
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("cli.wallet-move-cold")
    if json_mode:
        _emit_json({"ok": False, "error": fault.code, "fault_id": fault.fault_id, "message": fault.user_message})
    else:
        print(f"{fault.code}: {fault.user_message}")
        print("Payments are proposed and approved in the app (wallet.propose / the Wallet section); receipt " + fault.fault_id)
    return 2


def cmd_wallet_buy_credits(*, usdc: float, json_mode: bool = False) -> int:
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("cli.wallet-buy-credits")
    if json_mode:
        _emit_json({"ok": False, "error": fault.code, "fault_id": fault.fault_id, "message": fault.user_message})
    else:
        print(f"{fault.code}: {fault.user_message}")
        print("Payments are proposed and approved in the app (wallet.propose / the Wallet section); receipt " + fault.fault_id)
    return 2


def cmd_register(
    name: str,
    *,
    allow_spend: bool = False,
    max_spend_sol: float = 0.02,
    mainnet: bool = False,
    json_mode: bool = False,
) -> int:
    """Register a .null name (direct-sign). Preview-only unless --allow-spend AND --mainnet.

    The preview (name validity, availability, exact SOL cost, MAINNET flag) always runs and
    never signs. A real registration requires both flags, builds the spend gate from these
    explicit CLI flags (trusted context — never model output), and fires the OS-consent
    prompt (Windows Hello / CredUI) before signing. 1-3 char names are auction-only and
    refused here.
    """
    from core.null_register_execute import SpendGate, execute_registration, preview_registration
    from core.null_registrar import validate_registrable_name

    clean = _strip_null_suffix(name)
    if not clean:
        print('usage: vool register <name>.null [--allow-spend --max-spend <sol> --mainnet]')
        return 2

    ok, reason, _premium = validate_registrable_name(clean)
    if not ok:
        if json_mode:
            _emit_json({"name": clean, "registrable": False, "reason": reason})
        else:
            print(reason)
        return 2

    # Read-only adapter: the owner is the canonical core.wallet key. No legacy key file is
    # probed, loaded or minted here; signing is the canonical wallet's alone.
    from types import SimpleNamespace

    from core.wallet.custody import default_wallet

    registered = default_wallet()
    owner_pubkey = str(getattr(registered, "pubkey", "") or "")
    if not owner_pubkey:
        if json_mode:
            _emit_json({"name": clean, "status": "no_wallet", "message": "No wallet is registered; add one in the Wallet section first."})
        else:
            print("No wallet is registered on this machine; add one in the Wallet section first.")
        return 1
    wallet = SimpleNamespace(pubkey=owner_pubkey)

    preview = preview_registration(clean, owner_pubkey)
    if json_mode:
        _emit_json(
            {
                "name": clean,
                "status": preview.status,
                "message": preview.message,
                "cost_sol": (preview.plan.total_sol if preview.plan else None),
            }
        )
    else:
        print(preview.message)
    if preview.status != "preview":
        return 1

    if not (allow_spend and mainnet):
        if not json_mode:
            print("Dry run. Re-run with --allow-spend --max-spend <sol> --mainnet to register for real.")
        return 0

    cap_lamports = int(float(max_spend_sol) * 1_000_000_000)
    gate = SpendGate(allow_spend=True, approve=True, max_spend_lamports=cap_lamports, wallet_present=True)
    outcome = execute_registration(clean, gate=gate, wallet=wallet)
    if json_mode:
        _emit_json({"name": clean, "status": outcome.status, "message": outcome.message, "signature": outcome.signature})
    else:
        print(outcome.message)
    if outcome.status == "submitted":
        return 0
    return 2 if outcome.status == "refused" else 1


_LAMPORTS_PER_SOL = 1_000_000_000


def _sol_str(lamports: int | None) -> str:
    if lamports is None:
        return "unset"
    return f"{int(lamports) / _LAMPORTS_PER_SOL:.4f} SOL"


def _load_agent_wallet_or_none():
    """The legacy signing wallet is retired; spend-policy commands operate on the device-keyed policy file."""
    return None


def _policy_wallet_label() -> str:
    """Read-only: the canonical wallet the policy governs, or the device-keyed default."""
    try:
        from core.wallet.custody import default_wallet

        pubkey = str(getattr(default_wallet(), "pubkey", "") or "")
    except Exception:
        pubkey = ""
    return pubkey or "device-keyed policy (no wallet registered)"


def cmd_spend_policy(*, json_mode: bool = False) -> int:
    """Show the agent wallet's spend policy: freeze state, caps, and spend used/remaining."""
    import time

    from core.wallet_spend_policy import remaining_today, remaining_week
    from core.wallet_spend_policy_store import PolicyIntegrityError, load_policy_and_ledger

    wallet = _load_agent_wallet_or_none()
    try:
        # Operator-facing display: a not-yet-established policy shows permissive defaults
        # rather than erroring. The agent's spend-authorization path stays fail-closed.
        policy, ledger = load_policy_and_ledger(wallet, allow_missing_policy=True)
    except PolicyIntegrityError as exc:
        msg = f"Spend policy integrity check FAILED ({exc}). Possible tampering. Run `vool spend-freeze` to reset to a safe frozen state."
        if json_mode:
            _emit_json({"ok": False, "error": "integrity", "detail": str(exc)})
        else:
            print(msg)
        return 1

    from core.wallet import limits as wallet_limits

    now = time.time()
    day = 24 * 60 * 60
    week = 7 * day
    # The canonical freeze (core.wallet.limits) is authoritative; the legacy file mirrors it.
    frozen = bool(policy.frozen) or wallet_limits.is_frozen()
    payload = {
        "frozen": frozen,
        "per_tx_cap_lamports": policy.per_tx_cap_lamports or None,
        "daily_cap_lamports": policy.daily_cap_lamports or None,
        "weekly_cap_lamports": policy.weekly_cap_lamports or None,
        "spent_today_lamports": ledger.spent_within(now, day),
        "spent_week_lamports": ledger.spent_within(now, week),
        "remaining_today_lamports": remaining_today(policy, ledger, now),
        "remaining_week_lamports": remaining_week(policy, ledger, now),
    }
    if json_mode:
        _emit_json(payload)
        return 0
    lines = [
        "VOOL wallet spend policy",
        f"  Wallet:         {_policy_wallet_label()}",
        f"  Frozen:         {'YES (panic freeze active)' if frozen else 'no'}",
        f"  Per-tx cap:     {_sol_str(policy.per_tx_cap_lamports or None)}",
        f"  Daily cap:      {_sol_str(policy.daily_cap_lamports or None)}",
        f"  Weekly cap:     {_sol_str(policy.weekly_cap_lamports or None)}",
        f"  Spent today:    {_sol_str(payload['spent_today_lamports'])}",
        f"  Spent this wk:  {_sol_str(payload['spent_week_lamports'])}",
        f"  Left today:     {_sol_str(payload['remaining_today_lamports'])}",
        f"  Left this wk:   {_sol_str(payload['remaining_week_lamports'])}",
    ]
    print("\n".join(lines))
    return 0


def cmd_spend_freeze(*, json_mode: bool = False) -> int:
    """Panic freeze: instantly block all wallet spending. No consent needed to get safer."""
    from core.wallet_spend_policy import SpendLedger
    from core.wallet_spend_policy_store import load_policy_and_ledger, set_frozen_mirrored

    wallet = _load_agent_wallet_or_none()
    # Even if the on-disk policy is unreadable/tampered, freezing must still work: start
    # from a fresh policy and overwrite with a frozen, freshly-signed one. Track that path
    # so we can WARN that the prior caps were lost (rather than silently zeroing them).
    caps_reset = False
    try:
        # allow_missing: a first freeze on a wallet with no policy yet is clean (no caps
        # to lose). A genuinely tampered/unreadable policy still raises -> caps_reset warning.
        policy, ledger = load_policy_and_ledger(wallet, allow_missing_policy=True)
    except Exception:
        from core.wallet_spend_policy import SpendPolicy

        policy, ledger = SpendPolicy(), SpendLedger()
        caps_reset = True
    set_frozen_mirrored(True, wallet=wallet, policy=policy, ledger=ledger)
    if json_mode:
        _emit_json({"ok": True, "frozen": True, "caps_reset": caps_reset})
    else:
        print("Wallet FROZEN. All spending is blocked until you run `vool spend-unfreeze`.")
        if caps_reset:
            print(
                "WARNING: the previous spend policy could not be read (missing or tampered), so your "
                "caps were reset to none. Re-set them with `vool spend-set-cap` after you unfreeze."
            )
    return 0


def cmd_spend_unfreeze(*, json_mode: bool = False) -> int:
    """Unfreeze the wallet. Gated behind a live OS-consent prompt (Windows Hello)."""
    from core.os_consent_gate import require_os_user_consent
    from core.wallet import limits as wallet_limits
    from core.wallet_spend_policy_store import (
        PolicyIntegrityError,
        load_policy_and_ledger,
        set_frozen_mirrored,
    )

    wallet = _load_agent_wallet_or_none()
    try:
        # allow_missing: no policy on disk means nothing is frozen -> the unfreeze is a
        # no-op below. A tampered (present-but-invalid) policy still raises and is refused.
        policy, ledger = load_policy_and_ledger(wallet, allow_missing_policy=True)
    except PolicyIntegrityError as exc:
        # Never unfreeze from a tampered state.
        if json_mode:
            _emit_json({"ok": False, "error": "integrity", "detail": str(exc)})
        else:
            print(f"Refusing to unfreeze: spend policy integrity check failed ({exc}). Run `vool spend-freeze` to reset.")
        return 1
    if not policy.frozen and not wallet_limits.is_frozen():
        if json_mode:
            _emit_json({"ok": True, "frozen": False, "note": "already unfrozen"})
        else:
            print("Wallet is not frozen.")
        return 0
    # Unfreezing is loosening -> require a live OS confirmation. Fail closed on deny/unavailable.
    try:
        if not require_os_user_consent("Unfreeze the VOOL wallet spend policy (re-enable spending)"):
            if not json_mode:
                print("Unfreeze declined at the OS prompt. Wallet stays frozen.")
            return 1
    except Exception as exc:
        if not json_mode:
            print(f"OS consent unavailable ({exc}); wallet stays frozen.")
        return 1
    set_frozen_mirrored(False, wallet=wallet, policy=policy, ledger=ledger)
    if json_mode:
        _emit_json({"ok": True, "frozen": False})
    else:
        print("Wallet UNFROZEN. Spending is re-enabled (still subject to caps + OS consent per spend).")
    return 0


def cmd_spend_set_cap(
    *,
    per_tx: float | None = None,
    daily: float | None = None,
    weekly: float | None = None,
    clear: bool = False,
    json_mode: bool = False,
) -> int:
    """Set (or clear) the per-transaction / daily / weekly spend caps, in SOL."""
    from core.wallet_spend_policy_store import (
        PolicyIntegrityError,
        load_policy_and_ledger,
        save_policy_and_ledger,
    )

    wallet = _load_agent_wallet_or_none()
    try:
        # allow_missing: setting the first cap establishes the policy; bootstrap from
        # defaults. A tampered (present-but-invalid) policy still raises and is refused.
        policy, ledger = load_policy_and_ledger(wallet, allow_missing_policy=True)
    except PolicyIntegrityError as exc:
        if json_mode:
            _emit_json({"ok": False, "error": "integrity", "detail": str(exc)})
        else:
            print(f"Refusing to change caps: spend policy integrity check failed ({exc}). Run `vool spend-freeze` to reset.")
        return 1

    def _to_lamports(sol: float | None) -> int | None:
        if sol is None:
            return None
        return max(0, round(float(sol) * _LAMPORTS_PER_SOL))

    if clear:
        policy.per_tx_cap_lamports = 0
        policy.daily_cap_lamports = 0
        policy.weekly_cap_lamports = 0
    else:
        pt, dl, wk = _to_lamports(per_tx), _to_lamports(daily), _to_lamports(weekly)
        if pt is not None:
            policy.per_tx_cap_lamports = pt
        if dl is not None:
            policy.daily_cap_lamports = dl
        if wk is not None:
            policy.weekly_cap_lamports = wk

    save_policy_and_ledger(wallet, policy, ledger)
    if json_mode:
        _emit_json(
            {
                "ok": True,
                "per_tx_cap_lamports": policy.per_tx_cap_lamports or None,
                "daily_cap_lamports": policy.daily_cap_lamports or None,
                "weekly_cap_lamports": policy.weekly_cap_lamports or None,
            }
        )
    else:
        print("Spend caps updated:")
        print(f"  Per-tx: {_sol_str(policy.per_tx_cap_lamports or None)}")
        print(f"  Daily:  {_sol_str(policy.daily_cap_lamports or None)}")
        print(f"  Weekly: {_sol_str(policy.weekly_cap_lamports or None)}")
    return 0


def cmd_update(*, apply: bool = False, json_mode: bool = False) -> int:
    """Check for a signed VOOL update and (with --apply) install it safely.

    ONE authority since the 2026-09-01 amendment: this delegates to the signed-manifest
    updater (core/updater) that the server, the chat UI and the wrapper chip all share.
    The legacy GitHub-releases/sha-sidecar path is retired from production.
    """
    import time as _time

    from core.updater.runtime import (
        boot_update_subsystem,
        get_update_subsystem,
    )
    from core.updater.service import UserGesture

    boot_update_subsystem()
    subsystem = get_update_subsystem()
    assert subsystem is not None

    payload = subsystem.status_payload()
    installed = payload["installed_version"]

    if not payload["configured"]:
        if json_mode:
            _emit_json({"ok": False, "error": "unavailable", "detail": payload["unavailable_plain"], "installed": installed})
        else:
            print(f"Updates are unavailable — {payload['unavailable_plain']}.")
        return 1

    checked = subsystem.trigger_check()
    payload = subsystem.status_payload()

    if not checked.get("check_ok"):
        if json_mode:
            _emit_json({"ok": False, "error": "check_failed", "detail": checked.get("detail", ""), "installed": installed})
        else:
            print(f"The update check didn't complete: {checked.get('detail', '')}")
        return 1

    if payload["phase"] != "ready":
        if json_mode:
            _emit_json({"ok": True, "available": False, "installed": installed, "phase": payload["phase"], "message": payload["message"]})
        else:
            print(payload["message"] or f"VOOL {installed} is up to date.")
        return 0

    if json_mode:
        _emit_json(
            {
                "ok": True,
                "available": True,
                "installed": installed,
                "target_version": payload["target_version"],
                "notes": payload["notes"],
            }
        )
    else:
        print(f"Update available: {payload['target_version']}  (installed {installed})")
        for line in (payload["notes"] or "").splitlines():
            if line.strip():
                print(f"  - {line.strip()}")

    if not apply:
        if not json_mode:
            print("Run `vool update --apply` (or press Update in the app) to install — verified, auto-rollback on failure.")
        return 0

    outcome = subsystem.press_install(UserGesture(pressed_at=_time.time(), origin="cli"))
    if not outcome.accepted:
        if not json_mode:
            print(outcome.detail or "Could not start the update.")
        return 1
    if not json_mode:
        print("Update download started. Press Restart in the app (or `installer/update_cli.py restart`) when it is ready.")
    return 0


def cmd_wallet_address(*, json_mode: bool = False) -> int:
    """Read-only adapter over the canonical wallet: prints the registered public key, never mints one."""
    from core.vool_wallet import legacy_wallet_view

    view = legacy_wallet_view(include_balances=False)
    if json_mode:
        _emit_json({"authority": "core.wallet", "address": view.get("pubkey") or "", "network": view.get("network"), "custody_mode": view.get("custody_mode"), "enabled": view.get("enabled")})
        return 0
    if not view.get("enabled"):
        print("core.wallet is switched off (VOOL_WALLET_ENABLED); no wallet address to show.")
        return 0
    if view.get("pubkey"):
        print(f"core.wallet address ({view.get('custody_mode')}, {view.get('network')}): {view.get('pubkey')}")
    else:
        print("core.wallet: no wallet registered yet. Register a watch-only address or create a pocket wallet in the app.")
    return 0


def cmd_wallet_export(*, json_mode: bool = False) -> int:
    """RETIRED: there is no private-key export. The recovery phrase is shown once at creation, in the app."""
    from core.wallet.authority import refuse_export

    fault = refuse_export("cli.wallet-export")
    if json_mode:
        _emit_json({"ok": False, "error": fault.code, "fault_id": fault.fault_id, "message": fault.user_message})
    else:
        print(f"{fault.code}: {fault.user_message}")
    return 2


def cmd_model_tool_certification(
    *,
    provider_name: str,
    model_name: str,
    run: bool,
    timeout_seconds: float,
    json_mode: bool,
) -> int:
    """Inspect or explicitly rerun the sealed local-model tool certification suite."""

    _bootstrap_cli_storage()
    from core.local_model_tool_certification import (
        CertificationBoundaryError,
        certification_status,
        run_local_model_tool_certification,
    )
    from core.model_registry import ModelRegistry

    manifest = ModelRegistry().get_manifest(provider_name, model_name)
    if manifest is None:
        payload = {"error": "model provider not found", "provider_name": provider_name, "model_name": model_name}
        if json_mode:
            _emit_json(payload)
        else:
            print(f"Model provider not found: {provider_name}:{model_name}")
        return 2
    try:
        payload = (
            run_local_model_tool_certification(
                manifest,
                timeout_seconds=max(1.0, min(float(timeout_seconds or 90.0), 300.0)),
            )
            if run
            else certification_status(manifest)
        )
    except CertificationBoundaryError as exc:
        payload = {"error": str(exc), "routing_effect": "none"}
        if json_mode:
            _emit_json(payload)
        else:
            print(str(exc))
        return 2
    if json_mode:
        _emit_json(payload)
    else:
        print(f"Tool certification: {payload.get('state', 'unknown')}")
        print(f"Model:              {provider_name}:{model_name}")
        print(f"Observe-only:       {'yes' if payload.get('observe_only', True) else 'no'}")
        print("Routing effect:     none")
        if payload.get("completed_at"):
            print(f"Completed:          {payload['completed_at']}")
        if payload.get("regression_confirmed"):
            print("Regression:         confirmed by repeated failures after a verified run")
        stages = dict(payload.get("stages") or {})
        for name in (
            "transport_acceptance",
            "model_emission",
            "adapter_translation",
            "result_continuation",
        ):
            if name in stages:
                stage = dict(stages[name] or {})
                suffix = f" ({stage.get('failure_code')})" if stage.get("failure_code") else ""
                print(f" - {name}: {stage.get('state', 'unknown')}{suffix}")
        if not run:
            print("Run manually with the same command plus --run.")
    return 0


def cmd_bug_report(args: argparse.Namespace) -> int:
    """The opt-in, privacy-safe bug reporter. Thin shell over core/bug_report."""
    import json as _json
    import sys

    from core.bug_report import get_service

    service = get_service()
    action = str(getattr(args, "action", "") or "")

    def _emit(payload: dict) -> int:
        if getattr(args, "json", False):
            print(_json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            for key, value in payload.items():
                print(f"{key}: {value}")
        return 0

    if action == "draft":
        log_sources = []
        for entry in list(getattr(args, "log", None) or []):
            name, sep, path = str(entry).partition(":")
            if not sep or not name or not path:
                print(f"error: --log expects name:path, got {entry!r}", file=sys.stderr)
                return 2
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    lines = handle.read().splitlines()
            except OSError as exc:
                print(f"error: cannot read log {path!r}: {exc}", file=sys.stderr)
                return 2
            log_sources.append({"name": name, "lines": lines})
        try:
            draft = service.create_draft(
                expected=str(args.expected or ""),
                actual=str(args.actual or ""),
                repro_steps=list(args.repro or []),
                error_text=str(args.error_text or ""),
                category=str(args.category or ""),
                lanes=list(args.lane or []),
                tools=list(args.tool or []),
                models=list(args.model or []),
                log_sources=log_sources,
                destination_repo=str(args.destination or ""),
                title=str(args.title or ""),
            )
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit({
            "report_id": draft.report_id,
            "fingerprint": draft.fingerprint,
            "destination_repo": draft.destination_repo,
            "created_at": draft.created_at,
            "next": f"vool bug-report preview {draft.report_id}",
        })

    if action == "preview":
        try:
            preview = service.preview(
                str(args.report_id),
                remove_fields=list(getattr(args, "remove_field", None) or []),
                remove_attachments=list(getattr(args, "remove_attachment", None) or []),
            )
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit({
            "report_id": preview.report_id,
            "payload_sha256": preview.payload_sha256,
            "total_bytes": preview.total_bytes,
            "title": preview.issue.get("title", ""),
            "next": f"vool bug-report approve {preview.report_id} --sha {preview.payload_sha256} --confirm",
        })

    if action == "approve":
        try:
            consent = service.approve(
                str(args.report_id),
                payload_sha256=str(args.sha or ""),
                remove_fields=list(getattr(args, "remove_field", None) or []),
                remove_attachments=list(getattr(args, "remove_attachment", None) or []),
                confirm=bool(args.confirm),
            )
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit({"ok": True, "payload_sha256": consent.payload_sha256, "approved_at": consent.approved_at})

    if action == "submit":
        try:
            result = service.submit(str(args.report_id))
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit({
            "status": result.status,
            "issue_url": result.issue_url,
            "duplicate_of": result.duplicate_of,
            "detail": result.detail,
            "failure_code": result.failure_code,
            "upstream_status": result.upstream_status,
        })

    if action == "export":
        try:
            exported = service.export(
                str(args.report_id),
                remove_fields=list(getattr(args, "remove_field", None) or []),
                remove_attachments=list(getattr(args, "remove_attachment", None) or []),
            )
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit({
            "ok": True,
            "report_id": exported["report_id"],
            "path": exported["path"],
            "payload_sha256": exported["payload_sha256"],
            "total_bytes": exported["total_bytes"],
        })

    if action == "revoke":
        try:
            cleared = service.revoke(str(args.report_id))
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit({"ok": True, "report_id": cleared.report_id, "consent": None if cleared.consent is None else "present"})

    if action == "destination":
        from core.bug_report.destination import BUILTIN_DESTINATION, default_destination, reset_default_destination, set_default_destination

        new_value = str(getattr(args, "set", "") or "").strip()
        if new_value:
            try:
                set_default_destination(new_value)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        elif getattr(args, "reset", False):
            reset_default_destination()
        try:
            configured = default_destination()
            configured_error = ""
        except ValueError as exc:
            configured, configured_error = BUILTIN_DESTINATION, str(exc)
        return _emit({
            "destination": configured,
            "builtin": BUILTIN_DESTINATION,
            "configuration_error": configured_error,
        })

    if action == "status":
        try:
            status = service.status(str(args.report_id))
        except Exception as exc:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return _emit(status)

    if action == "receipts":
        rows = service.receipts()
        if getattr(args, "json", False):
            print(_json.dumps(rows, ensure_ascii=False, sort_keys=True))
        else:
            for row in rows:
                print(f"{row.get('submitted_at')} {row.get('report_id')} -> {row.get('issue_url')} ({row.get('total_bytes')} bytes)")
        return 0

    print(f"error: unknown bug-report action {action!r}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vool")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("up", help="Auto-detect hardware and start Vool")
    summary = sub.add_parser("summary", help="Show what Vool learned, stored, indexed, and exchanged.")
    summary.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    summary.add_argument("--limit", type=int, default=5, help="Number of recent items to show per section.")

    resolve = sub.add_parser("resolve", help="Resolve a .null name on mainnet (read-only): owner, Arweave, x402 endpoint.")
    resolve.add_argument("name", help="The .null name to resolve, e.g. web0.null")
    resolve.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    web = sub.add_parser(
        "web",
        help="Live web search/fetch/browse. Opt-in: off unless VOOL_ENABLE_WEB=1 is set.",
    )
    web.add_argument("query", nargs="*", help="Search query words.")
    web.add_argument("--fetch", default="", metavar="URL", help="Fetch text from a specific URL instead of searching.")
    web.add_argument("--browse", default="", metavar="URL", help="Render a JS-heavy URL instead of searching.")
    web.add_argument("--limit", type=int, default=5, help="Max search results (1-5).")
    web.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    receipts = sub.add_parser("receipts", help="Show locally recorded anchored/finalized proofs with explorer links.")
    receipts.add_argument("--limit", type=int, default=20, help="Number of recent receipts to show.")
    receipts.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    session_bundle = sub.add_parser(
        "session-bundle",
        help="Export, inspect or import one conversation as a portable, integrity-checked bundle.",
    )
    session_bundle_sub = session_bundle.add_subparsers(dest="bundle_command", required=True)
    sb_preview = session_bundle_sub.add_parser("preview", help="Show exactly what an export will contain.")
    sb_preview.add_argument("--session-id", required=True, help="The chat session to preview.")
    sb_export = session_bundle_sub.add_parser("export", help="Write one session to a .voolsession file.")
    sb_export.add_argument("--session-id", required=True, help="The chat session to export.")
    sb_export.add_argument("--out", required=True, help="Output file path.")
    sb_export.add_argument("--passphrase", default="", help="Optional passphrase (envelope is AES-256-GCM).")
    sb_inspect = session_bundle_sub.add_parser("inspect", help="Read a bundle's identity and manifest without importing.")
    sb_inspect.add_argument("--path", required=True, help="Bundle file path.")
    sb_inspect.add_argument("--passphrase", default="", help="Passphrase if the bundle is encrypted.")
    sb_import = session_bundle_sub.add_parser("import", help="Import a bundle into a VOOL home. Never overwrites an existing session.")
    sb_import.add_argument("--path", required=True, help="Bundle file path.")
    sb_import.add_argument("--passphrase", default="", help="Passphrase if the bundle is encrypted.")
    sb_import.add_argument("--home", default="", help="Target VOOL home (defaults to this process's home).")

    manifest = sub.add_parser("manifest", help="Show this node's advertised capability + price card.")
    manifest.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    bug_report = sub.add_parser(
        "bug-report",
        help="Opt-in privacy-safe bug reporting: sanitized local draft, exact preview, explicit approval, then submit.",
    )
    bug_report_sub = bug_report.add_subparsers(dest="action")
    br_draft = bug_report_sub.add_parser("draft", help="Capture bounded diagnostics into a sanitized LOCAL draft.")
    br_draft.add_argument("--expected", default="", help="What you expected to happen.")
    br_draft.add_argument("--actual", default="", help="What actually happened.")
    br_draft.add_argument("--repro", action="append", default=[], help="One reproduction step; repeatable.")
    br_draft.add_argument("--error-text", default="", help="Raw error/traceback text; it is redacted and typed on capture.")
    br_draft.add_argument("--category", default="", help="Short bug category, e.g. crash, hang, routing.")
    br_draft.add_argument("--lane", action="append", default=[], help="Involved lane id; repeatable.")
    br_draft.add_argument("--tool", action="append", default=[], help="Involved tool id; repeatable.")
    br_draft.add_argument("--model", action="append", default=[], help="Involved model id; repeatable.")
    br_draft.add_argument("--log", action="append", default=[], help="Bounded log source as name:path; repeatable.")
    br_draft.add_argument("--destination", default="", help="Destination GitHub repo as owner/name (default: the configured feedback repository).")
    br_draft.add_argument("--title", default="", help="Short report title.")
    br_draft.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_preview = bug_report_sub.add_parser("preview", help="Show the EXACT bytes that would be submitted.")
    br_preview.add_argument("report_id", help="Draft id from `bug-report draft`.")
    br_preview.add_argument("--remove-field", action="append", default=[], help="Field to drop (logs, flags, repro, components, error, attachments).")
    br_preview.add_argument("--remove-attachment", action="append", default=[], help="Attachment name to drop.")
    br_preview.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_approve = bug_report_sub.add_parser("approve", help="Grant consent for these exact outbound bytes.")
    br_approve.add_argument("report_id", help="Draft id.")
    br_approve.add_argument("--sha", required=True, help="payload_sha256 from the preview you reviewed.")
    br_approve.add_argument("--remove-field", action="append", default=[], help="Field dropped in the preview you approved.")
    br_approve.add_argument("--remove-attachment", action="append", default=[], help="Attachment dropped in the preview you approved.")
    br_approve.add_argument("--confirm", action="store_true", help="Explicit consent marker; required.")
    br_approve.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_submit = bug_report_sub.add_parser("submit", help="Submit the approved bytes to GitHub (deduplicates first).")
    br_submit.add_argument("report_id", help="Draft id.")
    br_submit.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_export = bug_report_sub.add_parser("export", help="Save the exact previewed bytes locally. Nothing is sent anywhere.")
    br_export.add_argument("report_id", help="Draft id.")
    br_export.add_argument("--remove-field", action="append", default=[], help="Field to drop, matching the preview you reviewed.")
    br_export.add_argument("--remove-attachment", action="append", default=[], help="Attachment name to drop.")
    br_export.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_revoke = bug_report_sub.add_parser("revoke", help="Withdraw approval; submission demands a fresh decision.")
    br_revoke.add_argument("report_id", help="Draft id.")
    br_revoke.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_destination = bug_report_sub.add_parser("destination", help="Show (or set/reset) the configured default destination repository.")
    br_destination.add_argument("--set", default="", metavar="OWNER/NAME", help="Persist a new default destination for this install.")
    br_destination.add_argument("--reset", action="store_true", help="Forget the stored choice; the built-in default applies again.")
    br_destination.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_status = bug_report_sub.add_parser("status", help="Show a draft's local state.")
    br_status.add_argument("report_id", help="Draft id.")
    br_status.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    br_receipts = bug_report_sub.add_parser("receipts", help="List durable submission receipts (metadata only).")
    br_receipts.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    sell_quote = sub.add_parser("sell-quote", help="Preview the x402 invoice for a null:// request or a .null name (read-only).")
    sell_quote.add_argument("target", help="A null:// service URI or a <name>.null")
    sell_quote.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    dial = sub.add_parser(
        "dial",
        help="Reach a named .null agent's endpoint and return its result. Opt-in: off unless VOOL_ENABLE_NULL_DIAL=1.",
    )
    dial.add_argument("name", help="The .null name to dial, e.g. web0.null")
    dial.add_argument("task", help="The task to hand the named agent.")
    dial.add_argument("--allow-spend", action="store_true", help="Permit paying the endpoint via x402 (within --max-spend).")
    dial.add_argument("--max-spend", type=float, default=1.0, metavar="USDC", help="Spend cap in USDC (clamped to 1.0).")
    dial.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    x402pay = sub.add_parser(
        "x402-pay",
        help="Settle a single x402 payment on Solana (devnet by default). Real spend requires --allow-spend.",
    )
    x402pay.add_argument("amount", type=float, help="Amount in USDC, e.g. 0.001")
    x402pay.add_argument("recipient", help="Recipient Solana wallet (base58); its token account for the asset must exist.")
    x402pay.add_argument("--keypair", default="", metavar="PATH", help="Payer Solana JSON keypair (required to actually pay).")
    x402pay.add_argument("--mainnet", action="store_true", help="Settle on mainnet (real funds). Default is devnet.")
    x402pay.add_argument("--asset", default="", metavar="MINT", help="SPL mint to transfer (default: the cluster USDC mint).")
    x402pay.add_argument("--memo", default="", metavar="TEXT", help="On-chain memo describing the payment (shown on explorers).")
    x402pay.add_argument("--allow-spend", action="store_true", help="Actually spend. Without it this is a dry run.")
    x402pay.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    register = sub.add_parser(
        "register",
        help="Register a .null name on mainnet (direct-sign). Dry-run preview unless --allow-spend --mainnet.",
    )
    register.add_argument("name", help="The .null name to register (4-32 chars; 1-3 char names are auction-only).")
    register.add_argument("--allow-spend", action="store_true", help="Permit the real registration spend. Without it (or --mainnet) this is a dry-run preview.")
    register.add_argument("--max-spend", type=float, default=0.02, metavar="SOL", help="Spend cap in SOL (default 0.02; a hard 0.05 ceiling also applies).")
    register.add_argument("--mainnet", action="store_true", help="Actually register on Solana mainnet (real SOL). Required with --allow-spend for a real registration.")
    register.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    spend_policy = sub.add_parser(
        "spend-policy",
        help="Show the agent wallet's spend policy: freeze state, per-tx/daily/weekly caps, and spend used/left.",
    )
    spend_policy.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    spend_freeze = sub.add_parser(
        "spend-freeze",
        help="Panic freeze: instantly block ALL wallet spending. No consent needed to get safer.",
    )
    spend_freeze.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    spend_unfreeze = sub.add_parser(
        "spend-unfreeze",
        help="Re-enable spending after a freeze. Requires a live OS-consent prompt (Windows Hello).",
    )
    spend_unfreeze.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    spend_set_cap = sub.add_parser(
        "spend-set-cap",
        help="Set (or clear) the per-transaction / daily / weekly spend caps, in SOL.",
    )
    spend_set_cap.add_argument("--per-tx", type=float, default=None, metavar="SOL", help="Per-transaction cap in SOL.")
    spend_set_cap.add_argument("--daily", type=float, default=None, metavar="SOL", help="Daily cumulative cap in SOL.")
    spend_set_cap.add_argument("--weekly", type=float, default=None, metavar="SOL", help="Weekly cumulative cap in SOL.")
    spend_set_cap.add_argument("--clear", action="store_true", help="Clear all caps (no limits; spends still need OS consent).")
    spend_set_cap.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    update = sub.add_parser(
        "update",
        help="Check for a newer VOOL release; --apply installs it (verified swap, auto-rollback). Wallet + data are never touched.",
    )
    update.add_argument("--apply", action="store_true", help="Install the update (default is check-only).")
    update.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    wallet_address = sub.add_parser(
        "wallet-address",
        help="Show the agent Solana wallet's public address (safe to share — for funding).",
    )
    wallet_address.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    sub.add_parser(
        "wallet-export",
        help="Reveal the agent wallet private key in Phantom-import (base58) format. Requires a Windows Hello prompt.",
    )

    nullpass = sub.add_parser(
        "nullpass",
        help="Portable .nullpass work credential: issue one (signed by your wallet) or verify one offline.",
    )
    nullpass.add_argument("action", choices=["verify", "issue"], help="verify a .nullpass file, or issue a new one")
    nullpass.add_argument("target", help="file path (verify) or task id (issue)")
    nullpass.add_argument("--result", default="", help="result text to bind into the receipt (issue)")
    nullpass.add_argument("--out", default="", metavar="FILE", help="write the issued .nullpass here (issue)")
    nullpass.add_argument("--confirm-onchain", action="store_true", help="also confirm the payment settled on-chain (verify)")
    nullpass.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    providers = sub.add_parser("providers", help="Show registered external model providers and declared licenses.")
    providers.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    tool_cert = sub.add_parser(
        "model-tool-certification",
        help="Inspect or explicitly run the sealed, observe-only tool reliability probe for one local model.",
    )
    tool_cert.add_argument("--provider-name", required=True, help="Registered provider name, e.g. ollama-local.")
    tool_cert.add_argument("--model-name", required=True, help="Registered model name/tag.")
    tool_cert.add_argument("--run", action="store_true", help="Make the local diagnostic calls now.")
    tool_cert.add_argument("--timeout-seconds", type=float, default=90.0, help="Per-exchange local timeout (1-300).")
    tool_cert.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    install_profile = sub.add_parser(
        "install-profile",
        help="Show or switch the active install/provider profile for this runtime home.",
    )
    install_profile.add_argument(
        "--set",
        default="",
        metavar="PROFILE",
        help=(
            "Persist a new install profile and require a restart before it takes effect. "
            "Canonical values: "
            + ", ".join(PUBLIC_INSTALL_PROFILE_CHOICES)
            + ". Friendly aliases: ollama-only, ollama-max."
        ),
    )
    install_profile.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    identities = sub.add_parser("identities", help="Show local identity lifecycle, revocations, and key history.")
    identities.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    release = sub.add_parser("release-status", help="Show the current release/update manifest and compatibility contract.")
    release.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    credits = sub.add_parser("credits", help="Show current compute credit balance.")
    credits.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    adapt_status = sub.add_parser("adaptation-status", help="Show LoRA/adaptation dependency and job status.")
    adapt_status.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    adapt_corpus = sub.add_parser("adapt-corpus", help="Create or rebuild a training corpus from chats, final responses, and Hive.")
    adapt_corpus.add_argument("--corpus-id", default="", help="Rebuild an existing corpus instead of creating a new one.")
    adapt_corpus.add_argument("--label", default="default-corpus")
    adapt_corpus.add_argument("--no-conversations", action="store_true")
    adapt_corpus.add_argument("--no-final-responses", action="store_true")
    adapt_corpus.add_argument("--no-hive-posts", action="store_true")
    adapt_corpus.add_argument("--limit-per-source", type=int, default=250)
    adapt_corpus.add_argument("--json", action="store_true")

    adapt_import = sub.add_parser("adapt-corpus-import", help="Import a corpus JSONL from another node or machine into local adaptation storage.")
    adapt_import.add_argument("--input-path", required=True)
    adapt_import.add_argument("--label", default="imported-corpus")
    adapt_import.add_argument("--json", action="store_true")

    adapt_export = sub.add_parser("adapt-corpus-export", help="Export an existing local adaptation corpus JSONL to a target path.")
    adapt_export.add_argument("--corpus-id", required=True)
    adapt_export.add_argument("--output-path", required=True)
    adapt_export.add_argument("--json", action="store_true")

    adapt_create = sub.add_parser("adapt-job-create", help="Create a LoRA adaptation job definition.")
    adapt_create.add_argument("--corpus-id", required=True)
    adapt_create.add_argument("--base-model-ref", required=True, help="HF model id or local model path for training.")
    adapt_create.add_argument("--base-provider-name", default="")
    adapt_create.add_argument("--base-model-name", default="")
    adapt_create.add_argument("--adapter-provider-name", default="")
    adapt_create.add_argument("--adapter-model-name", default="")
    adapt_create.add_argument("--license-name", default="")
    adapt_create.add_argument("--license-reference", default="")
    adapt_create.add_argument("--capability", action="append", default=[])
    adapt_create.add_argument("--target-module", action="append", default=[])
    adapt_create.add_argument("--epochs", type=int, default=1)
    adapt_create.add_argument("--max-steps", type=int, default=32)
    adapt_create.add_argument("--batch-size", type=int, default=1)
    adapt_create.add_argument("--gradient-accumulation-steps", type=int, default=4)
    adapt_create.add_argument("--learning-rate", type=float, default=2e-4)
    adapt_create.add_argument("--cutoff-len", type=int, default=768)
    adapt_create.add_argument("--lora-r", type=int, default=8)
    adapt_create.add_argument("--lora-alpha", type=int, default=16)
    adapt_create.add_argument("--lora-dropout", type=float, default=0.05)
    adapt_create.add_argument("--promote", action="store_true")
    adapt_create.add_argument("--json", action="store_true")

    adapt_jobs = sub.add_parser("adapt-jobs", help="List adaptation jobs.")
    adapt_jobs.add_argument("--json", action="store_true")

    adapt_evals = sub.add_parser("adapt-evals", help="List adaptation evaluation and canary runs.")
    adapt_evals.add_argument("--job-id", default="")
    adapt_evals.add_argument("--json", action="store_true")

    adapt_events = sub.add_parser("adapt-job-events", help="Show adaptation job event log.")
    adapt_events.add_argument("--job-id", required=True)
    adapt_events.add_argument("--json", action="store_true")

    adapt_run = sub.add_parser("adapt-job-run", help="Run a queued LoRA adaptation job.")
    adapt_run.add_argument("--job-id", required=True)
    adapt_run.add_argument("--promote", action="store_true")
    adapt_run.add_argument("--json", action="store_true")

    adapt_promote = sub.add_parser("adapt-promote", help="Promote a completed LoRA job into the live provider registry.")
    adapt_promote.add_argument("--job-id", required=True)
    adapt_promote.add_argument("--json", action="store_true")

    adapt_loop_status = sub.add_parser("adapt-loop-status", help="Show the closed-loop adaptation controller state.")
    adapt_loop_status.add_argument("--json", action="store_true")

    adapt_loop_tick = sub.add_parser("adapt-loop-tick", help="Run the closed-loop adaptation controller once.")
    adapt_loop_tick.add_argument("--force", action="store_true")
    adapt_loop_tick.add_argument("--json", action="store_true")

    adapt_auto = sub.add_parser("adapt-autopilot", help="One-shot corpus build + LoRA job create + run.")
    adapt_auto.add_argument("--label", default="autopilot")
    adapt_auto.add_argument("--base-model-ref", required=True)
    adapt_auto.add_argument("--base-provider-name", default="")
    adapt_auto.add_argument("--base-model-name", default="")
    adapt_auto.add_argument("--adapter-provider-name", default="")
    adapt_auto.add_argument("--adapter-model-name", default="")
    adapt_auto.add_argument("--license-name", default="")
    adapt_auto.add_argument("--license-reference", default="")
    adapt_auto.add_argument("--capability", action="append", default=[])
    adapt_auto.add_argument("--target-module", action="append", default=[])
    adapt_auto.add_argument("--limit-per-source", type=int, default=250)
    adapt_auto.add_argument("--epochs", type=int, default=1)
    adapt_auto.add_argument("--max-steps", type=int, default=32)
    adapt_auto.add_argument("--batch-size", type=int, default=1)
    adapt_auto.add_argument("--gradient-accumulation-steps", type=int, default=4)
    adapt_auto.add_argument("--learning-rate", type=float, default=2e-4)
    adapt_auto.add_argument("--cutoff-len", type=int, default=768)
    adapt_auto.add_argument("--lora-r", type=int, default=8)
    adapt_auto.add_argument("--lora-alpha", type=int, default=16)
    adapt_auto.add_argument("--lora-dropout", type=float, default=0.05)
    adapt_auto.add_argument("--promote", action="store_true")
    adapt_auto.add_argument("--json", action="store_true")

    control_sync = sub.add_parser("control-sync", help="Mirror real queue/lease/run/budget/approval state into workspace/control.")
    control_sync.add_argument("--json", action="store_true")

    base_status = sub.add_parser("trainable-base-status", help="Show staged real trainable bases and the active adaptation base.")
    base_status.add_argument("--json", action="store_true")

    base_stage = sub.add_parser("stage-trainable-base", help="Download and activate a real trainable Transformers base for LoRA.")
    base_stage.add_argument("--model-ref", default="qwen-0.5b", help="Curated alias or full Hugging Face repo id.")
    base_stage.add_argument("--activate", action="store_true", help="Write the staged base into local adaptation policy.")
    base_stage.add_argument("--skip-verify-load", action="store_true", help="Skip tokenizer/model load verification after download.")
    base_stage.add_argument("--force-download", action="store_true", help="Force a fresh snapshot even if the model already looks staged.")
    base_stage.add_argument("--license-name", default="", help="Required for custom full HF repo ids.")
    base_stage.add_argument("--license-reference", default="", help="Required for custom full HF repo ids.")
    base_stage.add_argument("--trust-remote-code", action="store_true", help="Enable trust_remote_code for custom model layouts.")
    base_stage.add_argument("--json", action="store_true")

    wallet_init = sub.add_parser("wallet-init", help="Configure hot/cold DNA wallets and cold approval secret.")
    wallet_init.add_argument("--hot-address", required=True)
    wallet_init.add_argument("--cold-address", required=True)
    wallet_init.add_argument("--cold-secret", default="")
    wallet_init.add_argument("--hot-usdc", type=float, default=0.0)
    wallet_init.add_argument("--cold-usdc", type=float, default=0.0)

    wallet_status = sub.add_parser("wallet-status", help="Show hot/cold wallet balances.")
    wallet_status.add_argument("--json", action="store_true")

    wallet_topup = sub.add_parser("wallet-topup-hot", help="Move USDC from cold wallet to hot wallet (requires approval secret).")
    wallet_topup.add_argument("--usdc", type=float, required=True)
    wallet_topup.add_argument("--cold-secret", default="")

    wallet_to_cold = sub.add_parser("wallet-move-cold", help="Move USDC from hot wallet to cold wallet (requires approval secret).")
    wallet_to_cold.add_argument("--usdc", type=float, required=True)
    wallet_to_cold.add_argument("--cold-secret", default="")

    wallet_buy = sub.add_parser(
        "wallet-buy-credits",
        help="Disabled by default. Credits are work-earned until purchase rails are enabled.",
    )
    wallet_buy.add_argument("--usdc", type=float, required=True)
    return parser

def main(argv: list[str] | None = None) -> int:
    # C15: the ONE bounded unattended preflight before any command reaches an OS
    # credential API.
    from core.unattended_preflight import preflight

    preflight('apps.vool_cli')
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "up":
        return cmd_up()
    if args.command == "summary":
        return cmd_summary(json_mode=bool(args.json), limit=int(args.limit))
    if args.command == "resolve":
        return cmd_resolve(str(args.name or ""), json_mode=bool(args.json))
    if args.command == "bug-report":
        return cmd_bug_report(args)
    if args.command == "web":
        return cmd_web(
            query=" ".join(list(args.query or [])),
            fetch_url=str(args.fetch or ""),
            render_url=str(args.browse or ""),
            limit=int(args.limit),
            json_mode=bool(args.json),
        )
    if args.command == "receipts":
        return cmd_receipts(limit=int(args.limit), json_mode=bool(args.json))
    if args.command == "session-bundle":
        return cmd_session_bundle(args)
    if args.command == "manifest":
        return cmd_manifest(json_mode=bool(args.json))
    if args.command == "sell-quote":
        return cmd_sell_quote(str(args.target or ""), json_mode=bool(args.json))
    if args.command == "dial":
        return cmd_dial(
            str(args.name or ""),
            str(args.task or ""),
            allow_spend=bool(args.allow_spend),
            max_spend_usdc=float(args.max_spend),
            json_mode=bool(args.json),
        )
    if args.command == "x402-pay":
        return cmd_x402_pay(
            float(args.amount),
            str(args.recipient or ""),
            keypair_path=str(args.keypair or ""),
            mainnet=bool(args.mainnet),
            asset_mint=str(args.asset or ""),
            memo=str(args.memo or ""),
            allow_spend=bool(args.allow_spend),
            json_mode=bool(args.json),
        )
    if args.command == "register":
        return cmd_register(
            str(args.name or ""),
            allow_spend=bool(args.allow_spend),
            max_spend_sol=float(args.max_spend),
            mainnet=bool(args.mainnet),
            json_mode=bool(args.json),
        )
    if args.command == "spend-policy":
        return cmd_spend_policy(json_mode=bool(args.json))
    if args.command == "spend-freeze":
        return cmd_spend_freeze(json_mode=bool(args.json))
    if args.command == "spend-unfreeze":
        return cmd_spend_unfreeze(json_mode=bool(args.json))
    if args.command == "spend-set-cap":
        return cmd_spend_set_cap(
            per_tx=args.per_tx,
            daily=args.daily,
            weekly=args.weekly,
            clear=bool(args.clear),
            json_mode=bool(args.json),
        )
    if args.command == "update":
        return cmd_update(apply=bool(args.apply), json_mode=bool(args.json))
    if args.command == "wallet-address":
        return cmd_wallet_address(json_mode=bool(args.json))
    if args.command == "wallet-export":
        return cmd_wallet_export()
    if args.command == "nullpass":
        return cmd_nullpass(
            str(args.action),
            str(args.target or ""),
            result=str(args.result or ""),
            out=str(args.out or ""),
            confirm_onchain=bool(args.confirm_onchain),
            json_mode=bool(args.json),
        )
    if args.command == "providers":
        return cmd_providers(json_mode=bool(args.json))
    if args.command == "model-tool-certification":
        return cmd_model_tool_certification(
            provider_name=str(args.provider_name or ""),
            model_name=str(args.model_name or ""),
            run=bool(args.run),
            timeout_seconds=float(args.timeout_seconds),
            json_mode=bool(args.json),
        )
    if args.command == "install-profile":
        return cmd_install_profile(set_profile=str(args.set or ""), json_mode=bool(args.json))
    if args.command == "identities":
        return cmd_identity_report(json_mode=bool(args.json))
    if args.command == "release-status":
        return cmd_release_status(json_mode=bool(args.json))
    if args.command == "credits":
        return cmd_credits(json_mode=bool(args.json))
    if args.command == "adaptation-status":
        return cmd_adaptation_status(json_mode=bool(args.json))
    if args.command == "adapt-corpus":
        return cmd_adaptation_corpus(
            corpus_id=str(args.corpus_id or ""),
            label=str(args.label or ""),
            include_conversations=not bool(args.no_conversations),
            include_final_responses=not bool(args.no_final_responses),
            include_hive_posts=not bool(args.no_hive_posts),
            limit_per_source=int(args.limit_per_source),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-corpus-import":
        return cmd_adaptation_corpus_import(
            input_path=str(args.input_path or ""),
            label=str(args.label or ""),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-corpus-export":
        return cmd_adaptation_corpus_export(
            corpus_id=str(args.corpus_id or ""),
            output_path=str(args.output_path or ""),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-job-create":
        return cmd_adaptation_job_create(
            corpus_id=str(args.corpus_id),
            base_model_ref=str(args.base_model_ref),
            base_provider_name=str(args.base_provider_name or ""),
            base_model_name=str(args.base_model_name or ""),
            adapter_provider_name=str(args.adapter_provider_name or ""),
            adapter_model_name=str(args.adapter_model_name or ""),
            license_name=str(args.license_name or ""),
            license_reference=str(args.license_reference or ""),
            capabilities=list(args.capability or []),
            target_modules=list(args.target_module or []),
            epochs=int(args.epochs),
            max_steps=int(args.max_steps),
            batch_size=int(args.batch_size),
            gradient_accumulation_steps=int(args.gradient_accumulation_steps),
            learning_rate=float(args.learning_rate),
            cutoff_len=int(args.cutoff_len),
            lora_r=int(args.lora_r),
            lora_alpha=int(args.lora_alpha),
            lora_dropout=float(args.lora_dropout),
            promote=bool(args.promote),
            json_mode=bool(args.json),
        )
    if args.command == "adapt-jobs":
        return cmd_adaptation_jobs(json_mode=bool(args.json))
    if args.command == "adapt-evals":
        return cmd_adaptation_eval_runs(job_id=str(args.job_id or ""), json_mode=bool(args.json))
    if args.command == "adapt-job-events":
        return cmd_adaptation_job_events(str(args.job_id), json_mode=bool(args.json))
    if args.command == "adapt-job-run":
        return cmd_adaptation_job_run(str(args.job_id), promote=bool(args.promote), json_mode=bool(args.json))
    if args.command == "adapt-promote":
        return cmd_adaptation_job_promote(str(args.job_id), json_mode=bool(args.json))
    if args.command == "adapt-loop-status":
        return cmd_adaptation_loop_status(json_mode=bool(args.json))
    if args.command == "adapt-loop-tick":
        return cmd_adaptation_loop_tick(force=bool(args.force), json_mode=bool(args.json))
    if args.command == "adapt-autopilot":
        return cmd_adaptation_autopilot(
            label=str(args.label or ""),
            base_model_ref=str(args.base_model_ref),
            base_provider_name=str(args.base_provider_name or ""),
            base_model_name=str(args.base_model_name or ""),
            adapter_provider_name=str(args.adapter_provider_name or ""),
            adapter_model_name=str(args.adapter_model_name or ""),
            limit_per_source=int(args.limit_per_source),
            epochs=int(args.epochs),
            max_steps=int(args.max_steps),
            batch_size=int(args.batch_size),
            gradient_accumulation_steps=int(args.gradient_accumulation_steps),
            learning_rate=float(args.learning_rate),
            cutoff_len=int(args.cutoff_len),
            lora_r=int(args.lora_r),
            lora_alpha=int(args.lora_alpha),
            lora_dropout=float(args.lora_dropout),
            license_name=str(args.license_name or ""),
            license_reference=str(args.license_reference or ""),
            capabilities=list(args.capability or []),
            target_modules=list(args.target_module or []),
            promote=bool(args.promote),
            json_mode=bool(args.json),
        )
    if args.command == "control-sync":
        return cmd_control_plane_sync(json_mode=bool(args.json))
    if args.command == "trainable-base-status":
        return cmd_trainable_base_status(json_mode=bool(args.json))
    if args.command == "stage-trainable-base":
        return cmd_stage_trainable_base(
            model_ref=str(args.model_ref or ""),
            activate=bool(args.activate),
            verify_load=not bool(args.skip_verify_load),
            force_download=bool(args.force_download),
            license_name=str(args.license_name or ""),
            license_reference=str(args.license_reference or ""),
            trust_remote_code=bool(args.trust_remote_code),
            json_mode=bool(args.json),
        )
    if args.command == "wallet-init":
        return cmd_wallet_init(
            hot_address=str(args.hot_address),
            cold_address=str(args.cold_address),
            cold_secret=str(args.cold_secret or ""),
            hot_usdc=float(args.hot_usdc),
            cold_usdc=float(args.cold_usdc),
        )
    if args.command == "wallet-status":
        return cmd_wallet_status(json_mode=bool(args.json))
    if args.command == "wallet-topup-hot":
        return cmd_wallet_topup_hot(usdc=float(args.usdc), cold_secret=str(args.cold_secret or ""))
    if args.command == "wallet-move-cold":
        return cmd_wallet_move_to_cold(usdc=float(args.usdc), cold_secret=str(args.cold_secret or ""))
    if args.command == "wallet-buy-credits":
        return cmd_wallet_buy_credits(usdc=float(args.usdc))

    parser.print_help()
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
