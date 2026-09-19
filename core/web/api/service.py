from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.adaptation_autopilot import get_adaptation_autopilot_status, schedule_adaptation_autopilot_tick
from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.control_plane_workspace import collect_control_plane_status
from core.error_surface import safe_error_text
from core.mode_permission_policy import (
    OperatingMode,
    normalize_mode,
    resolve_effective_mode,
    revoke_bypass_grant,
    set_active_mode,
    supported_mode_values,
)
from core.persistent_memory import augment_history_from_session_log
from core.request_trust import OWNER_LOCAL_KEY, is_loopback_host, strip_reserved_trust_keys
from core.response_provenance import strip_provenance_footer as strip_provenance_footer_str
from core.runtime_capabilities import runtime_capability_snapshot
from core.runtime_operator_snapshot import (
    build_runtime_operator_snapshot,
    redact_runtime_operator_snapshot,
)
from core.runtime_task_events import (
    emit_runtime_event,
    list_recent_runtime_session_events,
    list_runtime_session_events,
    list_runtime_sessions,
)
from core.runtime_task_rail import render_runtime_task_rail_html
from core.self_update_offer import maybe_handle_update_offer
from core.vool_agent_brake import maybe_handle_agent_brake
from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
from core.web.api import diagnostics
from core.web.api.response_control import apply_exact_response_control
from storage.adaptation_store import (
    list_adaptation_eval_runs,
    list_adaptation_job_events,
    list_adaptation_jobs,
)

from .runtime import (
    RuntimeServices,
    extract_user_message,
    normalize_chat_history,
    ollama_chat_response,
    ollama_stream_chunks,
    openai_chat_response,
    openai_sse_stream_from_ollama_chunks,
    parameter_count_for_model,
    parameter_size_for_model,
    run_agent,
    runtime_headers,
    stable_openclaw_session_id,
    stream_agent_with_events,
)


@dataclass
class ApiResponse:
    status: int
    content_type: str
    body: bytes | None = None
    stream: Iterable[bytes] | None = None
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, payload: Any, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=dict(headers or {}),
    )


def text_response(status: int, text: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="text/plain; charset=utf-8",
        body=str(text).encode("utf-8"),
        headers=dict(headers or {}),
    )


#: The pages that host wallet controls (chat approval cards, Settings) may be framed only by this origin.
WALLET_PAGE_FRAMING_HEADERS: dict[str, str] = {"Content-Security-Policy": "frame-ancestors 'self'", "X-Frame-Options": "SAMEORIGIN"}


def html_response(status: int, html: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="text/html; charset=utf-8",
        body=str(html).encode("utf-8"),
        headers=dict(headers or {}),
    )


def stream_response(
    status: int,
    stream: Iterable[bytes],
    *,
    content_type: str,
    headers: dict[str, str] | None = None,
) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type=content_type,
        stream=stream,
        headers=dict(headers or {}),
    )


# Task-completion self-credit: minted only against an internally-consistent work
# receipt, paid only to the LOCAL peer (no cross-peer transfer is possible here), and
# capped per rolling window so a flood of requests cannot inflate the balance.
_TASK_AWARD_WINDOW_SEC = 60
_TASK_AWARD_MAX_PER_WINDOW = 30
_task_award_window: dict[int, int] = {}

# Public values backed by the controller-owned permission matrix. Legacy ask/build aliases remain
# accepted at the API boundary for installed clients, but are normalized to Manual/Auto and are no
# longer exposed by the VOOL interface.
_SUPPORTED_OPERATING_MODES = supported_mode_values() | frozenset({"ask", "build"})


def _award_task_completion_credit(receipt: Any) -> dict[str, Any]:
    """Award the local node's task-completion credit — gated + rate-limited.

    Returns ``{"awarded": bool, "reason": str}``. The receipt's proof must recompute
    and bind the same result (no minting on a malformed/forged receipt); the recipient
    is always the local peer; and awards are bounded per window against spam-inflation.
    """
    import time as _time

    from core.proof_of_execution import verify_proof_receipt

    try:
        if not verify_proof_receipt(receipt.proof):
            return {"awarded": False, "reason": "invalid_proof"}
        if receipt.result_hash != receipt.proof.result_hash:
            return {"awarded": False, "reason": "result_binding_mismatch"}
    except Exception:
        return {"awarded": False, "reason": "proof_check_error"}

    window = int(_time.time()) // _TASK_AWARD_WINDOW_SEC
    for w in [w for w in _task_award_window if w < window]:
        _task_award_window.pop(w, None)
    if _task_award_window.get(window, 0) >= _TASK_AWARD_MAX_PER_WINDOW:
        return {"awarded": False, "reason": "rate_limited"}

    try:
        from core.credit_ledger import award_credits
        from network.signer import get_local_peer_id
        ok = award_credits(get_local_peer_id(), amount=1.0, reason="task_completion",
                           receipt_id=receipt.receipt_id)
    except Exception:
        return {"awarded": False, "reason": "ledger_error"}
    if ok:
        _task_award_window[window] = _task_award_window.get(window, 0) + 1
    return {"awarded": bool(ok), "reason": "awarded" if ok else "ledger_declined"}


def _profile_candidate_text(item) -> str:
    from core.operator_profile import _candidate_phrase

    if item.conflict_with:
        return f"Replace {item.label} with {item.value_text!r}?"
    return f"Remember: {_candidate_phrase(item.category, item.value_text)}"


def _attach_profile_frame(payload: dict, *, result: dict) -> dict:
    """Operator Profile (P1): the chip / confirmation / used-preferences frame on the buffered
    door. Absent entirely when the turn touched nothing -- an ordinary payload is unchanged."""
    try:
        from core.operator_profile_turn import profile_frame_for_result

        frame = profile_frame_for_result(result)
    except Exception:
        frame = None
    if not frame:
        return payload
    payload = dict(payload)
    payload["vool_profile"] = frame
    return payload


def _attach_work_receipt(
    payload: dict[str, Any],
    *,
    result: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    """Issue a Web0WorkReceipt for the completed turn; return payload unchanged on failure."""
    # Expose the turn's session id so a completed turn can actually be traced afterwards.
    # `collect_turn_trace()` needs one and the session id is derived from message content by
    # `stable_openclaw_session_id_provider`, so from outside the daemon it could not be known --
    # calling the collector without it silently returns whichever session was most recent, which
    # on 2026-08-05 returned another lane's file reads and looked like real evidence. Combined with
    # `include_runtime_events` returning nothing on the non-streaming path, that blocked three
    # separate root-cause investigations in one session: the ordinary-question fallback, the
    # provider-status misroute, and the decorator sentence loss. Set before the receipt work and
    # outside its try, so a receipt failure cannot also cost the turn its traceability.
    payload = dict(payload)
    payload["vool_session_id"] = str(session_id or "")
    try:
        import os

        from core.web0_work_receipt import issue_work_receipt
        response_text = str(result.get("response") or "").strip()
        if not response_text:
            return payload
        worker_id = str(os.environ.get("VOOL_WORKER_ID") or "vool")
        # Wire live wallet pubkey as payment recipient when available
        recipient_wallet = "stub-wallet"
        try:
            # read-only: the canonical wallet's public key if one exists; never a key minted as a side effect
            from core.wallet import config as _wallet_config
            from core.wallet import custody as _wallet_custody

            _profile = _wallet_custody.default_wallet() if _wallet_config.wallet_enabled() else None
            if _profile is not None:
                recipient_wallet = _profile.public_key
        except Exception:
            pass
        # A-9 ride-along (R-8): hash the COMMITTED content identity, never raw
        # footer-bearing display text.
        _wr_commit = (
            result.get("vool_response_commit")
            if isinstance(result.get("vool_response_commit"), dict)
            else {}
        )
        receipt = issue_work_receipt(
            task_id=session_id,
            result=str(_wr_commit.get("content_hash") or response_text),
            worker_id=worker_id,
            recipient_wallet=recipient_wallet,
        )
        payload = dict(payload)
        payload["web0_receipt"] = receipt.to_dict()
        # Award the local node a bounded, proof-gated task-completion credit.
        with contextlib.suppress(Exception):
            _award_task_completion_credit(receipt)
        # Anchor receipt hash on Solana when anchoring is opted in (shared gate)
        # The inline anchor broadcast is retired: a signed, broadcast effect only happens through the
        # canonical wallet lifecycle with the operator's approval (core.wallet).
    except Exception:
        pass
    return payload


def apply_runtime_headers(response: ApiResponse, runtime: RuntimeServices) -> ApiResponse:
    headers = runtime_headers(runtime)
    headers.update(response.headers)
    response.headers = headers
    return response


def _qint(query: dict[str, list[str]], key: str, default: int) -> int:
    """A query int parsed fail-soft: a non-numeric ?limit=abc yields the default instead of an
    unhandled 500. Callers that need to reject bad input can validate the result separately."""
    raw = str((query.get(key) or [str(default)])[0] or str(default)).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _normalize_web0_name(raw: str) -> str:
    """A user-typed address -> the bare .null name the resolver expects.

    Accepts 'web0.null', 'web0', 'null://web0.null/path', 'web0://web0', with any
    trailing path/query stripped, lowercased.
    """
    name = str(raw or "").strip().lower()
    for prefix in ("null://", "web0://", "https://", "http://"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.split("/", 1)[0].split("?", 1)[0].strip()
    if name.endswith(".null"):
        name = name[: -len(".null")]
    return name.strip()


def _web0_gateway_urls(txid: str) -> list[str]:
    # Try more than one Arweave gateway: right after a publish a given gateway can
    # 404 for ~30-60s while another already serves it (AGENTS.md pitfall #5).
    tx = str(txid or "").strip()
    if not tx:
        return []
    return [f"https://arweave.net/{tx}", f"https://gateway.irys.xyz/{tx}", f"https://{tx}.ar-io.net"]


def _web0_resolve_response(query: dict[str, list[str]]) -> ApiResponse:
    """Read-only: resolve a .null NAME to its Arweave content URL for the /web0 browser.

    Public on-chain read + a public gateway URL. No key, no signing, no payment.
    Honors local_only_mode (a hard no-remote switch).
    """
    from core import policy_engine

    values = query.get("name") or query.get("q") or []
    name = _normalize_web0_name(str(values[0]) if values else "")
    if not name:
        return json_response(400, {"ok": False, "error": "missing 'name' query parameter"})
    if policy_engine.local_only_mode():
        return json_response(
            403,
            {"ok": False, "name": name, "error": "local_only_mode is on; remote .null resolution is disabled"},
        )
    try:
        from core.null_resolver import resolve_null_domain

        record = resolve_null_domain(name)
    except Exception as exc:
        return json_response(502, {"ok": False, "name": name, "error": f"resolver error: {exc}"})
    if record is None:
        return json_response(404, {"ok": False, "name": name, "error": "unregistered or RPC unreachable"})
    txid = getattr(record, "arweave_txid", None) or ""
    gateways = _web0_gateway_urls(txid)
    return json_response(
        200,
        {
            "ok": True,
            "name": name,
            "owner": record.owner,
            "arweave_txid": txid,
            "gateway_url": gateways[0] if gateways else "",
            "gateways": gateways,
            "x402_endpoint": getattr(record, "x402_endpoint", "") or "",
            "has_content": bool(txid),
        },
    )


# A runtime-status question is a short direct question. Generous: the longest genuine phrasing
# measured is "what model are you running right now" (7 words), and the prompts that were
# wrongly hijacked were 150+ words of research instructions.
_MAX_STATUS_QUESTION_WORDS = 25


def _looks_like_runtime_model_status_question(text: str) -> bool:
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    if any(term in clean for term in ("recommend", "should i download", "which model should")):
        return False
    has_model_term = any(term in clean for term in ("llm", "model", "model lane"))
    has_status_term = any(
        term in clean
        for term in (
            "active",
            "current",
            "standard",
            "using now",
            "what are you using",
            "what model",
            "which model",
        )
    )
    # Follow-up phrasings that clearly mean "which model is serving me" even without the word
    # "model" ("ok so which one is active?"). Without this they fall through to the model, which
    # invents an identity ("qwen2.5:7b") that contradicts the footer showing the model that ran.
    identity_followup = any(
        term in clean
        for term in (
            "which one is active",
            "which one active",
            "which is active",
            "what are you running",
            "which are you running",
            "what are you running on",
            "what are you on now",
            "which are you on",
            "which lane",
        )
    )
    # The question must be about THIS runtime, not merely contain the words. `_looks_like_runtime_
    # version_question` directly below already requires exactly this (`has_version_term and
    # has_subject`); this function never did, and the omission is the whole defect.
    #
    # Measured 2026-08-06, session openclaw:c83c83bd7836b6dd968b: a question about the best-selling
    # mobile PHONE returned the model-pin acknowledgement in 0.0s with zero events, because
    # "mobile phone model" supplied `model` and "Use current, authoritative sources" supplied
    # `current`. Two ordinary English words, neither about the runtime. Same shape as a GPU pricing
    # question answered with this machine's hardware specs, and "the requirements I gave you" read
    # as a filename.
    #
    # `identity_followup` keeps its own path: those phrasings ("which one is active", "what are you
    # running") already name the assistant as the subject, which is why they work without the word
    # "model" at all.
    has_runtime_subject = any(
        term in clean
        for term in (
            "you", "your", "yours", "vool", "vool", "this build", "this runtime",
            "this chat", "this turn", "am i using", "are we using",
        )
    )
    # `this turn` STAYS, and the length test is why. Both hijacked briefs said "evidence
    # retrieved during this turn", but so does a genuine question -- "Which model did I select for
    # this turn?" -- and removing the phrase broke that. The word is ambiguous; the LENGTH is not.
    # `right now` came out of the list: BOTH
    # measured failures said "evidence retrieved during this turn", which is an ordinary instruction
    # about the work, not a question about the runtime. `this chat` STAYS -- it appeared in neither
    # brief and removing it broke a real phrasing, "what llm is active for this chat". That is
    # twice a word list has been wrong
    # here, so the length test below carries the weight instead of a third revision of the words.
    #
    # A runtime-status question is SHORT and direct -- "which model are you using?" is five words,
    # "what llm is active for this chat" is seven. The prompts that were hijacked are 150-word
    # research briefs that happen to contain "model" and "current". No amount of vocabulary tuning
    # separates those; length does, and it cannot be defeated by a phrase nobody anticipated.
    word_count = len(clean.split())
    looks_like_a_short_question = word_count <= _MAX_STATUS_QUESTION_WORDS
    return (
        has_model_term and has_status_term and has_runtime_subject and looks_like_a_short_question
    ) or (identity_followup and looks_like_a_short_question)


def _runtime_text_model_lanes(runtime: RuntimeServices) -> list[str]:
    lanes: list[str] = []
    for item in tuple(runtime.provider_capability_truth or ()):
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        if not provider_id.lower().startswith("ollama-local:"):
            continue
        model_id = str(item.get("model_id") or "").strip()
        if model_id and model_id not in lanes:
            lanes.append(model_id)
    default_model = str(runtime.runtime_model_tag or "").strip()
    if default_model and default_model not in lanes:
        lanes.insert(0, default_model)
    return lanes


def runtime_model_status_response(
    text: str,
    runtime: RuntimeServices,
    *,
    requested_model: str = "",
) -> dict[str, Any] | None:
    if not _looks_like_runtime_model_status_question(text):
        return None
    from core.auto_local_only_mode import is_auto_selection

    selected = str(requested_model or "").strip()
    if selected and not is_auto_selection(selected):
        return {
            "response": (
                f"- Selected model for this turn: `{selected}`.\n"
                "- This explicit composer pin overrides VOOL Auto/local model routing for the "
                "model call. Local tools and safety/control commands can still execute locally.\n"
                "- The provider/model footer on the completed reply remains the execution receipt."
            ),
            "confidence": 1.0,
            "source": "selected_model_status",
            "deterministic": True,
            "requested_model": selected,
        }
    default_model = str(runtime.runtime_model_tag or "").strip() or "unknown"
    lanes = _runtime_text_model_lanes(runtime)
    largest = max(lanes, key=parameter_count_for_model) if lanes else default_model
    lane_text = ", ".join(f"`{item}`" for item in lanes) if lanes else "`none`"
    if largest and largest != default_model:
        router_line = f"- Routing can select `{largest}` for heavier local turns; no larger local text model is installed right now."
    else:
        router_line = "- No separate larger local text model is installed right now."
    return {
        "response": (
            f"- Boot/default local model: `{default_model}` ({parameter_size_for_model(default_model)}).\n"
            f"- Visible local text lanes: {lane_text}.\n"
            f"{router_line}\n"
            "- There is no single fixed model: routing picks one per turn (small talk uses the smallest, "
            "normal turns the daily model, heavy turns the largest). The footer under each reply shows "
            "the model that actually ran — that is the source of truth, not this default."
        ),
        "confidence": 1.0,
        "source": "runtime_model_status",
        "deterministic": True,
        "runtime_model_tag": default_model,
        "local_text_lanes": lanes,
    }


# An operating system named in the sentence. "What macOS version is this machine running?" carries
# both "version" and "running", so it was answered with the VOOL release and build id -- a correct
# fact about the wrong subject. The OS version is a machine spec and belongs to machine.inspect_specs,
# which already reads and prints it.
_OS_VERSION_SUBJECT_RE = re.compile(
    r"\b(?:mac\s?os|macos|osx|os\s?x|windows|linux|ubuntu|debian|fedora|operating\s+system"
    r"|os\s+version|kernel|sonoma|sequoia|ventura|monterey)\b",
    re.IGNORECASE,
)


def _looks_like_runtime_version_question(text: str) -> bool:
    clean = " ".join(str(text or "").lower().split())
    if not clean:
        return False
    if clean in {"version", "/version", "about", "/about", "build", "/build"}:
        return True
    if _OS_VERSION_SUBJECT_RE.search(clean) and "vool" not in clean and "vool" not in clean:
        # The user named an OS, so they are asking about the OS -- unless they named this product
        # too ("which VOOL build am I running on Windows?"), where the build really is the subject.
        return False
    has_version_term = any(term in clean for term in ("version", "which build", "what build", "release version", "build number"))
    has_subject = any(term in clean for term in ("vool", "you", "your", "running", "installed", "this build", "this version"))
    # Live incident, 2026-08-06, session openclaw:2fd81093adaf0eb4d1d1: a 150-word correctness-audit
    # brief was answered with the build stamp instead of ever reaching the agent, because "historical
    # Git versions" supplied `version` and "you may run safe read-only commands" supplied `you` -- two
    # ordinary English words, neither one an actual question about this runtime's version. The sibling
    # detector directly above, `_looks_like_runtime_model_status_question`, was already hardened
    # against this exact shape (see its own comment, same date) with a length gate; this function
    # never got the matching fix. A genuine version question is short -- "what version are you
    # running?" is five words -- so the same gate applies here for the same reason.
    looks_like_a_short_question = len(clean.split()) <= _MAX_STATUS_QUESTION_WORDS
    return has_version_term and has_subject and looks_like_a_short_question


def _openclaw_client_version() -> str:
    """Best-effort read of the installed OpenClaw client version from its global npm
    package.json, so a version report can name the OpenClaw UI the user is actually running."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    roots: list[Path] = []
    for cmd in (["npm", "root", "-g"], ["npm.cmd", "root", "-g"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if out.returncode == 0 and out.stdout.strip():
                roots.append(Path(out.stdout.strip()))
                break
        except Exception:
            continue
    which = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    if which:
        roots.append(Path(which).resolve().parent / "node_modules")
    for root in roots:
        pkg = root / "openclaw" / "package.json"
        try:
            if pkg.is_file():
                version = str(json.loads(pkg.read_text(encoding="utf-8")).get("version") or "").strip()
                if version:
                    return version
        except Exception:
            continue
    return ""


def runtime_version_response(text: str, runtime: RuntimeServices) -> dict[str, Any] | None:
    """Deterministic answer to 'what version are you running' -- reports the exact VOOL build
    the API headers report plus the OpenClaw client version, so version-correlated issues can be
    debugged from what the user sees."""
    if not _looks_like_runtime_version_question(text):
        return None
    stamp = dict(runtime.runtime_version_stamp or {})
    release = str(stamp.get("release_version") or "unknown")
    build = str(stamp.get("build_id") or "unknown")
    commit = str(stamp.get("commit") or "")
    channel = str(stamp.get("channel_name") or "")
    lines = [f"- VOOL release: `{release}`" + (f" (channel `{channel}`)" if channel else "")]
    lines.append(f"- Build: `{build}`" + (" (uncommitted changes)" if bool(stamp.get("dirty")) else ""))
    if commit:
        lines.append(f"- Commit: `{commit}`")
    openclaw_version = _openclaw_client_version()
    if openclaw_version:
        lines.append(f"- OpenClaw client: `{openclaw_version}`")
    model = str(runtime.runtime_model_tag or "").strip()
    if model:
        lines.append(f"- Local model: `{model}`")
    return {
        "response": "\n".join(lines),
        "confidence": 1.0,
        "source": "runtime_version",
        "deterministic": True,
        "runtime_release_version": release,
        "runtime_build_id": build,
        "openclaw_client_version": openclaw_version,
    }


def capability_snapshot_with_runtime(
    runtime: RuntimeServices,
    capability_snapshot_provider: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(capability_snapshot_provider() or {})
    public_hive_auth = dict(runtime.public_hive_auth or {})
    runtime_provider_truth = tuple(
        dict(item)
        for item in tuple(runtime.provider_capability_truth or ())
        if isinstance(item, dict)
    )
    if runtime_provider_truth:
        payload["provider_capability_truth"] = list(runtime_provider_truth)
        model_lane_defaults = dict(payload.get("model_lane_defaults") or {})
        fast_model = "vool-qwen3-30b-a3b:nothink"
        if any(str(item.get("model_id") or "").strip() == fast_model for item in runtime_provider_truth):
            model_lane_defaults["default_model"] = fast_model
            model_lane_defaults["fast_local_preferred_model"] = fast_model
            model_lane_defaults["fast_local_installed"] = True
            model_lane_defaults["no_think_default"] = True
            payload["model_lane_defaults"] = model_lane_defaults
    capabilities = [dict(item) for item in list(payload.get("capabilities") or []) if isinstance(item, dict)]
    feature_flags = dict(payload.get("feature_flags") or {})

    if feature_flags.get("helper_mesh_enabled"):
        for item in capabilities:
            if str(item.get("name") or "").strip() == "helper_mesh":
                item["state"] = "implemented"
                item["reason"] = "Helper coordination lanes are enabled for this runtime."
                break

    if public_hive_auth:
        payload["public_hive_auth"] = public_hive_auth
        status = str(public_hive_auth.get("status") or "").strip()
        ok = bool(public_hive_auth.get("ok"))
        for item in capabilities:
            if str(item.get("name") or "").strip() != "public_hive_surface":
                continue
            if ok or status in {"already_configured", "hydrated_from_bundle", "hydrated_from_local_cluster", "synced_from_ssh", "no_auth_required", "disabled"}:
                item["state"] = "implemented"
                item["reason"] = f"Public Hive surface is live for this runtime ({status or 'ready'})."
            else:
                item["state"] = "blocked_by_configuration"
                item["reason"] = f"Public Hive surface is enabled but not ready for writes ({status or 'unknown'})."
            break
    if capabilities:
        payload["capabilities"] = capabilities
    return payload


def _plugin_storage_health_summary() -> dict[str, Any]:
    """The plugin-storage state for /healthz: the recorded state only, no filesystem access."""
    try:
        from core.plugin_catalog import storage_state

        state = storage_state()
        return {
            "state": str(state.get("state") or ""),
            "plugins_dir": str(state.get("plugins_dir") or ""),
            "detail": str(state.get("detail") or ""),
            "loaded": list(state.get("loaded") or []),
            "attempts": int(state.get("attempts") or 0),
            "last_attempt_at": str(state.get("last_attempt_at") or ""),
            "in_flight": bool(state.get("in_flight")),
        }
    except Exception as exc:  # health must answer even if the plugin layer is broken
        return {"state": "unknown", "detail": f"{type(exc).__name__}: {exc}"[:200]}


def _health_capability_snapshot(
    runtime: RuntimeServices,
    capability_snapshot_provider: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    if capability_snapshot_provider is runtime_capability_snapshot:
        return capability_snapshot_with_runtime(runtime, lambda: {})
    return capability_snapshot_with_runtime(runtime, capability_snapshot_provider)


def _augment_history_from_session_log(
    history: list[dict[str, str]],
    *,
    session_id: str,
    user_text: str,
    limit: int = 6,
) -> list[dict[str, str]]:
    return augment_history_from_session_log(
        history,
        session_id=session_id,
        user_text=user_text,
        limit=limit,
    )


def _inbound_source_context(body: dict[str, Any]) -> dict[str, Any]:
    payload = body.get("source_context")
    return dict(payload) if isinstance(payload, dict) else {}


def _runtime_model_catalog(
    *,
    capability_snapshot: dict[str, Any],
    default_model_name: str,
) -> list[dict[str, str]]:
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(model_id: str, *, owned_by: str) -> None:
        normalized = str(model_id or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        catalog.append({"id": normalized, "owned_by": str(owned_by or "vool-runtime").strip() or "vool-runtime"})

    _add(default_model_name, owned_by="vool-runtime")
    for item in list(capability_snapshot.get("provider_capability_truth") or []):
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("provider_id") or "").strip()
        model_id = str(item.get("model_id") or "").strip()
        owned_by = provider_id or "vool-runtime"
        if model_id:
            _add(model_id, owned_by=owned_by)
        if provider_id:
            _add(provider_id, owned_by=owned_by)
    return catalog


def _ollama_tag_parameter_size(model_id: str, *, model_name: str, runtime: RuntimeServices) -> str:
    normalized_model_id = str(model_id or "").strip()
    normalized_default = str(model_name or "").strip()
    if normalized_model_id in {normalized_default, f"{normalized_default}:latest"}:
        return str(runtime.runtime_parameter_size or "").strip() or parameter_size_for_model(runtime.runtime_model_tag)
    return parameter_size_for_model(normalized_model_id)


def _ollama_tag_payload(*, capability_snapshot: dict[str, Any], model_name: str, runtime: RuntimeServices) -> dict[str, Any]:
    models = []
    for entry in _runtime_model_catalog(capability_snapshot=capability_snapshot, default_model_name=model_name):
        parameter_size = _ollama_tag_parameter_size(entry["id"], model_name=model_name, runtime=runtime)
        models.append(
            {
                "name": entry["id"],
                "model": entry["id"],
                "modified_at": datetime.now(timezone.utc).isoformat(),
                "size": 0,
                "digest": "vool-runtime",
                "details": {
                    "parent_model": "",
                    "format": "vool",
                    "family": "qwen",
                    "parameter_size": parameter_size,
                    "quantization_level": "runtime",
                },
            }
        )
    return {"models": models}


def _openai_models_payload(*, capability_snapshot: dict[str, Any], model_name: str) -> dict[str, Any]:
    data = []
    for entry in _runtime_model_catalog(capability_snapshot=capability_snapshot, default_model_name=model_name):
        data.append(
            {
                "id": entry["id"],
                "object": "model",
                "created": 0,
                "owned_by": entry["owned_by"],
            }
        )
    return {"object": "list", "data": data}



def _update_turn_guard(source_context: dict[str, Any] | None, session_id: str) -> contextlib.AbstractContextManager[str]:
    """Register a running chat turn with the updater's work coordinator so a restart
    press can honestly refuse while turns that may change things are in flight (the
    2026-09-01 amendment's pause-new-work law, wired at the real chat seam).
    Fail-safe: no subsystem, no registration, the turn runs regardless."""
    import contextlib

    @contextlib.contextmanager
    def _noop():
        yield ""

    try:
        from core.updater.runtime import get_update_subsystem

        subsystem = get_update_subsystem()
        if subsystem is None:
            return _noop()
        context = source_context or {}
        mode = str(context.get("operating_mode") or "").strip().lower()
        autonomy = str(context.get("autonomy_override") or "").strip().lower()
        destructive = mode in {"auto", "bypass_permissions", "full_local", "workspace_write"} or bool(autonomy)
        work_id = f"chat:{session_id}:{id(context) & 0xFFFFFFFF:x}"
        subsystem.register_turn(
            work_id,
            "an active chat turn that may still change files" if destructive else "an active chat turn",
            destructive=destructive,
        )

        @contextlib.contextmanager
        def _guard():
            try:
                yield work_id
            finally:
                subsystem.complete_turn(work_id)

        return _guard()
    except Exception:
        return _noop()



def _stream_under_update_work(stream: Iterable[bytes], _work_id: str) -> Iterable[bytes]:
    """Keep the updater's turn-registration alive for the whole streamed body."""
    yield from stream


def dispatch_get(
    *,
    path: str,
    query: dict[str, list[str]],
    runtime: RuntimeServices,
    model_name: str,
    capability_snapshot_provider: Callable[[], dict[str, Any]] = runtime_capability_snapshot,
    client_host: str = "",
    headers: dict[str, str] | None = None,
) -> ApiResponse:
    normalized_path = path.rstrip("/") or "/"

    def resolve_page_locale(page_query: dict[str, list[str]], page_headers: dict[str, str] | None) -> str:
        """The UI locale for one page render: ?ui_locale= > the vool_ui_locale cookie > en.

        The ONE deterministic precedence of core.i18n.catalog.resolve_render_locale; the
        cookie is what the page's own locale picker persists, so a selected language survives
        reloads and restarts without a second store. The app UI locale is DELIBERATELY
        separate from the model answer language (its own Settings widget) and from dictation
        recognition (its own composer control).
        """
        from http.cookies import SimpleCookie

        from core.i18n.catalog import resolve_render_locale

        cookie_locale = ""
        raw = _header_value(page_headers, "cookie")
        if raw:
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:
                jar = {}
            morsel = jar.get("vool_ui_locale")
            cookie_locale = morsel.value if morsel else ""
        query_locale = (page_query.get("ui_locale") or [""])[0]
        return resolve_render_locale(query_locale, cookie_locale, "en")

    if normalized_path.startswith("/chat-assets/"):
        from core.web.api.chat_assets_api import handle_chat_asset_get

        return handle_chat_asset_get(normalized_path)

    if normalized_path == "/media-editor" or normalized_path.startswith("/media-editor/"):
        from core.web.api.media_editor_api import handle_media_editor_get

        return handle_media_editor_get(normalized_path, query)

    # ---- Command registry projection (owner command centre): GET surfaces ----
    if normalized_path == "/api/commands" or normalized_path.startswith("/api/commands/"):
        from core.command_registry.api import handle_commands_get

        status, payload = handle_commands_get(normalized_path, query)
        return apply_runtime_headers(json_response(status, payload), runtime)
    # Calendar and notifications lane: the persistent notification centre the bell reads, and the calendar
    # alert view (accounts, chosen calendars, pending alerts, upcoming events, preferences). Owner-local reads.
    if normalized_path == "/api/notifications":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.operator import notification_center

        include_dismissed = str((query.get("include_dismissed") or ["0"])[0]).strip().lower() in {"1", "true", "yes"}
        try:
            listing = notification_center.list_items(
                after_seq=_qint(query, "after", 0), limit=_qint(query, "limit", 50), include_dismissed=include_dismissed,
            )
        except Exception as exc:
            return apply_runtime_headers(
                json_response(500, {"ok": False, "error": "notification_store_unavailable", "reason": type(exc).__name__}), runtime
            )
        return apply_runtime_headers(json_response(200, {"ok": True, **listing}), runtime)
    if normalized_path in {"/api/calendar/accounts", "/api/notifications/preferences"}:
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        try:
            if normalized_path == "/api/calendar/accounts":
                from core.operator import calendar_accounts

                view = calendar_accounts.settings_view()
            else:
                from core.operator import notification_center

                view = {"ok": True, "preferences": notification_center.load_preferences()}
        except Exception as exc:
            return apply_runtime_headers(
                json_response(500, {"ok": False, "error": "calendar_store_unavailable", "reason": type(exc).__name__}), runtime
            )
        return apply_runtime_headers(json_response(200, view), runtime)
    if normalized_path == "/api/notifications/native/status":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.operator import native_notifications

        try:
            view = native_notifications.bridge_status()
        except Exception as exc:
            return apply_runtime_headers(
                json_response(500, {"ok": False, "error": "notification_store_unavailable", "reason": type(exc).__name__}), runtime
            )
        return apply_runtime_headers(json_response(200, view), runtime)
    if normalized_path == "/api/calendar/alerts":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.operator import calendar_alerts

        try:
            view = calendar_alerts.alerts_view()
        except Exception as exc:
            return apply_runtime_headers(
                json_response(500, {"ok": False, "error": "calendar_store_unavailable", "reason": type(exc).__name__}), runtime
            )
        return apply_runtime_headers(json_response(200, {"ok": True, **view}), runtime)
    # Scheduling vertical: read-only projection of the durable reminder store. Scoped to the
    # session the caller names (chat isolation: one session never reads another's reminders).
    if normalized_path == "/api/reminders":
        session_ids = query.get("session_id") or query.get("session") or []
        session_id = str(session_ids[0]).strip() if session_ids else ""
        include_delivered = not (query.get("pending_only") and str(query.get("pending_only")[0]).strip().lower() in {"1", "true", "yes"})
        if not session_id:
            return apply_runtime_headers(
                json_response(400, {"error": "bad_request", "reason": "session_id is required"}), runtime
            )
        # The same fold the chat ingress applies, so a caller's raw handle maps to the
        # canonical namespace its turns actually wrote under.
        from core.chat_session_identity import canonical_chat_session_id

        session_id = canonical_chat_session_id(session_id)
        try:
            from core.operator.reminder_dispatcher import reminder_store_snapshot, reminders_for_session

            payload = {
                "session_id": session_id,
                "reminders": reminders_for_session(session_id, include_delivered=include_delivered),
                "store": reminder_store_snapshot(),
            }
            return apply_runtime_headers(json_response(200, payload), runtime)
        except Exception as exc:
            return apply_runtime_headers(
                json_response(500, {"error": "reminder_store_unavailable", "reason": str(exc)[:200]}), runtime
            )
    if normalized_path in {"/school", "/school/student"}:
        from core.school.api import handle_school_get

        return handle_school_get(normalized_path, query, headers or {})
    if normalized_path == "/school/api/state":
        from core.school.api import handle_school_api_get

        return handle_school_api_get(normalized_path, query, headers)
    if normalized_path.startswith("/api/money/"):
        # the monetary law's owner-local seam: reads only on GET (money_authority_api)
        from core.web.api.money_authority_api import handle_money_get

        return apply_runtime_headers(handle_money_get(normalized_path, query, client_host=client_host), runtime)
    if normalized_path.startswith("/api/wallet/") or normalized_path == "/v1/wallet/info":
        from core.product_edition import edition_allows

        wallet_ok, wallet_reason = edition_allows("wallet")
        if not wallet_ok:
            return apply_runtime_headers(
                json_response(404, {"error": "not_found", "reason": wallet_reason}), runtime
            )
        from core.web.api.wallet_api import handle_wallet_get

        return apply_runtime_headers(handle_wallet_get(normalized_path, query, client_host=client_host), runtime)
    if normalized_path.startswith("/api/mobile/"):
        from core.web.api.mobile_companion_api import handle_mobile_companion_get

        return handle_mobile_companion_get(normalized_path, query, runtime=runtime, model_name=model_name, client_host=client_host)

    if normalized_path in {"/task-rail", "/trace"}:
        return apply_runtime_headers(
            html_response(
                200,
                render_runtime_task_rail_html(),
                headers={
                    "X-Vool-Workstation-Version": VOOL_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Vool-Workstation-Surface": "trace-rail",
                },
            ),
            runtime,
        )

    if normalized_path == "/api/session/bundle/download":
        # Download an exported bundle file. Owner-local, and ONLY files under the home's
        # session_bundles directory are reachable (real-path containment check).
        from pathlib import Path
        from urllib.parse import unquote

        from core import runtime_paths

        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        requested = unquote((query.get("path") or [""])[0])
        bundles_root = runtime_paths.data_path("session_bundles").resolve()
        try:
            resolved = Path(requested).resolve()
            resolved.relative_to(bundles_root)
        except (ValueError, OSError):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "path_not_allowed"}), runtime)
        if not resolved.is_file():
            return apply_runtime_headers(json_response(404, {"ok": False, "error": "not_found"}), runtime)
        data = resolved.read_bytes()
        return apply_runtime_headers(
            ApiResponse(
                status=200,
                content_type="application/octet-stream",
                body=data,
                headers={
                    "Content-Disposition": f'attachment; filename="{resolved.name}"',
                },
            ),
            runtime,
        )

    if normalized_path == "/chat":
        # VOOL's own chat surface (Phase B option b): the product serves its main UI itself, so
        # the desktop bundle does not depend on the external OpenClaw Node app.
        from core.vool_chat_page import render_vool_chat_html

        return apply_runtime_headers(
            html_response(
                200,
                render_vool_chat_html(
                    build_commit=str((runtime.runtime_version_stamp or {}).get("commit") or ""),
                    ui_locale=resolve_page_locale(query, headers),
                ),
                headers={
                    "X-Vool-Workstation-Version": VOOL_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Vool-Workstation-Surface": "chat",
                    **WALLET_PAGE_FRAMING_HEADERS,
                },
            ),
            runtime,
        )

    if normalized_path == "/settings":
        # VOOL Settings: one organised surface for values that used to be spread across a long
        # chat modal, the composer popover and a plugins dialog. Served from THIS runtime on THIS
        # origin, so the native Settings window and a browser tab are the same page against the
        # same backend - a second window, never a second runtime. Every control it renders writes
        # through an endpoint that already exists and already carries that value's authority,
        # gate and receipt; the page itself owns no state.
        from core.vool_settings_page import render_vool_settings_html

        return apply_runtime_headers(
            html_response(
                200,
                render_vool_settings_html(
                    build_commit=str((runtime.runtime_version_stamp or {}).get("commit") or ""),
                    ui_locale=resolve_page_locale(query, headers),
                ),
                headers={
                    "X-Vool-Workstation-Version": VOOL_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Vool-Workstation-Surface": "settings",
                    **WALLET_PAGE_FRAMING_HEADERS,
                },
            ),
            runtime,
        )

    if normalized_path == "/setup":
        # VOOL guided setup: one step per screen, served from THIS runtime on THIS origin exactly
        # like /settings (a native window, a tab or the chat's frame overlay all load the same
        # page against the same backend). The page owns no state: done-ness is derived by
        # core/setup_progress.py and every control writes through a door that already exists.
        from core.vool_setup_page import render_vool_setup_html

        return apply_runtime_headers(
            html_response(
                200,
                render_vool_setup_html(
                    build_commit=str((runtime.runtime_version_stamp or {}).get("commit") or ""),
                    ui_locale=resolve_page_locale(query, headers),
                ),
                headers={
                    "X-Vool-Workstation-Version": VOOL_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Vool-Workstation-Surface": "setup",
                    **WALLET_PAGE_FRAMING_HEADERS,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/setup/state":
        # The derived first-run setup state (core/setup_progress.py): which steps are really done,
        # read fresh from the authorities that own each value. A plain local read -- no provider,
        # no model, no Keychain -- so Settings may read it at boot. Owner-local like every other
        # projection of the operator's configuration.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import setup_progress

        return apply_runtime_headers(json_response(200, setup_progress.snapshot()), runtime)

    if normalized_path in {"/web0", "/null-browser"}:
        # The .null browser: a normal browser can't open a .null site (Arweave, behind
        # the resolver), so VOOL serves this entry page. Served here on the always-on
        # API server (11435) - the Meet server (8766) is not guaranteed to be running -
        # and the page's own JS talks back to this same origin.
        from core.null_browser_page import render_null_browser_html

        return apply_runtime_headers(
            html_response(
                200,
                render_null_browser_html(),
                headers={
                    "X-Vool-Workstation-Version": VOOL_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Vool-Workstation-Surface": "web0-browser",
                },
            ),
            runtime,
        )

    if normalized_path == "/api/web0/resolve":
        # Read-only: a .null NAME -> its Arweave content URL, so the /web0 browser can
        # load the live site. This is a public on-chain read + a public Arweave gateway
        # URL; no key, no signing, no payment (distinct from the /api/null dial path).
        return apply_runtime_headers(_web0_resolve_response(query), runtime)

    if normalized_path == "/":
        return apply_runtime_headers(text_response(200, "Ollama is running"), runtime)

    if normalized_path == "/api/tags":
        capability_snapshot = capability_snapshot_with_runtime(runtime, capability_snapshot_provider)
        payload = _ollama_tag_payload(capability_snapshot=capability_snapshot, model_name=model_name, runtime=runtime)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/v1/models":
        capability_snapshot = capability_snapshot_with_runtime(runtime, capability_snapshot_provider)
        payload = _openai_models_payload(capability_snapshot=capability_snapshot, model_name=model_name)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/healthz", "/v1/healthz"}:
        capability_snapshot = _health_capability_snapshot(runtime, capability_snapshot_provider)
        payload = {
            "ok": True,
            "agent": runtime.display_name,
            "daemon": runtime.daemon is not None,
            "runtime": dict(runtime.runtime_version_stamp or {}),
            "capabilities": capability_snapshot,
            # Plugin STORAGE state, read from the record the boot probe left -- never a directory
            # open on the health path (the native host gates the window on this endpoint, and a
            # plugin folder that stalls is exactly the condition it must keep answering through).
            "plugins": _plugin_storage_health_summary(),
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/api/runtime/version", "/v1/runtime/version"}:
        return apply_runtime_headers(json_response(200, dict(runtime.runtime_version_stamp or {})), runtime)

    if normalized_path in {"/api/runtime/capabilities", "/v1/runtime/capabilities"}:
        capability_snapshot = capability_snapshot_with_runtime(runtime, capability_snapshot_provider)
        return apply_runtime_headers(json_response(200, capability_snapshot), runtime)

    if normalized_path == "/api/runtime/sessions":
        summary_only = str((query.get("summary") or [""])[0]).lower() in {"1", "true"}
        sessions = list_runtime_sessions(limit=100, include_execution_history=False) if summary_only else list_runtime_sessions(limit=100)
        return apply_runtime_headers(json_response(200, {"sessions": sessions}), runtime)

    if normalized_path == "/api/runtime/unresolved-effects":
        # A6 surface (NIA-006): every active unresolved effect with its immutable
        # resolution history, so an UNKNOWN in a send-message/payment intent is a
        # visible row the owner can act on — not a forever-block buried in receipts.
        # Read-only; the state-changing twin lives in dispatch_post (loopback-only).
        from core.runtime_continuity import (
            list_active_unresolved_effects,
            list_unresolved_effect_resolutions,
        )

        try:
            rows = list_active_unresolved_effects(limit=100)
            for row in rows:
                try:
                    row["resolutions"] = list_unresolved_effect_resolutions(
                        row.get("logical_effect_id") or ""
                    )
                except Exception:
                    row["resolutions"] = []
            payload = {"ok": True, "unresolved_effects": rows}
        except Exception as exc:
            # A read that failed is a server failure, reported as HTTP 500 with a
            # stable diagnostic -- never an error body riding a success status.
            from core.web.request_ids import resolve_request_id

            return apply_runtime_headers(
                json_response(
                    500,
                    diagnostics.with_diagnostic(
                        {"ok": False, "error": safe_error_text(exc)},
                        diagnostics.diagnostic_envelope(
                            f"{diagnostics.NAMESPACE}.unknown",
                            detail=safe_error_text(exc),
                            correlation_id=resolve_request_id(headers),
                            http_status=500,
                        ),
                    ),
                ),
                runtime,
            )
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/chat/sessions":
        # Sidebar list: distinct chat threads from the conversation log (verbatim user/assistant
        # turns), distinct from /api/runtime/sessions which is the task-rail/event view.
        from core.persistent_memory import list_conversation_sessions

        # The page is capped, so the sidebar cannot count from it. It used to: the group headers
        # rendered `general.length` over the 100 rows this endpoint returned, against 2,005 real
        # chats. Deleting a chat freed a slot, the next chat by recency slid into the window, and
        # since most chats are unbound that arrival was almost always a General one -- so deleting
        # a chat OUT of a project made General appear to grow, and deleting one IN General left the
        # number unchanged because one left as another arrived. Neither number was ever wrong about
        # the page; both were wrong about the chats. Counts are computed over every chat and sent
        # alongside, keyed by project id with "" for unbound, and archived rows are excluded to
        # match what the sidebar groups.
        every_session = list_conversation_sessions(limit=1_000_000)
        counts: dict[str, int] = {}
        for row in every_session:
            if row.get("archived"):
                continue
            counts[str(row.get("project_id") or "")] = (
                counts.get(str(row.get("project_id") or ""), 0) + 1
            )
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "sessions": every_session[:100],
                    "counts": counts,
                    "total": sum(counts.values()),
                    "page_size": 100,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/chat/attachments/limits":
        # The rules the composer states up front, from the same tables the door enforces.
        from core.chat_attachments import limits_payload

        return apply_runtime_headers(json_response(200, limits_payload()), runtime)

    if normalized_path == "/api/chat/attachments":
        # A chat's staged-but-unsent attachments, so a reload can paint its draft chips again.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import chat_attachments

        att_session = str((query.get("session") or [""])[0] or "").strip()
        try:
            items = chat_attachments.list_staged(att_session) if att_session else []
        except chat_attachments.AttachmentRefused as exc:
            return apply_runtime_headers(json_response(exc.http_status, exc.to_dict()), runtime)
        return apply_runtime_headers(json_response(200, {"session_id": att_session, "attachments": items}), runtime)

    if normalized_path == "/api/chat/attachments/documents":
        # The chat's documents that still hold bytes (sent, retained), for retrieval and export.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import chat_attachments

        doc_session = str((query.get("session") or [""])[0] or "").strip()
        try:
            documents = chat_attachments.list_documents(doc_session) if doc_session else []
        except chat_attachments.AttachmentRefused as exc:
            return apply_runtime_headers(json_response(exc.http_status, exc.to_dict()), runtime)
        return apply_runtime_headers(json_response(200, {"session_id": doc_session, "documents": documents}), runtime)

    if normalized_path == "/api/chat/attachments/preview":
        # The stored bytes of an item the SAME chat staged: an image (metadata-stripped) for its
        # thumbnail, or a document's exact text for its expandable preview -- before the turn,
        # after it (documents are retained), and 410 once the document was erased.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import chat_attachments
        from core.chat_session_identity import canonical_chat_session_id

        att_session = str((query.get("session") or [""])[0] or "").strip()
        att_id = str((query.get("id") or [""])[0] or "").strip()
        try:
            found = chat_attachments.read_staged_bytes(session_id=att_session, attachment_id=att_id) if att_session and att_id else None
        except chat_attachments.AttachmentRefused:
            found = None
        if found is None:
            erased = chat_attachments.get_record(att_id) if att_session and att_id else None
            # Records are keyed by the chat's identity, not by the raw handle a client happened
            # to send, so this comparison resolves through the same authority the doors use.
            erased_owner = canonical_chat_session_id(att_session)
            if erased and erased.get("session_id") == erased_owner and erased.get("state") == "erased":
                return apply_runtime_headers(json_response(410, {"error": "erased", "message": "This document was erased from the chat."}), runtime)
            return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)
        record, data = found
        if record.get("document"):
            content_type = f"{record['media_type']}; charset=utf-8"
        elif record.get("kind") == "image":
            content_type = str(record["media_type"])
        else:
            return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)
        return apply_runtime_headers(
            ApiResponse(
                status=200,
                content_type=content_type,
                body=data,
                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            ),
            runtime,
        )

    if normalized_path == "/api/chat/export":
        # PB05 chat export (additive delegation): the ENTIRE selected conversation as
        # TXT or Markdown, collected from the persisted transcript under a stable
        # server-side snapshot with paginated reads — not the rendered DOM and not a
        # last-N window. This is the ONLY arm: the route's guards, collection and
        # formatting live in core.web.api.chat_export_api; nothing existing is rewritten.
        from core.web.api.chat_export_api import handle_chat_export_get

        return handle_chat_export_get(query=query, runtime=runtime, client_host=client_host)

    if normalized_path == "/api/context/pages":
        # C09/C13 turn-context inspect (additive delegation): scoped, provenance-
        # carrying inspection of the context pages real turns admit; bytes are
        # opt-in and the read is principal-bounded. The route's guards and
        # records live in core.web.api.context_pages_api; nothing existing is
        # rewritten.
        from core.web.api.context_pages_api import handle_context_pages_get

        return handle_context_pages_get(query=query, runtime=runtime, client_host=client_host)

    if normalized_path == "/api/chat/history":
        # Reload a past thread's transcript so the sidebar can reopen it as [{role, content}].
        from core.persistent_memory import recent_conversation_events

        session_id = str((query.get("session") or [""])[0] or "").strip()
        try:
            limit = int(str((query.get("limit") or ["200"])[0] or "200").strip())
        except ValueError:
            limit = 200
        messages: list[dict[str, str]] = []
        if session_id:
            from core.context_namespace import load_chat_namespace

            namespace = load_chat_namespace(session_id)
            if (
                namespace is not None
                and namespace.lifecycle_state == "deleted"
            ):
                return apply_runtime_headers(
                    json_response(404, {"error": "session not found"}),
                    runtime,
                )
            # `include_artifacts=True` is what makes this the TRANSCRIPT reader rather than
            # a recall reader: assistant-authored artifacts (a council verdict card) are
            # part of what the chat log shows and part of nothing the model is fed.
            for event in recent_conversation_events(
                session_id, limit=max(1, min(limit, 500)), include_artifacts=True
            ):
                # Background refresh uses the same governed transcript projection,
                # with only reminder artifacts crossing the wire.
                event_kind = str((query.get("event_kind") or [""])[0])
                if event_kind and str(event.get("artifact_kind") or "") != event_kind:
                    continue
                user_text = str(event.get("user") or "").strip()
                assistant_text = str(event.get("assistant") or "").strip()
                # `ts` is written once per turn, right after the assistant's answer completes (see
                # append_conversation_event) -- a genuine persisted completion time for the
                # assistant side. It is deliberately NOT attached to the user message: this store
                # has no separately-recorded moment the user actually sent their message, and
                # reusing the completion timestamp for that would mislabel it as something it
                # isn't. The live session already shows the real send instant (captured client-side
                # at the moment of sending); only the reload path is missing it, and that gap is a
                # real backend contract gap, not something to paper over here.
                turn_ts = event.get("ts")
                if user_text:
                    user_row: dict[str, Any] = {"role": "user", "content": user_text}
                    # The attachment receipt the runtime wrote with this turn: names, kinds, sizes
                    # and outcomes, so a reload paints the chips the turn actually carried.
                    attachment_receipt = event.get("attachments")
                    if isinstance(attachment_receipt, list) and attachment_receipt:
                        user_row["attachments"] = [
                            {
                                key: entry.get(key)
                                for key in ("id", "name", "kind", "media_type", "size_bytes", "outcome", "document", "sha256", "chars", "lines", "state")
                                if key in entry
                            }
                            for entry in attachment_receipt
                            if isinstance(entry, dict)
                        ]
                    messages.append(user_row)
                if assistant_text:
                    # A7 W5 (S13) + A8 availability gate: the CONTENT is gated,
                    # not the label. Verified bytes require an AVAILABLE
                    # governing finalization; WITHHELD/ERASED payloads are
                    # never served — typed marker, no bytes, never regenerated.
                    # Lineage (the event's request_id) is authoritative; the
                    # content-hash verdict covers legacy pre-lineage turns
                    # (erasure digest tombstones make post-erase lookups
                    # deterministic even though the a7 hash is salted).
                    from core.finalization import (
                        AVAILABILITY_AVAILABLE,
                        AVAILABILITY_ERASED,
                        AVAILABILITY_WITHHELD,
                        get_finalization_by_content,
                        get_finalization_by_request_id,
                        payload_availability_for_text,
                    )

                    event_request_id = str(event.get("request_id") or "").strip()
                    binding = None
                    if event_request_id:
                        try:
                            binding = get_finalization_by_request_id(
                                event_request_id, principal="owner_local"
                            )
                        except Exception:
                            binding = None
                    if binding is not None:
                        availability = str(binding.get("availability") or "")
                    else:
                        availability = payload_availability_for_text(assistant_text)
                        if availability == AVAILABILITY_AVAILABLE:
                            binding = get_finalization_by_content(assistant_text)
                    if availability in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):
                        messages.append(
                            {
                                "role": "assistant",
                                "content": "",
                                "ts": turn_ts,
                                "a7": {
                                    "status": "unavailable_by_policy",
                                    "availability": availability,
                                },
                            }
                        )
                        continue
                    if binding is not None and availability == AVAILABILITY_AVAILABLE:
                        a7_marker = {
                            "status": "verified",
                            "finalization_id": str(binding.get("finalization_id") or ""),
                            "semantic_result_id": str(binding.get("semantic_result_id") or ""),
                            "content_hash": str(binding.get("content_hash") or ""),
                        }
                    else:
                        # No governing AVAILABLE truth: honest legacy label
                        # (LEGACY_UNKNOWN or no governance record at all).
                        a7_marker = {"status": "legacy_unverified"}
                    assistant_message = {
                        "role": "assistant",
                        "content": assistant_text,
                        "ts": turn_ts,
                        "a7": a7_marker,
                    }
                    # The canonical turn identity rides with the message, so the Proof Chip
                    # addresses its read by (session, request) instead of guessing by position
                    # or content. Additive: clients that ignore it are unchanged. Empty on
                    # legacy rows means no chip — an absent identity is never invented.
                    if event_request_id:
                        assistant_message["request_id"] = event_request_id
                    # A typed artifact row keeps its identity across the wire, so a client
                    # can rebuild the card it stands for without parsing prose. The prose
                    # marker inside `content` remains the contract for clients that
                    # predate this field — they render a readable summary either way.
                    artifact_kind = str(event.get("artifact_kind") or "").strip()
                    if artifact_kind:
                        artifact_meta = event.get("artifact")
                        assistant_message["artifact"] = {
                            "kind": artifact_kind,
                            **(artifact_meta if isinstance(artifact_meta, dict) else {}),
                        }
                    messages.append(assistant_message)
        return apply_runtime_headers(
            json_response(200, {"session_id": session_id, "messages": messages}),
            runtime,
        )

    if normalized_path == "/api/chat/proof":
        # The Proof Chip's read door: ONE served turn's evidence, projected read-only by
        # core.proof_projection from the stores that own it. The route owns no evidence logic —
        # binding (session+request) and the honest state vocabulary live in the projection, so
        # the API can never show a friendlier truth than the module derives. A pair that does
        # not bind to a served turn is a typed 404, never "the latest record".
        from core.proof_projection import ProofNotBound, build_turn_proof

        proof_session = str((query.get("session") or [""])[0] or "").strip()
        proof_request = str((query.get("request_id") or [""])[0] or "").strip()
        if not proof_session or not proof_request:
            return apply_runtime_headers(
                json_response(400, {"error": "session and request_id are required"}),
                runtime,
            )
        try:
            proof_payload = build_turn_proof(
                session_id=proof_session,
                request_id=proof_request,
                principal="owner_local",
            )
        except ProofNotBound as exc:
            return apply_runtime_headers(
                json_response(404, {"error": "proof_not_bound", "detail": str(exc)}),
                runtime,
            )
        return apply_runtime_headers(json_response(200, proof_payload), runtime)

    if normalized_path == "/api/chat/pins":
        # Pinned messages for one chat (owner-local snapshots). Backs the header pin panel.
        # A8 gate: pins whose governing payload is WITHHELD/ERASED are not served.
        from core import message_pins

        session_id = str((query.get("session") or [""])[0] or "").strip()
        return apply_runtime_headers(
            json_response(200, {"session_id": session_id, "pins": message_pins.list_servable_pins(session_id)}),
            runtime,
        )

    # ---- First-Run Pact + provider choice: owner GET projections (thin adapters) ----
    if normalized_path in {"/api/onboarding/pact", "/api/onboarding/state", "/api/intake/quarantine/list"}:
        from core.web.api import onboarding_endpoints as _first_run_api

        response = _first_run_api.handle_pact_get(normalized_path, query, runtime, client_host=client_host)
        if response is None:
            response = _first_run_api.handle_provider_get(normalized_path, query, runtime)
        return response

    if normalized_path in {"/api/profile", "/api/profile/export"}:
        # Operator Profile (P1): what VOOL remembers about the operator. Owner-local, A8-gated at
        # the source (a WITHHELD/ERASED item is not in the listing), chat-scoped items only for
        # the chat named in ?session=. Values only -- an account preference is an opaque
        # credential reference; no secret can be in this store by construction.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import operator_profile
        from core.request_trust import OWNER_LOCAL_KEY

        principal = operator_profile.principal_for_request({OWNER_LOCAL_KEY: True, "surface": "web"})
        if normalized_path == "/api/profile/export":
            return apply_runtime_headers(json_response(200, {"ok": True, **operator_profile.export_profile(principal)}), runtime)
        session_id = str((query.get("session") or [""])[0] or "").strip()
        if session_id:
            # The same canonicalization the chat door applies to a client session handle, so a
            # chat-scoped item is listed for the chat that owns it. (Module-level name: a
            # function-local import here shadowed it for the whole function and raised
            # UnboundLocalError on the earlier /api/runtime/operator-snapshot use.)
            session_id = stable_openclaw_session_id(body={"session_id": session_id}, history=[], headers={})
        items = [i.as_dict() for i in operator_profile.list_items(principal, session_id=session_id)]
        candidates = [
            {**i.as_dict(), "candidate_id": i.item_id, "text": _profile_candidate_text(i),
             "actions": ["replace", "scope", "keep"] if i.conflict_with else ["save", "edit", "only_this_chat"]}
            for i in operator_profile.list_items(principal, include_candidates=True, session_id=session_id)
            if i.status == "candidate"
        ]
        return apply_runtime_headers(json_response(200, {
            "ok": True,
            "principal_known": bool(principal),
            "paused": operator_profile.is_paused(principal),
            "items": items,
            "candidates": candidates,
            "scopes": list(operator_profile.SCOPES),
            "categories": {k: v["label"] for k, v in operator_profile.CATEGORIES.items()},
        }), runtime)

    if normalized_path == "/api/settings/prefs":
        # Read the user-facing preferences the Settings panel controls (behaviour + limits). The
        # user's name is an Operator Profile item now; it is echoed here read-only for older
        # clients and resolved through the profile authority (A8-gated), never the JSON field.
        from core.command_registry.legacy import forward as _cr_forward

        _sp_status, _sp_payload = _cr_forward("settings.prefs.list", {})
        return apply_runtime_headers(json_response(_sp_status, _sp_payload), runtime)

    if normalized_path == "/api/settings/credentials":
        # Settings surface: list stored credential NAMES + non-secret labels so the UI can show which
        # cloud keys are connected. Never returns the secret value (list_credentials is name+label
        # only). Loopback-only, like every route (Host-header guard applies upstream).
        from core.command_registry.legacy import forward as _cr_forward

        _sc_status, _sc_payload = _cr_forward("settings.credentials.list", {})
        return apply_runtime_headers(json_response(_sc_status, _sc_payload), runtime)

    if normalized_path == "/api/contacts":
        # Owner-local, read-only: the saved contacts, or a search by name, alias or part of an address (core.contacts).
        # The model's contacts.* tools read the same owner; the owner's edits live in dispatch_post.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _cl_status, _cl_payload = _cr_forward("contacts.book.list", {"payload": {"query": (query.get("q") or [""])[0]}})
        return apply_runtime_headers(json_response(_cl_status, _cl_payload), runtime)

    if normalized_path == "/api/contacts/detail":
        # Owner-local, read-only: one contact with every entry, where each came from, and its change journal.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _cd_status, _cd_payload = _cr_forward("contacts.book.detail", {"payload": {"contact_id": (query.get("id") or [""])[0]}})
        return apply_runtime_headers(json_response(_cd_status, _cd_payload), runtime)

    if normalized_path == "/api/contacts/resolve":
        # Owner-local, read-only: the quick chooser's check that a name maps back to exactly one saved entry.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _cz_payload_in = {key: (query.get(key) or [""])[0] for key in ("name", "kind", "label", "network", "chain", "channel", "account")}
        _cz_status, _cz_payload = _cr_forward("contacts.book.resolve", {"payload": _cz_payload_in})
        return apply_runtime_headers(json_response(_cz_status, _cz_payload), runtime)

    if normalized_path == "/api/contacts/options":
        # Owner-local, read-only: the entry kinds, messaging channels and wallet networks the Contacts form offers.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _co_status, _co_payload = _cr_forward("contacts.book.options", {"payload": {}})
        return apply_runtime_headers(json_response(_co_status, _co_payload), runtime)


    if normalized_path == "/api/email/accounts":
        # Owner-local: the configured email accounts as identities and capabilities (never a secret),
        # the operator's chosen default, and what a read and a send would select right now
        # (core.email_accounts). Read lazily by Settings > Email: it lists credential NAMES.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _ea_status, _ea_payload = _cr_forward("email.accounts.list", {})
        return apply_runtime_headers(json_response(_ea_status, _ea_payload), runtime)
    if normalized_path == "/api/email/recovery":
        # Owner-local, read-only: the draft store's recovery hold in plain terms -- what was preserved,
        # the Message-IDs and accounts the damaged bytes named, the checks recorded, the current
        # generation and the exact effect an acknowledgement would have. The state-changing twins
        # (acknowledge, check) live in dispatch_post.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _er_status, _er_payload = _cr_forward("email.recovery.status", {})
        return apply_runtime_headers(json_response(_er_status, _er_payload), runtime)

    if normalized_path == "/api/contacts/suggestions":
        # Owner-local, read-only: destinations read from untrusted content that wait for the owner's confirmation.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _cs_status, _cs_payload = _cr_forward("contacts.book.suggestions", {"payload": {}})
        return apply_runtime_headers(json_response(_cs_status, _cs_payload), runtime)

    if normalized_path == "/api/contacts/import/sources":
        # Owner-local, read-only: import sources (Apple Contacts, Google, Microsoft), their state and recovery, and the
        # verified credential bindings an import may name (names and accounts only, never a secret).
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _ci_status, _ci_payload = _cr_forward("contacts.import.sources", {"payload": {}})
        return apply_runtime_headers(json_response(_ci_status, _ci_payload), runtime)

    if normalized_path == "/api/contacts/operations":
        # Owner-local, read-only: changes to saved contacts that wait for the owner's PIN or password, each with its review.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _cop_status, _cop_payload = _cr_forward("contacts.operations.list", {"payload": {}})
        return apply_runtime_headers(json_response(_cop_status, _cop_payload), runtime)

    if normalized_path == "/api/contacts/operations/detail":
        # Owner-local, read-only: one change with the saved contact as it is, the exact change and every lookalike name.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _cod_status, _cod_payload = _cr_forward("contacts.operations.detail", {"payload": {"operation_id": (query.get("id") or [""])[0]}})
        return apply_runtime_headers(json_response(_cod_status, _cod_payload), runtime)

    if normalized_path == "/api/contacts/credential":
        # Owner-local, read-only: whether the PIN or password that protects contact changes is set, and any lock (never a secret).
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _ccs_status, _ccs_payload = _cr_forward("contacts.credential.status", {"payload": {}})
        return apply_runtime_headers(json_response(_ccs_status, _ccs_payload), runtime)

    if normalized_path == "/api/cloud/providers":
        # The provider catalog for the Settings selector: id + label + key-id style, so the UI can
        # offer "Auto-detect / OpenAI / Anthropic / …" without hardcoding the list. No secrets.
        from core.cloud_providers import PROVIDERS

        provider_rows = [
            {"id": cfg.provider_id, "label": cfg.label, "model_id_style": cfg.model_id_style,
             "user_base_url": cfg.user_base_url, "auth_placement": cfg.auth_placement}
            for cfg in PROVIDERS.values()
        ]
        return apply_runtime_headers(json_response(200, {"providers": provider_rows}), runtime)

    if normalized_path == "/api/cloud/usepod/discovery":
        # What the UsePod settings panel shows, from caches only: the marketplace snapshot with its
        # provenance and staleness, what this credential was observed to see, the routing policy,
        # approved price bounds, route trust and the evidence behind each capability. No network.
        from core.usepod.discovery import discovery_view

        _ud_filter = str((query.get("q") or [""])[0] or "")[:128]
        view = discovery_view(limit=_qint(query, "limit", 250), model_filter=_ud_filter)
        if _ud_filter:
            from core.usepod.spend_approval import prepaid_spend_readiness
            try:
                view["spend_readiness"] = prepaid_spend_readiness(_ud_filter)
            except Exception:
                view["spend_readiness"] = {"available": False, "state": "unavailable"}
        return apply_runtime_headers(json_response(200, view), runtime)

    if normalized_path == "/api/cloud/usepod/x402/unresolved":
        # Paid accountless calls whose answer never arrived: public facts and the liability state, never the request
        # bytes. A row is resumable once the money law holds its liability as unknown.
        from core.usepod.money_law import unresolved_x402_operations

        return apply_runtime_headers(json_response(200, {"operations": unresolved_x402_operations()}), runtime)

    if normalized_path == "/api/search/providers":
        # The web-search BYOK catalog for Settings: which providers exist, whether each currently
        # holds a key, and where to get one. Presence only — never a key, and no live calls (the
        # Test button is the only path that spends a provider request).
        from core.command_registry.legacy import forward as _cr_forward

        _sr_status, _sr_payload = _cr_forward("search.providers", {})
        return apply_runtime_headers(json_response(_sr_status, _sr_payload), runtime)

    if normalized_path == "/api/discovery":
        # Provider capability discovery truth for the Settings surface: per provider, the
        # model rows with provenance (observed/documented/unknown), freshness and expiry, the
        # last refresh's own outcome word, and missing (disappeared) models kept and labelled.
        # Read-only, cache-only — no network here; the refresh door is the only one that calls
        # out. Never any key material: the store holds digests and endpoint origins only.
        from core.credential_intelligence.discovery import discovery_snapshot

        return apply_runtime_headers(json_response(200, {"providers": discovery_snapshot()}), runtime)

    if normalized_path == "/api/plugins/lifecycle":
        # The nine lifecycle acts as state, not as presence on disk. Read-only: what is installed,
        # what is verified, what is enabled, what is REVOKED, and which packs are actually
        # available to the model right now.
        from core.plugin_lifecycle import lifecycle_snapshot

        return apply_runtime_headers(json_response(200, lifecycle_snapshot()), runtime)

    if normalized_path == "/api/learning/procedures":
        # PB01 hook 4 — the operator's INSPECTION surface for learned procedures. Read-only and
        # secret-free: the records projection carries identity, status, counters, provenance and
        # bounded guidance — never raw step/receipt payloads.
        from core.learning import list_procedure_records

        return apply_runtime_headers(json_response(200, {"procedures": list_procedure_records()}), runtime)

    if normalized_path == "/api/repoops/sessions":
        from core.web.api.repoops_api import repo_sessions_payload

        return apply_runtime_headers(json_response(200, repo_sessions_payload()), runtime)

    if normalized_path == "/api/repoops/session":
        from core.web.api.repoops_api import repo_session_payload

        session_id = (query.get("id") or [""])[0]
        payload = repo_session_payload(str(session_id))
        return apply_runtime_headers(json_response(200 if payload.get("found") else 404, payload), runtime)

    if normalized_path == "/api/plugins":
        # The installed Plugins & Skills catalog for the console panel. Read-only, no secrets: reads
        # the local plugin monorepo (marketplace + manifests + SKILL.md frontmatter). Plugins give
        # VOOL powers; skills teach it how to use them.
        from core.plugin_catalog import read_plugin_catalog

        return apply_runtime_headers(json_response(200, read_plugin_catalog()), runtime)

    if normalized_path == "/api/skills":
        # The NATIVE skill library's inventory: every shipped skill with its version, enabled
        # state, and the typed reason when it is not available (prerequisite missing,
        # capability unavailable, contract invalid). Read-only and fail-soft; the catalog the
        # toolbelt console already reads carries the same rows under the native-library entry.
        from core.native_skill_library import skill_inventory

        return apply_runtime_headers(json_response(200, {"skills": skill_inventory()}), runtime)

    if normalized_path == "/api/projects":
        # Codex-style projects: the folders a chat can be bound to for an isolated workspace + context.
        # Read-only; each entry is {id, name, root, created_at, exists}.
        from core import project_store

        return apply_runtime_headers(json_response(200, {"projects": project_store.list_projects()}), runtime)

    if normalized_path == "/api/files":
        # Everything VOOL has generated (renders, docs), newest first, for the console's Files panel.
        # Read-only; reads real folders (never invents entries).
        from core import generated_files

        return apply_runtime_headers(json_response(200, generated_files.list_generated_files()), runtime)

    if normalized_path == "/api/cloud/keys":
        # Every stored provider key (for the Settings keys panel): {kind, provider, label}. No secret,
        # no last-4 here — just which providers are keyed, so each can be tested individually.
        from core import credential_store, media_tools
        from core.cloud_providers import PROVIDERS, provider_for_slot

        keys: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            for cred in credential_store.list_credentials():
                slot = str(cred.get("name") or "")
                prov = provider_for_slot(slot)
                if prov and prov in PROVIDERS:
                    keys.append({"kind": "cloud", "provider": prov,
                                 "label": str(getattr(PROVIDERS[prov], "label", "") or prov)})
        with contextlib.suppress(Exception):
            if media_tools.has_image_service():
                keys.append({"kind": "fal", "provider": "fal", "label": "fal.ai (images)"})
        return apply_runtime_headers(json_response(200, {"keys": keys}), runtime)

    if normalized_path == "/api/cloud/diagnostics":
        # Redacted support snapshot: names, labels, backend, masked last-4 — never a full secret
        # (credential_store.export_diagnostics is redacted by construction). Loopback-bound like the
        # rest of this API. Satisfies VOOL_MIGRATION.md §4 "export-diagnostics-with-secrets-redacted".
        from core import credential_store

        diag: dict[str, Any] = {"backend": "vault", "count": 0, "credentials": []}
        with contextlib.suppress(Exception):
            diag = credential_store.export_diagnostics()
        return apply_runtime_headers(json_response(200, diag), runtime)

    if normalized_path == "/api/model-tool-certification":
        # Read-only history. It is still not merged into provider capability truth -- ranking is
        # unchanged -- but it is no longer without effect: `core.final_answer_authorship` reads a
        # verified run to decide whether a model may take the final-answer author role, and the
        # surface names that effect rather than reporting "none" over a row that now decides
        # something.
        from core.local_model_tool_certification import (
            CERTIFICATION_ROUTING_EFFECT,
            certification_history,
            certification_status,
        )
        from core.model_registry import ModelRegistry

        provider_name = str((query.get("provider_name") or [""])[0] or "").strip()
        requested_model = str((query.get("model_name") or [""])[0] or "").strip()
        if not provider_name and not requested_model:
            return apply_runtime_headers(
                json_response(
                    200,
                    {
                        "runs": certification_history(limit=100),
                        "routing_effect": CERTIFICATION_ROUTING_EFFECT,
                    },
                ),
                runtime,
            )
        if not provider_name or not requested_model:
            return apply_runtime_headers(
                json_response(400, {"error": "provider_name and model_name are required together"}),
                runtime,
            )
        manifest = ModelRegistry().get_manifest(provider_name, requested_model)
        if manifest is None:
            return apply_runtime_headers(json_response(404, {"error": "model provider not found"}), runtime)
        return apply_runtime_headers(json_response(200, certification_status(manifest)), runtime)

    if normalized_path == "/api/models/local":
        # The read side of the local-model door: what Ollama actually has installed on this
        # machine, which of those lanes are registered here, and each lane's tool-certification
        # state. Registration is curated by default (boot registers the bundle's models, not
        # everything a user happens to have in Ollama), so an installed-but-unregistered model is
        # invisible to every other surface -- this listing is what makes it addressable at all.
        from core.local_model_policy import resolve_local_model_policy
        from core.local_model_tool_certification import certification_status
        from core.model_registry import ModelRegistry
        from core.web.api.runtime import installed_ollama_model_inventory, is_text_generation_ollama_model

        policy = resolve_local_model_policy()
        models: list[dict[str, Any]] = []
        if not policy.local_models_disabled:
            registry = ModelRegistry()
            try:
                inventory = installed_ollama_model_inventory(timeout_seconds=5.0)
            except Exception:
                inventory = []
            for item in inventory:
                if not is_text_generation_ollama_model(item.name):
                    continue
                manifest = registry.get_manifest("ollama-local", item.name)
                entry: dict[str, Any] = {
                    "model_name": item.name,
                    "size_gb": round(float(item.size_bytes or 0) / (1024.0 ** 3), 2),
                    "registered": manifest is not None,
                    "enabled": bool(getattr(manifest, "enabled", False)),
                }
                if manifest is not None:
                    with contextlib.suppress(Exception):
                        entry["certification_state"] = str(
                            certification_status(manifest).get("state") or "unknown"
                        )
                models.append(entry)
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "models": models,
                    "local_models_disabled": bool(policy.local_models_disabled),
                },
            ),
            runtime,
        )

    if normalized_path == "/api/oauth/status":
        # Redacted view of a pending vool:// OAuth callback (provider, state, has_code, age) — never
        # the authorization code. Used to verify the scheme delivery end-to-end.
        from core import oauth_callback

        status: dict[str, Any] = {"pending": False}
        with contextlib.suppress(Exception):
            status = oauth_callback.callback_status()
        return apply_runtime_headers(json_response(200, status), runtime)

    if normalized_path == "/api/connections":
        # Every REAL external connection for the header dots: the active cloud provider (if a key is
        # stored) and fal.ai (if an image key is stored). No key -> empty list -> "Local only".
        from core import cloud_connection_state, media_tools

        probe = str((query.get("probe") or [""])[0] or "") in ("1", "true", "yes")
        conns: list[dict[str, Any]] = []
        try:
            provider_q = str((query.get("provider") or [""])[0] or "").strip().lower() or None
            cloud = cloud_connection_state.connection_status(provider=provider_q, probe=probe)
            if str(cloud.get("state") or "") != "no_key":
                conns.append({"id": "cloud", "label": str(cloud.get("label") or "Cloud"),
                              "provider": cloud.get("provider"), "state": str(cloud.get("state") or "untested")})
            else:
                # No key anywhere, but a lane that needs none may still be usable now: UsePod paid per call from the
                # wallet (accountless x402) once the wallet authority verifies a network.
                from core.usepod.discovery import accountless_x402_ready

                ready = accountless_x402_ready()
                if ready:
                    conns.append({"id": "cloud", "label": ready["label"], "provider": "usepod", "state": "ok", "mode": "accountless_x402"})
        except Exception:
            pass
        try:
            if media_tools.has_image_service():
                conns.append({"id": "fal", "label": "fal.ai", "state": "ok"})
        except Exception:
            pass
        return apply_runtime_headers(json_response(200, {"connections": conns}), runtime)

    if normalized_path == "/api/files/raw":
        # Serve a generated file's bytes so it renders inline in chat (a browser can't load file:// from
        # an http page). Read-only + hard-confined to the known Files roots via path_is_allowed.
        from core import generated_files

        raw_path = str((query.get("path") or [""])[0] or "")
        if not raw_path or not generated_files.path_is_allowed(raw_path):
            return apply_runtime_headers(json_response(403, {"error": "not a known file"}), runtime)
        data = generated_files.read_file_bytes(raw_path)
        if data is None:
            return apply_runtime_headers(json_response(404, {"error": "file not found"}), runtime)
        return apply_runtime_headers(
            ApiResponse(status=200, content_type=generated_files.content_type_for(raw_path), body=data), runtime
        )

    if normalized_path == "/api/cloud/status":
        # Connection indicator: the cached, evidence-based state (no_key/untested/ok/failed) for the
        # active provider (or ?provider=<id>) plus the active cloud model+mode for the pill tooltip.
        # Cache-only so the UI can poll freely; ?probe=1 re-probes only when stale. Never returns
        # any key material. Loopback-only (Host-header guard upstream).
        from core import cloud_escalation_policy as _cep
        from core.cloud_connection_state import connection_status

        probe_flag = str((query.get("probe") or [""])[0] or "").strip() in {"1", "true", "yes"}
        provider_q = str((query.get("provider") or [""])[0] or "").strip().lower() or None
        payload = connection_status(provider=provider_q, probe=probe_flag)
        try:
            policy = _cep.load_policy()
            payload["model"] = policy.model or ""
            payload["mode"] = policy.mode
        except Exception:
            payload["model"] = ""
            payload["mode"] = ""
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/update/status":
        # The signed-updater status surface the chat chip renders (2026-09-01 amendment).
        # Owner-local: it names versions, channels and configuration posture.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _us_status, payload = _cr_forward("update.status", {})
        return apply_runtime_headers(json_response(_us_status, payload), runtime)

    if normalized_path == "/api/memory/entries":
        # What the runtime remembers, as data the operator can inspect and prune. Owner-local:
        # memory is the operator's accumulated context. Values only — never secrets (the store's
        # own write path refuses secret-bearing facts; this read adds a redaction-free listing of
        # rows that were already admitted under that law).
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.context_namespace import list_chat_namespaces
        from core.memory.entries import list_memory_entries

        try:
            limit = int(str((query.get("limit") or ["50"])[0] or "50"))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        # Memory reads are governed per chat namespace: a listing is read under the policy of the
        # chat it belongs to, or it is refused (core/memory/entries.py resolve_memory_access_policy).
        # `?session=` names one chat, canonicalised the way the chat door canonicalises it. Without
        # it -- the Settings surface has no chat -- every ACTIVE namespace is read under its own
        # policy and the rows merged, de-duplicated by record id. Before this, a bare read raised
        # inside the except below and the route answered an empty list to every caller.
        mem_session = str((query.get("session") or [""])[0] or "").strip()
        if mem_session:
            chat_ids = [stable_openclaw_session_id(body={"session_id": mem_session}, history=[], headers={})]
        else:
            try:
                chat_ids = [ns.chat_id for ns in list_chat_namespaces(limit=None) if ns.lifecycle_state == "active"]
            except Exception:
                chat_ids = []
        merged: dict[str, dict[str, Any]] = {}
        for chat_id in chat_ids:
            try:
                chat_rows = list_memory_entries(chat_id=chat_id, limit=limit)
            except Exception:
                continue
            for row in chat_rows:
                record_id = str(row.get("record_id") or "")
                if record_id and record_id not in merged:
                    merged[record_id] = row
        # One listing keeps the store's own order; several are merged by creation time.
        rows = list(merged.values())
        if len(chat_ids) > 1:
            rows = sorted(rows, key=lambda r: (str(r.get("created_at") or ""), str(r.get("record_id") or "")))
        rows = rows[-limit:]
        entries = [
            {
                "record_id": str(row.get("record_id") or ""),
                "fact": str(row.get("fact") or row.get("text") or ""),
                "category": str(row.get("category") or ""),
                "scope": str(row.get("scope") or ""),
                "source": str(row.get("source") or ""),
                "authority": str(row.get("authority") or ""),
                "created_at": str(row.get("created_at") or ""),
                "project_id": str(row.get("project_id") or ""),
                "session_id": str(row.get("session_id") or ""),
            }
            for row in rows
        ]
        return apply_runtime_headers(json_response(200, {"ok": True, "entries": entries}), runtime)

    if normalized_path == "/api/cloud/market-events":
        # The model-market feed (typed catalog-diff events). Owner-local like every other
        # spend-posture read; `?after=<seq>` continues from the client's cursor.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.model_market_feed import read_events

        try:
            after = int(str((query.get("after") or ["0"])[0] or "0"))
        except (TypeError, ValueError):
            after = 0
        try:
            events = read_events(after=max(0, after))
        except Exception:
            events = []
        return apply_runtime_headers(json_response(200, {"ok": True, "events": events}), runtime)

    if normalized_path == "/api/cloud/price-history":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.model_market_feed import price_history

        model_q = str((query.get("model") or [""])[0] or "").strip()
        try:
            rows = price_history(model_q) if model_q else []
        except Exception:
            rows = []
        return apply_runtime_headers(
            json_response(200, {"ok": True, "model": model_q, "history": rows}), runtime
        )

    if normalized_path == "/api/model-radar/feed":
        # The Model Radar read surface: qualified free/price-drop findings with their
        # evidence, plus the operator's notification preferences. Owner-local like the
        # other spend-posture reads (the feed names what the operator's credits buy).
        # Fail-soft to an EMPTY feed on storage trouble -- never invented findings.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import model_radar_service

        # The first owner-local poll is also the radar's cue to start listening
        # hourly on its own (idempotent; VOOL_MODEL_RADAR_POLLER=0 disables).
        with contextlib.suppress(Exception):
            model_radar_service.ensure_background_observer()
        try:
            # The popover lists RECENT findings (read ones stay cards until dismissed);
            # ?include_read=0 hides them. The unread count drives the chip either way.
            include_read = str((query.get("include_read") or ["1"])[0] or "1").strip() not in {"0", "false", "no"}
            findings = model_radar_service.list_findings(include_read=include_read, include_dismissed=False)
            unread = model_radar_service.unread_findings()
            preferences = model_radar_service.get_preferences()
            conflicts = model_radar_service.list_conflicts(limit=5)
            payload = {
                "ok": True,
                "unread": len(unread),
                "findings": [finding.to_dict() for finding in findings],
                "preferences": preferences.to_dict(),
                "open_conflicts": len(conflicts),
            }
        except Exception:
            payload = {"ok": True, "unread": 0, "findings": [], "preferences": {}, "open_conflicts": 0, "degraded": True}
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/model-radar/preferences":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import model_radar_service

        try:
            preferences = model_radar_service.get_preferences().to_dict()
        except Exception:
            preferences = {}
        return apply_runtime_headers(json_response(200, {"ok": True, "preferences": preferences}), runtime)

    if normalized_path in {
        "/api/council/runs", "/api/council/status", "/api/council/events",
        "/api/council/lock", "/api/council/scorecard",
    }:
        # Council adjudication surfaces: run listing, the polled run state, and the
        # append-only evidence ledger. Owner-local — a council run names workspace paths,
        # seat models, and spend posture.
        #
        # `/api/council/events` is the ledger READER. Until it existed the store wrote
        # every retry, attempt outcome and round transition to a file with no consumer:
        # the work happened and no surface could show it. The snapshot stays the view;
        # this is the record, served in allocation order behind an `after` cursor.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)

        if normalized_path == "/api/council/scorecard":
            # Recomputed from the run's ledger on every read, never served from a stored
            # copy: the persisted `scorecard` row is durable evidence of what the run
            # concluded, and this is what guarantees it can never become the only copy of
            # that truth — or quietly drift from it.
            scorecard_run = str((query.get("run") or [""])[0] or "").strip()
            if not scorecard_run:
                return apply_runtime_headers(
                    json_response(400, {"ok": False, "error": "missing run parameter"}), runtime)
            try:
                from core.command_registry.legacy import forward as _cr_forward

                council_status, council_payload = _cr_forward("council.scorecard", {"run_id": scorecard_run})
            except ValueError:
                council_status, council_payload = 400, {"ok": False, "error": "invalid run id"}
        elif normalized_path == "/api/council/lock":
            # The ONE authority for "may this machine run an ordinary turn right now".
            # Every composer reads it, so a second tab is fenced by the same fact the
            # server enforces rather than by its own DOM. It carries the run id, the
            # state and the reason to show — and never the dispatch capability.

            from core.command_registry.legacy import forward as _cr_forward

            council_status, council_payload = _cr_forward("council.lock", {})
        elif normalized_path == "/api/council/runs":
            from core.command_registry.legacy import forward as _cr_forward

            council_status, council_payload = _cr_forward("council.runs", {"limit": 20})
        elif normalized_path == "/api/council/events":
            # `seq` resolves ONE event — what a snapshot's `text_ref` points at, so the
            # full report text is fetched from the record on demand instead of being
            # re-shipped inside every poll of the view.
            from core.command_registry.legacy import forward as _cr_forward

            _cr_seq = (query.get("seq") or [None])[0]
            council_status, council_payload = _cr_forward(
                "council.events",
                {"run_id": str((query.get("run") or [""])[0] or ""), "after": (query.get("after") or ["0"])[0], "seq": _cr_seq if _cr_seq is not None else ""},
            )
        else:
            run_param = str((query.get("run") or [""])[0] or "").strip()
            if not run_param:
                return apply_runtime_headers(json_response(400, {"ok": False, "error": "missing run parameter"}), runtime)
            from core.command_registry.legacy import forward as _cr_forward

            council_status, council_payload = _cr_forward("council.status", {"run_id": run_param})
        return apply_runtime_headers(json_response(council_status, council_payload), runtime)

    if normalized_path == "/media-editor" or normalized_path.startswith("/media-editor/"):
        # The Media Studio child editor. Recovered from feature/media-studio-m1 (2026-08-25),
        # whose worker docstring names VOOL as the host process. Additive: a new route only.
        from core.web.api.media_editor_api import handle_media_editor_get

        return handle_media_editor_get(normalized_path, query)

    if normalized_path == "/api/cloud/acceptances":
        # The recorded spend ceilings (accepted prices), owner-local for the same reason the
        # selection read is: what the owner authorized paying names their spending posture.
        # Read-only; the only writer is the confirmed-pin path in POST /api/cloud/model.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.model_price_acceptance import list_acceptances

        try:
            rows = list_acceptances()
        except Exception:
            rows = []
        return apply_runtime_headers(json_response(200, {"ok": True, "acceptances": rows}), runtime)

    if normalized_path == "/api/cloud/model":
        # A11 server-authority read: the ONE authoritative cloud-model selection state.
        # The page hydrates its composer FROM this on boot instead of trusting its own
        # localStorage, so two machines can never disagree about what is pinned.
        #
        # A11 owner-local symmetry: the state-changing POST /api/cloud/model is owner-local, so
        # this read is too (same TCP-peer-derived gate, same `owner_local_required` shape). The
        # persisted selection names what the owner's credits will be spent on — a remote GET on
        # an exposed API must not enumerate it. The legitimate consumer (the local browser page
        # hydrating its composer) always reaches this endpoint loopback, so symmetry costs
        # nothing there.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        try:
            _cr_status, payload = _cr_forward("models.current", {})
            if not isinstance(payload, dict) or _cr_status != 200:
                raise RuntimeError("unavailable")
            if "session_id" in query:
                from core.cloud_escalation_policy import chat_model_selection

                session_id = str((query.get("session_id") or [""])[0])
                if not re.fullmatch(r"openclaw:[0-9a-f]{20}", session_id):
                    return apply_runtime_headers(json_response(400, {"ok": False, "error": "invalid session_id"}), runtime)
                payload = dict(payload, **chat_model_selection(session_id), session_id=session_id)
            # Server-derived cost classification for the CURRENT pin, from the same classifier
            # that gates every switch. The page's send-time paid acknowledgment consumes THIS
            # verdict instead of trusting a client-side guess or a possibly-unhydrated catalog
            # map, so the guard can no longer fail open when the catalog never loaded.
            _pinned = str(payload["model"] or "").strip()
            if _pinned:
                try:
                    from core.cloud_model_control import classify_cloud_model_cost

                    payload["cost_state"] = classify_cloud_model_cost(
                        provider_id=str(payload["provider"] or "") or "openrouter",
                        model_id=_pinned,
                    )["cost_state"]
                except Exception:
                    payload["cost_state"] = "unknown"
        except Exception:
            # Fail closed to "unknown", never to an invented selection.
            payload = {"ok": False, "model": "", "provider": "", "source": "server", "error": "unavailable"}
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/cloud/spend-limits":
        # D4 (read-only): the four VOOL-wide paid-call ceilings the reservation enforces for
        # non-UsePod paid picks (per call/task/day/month USD). Distinct from provider credits and
        # from any UsePod grant; surfaced so the Models & Providers overview can show the limits
        # that actually bind, instead of them existing only inside the ledger.
        from core.paid_call_reservation import spend_limits

        limits = spend_limits()
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "limits": {
                        "per_call_usd": float(getattr(limits, "per_call_usd", 0.0) or 0.0),
                        "per_task_usd": float(getattr(limits, "per_task_usd", 0.0) or 0.0),
                        "daily_usd": float(getattr(limits, "daily_usd", 0.0) or 0.0),
                        "monthly_usd": float(getattr(limits, "monthly_usd", 0.0) or 0.0),
                    },
                    "scope": "VOOL-wide paid calls (legacy USD ledger; does not include UsePod grants)",
                    "enforced_by": "core.model_spend_ledger at reservation",
                },
            ),
            runtime,
        )

    if normalized_path == "/api/cloud/models":
        # Model list for the dropdown, per provider (?provider=<id>, default: the active provider).
        # OpenRouter uses its live public catalog (free models first, ?order=name|context|featured,
        # ?refresh=1 for a live fetch); direct providers use a curated static list. Prices are per
        # 1M tokens. Cache-only by default; fail-soft (no data -> empty list, never invented).
        from core.cloud_providers import active_provider, config_for

        provider_q = str((query.get("provider") or [""])[0] or "").strip().lower()
        prov = provider_q or active_provider()
        if not prov:
            # No provider holds a key: an accountless lane that is usable now is the one to list, else the legacy default.
            from core.usepod.discovery import accountless_x402_ready

            prov = "usepod" if accountless_x402_ready() else "openrouter"
        order = str((query.get("order") or ["featured"])[0] or "featured").strip().lower()
        if order not in {"name", "context", "featured"}:
            order = "featured"

        if prov == "openrouter":
            from core.cloud_model_control import force_catalog_refresh
            from core.openrouter_catalog import (
                _KNOWN_FAMILY_RE,
                model_is_coding,
                model_is_free,
                safe_all_models,
                safe_free_models,
            )

            refresh_flag = str((query.get("refresh") or [""])[0] or "").strip() in {"1", "true", "yes"}
            refresh_result: dict[str, Any] | None = force_catalog_refresh() if refresh_flag else None
            free_models, age = safe_free_models(allow_network=False)
            all_models, _age2 = safe_all_models(allow_network=False)
            free_ids = {m.model_id for m in free_models}
            paid = [m for m in all_models if m.model_id not in free_ids]
            if order == "name":
                paid.sort(key=lambda m: m.model_id.lower())
            elif order == "context":
                paid.sort(key=lambda m: m.context_length, reverse=True)
            else:
                paid.sort(key=lambda m: (1 if _KNOWN_FAMILY_RE.search(m.model_id) else 0, m.context_length), reverse=True)

            def _row(m) -> dict[str, Any]:
                # Pricing can be None/partial (a model with an unpublished per-token fee still lists);
                # coerce to 0.0 so the row renders instead of crashing the whole catalog.
                return {
                    "id": m.model_id,
                    "name": m.name,
                    "context_length": m.context_length,
                    "free": model_is_free(m),
                    "coding": model_is_coding(m),
                    "prompt_usd_per_m": round((m.prompt_usd_per_token or 0.0) * 1_000_000, 4),
                    "completion_usd_per_m": round((m.completion_usd_per_token or 0.0) * 1_000_000, 4),
                    "provider": "openrouter",
                    # The catalog's own word on image input, so the composer can say up front
                    # whether a photo will be read by this model or withheld from it.
                    "supports_images": "image" in {str(x).lower() for x in (m.input_modalities or ())},
                }

            payload = {
                "provider": "openrouter",
                "models": [_row(m) for m in free_models] + [_row(m) for m in paid],
                "free_count": len(free_models),
                "age_seconds": age,
                "order": order,
            }
            try:
                from core import cloud_escalation_policy as _auto_policy

                saved = _auto_policy.load_policy()
                payload["auto_free_model"] = saved.auto_free_model
                payload["free_cloud_enabled"] = bool(saved.free_cloud_enabled)
            except Exception:
                payload["auto_free_model"] = "auto"
                payload["free_cloud_enabled"] = False
            if refresh_result is not None:
                payload["refresh"] = refresh_result
            return apply_runtime_headers(json_response(200, payload), runtime)

        if prov == "usepod":
            # A dynamic marketplace: rows come from the cached live feed (?refresh=1 fetches it),
            # carrying exact integer prices per route and the snapshot's provenance and staleness.
            # Display floats are for the picker only; nothing here authorizes spend.
            from core.usepod import pricing as _usepod_pricing
            from core.usepod.discovery import configured_origin as _usepod_origin
            from core.usepod.discovery import resolve_credential as _usepod_credential

            _up_credential = _usepod_credential()
            _up_origin = _up_credential.origin if _up_credential else _usepod_origin()
            _up_refresh: dict[str, Any] | None = None
            if str((query.get("refresh") or [""])[0] or "").strip() in {"1", "true", "yes"}:
                try:
                    _usepod_pricing.fetch_marketplace_snapshot(origin=_up_origin)
                    _up_refresh = {"state": "fetched"}
                except _usepod_pricing.MarketplaceFeedError as exc:
                    _up_refresh = {"state": "unavailable", "error_code": exc.code}
            _up_snapshot = _usepod_pricing.load_cached_snapshot(origin=_up_origin)

            def _up_per_m(value: int | None) -> float | None:
                return None if value is None else value / _usepod_pricing.MICROUNITS_PER_USDC

            _up_rows: list[dict[str, Any]] = []
            for _up_id in sorted(_up_snapshot.models) if _up_snapshot else []:
                _up_model = _up_snapshot.models[_up_id]
                _up_price = _up_model.marketplace or _up_model.best_available
                _up_rows.append(
                    {
                        "id": _up_id,
                        "name": _up_id,
                        "context_length": 0,
                        "free": False,
                        "coding": False,
                        "prompt_usd_per_m": _up_per_m(_up_price.input_microunits_per_million) if _up_price else None,
                        "completion_usd_per_m": _up_per_m(_up_price.output_microunits_per_million) if _up_price else None,
                        "provider": "usepod",
                        "supports_images": False,
                        "usable_for_spend": not _up_model.unusable_reason,
                        "unusable_reason": _up_model.unusable_reason,
                        "route_prices": _up_model.as_dict(),
                    }
                )
            _up_payload: dict[str, Any] = {
                "provider": "usepod",
                "label": config_for("usepod").label if config_for("usepod") else "UsePod",
                "models": _up_rows,
                "free_count": 0,
                "age_seconds": _up_snapshot.age_seconds() if _up_snapshot else None,
                "order": "name",
                "price_source": _up_snapshot.provenance() if _up_snapshot else {"state": "no_cached_snapshot"},
            }
            if _up_refresh is not None:
                _up_payload["refresh"] = _up_refresh
            return apply_runtime_headers(json_response(200, _up_payload), runtime)

        # Direct provider: curated static list (no live free catalog).
        from core.cloud_model_catalog import curated_models

        cfg = config_for(prov)
        rows = curated_models(prov)
        for r in rows:
            r["provider"] = prov
        if order == "name":
            rows.sort(key=lambda r: str(r["id"]).lower())
        elif order == "context":
            rows.sort(key=lambda r: int(r.get("context_length") or 0), reverse=True)
        payload = {
            "provider": prov,
            "label": cfg.label if cfg else prov,
            "models": rows,
            "free_count": 0,
            "age_seconds": None,
            "order": order,
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/api/runtime/usage", "/v1/runtime/usage"}:
        # Token usage meter: free-local vs paid-cloud token totals (+ a paid-side dollar
        # estimate). Windows (most specific wins): ?range=today|week|month (local calendar),
        # or ?since=&?until= (epoch or YYYY-MM-DD), or the legacy ?window=<seconds> trailing
        # window. ?per_model=1 adds a per-(provider,model) breakdown for the same window.
        from core import usage_meter

        range_raw = str((query.get("range") or [""])[0] or "").strip()
        since_raw = str((query.get("since") or [""])[0] or "").strip()
        until_raw = str((query.get("until") or [""])[0] or "").strip()
        window_raw = str((query.get("window") or [""])[0] or "").strip()
        per_model = str((query.get("per_model") or [""])[0] or "").strip() in {"1", "true", "yes"}

        window_seconds: float | None = None
        since_ts: float | None = None
        until_ts: float | None = None
        window_meta: dict[str, Any] | None = None
        if range_raw or since_raw or until_raw:
            resolved = usage_meter.resolve_window(range_=range_raw, since=since_raw, until=until_raw)
            since_ts = resolved["since_ts"]
            until_ts = resolved["until_ts"]
            window_meta = {"range": resolved["range"], "since": resolved["since"], "until": resolved["until"]}
        elif window_raw:
            try:
                window_seconds = float(window_raw)
            except ValueError:
                window_seconds = None

        usage_payload = usage_meter.usage_summary(
            window_seconds=window_seconds, since_ts=since_ts, until_ts=until_ts
        )
        usage_payload["notices"] = usage_meter.usage_notices(summary=usage_payload, window_seconds=window_seconds)
        if window_meta is not None:
            usage_payload["window"] = window_meta
        if per_model:
            usage_payload["by_model"] = usage_meter.usage_by_model(since_ts=since_ts, until_ts=until_ts)
        return apply_runtime_headers(json_response(200, usage_payload), runtime)

    if normalized_path == "/api/runtime/events":
        session_id = str((query.get("session") or [""])[0] or "").strip()
        after_seq = _qint(query, "after", 0)
        limit = _qint(query, "limit", 120)
        events = list_runtime_session_events(session_id, after_seq=after_seq, limit=limit)
        next_after = after_seq
        if events:
            next_after = max(int(item.get("seq") or 0) for item in events)
        # The authoritative answer to "did this turn execute anything", served alongside the raw
        # events instead of left for the panel to infer. Activity used to decide by scanning the
        # ledger for `tool_executed`/`tool_selected`, which is a fifth independent reconstruction of
        # execution truth and got it wrong on 80 turns whose retrieval was recorded under a
        # different event type -- rendering "No tool ran -- answered directly" over turns that had
        # really gone out to the network. Derived from the same facts the signed receipts use.
        # Keyed by the `turn_key` the emit path already stamped -- deliberately NOT re-derived from
        # each row. A reader re-deriving has no source context, falls to different fallbacks than the
        # writer did, and files the same event under a different key; that produced twenty turn keys
        # (checkpoint ids, request ids) for a session with three real turns. A row with no stamped
        # key has no canonical identity and is skipped rather than given an invented one.
        execution_truth: dict[str, object] = {}
        try:
            from core.execution_truth import turn_execution_summary

            for item in events:
                turn_key = str(item.get("turn_key") or "").strip()
                if turn_key and turn_key not in execution_truth:
                    execution_truth[turn_key] = turn_execution_summary(turn_key, session_id=session_id)
        except Exception:
            execution_truth = {}
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "session_id": session_id,
                    "events": events,
                    "next_after": next_after,
                    "execution_truth": execution_truth,
                },
            ),
            runtime,
        )

    if normalized_path in {"/api/runtime/changes", "/v1/runtime/changes"}:
        from core.workspace_change_projection import (
            InvalidWorkspaceChangeSessionIdError,
            build_workspace_change_v1,
            invalid_workspace_change_v1,
            validate_workspace_change_session_id,
        )

        raw_session_id = (query.get("session") or [""])[0]
        try:
            session_id = validate_workspace_change_session_id(raw_session_id)
        except InvalidWorkspaceChangeSessionIdError as exc:
            return apply_runtime_headers(
                json_response(400, invalid_workspace_change_v1(str(exc))),
                runtime,
            )
        limit = _qint(query, "limit", 120)
        # Activity contributes optional correlation metadata only.  Read the newest bounded
        # window because the durable mutation ledger remains the source of which changes exist.
        events = list_recent_runtime_session_events(session_id, limit=200)
        payload = build_workspace_change_v1(session_id=session_id, events=events, limit=limit)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/chat/queue":
        # Pending/in-flight queued messages for a session -- restores the queue after a
        # UI refresh, app restart, or daemon reconnect (persisted server-side).
        # Queued user messages are the operator's own content; the read is
        # owner-local like the queue's write path.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.runtime_continuity import list_queued_messages

        session_id = str((query.get("session") or [""])[0] or "").strip()
        items = list_queued_messages(session_id) if session_id else []
        return apply_runtime_headers(json_response(200, {"session_id": session_id, "queue": items}), runtime)

    if normalized_path == "/api/bug-report/status":
        # Bug-report draft status (owner-local read of the local store).
        from core.bug_report import get_service as _br_service
        from core.bug_report.store import is_valid_report_id as _br_valid_id

        report_id = str((query.get("report_id") or [""])[0] or "").strip()
        if not _br_valid_id(report_id):
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid report_id"}), runtime)
        try:
            status = _br_service().status(report_id)
        except Exception:
            return apply_runtime_headers(json_response(404, {"error": f"no local draft for {report_id}"}), runtime)
        return apply_runtime_headers(json_response(200, status), runtime)

    if normalized_path == "/api/bug-report/candidates":
        # Server-side turn candidates for the chat UI's Report-a-problem flow. The
        # browser only ever receives SANITIZED material derived from the runtime's own
        # event records; conversation content is never consulted.
        from core.bug_report.candidates import session_candidates as _br_session_candidates

        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        session_id = str((query.get("session") or [""])[0] or "").strip()
        if not session_id or len(session_id) > 80 or not re.fullmatch(r"[A-Za-z0-9:_-]+", session_id):
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid session"}), runtime)
        try:
            found = _br_session_candidates(session_id)
        except Exception:
            found = []
        return apply_runtime_headers(
            json_response(200, {"ok": True, "session_id": session_id, "candidates": found}), runtime
        )

    if normalized_path == "/api/bug-report/destination":
        # The configured default destination (owner-local read), so the report form can
        # prefill it and the user always sees WHERE a report would go before approving.
        from core.bug_report.destination import BUILTIN_DESTINATION, default_destination

        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        try:
            configured = default_destination()
            configured_error = ""
        except ValueError as _dest_exc:
            configured = BUILTIN_DESTINATION
            configured_error = str(_dest_exc)
        return apply_runtime_headers(json_response(200, {
            "ok": True,
            "destination": configured,
            "builtin": BUILTIN_DESTINATION,
            "configuration_error": configured_error,
        }), runtime)

    if normalized_path == "/api/bug-report/receipts":
        # What left the machine, as durable metadata rows (never report content).
        from core.bug_report import get_service as _br_service

        rows = _br_service().receipts()
        return apply_runtime_headers(json_response(200, rows), runtime)

    if normalized_path == "/api/runtime/receipts":
        # Per-turn signed honesty receipts for the execution panel's Receipts tab.
        session_id = str((query.get("session") or [""])[0] or "").strip()
        limit = _qint(query, "limit", 20)
        receipts_out: list[dict] = []
        chain_verified: bool | None = None
        chain_detail = ""
        if session_id:
            try:
                from core.honesty_receipt import list_honesty_receipts, verify_honesty_chain

                raw_receipts = list_honesty_receipts(session_id)
                for item in raw_receipts[-max(1, limit):]:
                    # A8 free-text gate (adjudicated dispute 1): verdict_detail
                    # and claimed_actions are payload-derived free text — they
                    # are blanked when the underlying payload is WITHHELD or
                    # ERASED. Hash/signature fields remain (rebuildable
                    # evidence); the signed chain is never rewritten.
                    from core.finalization import (
                        AVAILABILITY_ERASED,
                        AVAILABILITY_WITHHELD,
                        availability_for_receipt_digest,
                    )

                    response_hash = str(item.get("response_hash") or "")
                    # PASS003 (CE08): at-rest receipt digests are KEYED; the
                    # gate reconciles against rows/erasure tombstones through
                    # one digest-aware helper (legacy unsalted values still
                    # resolve, keyed tombstoned values resolve to ERASED).
                    payload_state = (
                        availability_for_receipt_digest(response_hash)
                        if response_hash
                        else None
                    )
                    payload_unavailable = payload_state in (
                        AVAILABILITY_WITHHELD,
                        AVAILABILITY_ERASED,
                    )
                    receipts_out.append(
                        {
                            "receipt_id": item.get("receipt_id"),
                            "turn_index": item.get("turn_index"),
                            "issued_at": item.get("issued_at"),
                            "verdict": item.get("verdict"),
                            "verdict_detail": ""
                            if payload_unavailable
                            else item.get("verdict_detail"),
                            "content_hash": item.get("content_hash"),
                            "signed": bool(item.get("signature")),
                            "signer_peer_id": item.get("signer_peer_id"),
                            "executed_tools": item.get("executed_tools") or [],
                            "claimed_actions": []
                            if payload_unavailable
                            else (item.get("claimed_actions") or []),
                            "payload_availability": payload_state,
                        }
                    )
                if raw_receipts:
                    chain_verified, chain_detail = verify_honesty_chain(raw_receipts)
            except Exception as exc:  # fail-soft: the panel degrades, never 500s
                receipts_out = []
                chain_verified = None
                chain_detail = f"unavailable:{type(exc).__name__}"
        # Imported HERE, not inside the `if session_id:` branch above: the payload
        # always carries these, and binding them on one branch only made a
        # session-less request raise UnboundLocalError -- which the handler turned
        # into a dropped connection, not a 500, so it read as a served-surface
        # failure rather than a NameError.
        from core.honesty_receipt import CHAIN_PROVEN_CLAIM, CHAIN_UNPROVEN_CLAIM

        return apply_runtime_headers(
            json_response(
                200,
                {
                    "session_id": session_id,
                    "receipts": receipts_out,
                    "chain_verified": chain_verified,
                    # What a True here does and does not establish, carried with the
                    # value so no client has to infer it.
                    "chain_completeness_proven": False,
                    "chain_proven": CHAIN_PROVEN_CLAIM,
                    "chain_not_proven": CHAIN_UNPROVEN_CLAIM,
                    "chain_detail": chain_detail,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/runtime/control-plane/status":
        return apply_runtime_headers(json_response(200, collect_control_plane_status()), runtime)

    if normalized_path == "/api/runtime/operator-snapshot":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"error": "owner_local_required"}),
                runtime,
            )
        client_session_id = str((query.get("session") or [""])[0] or "").strip()
        if not client_session_id:
            return apply_runtime_headers(
                json_response(400, {"error": "session is required"}),
                runtime,
            )
        session_id = stable_openclaw_session_id(
            body={"session_id": client_session_id},
            history=[],
            headers={},
            allow_canonical_resume=True,
        )
        query_text = str((query.get("query") or [""])[0] or "").strip()
        topic_hints = [str(item).strip() for item in list(query.get("topic_hint") or []) if str(item).strip()]
        try:
            from core.memory.entries import resolve_memory_access_policy

            access_policy = resolve_memory_access_policy(
                chat_id=session_id,
            )
        except ValueError:
            return apply_runtime_headers(
                json_response(404, {"error": "chat namespace not found"}),
                runtime,
            )
        return apply_runtime_headers(
            json_response(
                200,
                redact_runtime_operator_snapshot(
                    build_runtime_operator_snapshot(
                        session_id=session_id,
                        query_text=query_text,
                        topic_hints=topic_hints,
                        access_policy=access_policy,
                    )
                ),
            ),
            runtime,
        )

    if normalized_path in {"/api/adaptation/status", "/api/adaptation/loop"}:
        return apply_runtime_headers(json_response(200, get_adaptation_autopilot_status()), runtime)

    if normalized_path == "/api/adaptation/jobs":
        limit = _qint(query, "limit", 24)
        return apply_runtime_headers(json_response(200, {"jobs": list_adaptation_jobs(limit=max(1, min(limit, 200)))}), runtime)

    if normalized_path == "/api/adaptation/job-events":
        job_id = str((query.get("job") or [""])[0] or "").strip()
        limit = _qint(query, "limit", 120)
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "job_id": job_id,
                    "events": list_adaptation_job_events(job_id, limit=max(1, min(limit, 500))),
                },
            ),
            runtime,
        )

    if normalized_path == "/api/adaptation/evals":
        job_id = str((query.get("job") or [""])[0] or "").strip()
        limit = _qint(query, "limit", 120)
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "job_id": job_id,
                    "evals": list_adaptation_eval_runs(job_id=job_id or None, limit=max(1, min(limit, 500))),
                },
            ),
            runtime,
        )

    if normalized_path in {"/v1/credits/balance", "/api/credits/balance"}:
        try:
            from core.credit_ledger import get_credit_balance, list_credit_ledger_entries
            from network.signer import get_local_peer_id
            raw_limit = str((query.get("limit") or ["20"])[0] or "20")
            limit = int(raw_limit) if raw_limit.isdigit() else 20
            peer_id = str((query.get("peer_id") or [""])[0] or get_local_peer_id())
            payload = {
                "peer_id": peer_id,
                "balance": get_credit_balance(peer_id),
                "entries": list_credit_ledger_entries(peer_id, limit=limit),
            }
        except Exception as exc:
            payload = {"error": str(exc)}
        return apply_runtime_headers(json_response(200, payload), runtime)

    return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)


def dispatch_post(
    *,
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    runtime: RuntimeServices,
    model_name: str,
    workspace_root_provider,
    normalize_chat_history_provider: Callable[[list[dict[str, Any]]], list[dict[str, str]]] = normalize_chat_history,
    extract_user_message_provider: Callable[[list[dict[str, Any]]], str] = extract_user_message,
    stable_openclaw_session_id_provider: Callable[..., str] = stable_openclaw_session_id,
    run_agent_provider: Callable[..., dict[str, Any]] = run_agent,
    stream_agent_with_events_provider: Callable[..., Iterable[bytes]] = stream_agent_with_events,
    resolve_null_domain_provider: Callable[[str], Any] | None = None,
    try_dial_provider: Callable[..., Any] | None = None,
    client_host: str = "",
    request_id: str = "",
) -> ApiResponse:
    normalized_path = path.rstrip("/") or "/"

    if not isinstance(body, dict):
        # Every POST handler below reads body.get(...); a non-object JSON body (e.g. [], "x", 5)
        # would otherwise raise AttributeError -> 500. Fail cleanly instead.
        return apply_runtime_headers(json_response(400, {"error": "body must be a JSON object"}), runtime)

    # ---- Command registry projection (owner command centre): the one dispatch ----
    if normalized_path == "/api/commands/dispatch":
        from core.command_registry.api import handle_commands_dispatch

        status, payload = handle_commands_dispatch(body)
        return apply_runtime_headers(json_response(status, payload), runtime)
    # ---- Signed-updater operations (2026-09-01 amendment): one authority, two presses.
    if normalized_path == "/api/update/check":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _uc_status, payload = _cr_forward(
            "update.check", {}, approval_context={"gesture": "web-ui-press", "origin": "web-ui"}
        )
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("ok", bool(payload.get("check_ok", _uc_status == 200)))
        return apply_runtime_headers(json_response(_uc_status, payload), runtime)

    # Calendar and notifications lane: owner-local actions on the notification centre, its preferences and the
    # calendar alert policy. Each changes this runtime's own schedule and history only; the one route that reaches
    # a provider is the explicit sync, which runs the same bounded account sync the background worker runs.
    if normalized_path in {
        "/api/notifications/action", "/api/notifications/preferences", "/api/calendar/alerts/policy", "/api/calendar/sync",
        "/api/notifications/native/outbox", "/api/notifications/native/report", "/api/notifications/native/test",
        "/api/notifications/native/authorize", "/api/calendar/accounts/add", "/api/calendar/accounts/discover",
        "/api/calendar/accounts/select", "/api/calendar/accounts/opt-in", "/api/calendar/accounts/disconnect",
        "/api/calendar/accounts/reconnect",
    }:
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.operator import calendar_accounts, calendar_alerts, native_notifications, notification_center

        # The macOS notification bridge (installer/bundle/native_notifications.py) and the Settings controls for it.
        if normalized_path == "/api/notifications/native/outbox":
            result = native_notifications.outbox(bridge_id=str(body.get("bridge_id") or ""), helper_version=str(body.get("helper_version") or ""))
            return apply_runtime_headers(json_response(200, result), runtime)
        if normalized_path == "/api/notifications/native/report":
            result = native_notifications.ingest_report(bridge_id=str(body.get("bridge_id") or ""), events=body.get("events"))
            return apply_runtime_headers(json_response(200 if result.get("ok") else 400, result), runtime)
        if normalized_path == "/api/notifications/native/test":
            result = native_notifications.send_test_notification()
            status_code = int(result.pop("status", 200 if result.get("ok") else 400))
            return apply_runtime_headers(json_response(status_code, result), runtime)
        if normalized_path == "/api/notifications/native/authorize":
            return apply_runtime_headers(json_response(200, native_notifications.request_authorization()), runtime)
        # Settings > Calendars: the account setup steps, each through the one account authority.
        account_id = str(body.get("account_id") or "")
        if normalized_path == "/api/calendar/accounts/add":
            result = calendar_accounts.add_account(provider=str(body.get("provider") or ""), base_url=str(body.get("base_url") or ""),
                                                   label=str(body.get("label") or ""), auth_binding=str(body.get("auth_binding") or ""))
            if not result.get("ok"):
                return apply_runtime_headers(json_response(400, result), runtime)
            view = {**calendar_accounts.presented(result), "ok": True, "created": bool(result.get("created"))}
            return apply_runtime_headers(json_response(200, view), runtime)
        if normalized_path == "/api/calendar/accounts/discover":
            result = calendar_accounts.discover_calendars(account_id)
            return apply_runtime_headers(json_response(404 if result.get("status") == "unknown_account" else 200, result), runtime)
        if normalized_path == "/api/calendar/accounts/select":
            default_write = body.get("default_write")
            result = calendar_accounts.select_calendar(account_id, str(body.get("calendar_id") or ""), selected=bool(body.get("selected")),
                                                       default_write=default_write if isinstance(default_write, bool) else None)
            status_code = 200 if result.get("ok") else 404 if result.get("reason") == "unknown_calendar" else 409
            return apply_runtime_headers(json_response(status_code, result), runtime)
        if normalized_path == "/api/calendar/accounts/opt-in":
            sync_enabled, alerts_enabled = body.get("sync_enabled"), body.get("alerts_enabled")
            result = calendar_accounts.set_opt_in(account_id, sync_enabled=sync_enabled if isinstance(sync_enabled, bool) else None,
                                                  alerts_enabled=alerts_enabled if isinstance(alerts_enabled, bool) else None)
            status_code = 200 if result.get("ok") else 404 if result.get("reason") == "unknown_account" else 409
            return apply_runtime_headers(json_response(status_code, result), runtime)
        if normalized_path == "/api/calendar/accounts/disconnect":
            result = calendar_accounts.disconnect_account(account_id)
            return apply_runtime_headers(json_response(200 if result.get("ok") else 404, result), runtime)
        if normalized_path == "/api/calendar/accounts/reconnect":
            result = calendar_accounts.reconnect_account(account_id)
            if not result.get("ok"):
                return apply_runtime_headers(json_response(409, result), runtime)
            view = {**calendar_accounts.presented(result), "ok": True}
            return apply_runtime_headers(json_response(200, view), runtime)
        if normalized_path == "/api/notifications/action":
            result = notification_center.apply_action(
                str(body.get("notification_id") or ""), action=str(body.get("action") or ""), minutes=body.get("minutes"),
            )
            status_code = int(result.pop("status", 200 if result.get("ok") else 400))
            return apply_runtime_headers(json_response(status_code, result), runtime)
        if normalized_path == "/api/notifications/preferences":
            patch = body.get("preferences")
            result = notification_center.save_preferences(patch if isinstance(patch, dict) else {})
            return apply_runtime_headers(json_response(200 if result.get("ok") else 400, result), runtime)
        if normalized_path == "/api/calendar/alerts/policy":
            result = calendar_alerts.set_lead_minutes(
                account_id=str(body.get("account_id") or ""), calendar_id=str(body.get("calendar_id") or ""),
                event_key=str(body.get("event_key") or ""), minutes=body.get("lead_minutes"), apply_now=bool(body.get("apply_now")),
            )
            status_code = 200 if result.get("ok") else 404 if result.get("reason") == "unknown_target" else 400
            return apply_runtime_headers(json_response(status_code, result), runtime)
        result = calendar_alerts.sync_now(str(body.get("account_id") or ""))
        return apply_runtime_headers(json_response(200, result), runtime)

    if normalized_path in {"/api/update/install", "/api/update/restart"}:
        # The REAL UI click arrives here: owner-local (the app's own webview) + the
        # transport CSRF guard already ran. The typed UserGesture is created on the
        # server from THIS request — a truthy body flag is never a press.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _ur_command = "update.apply" if normalized_path == "/api/update/install" else "update.restart"
        status_code, payload = _cr_forward(
            _ur_command,
            {"resolve_destructive": str(body.get("resolve_destructive") or "").strip()},
            approval_context={"gesture": "web-ui-press", "origin": "web-ui"},
        )
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("ok", status_code == 200)
        return apply_runtime_headers(json_response(status_code, payload), runtime)


    # R-2 (H-3) A0 ACCEPT-ONCE DOOR — the ONE ingress for BOTH HTTP shells
    # (starlette app._dispatch and the legacy BaseHTTPRequestHandler converge
    # here). Accept-once on the transport-proved request id; principal from
    # loopback fact only; raw body digest frozen at accept; A0 context bound
    # for the whole dispatch and ALWAYS reset in finally.
    # A2/R-6 hygiene at the ONE ingress: the active obligation binding is a
    # per-turn ContextVar. A binding left by an EARLIER turn on this serving
    # context must never shadow the turn's own seal-time verdict when the
    # post-turn commit frame finalizes under the ingress context (F-01) — it
    # made every such commit re-derive against a foreign set and refuse.
    from core.conductor.obligation_ledger import clear_active_set
    from core.invocation.ledger import accept_invocation
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID
    from core.semantic.semantic_admissions import set_request_context as _bind_req

    clear_active_set()

    # C3b COUNCIL MODEL-PIN FENCE — the earliest point in the ONE chat ingress.
    # A council owns the machine's global composer pin for the length of a run, so an
    # ordinary turn resolving its model in that window is answered by whichever seat is
    # pinned at that instant. Refused HERE, before accept-once mints an invocation
    # identity (so the caller can simply retry once the run ends) and long before any
    # routing runs. The council's own seat dispatch presents a server-minted capability
    # on a header and passes; nothing derivable from public material does.
    _council_admission = None
    if normalized_path in {"/api/chat", "/v1/chat/completions"}:
        from core.council import pin_lock

        _council_admission = pin_lock.admit_chat_turn(
            capability=_header_value(headers, "x-vool-council-dispatch"),
            owner_local=is_loopback_host(client_host),
        )
        if _council_admission.refusal is not None:
            return apply_runtime_headers(
                json_response(409, _council_admission.refusal), runtime
            )

    _req_token = None
    _transcript_request_id = ""
    _transcript_flush_deferred = False
    try:
        import hashlib as _hashlib

        _r2_request_id = str(request_id or "").strip() or (
            # No client id: mint a UNIQUE one per request — a path-derived id
            # would falsely CONFLICT distinct turns against each other.
            f"auto-{uuid.uuid4().hex}"
        )
        _principal = (
            "owner_local"
            if is_loopback_host(client_host)
            else f"channel:http:{client_host or 'unknown'}"
        )
        _raw_digest = "sha256:" + _hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
        ).hexdigest()
        _accepted = accept_invocation(
            external_kind="http",
            external_value=_r2_request_id,
            principal=_principal,
            session_binding="",
            privacy_local_only=True,
            raw_digest=_raw_digest,
        )
        # REPLAY-MINTS-NOTHING: an identical redelivery of an already-finalized
        # request is a pure read of committed truth — no new attempt, no new
        # admission, no model/tool work.
        if _accepted["outcome"] == "ACCEPTED_IDENTICAL":
            from core.finalization import get_finalization_by_request_id

            prior = get_finalization_by_request_id(
                _accepted["request_id"], principal=_principal
            )
            if prior is None:
                # F-02 (independent-proof repair): accept-once already decided
                # the ONE canonical execution owner via the SQL UNIQUE accept
                # (this copy is ACCEPTED_IDENTICAL). The old code fell through
                # into dispatch when the owner had not finalized YET -- a
                # check-then-act window that let a raced duplicate execute the
                # turn a second time. The loser of the durable accept never
                # executes: it waits a bounded time for the owner's committed
                # outcome and replays it byte-for-byte, or reports typed
                # in-progress truth.
                import os as _os
                import time as _time

                _wait_s = float(_os.environ.get("VOOL_ACCEPT_ONCE_REPLAY_WAIT") or 30.0)
                _deadline = _time.monotonic() + max(0.0, min(_wait_s, 300.0))
                while prior is None and _time.monotonic() < _deadline:
                    _time.sleep(min(0.05, max(0.001, _deadline - _time.monotonic())))
                    prior = get_finalization_by_request_id(
                        _accepted["request_id"], principal=_principal
                    )
            if prior is not None:
                # A8: the typed replay outcome reaches the wire — an
                # UNAVAILABLE_BY_POLICY replay is never misrepresented as a
                # successful empty answer.
                return apply_runtime_headers(
                    json_response(
                        200,
                        {
                            "message": {
                                "role": "assistant",
                                "content": str(prior.get("canonical_content") or ""),
                            },
                            "done": True,
                            "done_reason": "stop",
                            "replay_outcome": str(
                                prior.get("replay_outcome") or "AVAILABLE"
                            ),
                            "vool_response_commit": {
                                "type": "response.replay",
                                "finalization_id": prior.get("finalization_id"),
                                "content_hash": prior.get("content_hash"),
                                "request_id": _accepted["request_id"],
                            },
                        },
                    ),
                    runtime,
                )
            # Owner still executing (or died mid-turn): honest in-progress
            # state, never a second execution under the same request identity.
            return apply_runtime_headers(
                json_response(
                    200,
                    {
                        "message": {"role": "assistant", "content": ""},
                        "done": False,
                        "done_reason": "request_in_progress",
                        "vool_response_commit": {
                            "type": "response.in_progress",
                            "request_id": _accepted["request_id"],
                        },
                    },
                ),
                runtime,
            )
        _req_token = _bind_req(_accepted["request_id"])
        _transcript_request_id = str(_accepted["request_id"] or "")
        try:
            from core.persistent_memory import open_transcript_commit_boundary

            open_transcript_commit_boundary(_transcript_request_id)
        except Exception:
            _transcript_request_id = ""

        _response = _dispatch_post_inner(
            path=path,
            body=body,
            headers=headers,
            runtime=runtime,
            model_name=model_name,
            normalized_path=normalized_path,
            workspace_root_provider=workspace_root_provider,
            normalize_chat_history_provider=normalize_chat_history_provider,
            extract_user_message_provider=extract_user_message_provider,
            stable_openclaw_session_id_provider=stable_openclaw_session_id_provider,
            run_agent_provider=run_agent_provider,
            stream_agent_with_events_provider=stream_agent_with_events_provider,
            resolve_null_domain_provider=resolve_null_domain_provider,
            try_dial_provider=try_dial_provider,
            client_host=client_host,
            request_id=_r2_request_id,
        )
        # A streamed turn is not over when this function returns: the model is still
        # being read. The admission has to live as long as the turn actually does, or a
        # council could take the pin mid-stream — which is the same defect one layer
        # down. Ownership of the release passes to the generator.
        if _council_admission is not None and _response.stream is not None:
            _response.stream = _release_admission_after(
                _council_admission, _response.stream
            )
            _council_admission = None
        if _transcript_request_id and _response.stream is not None:
            # A streamed turn seals inside the generator (its response.commit frame), so its
            # staged transcript row is written there; the request-end flush below would run
            # before the turn even started. It rides the stream's own exit instead.
            _response.stream = _flush_staged_transcript_after(
                _transcript_request_id, _response.stream
            )
            _transcript_flush_deferred = True
        return _response
    finally:
        if _req_token is not None:
            if _transcript_request_id and not _transcript_flush_deferred:
                _flush_staged_transcript_rows(_transcript_request_id)
            _CURRENT_REQUEST_ID.reset(_req_token)
        if _council_admission is not None:
            _council_admission.release()


def _cloud_model_write(
    *,
    model: str,
    model_provider: str | None,
    confirm_paid: bool,
    cost: dict[str, Any],
    resolved_provider: str,
    probe_model: str,
    council_capability: str,
    runtime: RuntimeServices,
) -> ApiResponse:
    """The composer-pin mutation itself, lifted out so the endpoint can hold the council
    fence's admission across it in a `finally`. Behaviour is byte-for-byte what it was
    inline; the only new argument is the capability, which `set_cloud_model` needs to tell
    a council's own write apart from anyone else's."""
    from core.cloud_model_control import set_cloud_model

    ok, message, chosen = set_cloud_model(
        model, provider=model_provider, owner_local=True, confirm_paid=confirm_paid,
        council_capability=council_capability,
    )
    if (
        ok
        and confirm_paid
        and model.lower() not in {"auto", "default", "reset"}
        and cost["cost_state"] != "free"
    ):
        # The confirmation IS the authorization: record (or rewrite) the ceiling at the
        # rates the operator just accepted. Best-effort — a store failure must not undo a
        # pin the classifier already allowed; the safe direction is merely asking again.
        try:
            from core.model_price_acceptance import record_acceptance

            record_acceptance(resolved_provider, probe_model, cost_state=cost["cost_state"])
        except Exception:
            pass
    # The status is chosen from STRUCTURED facts, never by sniffing refusal prose:
    # `set_cloud_model` embeds a stable machine reason (PAID_MODEL_CONFIRM_REQUIRED,
    # MODEL_COST_UNKNOWN, PAID_STATUS_UNKNOWN) at the start of its paid-gate refusals,
    # and that documented prefix is the mapping key. The old "local session" substring
    # → 403 branch was unreachable from this seam (every caller passes owner_local=True
    # and the loopback guard already ran), so it is not replicated.
    pin_payload: dict[str, Any] = {"ok": ok, "message": message, "model": chosen, "selection_source": "server"}
    if ok:
        status_code = 200
    else:
        refusal = diagnostics.model_pin_refusal(message)
        if refusal is not None:
            status_code, condition_code = refusal
        else:
            status_code, condition_code = 400, f"{diagnostics.NAMESPACE}.request.invalid"
        pin_payload = diagnostics.with_diagnostic(
            pin_payload,
            diagnostics.diagnostic_envelope(
                condition_code,
                message=message,
                correlation_id=str(probe_model or "")[:64],
                http_status=status_code,
            ),
        )
    return apply_runtime_headers(json_response(status_code, pin_payload), runtime)


def _header_value(headers: dict[str, Any] | None, name: str) -> str:
    """One header, matched case-insensitively. Absent reads as empty, never as a match."""
    for key, value in (headers or {}).items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


# ---- Chat attachments: the ONE raw-body door, and the receipts every step leaves --------------
#
# Bytes never arrive as JSON. Both HTTP shells (starlette `_dispatch` and the legacy handler)
# recognise this path BEFORE json-decoding a body and hand the raw bytes here; everything else
# about an attachment -- validation, staging, binding, release -- is `core.chat_attachments`.

RAW_UPLOAD_PATH = "/api/chat/attachments/upload"
BUNDLE_UPLOAD_PATH = "/api/session/bundle/upload"
#: The bundle door may carry embedded attachment bytes; the pack bounds keep it sane.
BUNDLE_UPLOAD_CEILING = 256 * 1024 * 1024


def is_raw_upload_path(path: str) -> bool:
    return (str(path or "").rstrip("/") or "/") in (RAW_UPLOAD_PATH, BUNDLE_UPLOAD_PATH)


def raw_upload_body_ceiling(path: str = "") -> int:
    """The largest body the raw door will hold whole: the per-file limit plus header slack,
    or the bundle ceiling for the session-bundle door."""
    if (str(path or "").rstrip("/") or "/") == BUNDLE_UPLOAD_PATH:
        return BUNDLE_UPLOAD_CEILING
    from core.chat_attachments import MAX_BYTES_PER_FILE

    return MAX_BYTES_PER_FILE + 64 * 1024


def _human_size(size: int) -> str:
    value = int(size or 0)
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} B"


def _attachment_event(
    session_id: str,
    *,
    event_type: str,
    message: str,
    details: dict[str, Any],
    turn_id: str = "",
) -> None:
    """One Activity row about an attachment. Names, kinds, sizes, ids and outcomes -- never bytes,
    never a path. Recording must never be able to fail the door."""
    clean = str(session_id or "").strip()
    if not clean:
        return
    context: dict[str, Any] = {"session_id": clean, "runtime_session_id": clean}
    if turn_id:
        context["cancel_turn_id"] = str(turn_id)
    with contextlib.suppress(Exception):
        emit_runtime_event(context, event_type=event_type, message=message, details=dict(details))


def dispatch_upload(
    *,
    path: str,
    raw_body: bytes,
    headers: dict[str, Any],
    runtime: RuntimeServices | None,
    client_host: str = "",
) -> ApiResponse:
    """Stage one uploaded file for the chat named in the headers. Owner-local, same-origin only."""
    from urllib.parse import unquote

    from core import chat_attachments
    from core.web.api.runtime import host_header_allowed

    def _out(response: ApiResponse) -> ApiResponse:
        return apply_runtime_headers(response, runtime) if runtime is not None else response

    if path == "/api/session/bundle/upload":
        # A session-bundle file the operator picked for import: staged under the home's
        # session_bundles/incoming directory, returned by server path. Owner-local, same
        # origin, generous ceiling (a bundle may carry embedded attachment bytes). Nothing
        # is parsed or imported here — staging only; preview/import are separate doors.
        from core import runtime_paths as _sp_paths

        if not is_loopback_host(client_host):
            return _out(json_response(403, {"ok": False, "error": "owner_local_required"}))
        origin = _header_value(headers, "origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return _out(json_response(403, {"error": "cross-origin request not allowed"}))
        import uuid as _uuid

        data = bytes(raw_body or b"")
        if not data:
            return _out(json_response(400, {"ok": False, "error": "empty file"}))
        if len(data) > BUNDLE_UPLOAD_CEILING:
            return _out(json_response(413, {"ok": False, "error": "bundle over the 256 MB bound"}))
        incoming = _sp_paths.data_path("session_bundles") / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        declared = _header_value(headers, "x-vool-bundle-name").strip() or "bundle.voolsession"
        staged_path = incoming / f"import-{_uuid.uuid4().hex}.voolsession"
        staged_path.write_bytes(data)
        with contextlib.suppress(OSError):
            staged_path.chmod(0o600)
        return _out(
            json_response(
                201,
                {
                    "ok": True,
                    "path": str(staged_path),
                    "declared_name": declared[:128],
                    "size_bytes": len(data),
                },
            )
        )

    if not is_raw_upload_path(path):
        return _out(json_response(404, {"error": "not found"}))
    if not is_loopback_host(client_host):
        return _out(json_response(403, {"ok": False, "error": "owner_local_required"}))
    origin = _header_value(headers, "origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return _out(json_response(403, {"error": "cross-origin request not allowed"}))
    session_id = _header_value(headers, "x-vool-session-id").strip()
    declared_name = unquote(_header_value(headers, "x-vool-attachment-name"))
    declared_type = _header_value(headers, "x-vool-attachment-type").strip()[:128]
    # The same door, one header apart: `paste` means the bytes are composed text the operator
    # pasted, kept as a DOCUMENT of the chat (named by the authority, retained after the turn).
    source = _header_value(headers, "x-vool-attachment-source").strip().lower()[:32]
    # `paste` (composed text) and `drop` (a text file dropped on the composer) are the two
    # composer sources that make a DOCUMENT; both go through the same interceptor on the page and
    # the same `stage_document` here. A picker upload carries no source header.
    is_document = source in {"paste", "drop"}
    data = bytes(raw_body or b"")
    try:
        if is_document:
            record = chat_attachments.stage_document(
                session_id=session_id, data=data, declared_name=declared_name, source=f"composer_{source}"
            )
        else:
            record = chat_attachments.stage_attachment(
                session_id=session_id,
                declared_name=declared_name,
                declared_type=declared_type,
                data=data,
            )
    except chat_attachments.AttachmentRefused as exc:
        if exc.code != "missing_session":
            try:
                label = chat_attachments.sanitize_name(declared_name)
            except chat_attachments.AttachmentRefused:
                label = "pasted text" if is_document else "an attachment"
            _attachment_event(
                session_id,
                event_type="attachment_refused",
                message=f"{'Document' if is_document else 'Attachment'} refused: {label} — {exc.message}",
                details={"code": exc.code, "declared_type": declared_type, "size_bytes": len(data), "source": source},
            )
        return _out(json_response(exc.http_status, exc.to_dict()))
    _attachment_event(
        session_id,
        event_type="attachment_staged",
        message=(
            f"Kept pasted text as the document {record['name']} ({_human_size(record['size_bytes'])}, {record['lines']} lines)"
            if record.get("document")
            else f"Attached {record['name']} ({record['kind']}, {_human_size(record['size_bytes'])})"
        ),
        details={
            "attachment_id": record["id"],
            "kind": record["kind"],
            "media_type": record["media_type"],
            "size_bytes": record["size_bytes"],
            "sha256": record["sha256"],
            **({"document": True, "chars": record["chars"], "lines": record["lines"], "source": record["source"]} if record.get("document") else {}),
        },
    )
    return _out(json_response(201, {"ok": True, "attachment": record, "limits": chat_attachments.limits_payload()}))


# --- dictation: speech-to-text for the composer ----------------------------------------------------
#
# The one raw door for the microphone. A dictation recording arrives as BYTES (never JSON, the
# same law as an attachment), is transcribed ON DEVICE by the bounded recogniser behind
# `core.dictation`, and comes back as DRAFT TEXT the page puts in the composer's input — where
# the operator can edit or discard it before anything is sent. The endpoint never dispatches a
# turn, never touches an attachment slot, and on a machine without the speech dependency it
# answers with the typed reason instead of pretending to listen.

RAW_DICTATION_PATH = "/api/chat/dictation"


def _dictation_locale(value: str) -> str:
    """Normalise one caller-declared recognition locale, or return the recogniser default.

    Accepts 2-3 letters, optionally one dash/underscore subtag (en-US, lt-LT, en_US) and
    normalises to the dash form the recogniser names. Anything else is refused typed, never
    silently coerced — the recogniser's refusal for an unsupported language stays the
    recogniser's to give.
    """
    import re as _re

    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 16 or not _re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?", text):
        raise ValueError(text)
    return text.replace("_", "-")


def is_raw_dictation_path(path: str) -> bool:
    return (str(path or "").rstrip("/") or "/") == RAW_DICTATION_PATH


def dictation_body_ceiling() -> int:
    from core.artifact_readers._limits import MAX_DICTATION_BYTES

    return MAX_DICTATION_BYTES + 64 * 1024


def dispatch_dictation(
    *,
    method: str,
    raw_body: bytes,
    headers: dict[str, Any],
    runtime: RuntimeServices | None,
    client_host: str = "",
    query: str = "",
) -> ApiResponse:
    """POST transcribes one recording; GET reports whether this machine can."""
    from core import dictation as dictation_authority
    from core.web.api.runtime import host_header_allowed

    def _out(response: ApiResponse) -> ApiResponse:
        return apply_runtime_headers(response, runtime) if runtime is not None else response

    if not is_loopback_host(client_host):
        return _out(json_response(403, {"ok": False, "error": "owner_local_required"}))
    origin = _header_value(headers, "origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return _out(json_response(403, {"error": "cross-origin request not allowed"}))

    # Recognition language: explicit, bounded, and DISTINCT from the app UI locale and the
    # model answer language. An absent declaration keeps the recogniser's own default.
    from urllib.parse import parse_qs as _parse_qs

    query_locale = (_parse_qs(str(query or ""), keep_blank_values=True).get("locale") or [""])[0]
    try:
        locale = _dictation_locale(_header_value(headers, "x-vool-dictation-locale") or query_locale)
    except ValueError:
        return _out(json_response(400, {
            "ok": False,
            "error": "invalid_dictation_locale",
            "message": "The dictation language must be a locale tag like en-US or lt-LT.",
        }))
    recognised_locale = locale or dictation_authority.speech_tool.DEFAULT_LOCALE

    if str(method or "POST").upper() == "GET":
        return _out(json_response(200, dict(dictation_authority.availability(locale=recognised_locale))))

    from urllib.parse import unquote

    session_id = _header_value(headers, "x-vool-session-id").strip()
    if not session_id:
        return _out(json_response(400, {"ok": False, "error": "missing_session", "message": "Dictation must name the chat it is composing into."}))
    declared_type = _header_value(headers, "x-vool-audio-type").strip()[:128]
    data = bytes(raw_body or b"")
    try:
        result = dictation_authority.transcribe(
            data, media_type=unquote(declared_type), locale=recognised_locale
        )
    except dictation_authority.DictationUnavailable as exc:
        return _out(json_response(exc.http_status, exc.to_dict()))
    _attachment_event(
        session_id,
        event_type="dictation_transcribed",
        message=f"Dictated {len(result.get('text') or '')} characters locally, on device",
        details={"engine": result.get("engine"), "duration_s": result.get("duration_s"), "complete": result.get("complete")},
    )
    return _out(json_response(200, result))


def _release_turn_attachments(session_id: str, turn_id: str) -> None:
    """The turn is over, however it ended: bytes go, receipts stay, Activity says what became of each."""
    from core import chat_attachments

    try:
        released = chat_attachments.release_turn(session_id=session_id, turn_id=turn_id, outcomes=None)
        receipt = chat_attachments.receipt_for_turn(session_id=session_id, turn_id=turn_id) if released else []
    except Exception:
        return
    for entry in receipt:
        _attachment_event(
            session_id,
            event_type="attachment_released",
            message=f"Released {entry['name']}: {entry['outcome']}",
            details={
                "attachment_id": entry["id"],
                "kind": entry["kind"],
                "size_bytes": entry["size_bytes"],
                "outcome": entry["outcome"],
            },
            turn_id=turn_id,
        )


def _erase_session_attachments(session_id: str) -> int:
    """The chat is gone: so is everything it staged -- its retained documents included. The
    transcript that held their receipts is deleted in the same request, so no identity dangles."""
    from core import chat_attachments

    try:
        return chat_attachments.erase_session_documents(session_id)
    except Exception:
        return 0


def _release_attachments_after(stream: Iterable[bytes], *, session_id: str, turn_id: str) -> Iterator[bytes]:
    """Release the turn's attachments once the streamed answer has fully left -- or the client has."""
    try:
        yield from stream
    finally:
        _release_turn_attachments(session_id, turn_id)


def _flush_staged_transcript_rows(request_id: str) -> None:
    """Request-end fallback for the transcript's commit-boundary staging (core.persistent_memory).

    A turn that sealed has already written its row (with the committed bytes) and this finds
    nothing. A turn that never reached the seal -- an exception, an empty reply -- keeps the
    user's message and the lane's own text, marked ``unsealed``; nothing is lost and nothing is
    recorded as a committed answer that was not one.
    """
    try:
        from core.persistent_memory import flush_staged_conversation_events

        flush_staged_conversation_events(str(request_id or ""))
    except Exception:
        logging.getLogger(__name__).exception("staged transcript rows were not flushed at request end")


def _flush_staged_transcript_after(request_id: str, stream: Iterable[bytes]) -> Iterator[bytes]:
    """Yield ``stream`` through, then run the request-end transcript flush.

    Same shape as ``_release_admission_after``: the ``finally`` runs on exhaustion, on a client
    disconnect (GeneratorExit) and on collection of an unfinished generator.
    """
    try:
        yield from stream
    finally:
        _flush_staged_transcript_rows(request_id)


def _release_admission_after(admission: Any, stream: Iterable[bytes]) -> Iterator[bytes]:
    """Yield `stream` through, then hand the council-fence admission back.

    The `finally` runs on normal exhaustion, on a client disconnect (GeneratorExit) and
    on collection of a generator nobody finished, so a turn that dies mid-stream cannot
    hold the fence open. `Admission.release` is idempotent for exactly that reason.
    """
    try:
        yield from stream
    finally:
        admission.release()


def _iterate_under_ingress_context(context: Any, iterator: Iterable[bytes]) -> Iterator[bytes]:
    """F-01: pull `iterator` one item at a time under the captured ingress
    context so generator-frame finalization sees the A0/A2 binding."""
    nxt = iter(iterator)
    while True:
        try:
            yield context.run(next, nxt)
        except StopIteration:
            return


_FINAL_FRAME_KEYS = (b"vool_response_commit", b'"done":true')


def _inject_task_finalizing_event(stream_iter: Iterable[bytes], emit_task_events: bool) -> Iterator[bytes]:
    """Producer S1 (companion/voolling-status lane): the ONE ``task.finalizing`` typed event.

    Emitted at most once per streaming turn, immediately before the response-commit final NDJSON
    frame — the only window in which the server is genuinely handing the committed answer and its
    receipt to the transport. Downstream FINISH-style status copy is unreachable without this
    event, so "assembling the answer" can never be a timer guess. Guarded by the same
    ``emit_task_events`` flag as every other ``vool_event`` line — already off on the OpenAI
    /v1/ path, where this wrapper is a passthrough — so OpenAI-compat consumers never see it.
    Failure paths never yield a commit frame, so a failed turn never claims finalization.
    """
    if not emit_task_events:
        yield from stream_iter
        return
    from core.web.api.runtime import task_event_line

    pending: bytes | None = None
    try:
        for chunk in stream_iter:
            if pending is not None:
                yield pending
            pending = chunk
            if not any(needle in pending for needle in _FINAL_FRAME_KEYS):
                continue
            # The substring alone is not proof: an answer that quotes the key name must not fire
            # the event a frame early. Parse and require the real terminal-commit shape.
            try:
                frame = json.loads(pending.decode("utf-8"))
            except Exception:
                continue
            if (
                isinstance(frame, dict)
                and frame.get("done") is True
                and isinstance(frame.get("vool_response_commit"), dict)
            ):
                yield task_event_line(
                    {
                        "type": "task.finalizing",
                        "raw_type": "task_finalizing",
                        "stage": None,
                        "summary": "Assembling the answer",
                        "tool": None,
                        "status": "running",
                    }
                )
    finally:
        if pending is not None:
            yield pending


def _hashlast_serve_gate(commit: dict[str, Any]) -> str:
    """R-8/A-13.4: per-serve HASH-LAST gate for /api/generate (mirrors
    core.web.api.runtime._hashlast_gate)."""
    import hashlib as _hl

    if not isinstance(commit, dict):
        return "commit missing"
    body = str(commit.get("canonical_content") or "")
    expected = str(commit.get("content_hash") or "")
    if not expected or "sha256:" not in expected:
        return ""
    actual = "sha256:" + _hl.sha256(body.encode("utf-8")).hexdigest()
    if actual != expected:
        return f"canonical_content hashes {actual} != committed {expected}"
    return ""


def _usepod_balance_public(observation: dict[str, Any]) -> dict[str, Any]:
    """The secret-free balance observation for the page: integer microunits AND the exact decimal
    USDC string (3000000 -> "3.000000"), so no surface has to divide -- or mistake 3,000,000 µUSDC
    for three million dollars. Missing is named, never zero."""
    from core.usepod.discovery import BALANCE_REUSE_SECONDS

    amount = observation.get("usdc_balance_microunits")
    reported = str(observation.get("state") or "") == "reported" and isinstance(amount, int) and not isinstance(amount, bool)
    return {
        "state": str(observation.get("state") or "not_observed"),
        "usdc_balance_microunits": int(amount) if reported else None,
        "usdc_balance": f"{int(amount) // 1_000_000}.{int(amount) % 1_000_000:06d}" if reported else None,
        "unit": "usdc_microunit",
        "observed_at": observation.get("observed_at"),
        "age_seconds": observation.get("age_seconds"),
        "evidence": str(observation.get("evidence") or ""),
        "fingerprint": str(observation.get("fingerprint") or ""),
        "http_status": observation.get("http_status"),
        "error_code": observation.get("error_code"),
        "previous": observation.get("previous"),
        "reuse_seconds": float(BALANCE_REUSE_SECONDS),
    }


def _usepod_owner_action(normalized_path: str, body: dict[str, Any], headers: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    """One UsePod owner action, behind the same guard suite as the other state-changing POSTs."""
    import json as _json

    from core.web.api.runtime import host_header_allowed

    content_type = _header_value(headers, "content-type").lower()
    if content_type and "json" not in content_type:
        return 415, {"error": "content-type must be application/json"}
    origin = _header_value(headers, "origin").strip()
    if origin and not host_header_allowed(origin.split("://", 1)[-1]):
        return 403, {"error": "cross-origin request not allowed"}
    try:
        if len(_json.dumps(body)) > 65536:
            return 413, {"error": "request body too large"}
    except (TypeError, ValueError):
        return 400, {"error": "unserializable body"}

    from core.usepod import discovery, routing

    def _routes(state: Any) -> dict[str, Any]:
        return {
            "route_policy": state.policy.to_dict(),
            "approved_routes": {model_id: bound.to_dict() for model_id, bound in state.bounds.items()},
            "route_store_error": state.error,
        }

    if normalized_path.startswith("/api/cloud/usepod/price-wait/"):
        from core.usepod import price_wait
        try:
            if normalized_path.endswith("/save"):
                return 200, {"bound": price_wait.save_target(**body).to_dict()}
            if normalized_path.endswith("/check"):
                return 200, price_wait.check(str(body.get("model_id") or ""))
            if normalized_path.endswith("/pending"):
                from core.runtime_continuity import price_wait_sessions
                return 200, {"sessions": price_wait_sessions()}
        except (ValueError, TypeError, routing.RouteUnavailableError) as exc:
            return 400, {"error": str(exc), "code": "price_wait_refused"}
    if normalized_path == "/api/cloud/usepod/refresh":
        if body:
            return 400, {"error": f"unknown fields: {sorted(body)}", "code": "unknown_fields"}
        return 200, discovery.refresh_discovery()
    if normalized_path == "/api/cloud/usepod/balance":
        # ONE read-only balance observation for the stored credential, through the same door the
        # dispatch reservation reads (core.usepod.discovery.observe_balance): the record while it is
        # fresh, otherwise one GET of /proxy/<token>/balance. No inference, no spend, no authority --
        # the composer preflight and the Settings budget panel verify liquidity here BEFORE a draft is
        # consumed, so a send never fails on a balance nobody read. ``max_age_seconds: 0`` reads now.
        unknown = sorted(set(body) - {"max_age_seconds"})
        if unknown:
            return 400, {"error": f"unknown fields: {unknown}", "code": "unknown_fields"}
        raw_age = body.get("max_age_seconds")
        if raw_age is None:
            max_age: float | None = discovery.BALANCE_REUSE_SECONDS
        elif isinstance(raw_age, bool) or not isinstance(raw_age, (int, float)) or raw_age < 0 or raw_age > 900:
            return 400, {"error": "max_age_seconds must be a number of seconds from 0 to 900", "code": "max_age_invalid"}
        else:
            max_age = float(raw_age)
        try:
            observation = discovery.observe_balance(max_age_seconds=max_age, strict=True)
        except discovery.UsePodCredentialPairUnavailableError as exc:
            return 409, {"state": "credential_pair_unresolved", "code": exc.code, "error": str(exc)}
        return 200, _usepod_balance_public(observation)
    if normalized_path == "/api/cloud/usepod/route-policy":
        try:
            state = routing.save_route_policy(routing.RoutePolicy.from_dict(body))
        except ValueError as exc:
            return 400, {"error": "routing policy refused", "code": str(getattr(exc, "code", "") or "route_policy_invalid")}
        return 200, _routes(state)
    if normalized_path in {"/api/cloud/usepod/approve-route", "/api/cloud/usepod/forget-route"}:
        unknown = sorted(set(body) - {"model_id", "max_input_usdc_per_million", "max_output_usdc_per_million"})
        if unknown:
            return 400, {"error": f"unknown fields: {unknown}", "code": "unknown_fields"}
        model_id = body.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            return 400, {"error": "missing model_id", "code": "model_id_required"}
        if normalized_path.endswith("/forget-route"):
            return 200, _routes(routing.forget_approved_bound(model_id.strip()))
        # The two-axis reviewed maxima from the price review: exact decimal USDC-per-1M strings,
        # both axes together or neither. Conversion and validation happen in the owner
        # (core.usepod.discovery.approve_model_route) through the exact decimal utility; a bad
        # value is refused with its typed code and nothing is written.
        max_input = body.get("max_input_usdc_per_million")
        max_output = body.get("max_output_usdc_per_million")
        if (max_input is None) != (max_output is None):
            return 400, {"error": "the reviewed price review is two-axis: give both maximum input and output prices", "code": "owner_review_requires_both_axes"}
        try:
            bound = discovery.approve_model_route(
                model_id.strip(),
                max_input_usdc_per_million=max_input,
                max_output_usdc_per_million=max_output,
            )
        except routing.RouteUnavailableError as exc:
            return 409, {"error": "no route can be approved at current prices", "code": exc.code, "evidence": exc.evidence}
        except ValueError as exc:
            return 400, {"error": "routing policy refused", "code": str(getattr(exc, "code", "") or "route_policy_invalid")}
        return 200, {"approved_route": bound.to_dict()}
    if normalized_path in {"/api/cloud/usepod/spend-approval/propose", "/api/cloud/usepod/spend-approval/confirm", "/api/cloud/usepod/spend-approval/pending"}:
        # The trusted spend-consent bridge (core.usepod.spend_approval): propose registers a
        # PENDING approval in the SAME approval store the tool gate uses; the OPERATOR resolves
        # it through the existing approval door; confirm mints the grant from the recorded facts
        # through the real operator authority. /api/money widening stays refused. Route approval
        # and spend consent remain separate facts.
        from core.usepod import spend_approval

        if normalized_path.endswith("/pending"):
            if body:
                return 400, {"error": f"unknown fields: {sorted(body)}", "code": "unknown_fields"}
            return 200, {"pending": spend_approval.pending_spend_approval()}
        if normalized_path.endswith("/propose"):
            unknown = sorted(set(body) - {"per_call_atomic", "max_total_atomic", "expiry_epoch", "asset", "budget_mode"})
            if unknown:
                return 400, {"error": f"unknown fields: {unknown}", "code": "unknown_fields"}
            try:
                return 200, spend_approval.propose_spend_grant(
                    per_call_atomic=int(body.get("per_call_atomic") or 0),
                    max_total_atomic=int(body.get("max_total_atomic") or 0),
                    expiry_epoch=float(body.get("expiry_epoch") or 0.0),
                    asset=str(body.get("asset") or "USDC"),
                    budget_mode=str(body.get("budget_mode") or "once"),
                )
            except (ValueError, TypeError) as exc:
                return 400, {"error": str(exc), "code": "spend_proposal_refused"}
        unknown = sorted(set(body) - {"approval_id"})
        if unknown:
            return 400, {"error": f"unknown fields: {unknown}", "code": "unknown_fields"}
        if not isinstance(body.get("approval_id"), str) or not body["approval_id"].strip():
            return 400, {"error": "missing approval_id", "code": "approval_id_required"}
        try:
            return 200, spend_approval.confirm_spend_grant(body["approval_id"].strip())
        except PermissionError as exc:
            return 403, {"error": str(exc), "code": "spend_approval_not_granted"}
        except Exception as exc:
            return 400, {"error": f"grant mint refused: {exc}", "code": "spend_grant_refused"}
    if normalized_path == "/api/cloud/usepod/lane":
        from core.runtime_provider_defaults import refresh_path_token_lanes
        from core.usepod import discovery as usepod_discovery
        from core.usepod.lane import LanePreference, save_lane_preference

        fields = dict(body or {})
        try:
            requested = LanePreference.from_dict({key: value for key, value in fields.items() if key != "origin"})
        except ValueError:
            return 400, {"error": "lane refused", "code": "lane_invalid"}
        if "origin" in fields:
            # Accountless x402 only: with no UsePod token stored the owner may choose another origin explicitly (a
            # staging gateway, a local stand-in); an empty value returns to the documented one. A stored token's
            # origin belongs to its credential pair and is refused here.
            try:
                usepod_discovery.save_accountless_origin(str(fields.get("origin") or ""))
            except usepod_discovery.AccountlessOriginRefusedError as exc:
                return exc.http_status, {"error": str(exc), "code": exc.code}
        preference = save_lane_preference(requested)
        return 200, {"lane": preference.to_dict(), "origin": usepod_discovery.configured_origin(), "refreshed_lanes": refresh_path_token_lanes("usepod")}
    if normalized_path == "/api/cloud/usepod/x402/resume":
        # The owner finishes ONE paid accountless call whose answer never arrived: the recorded bytes and proof are resent
        # once, never a new quote or a second payment. The conversation that asked has ended, so the answer comes back here.
        unknown = sorted(set(body) - {"operation_id"})
        if unknown:
            return 400, {"error": f"unknown fields: {unknown}", "code": "unknown_fields"}
        operation_id = str(body.get("operation_id") or "").strip()
        if not operation_id:
            return 400, {"error": "missing operation_id", "code": "operation_id_required"}
        from adapters.usepod_adapter import (
            UsePodDispatchRefusedError,
            UsePodRouteNotCompliantError,
            resume_x402_operation,
        )
        from core.usepod.transport import UsePodTransportError, X402OperationStateError

        try:
            return 200, resume_x402_operation(operation_id)
        except X402OperationStateError as exc:
            return 409, {"error": "resume refused", "code": str(getattr(exc, "code", "") or "resume_refused")}
        except UsePodDispatchRefusedError as exc:
            return 409, {"error": "resume refused", "code": str(getattr(exc, "code", "") or "resume_refused")}
        except UsePodRouteNotCompliantError:
            return 409, {"error": "the answer came from a route nobody approved; its liability is retained", "code": "route_not_compliant"}
        except UsePodTransportError as exc:
            # The HTTP status comes from TYPED dispatch facts, not a blanket 502:
            # - a request that never left this machine (dispatch_state=not_sent) is a
            #   400 contract refusal -- claiming a transport failure would be fake;
            # - a genuine x402 payment challenge keeps its protocol HTTP 402;
            # - upstream throttling surfaces as 429; an unanswered send is a 504;
            # - an upstream that answered and refused/failed is a 502 carrying the
            #   upstream's REAL status as data (served contracts read ``http_status``).
            resume_status, resume_condition = diagnostics.gateway_condition(exc.dispatch_state, exc.http_status)
            resume_payload = {
                "error": (
                    "the resend was refused before anything was sent; the liability stays held"
                    if resume_status == 400
                    else "the resend did not complete; the liability stays held"
                ),
                "code": exc.code,
                "dispatch_state": exc.dispatch_state,
                "http_status": exc.http_status,
                "detail": exc.detail,
            }
            return resume_status, diagnostics.with_diagnostic(
                resume_payload,
                diagnostics.diagnostic_envelope(
                    resume_condition,
                    detail=exc.detail,
                    correlation_id=operation_id,
                    http_status=resume_status,
                    upstream_status=exc.http_status,
                    retry_after_seconds=getattr(exc, "retry_after_seconds", None),
                ),
            )
    return 404, {"error": "unknown UsePod action", "code": "unknown_action"}


def _submit_http_status(result) -> int:
    """The STANDARD HTTP status for one bug-report submission outcome, from the typed
    failure facts -- never a blanket "409" for every failure.

    200 the submit call completed (issue created, or duplicate returned); 401 no
    credential for the destination; 403 the destination refused access (a private
    repository the token cannot see); 404 the destination or draft is absent; 409 the
    consent conflicts with the current bytes; 429 the destination throttled us;
    502/504 the upstream failed or timed out; 400 the payload itself was refused
    (outbound scan) or the request was invalid.
    """
    if result.status in ("submitted", "duplicate"):
        return 200
    code = str(result.failure_code or "")
    if code == "credential_unavailable":
        return 401
    if code == "upstream_status" and result.upstream_status is not None:
        upstream = int(result.upstream_status)
        if upstream in (401,):
            return 401
        if upstream in (403,):
            return 403
        if upstream in (404,):
            return 404
        if upstream == 429:
            return 429
        return 502
    if code == "upstream_timeout":
        return 504
    if code in ("upstream_unreachable", "upstream_response_unparseable"):
        return 502
    if code in ("approval_required", "consent_mismatch"):
        return 409
    if code == "draft_not_found":
        return 404
    if code == "outbound_scan_refused":
        return 400
    return 400


def _submit_condition(result) -> str:
    """The stable diagnostic condition for a failed submission outcome."""
    code = str(result.failure_code or "")
    mapping = {
        "credential_unavailable": f"{diagnostics.NAMESPACE}.request.unauthenticated",
        "approval_required": f"{diagnostics.NAMESPACE}.request.conflict",
        "consent_mismatch": f"{diagnostics.NAMESPACE}.request.conflict",
        "draft_not_found": f"{diagnostics.NAMESPACE}.request.not_found",
        "outbound_scan_refused": f"{diagnostics.NAMESPACE}.request.invalid",
        "upstream_timeout": f"{diagnostics.NAMESPACE}.upstream.timeout",
        "upstream_unreachable": f"{diagnostics.NAMESPACE}.upstream.unreachable",
        "upstream_response_unparseable": f"{diagnostics.NAMESPACE}.upstream.failed",
    }
    if code in mapping:
        return mapping[code]
    if code == "upstream_status" and result.upstream_status is not None:
        upstream = int(result.upstream_status)
        if upstream == 402:
            return f"{diagnostics.NAMESPACE}.upstream.payment_challenge"
        if upstream == 429:
            return f"{diagnostics.NAMESPACE}.upstream.throttled"
        if 400 <= upstream <= 499:
            return f"{diagnostics.NAMESPACE}.upstream.refused"
        return f"{diagnostics.NAMESPACE}.upstream.failed"
    return f"{diagnostics.NAMESPACE}.request.invalid"


def _dispatch_post_inner(
    *,
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    runtime: RuntimeServices,
    model_name: str,
    normalized_path: str,
    workspace_root_provider,
    normalize_chat_history_provider: Callable[[list[dict[str, Any]]], list[dict[str, str]]] = normalize_chat_history,
    extract_user_message_provider: Callable[[list[dict[str, Any]]], str] = extract_user_message,
    stable_openclaw_session_id_provider: Callable[..., str] = stable_openclaw_session_id,
    run_agent_provider: Callable[..., dict[str, Any]] = run_agent,
    stream_agent_with_events_provider: Callable[..., Iterable[bytes]] = stream_agent_with_events,
    resolve_null_domain_provider: Callable[[str], Any] | None = None,
    try_dial_provider: Callable[..., Any] | None = None,
    client_host: str = "",
    request_id: str = "",
) -> ApiResponse:
    if normalized_path == "/media-editor" or normalized_path.startswith("/media-editor/"):
        from core.web.api.media_editor_api import handle_media_editor_post

        return handle_media_editor_post(normalized_path, body)

    if normalized_path.startswith("/school/api/"):
        from core.school.api import handle_school_api_post

        return handle_school_api_post(normalized_path, body, headers, client_host=client_host)
    if normalized_path.startswith("/api/money/"):
        # revocation and reconciliation only: minting or widening money authority is never served
        from core.web.api.money_authority_api import handle_money_post

        return apply_runtime_headers(handle_money_post(normalized_path, body, headers, client_host=client_host), runtime)
    if normalized_path.startswith("/api/wallet/"):
        from core.product_edition import edition_allows

        wallet_ok, wallet_reason = edition_allows("wallet")
        if not wallet_ok:
            return apply_runtime_headers(
                json_response(404, {"error": "not_found", "reason": wallet_reason}), runtime
            )
        from core.web.api.wallet_api import handle_wallet_post

        return apply_runtime_headers(handle_wallet_post(normalized_path, body, client_host=client_host, headers=headers), runtime)
    if normalized_path.startswith("/api/mobile/"):
        from core.web.api.mobile_companion_api import handle_mobile_companion_post

        return handle_mobile_companion_post(
            normalized_path,
            body,
            headers,
            runtime=runtime,
            model_name=model_name,
            client_host=client_host,
            workspace_root_provider=workspace_root_provider,
        )

    if normalized_path == "/api/model-tool-certification/run":
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"error": "model tool certification is local-session only"}),
                runtime,
            )
        unknown = set(body) - {"provider_name", "model_name", "timeout_seconds"}
        if unknown:
            return apply_runtime_headers(
                json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime
            )
        provider_name = str(body.get("provider_name") or "").strip()
        requested_model = str(body.get("model_name") or "").strip()
        if not provider_name or not requested_model:
            return apply_runtime_headers(
                json_response(400, {"error": "provider_name and model_name are required"}), runtime
            )
        try:
            timeout_seconds = float(body.get("timeout_seconds") or 90.0)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "timeout_seconds is invalid"}), runtime)
        from core.local_model_tool_certification import (
            CertificationBoundaryError,
            run_local_model_tool_certification,
        )
        from core.model_registry import ModelRegistry

        manifest = ModelRegistry().get_manifest(provider_name, requested_model)
        if manifest is None:
            return apply_runtime_headers(json_response(404, {"error": "model provider not found"}), runtime)
        try:
            result = run_local_model_tool_certification(
                manifest,
                timeout_seconds=max(1.0, min(timeout_seconds, 300.0)),
            )
        except CertificationBoundaryError as exc:
            return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
        except Exception as exc:
            from core.local_model_tool_certification import CERTIFICATION_ROUTING_EFFECT

            return apply_runtime_headers(
                json_response(
                    500,
                    {
                        "error": safe_error_text(exc),
                        "routing_effect": CERTIFICATION_ROUTING_EFFECT,
                    },
                ),
                runtime,
            )
        return apply_runtime_headers(json_response(200, result), runtime)

    if normalized_path == "/api/models/local/register":
        # The write side of the local-model door: register ONE installed Ollama model as a lane
        # on this runtime, through the same manifest factory boot uses (`_ensure_local_ollama_provider`
        # owns license metadata, context sizing and the think flag). Registration alone certifies
        # nothing: the certification endpoint above decides authorship, and a model that never
        # passes it still cannot write a final answer. Local-session only, like every state door.
        from core.web.api.runtime import host_header_allowed

        def _localreg_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _localreg_header("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _localreg_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"error": "local models can only be registered from your own local session"}),
                runtime,
            )
        unknown = set(body.keys()) - {"model_name"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        model_name = str(body.get("model_name") or "").strip()
        if not model_name:
            return apply_runtime_headers(json_response(400, {"error": "model_name is required"}), runtime)
        from core.local_model_policy import resolve_local_model_policy
        from core.web.api.runtime import (
            installed_ollama_model_names,
            is_text_generation_ollama_model,
            ollama_base_url,
        )

        policy = resolve_local_model_policy()
        if policy.local_models_disabled:
            return apply_runtime_headers(
                json_response(409, {"error": "local models are disabled by policy on this runtime"}),
                runtime,
            )
        installed = set(
            installed_ollama_model_names(base_url=ollama_base_url(), timeout_seconds=5.0)
        )
        # Exact-tag membership, never a substring: `qwen3:4b` must not match `qwen3:4b-instruct`.
        if model_name not in installed:
            return apply_runtime_headers(
                json_response(404, {"error": "model is not installed in Ollama", "model_name": model_name}),
                runtime,
            )
        if not is_text_generation_ollama_model(model_name):
            return apply_runtime_headers(
                json_response(422, {"error": "model cannot generate text", "model_name": model_name}),
                runtime,
            )
        from core.model_registry import ModelRegistry
        from core.runtime_provider_defaults import register_installed_local_model

        registry = ModelRegistry()
        manifest = register_installed_local_model(registry, model_tag=model_name)
        if manifest is None:
            return apply_runtime_headers(
                json_response(500, {"error": "registration did not persist", "model_name": model_name}),
                runtime,
            )
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "ok": True,
                    "provider_id": manifest.provider_id,
                    "model_name": model_name,
                    "bundle_role": str(
                        (manifest.metadata or {}).get("bundle_role") or "general"
                    ),
                    "certification_url": "/api/model-tool-certification/run",
                },
            ),
            runtime,
        )

    if normalized_path == "/api/task/recovery":
        # GENERATED_ADAPTER: tasks.recovery owns the action; the verbatim legacy
        # implementation lives in core.web.api.registry_authorities.
        from core.command_registry.legacy import forward as _cr_forward

        _tr_status, _tr_payload = _cr_forward(
            "tasks.recovery",
            {
                "session_id": str(body.get("session_id") or ""),
                "checkpoint_id": str(body.get("checkpoint_id") or ""),
                "action": str(body.get("action") or ""),
            },
            client_host=client_host,
        )
        return apply_runtime_headers(json_response(_tr_status, _tr_payload), runtime)

    if normalized_path == "/api/mode":
        # Trusted application-state endpoint. It is loopback-only; prompt text, retrieved content,
        # tool arguments, and model output have no route to it. Every change is also a runtime
        # receipt event so an active task and the permanent Activity ledger can show what happened.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "mode changes are local-session only"}), runtime)
        op = str(body.get("op") or "set").strip().lower()
        mode_session = str(body.get("session_id") or "").strip()
        project_id = str(body.get("project_id") or "").strip()
        client_turn_id = str(body.get("turn_id") or "").strip()[:80]
        if not mode_session:
            return apply_runtime_headers(json_response(400, {"error": "session_id is required"}), runtime)
        event_context = {
            "runtime_session_id": mode_session,
            "session_id": mode_session,
            "cancel_turn_id": client_turn_id,
        }
        if op == "request_bypass_confirmation":
            # Step 1 of the two-step activation: mint a single-use, 60-second
            # confirmation bound to THIS exact activation. Minting grants nothing;
            # only the matching activate consumes it. The old caller-asserted
            # `explicit_confirmation` boolean let any local process (or a model
            # driving the shell lane) confirm on the user's behalf.
            from core.mode_permission_policy import request_bypass_confirmation as _mint_confirmation

            body_until_off_mint = body.get("until_off") is True
            _mint_root = ""
            if body_until_off_mint:
                from core.context_namespace import authoritative_chat_workspace as _acw

                _mint_root, _mint_root_reason = _acw(mode_session)
                if _mint_root_reason == "deleted_chat":
                    return apply_runtime_headers(
                        json_response(409, {"error": "this chat was deleted, so no bypass can be granted in it"}), runtime
                    )
            try:
                _cid = _mint_confirmation(
                    session_id=mode_session,
                    project_id=project_id,
                    task_id=client_turn_id,
                    scope=str(body.get("scope") or "task"),
                    duration_seconds=int(body.get("duration_seconds") or 900),
                    until_off=body_until_off_mint,
                    workspace_root=_mint_root,
                )
            except ValueError as exc:
                return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
            return apply_runtime_headers(
                json_response(200, {"ok": True, "confirmation_id": _cid, "expires_in_seconds": 60}),
                runtime,
            )
        if op == "activate_bypass":
            # WORKSPACE AUTHORITY IS SERVER-OWNED. A client body may carry a workspace string,
            # but it is a CONSISTENCY ASSERTION at most: the root a bypass grant binds to is
            # resolved from the chat's server-side namespace/project state, so a client, plugin
            # or model cannot widen the grant by naming another workspace, the home directory,
            # the filesystem root, or another chat's folder.
            from core.context_namespace import authoritative_chat_workspace

            authoritative_root, root_reason = authoritative_chat_workspace(mode_session)
            if root_reason == "deleted_chat":
                return apply_runtime_headers(
                    json_response(409, {"error": "this chat was deleted, so no bypass can be granted in it"}), runtime
                )
            if root_reason == "missing_chat":
                return apply_runtime_headers(
                    json_response(
                        409,
                        {
                            "error": (
                                "this chat has no server-side session yet; send one message in it "
                                "before granting bypass permissions"
                            )
                        },
                    ),
                    runtime,
                )
            body_until_off = body.get("until_off") is True
            if body_until_off:
                if root_reason != "project":
                    actionable = {
                        "unbound": "bind this chat to a project folder first (Projects → New project or an existing project), then approve until-off bypass",
                        "project_missing": "the project folder this chat was bound to no longer exists; re-bind a workspace first",
                        "unreadable": "the chat workspace could not be read from server state; retry, then re-bind the project if it persists",
                    }.get(root_reason, f"the chat workspace is unavailable ({root_reason})")
                    return apply_runtime_headers(
                        json_response(
                            409,
                            {
                                "error": (
                                    "an until-off bypass needs the chat's trusted workspace, and this "
                                    f"chat has none: {actionable}"
                                )
                            },
                        ),
                        runtime,
                    )
                claimed_root = str(body.get("workspace_root") or "").strip()
                if claimed_root:
                    from pathlib import Path as _Path

                    try:
                        matches = _Path(claimed_root).expanduser().resolve() == _Path(authoritative_root).resolve()
                    except (OSError, RuntimeError):
                        matches = False
                    if not matches:
                        return apply_runtime_headers(
                            json_response(
                                409,
                                {
                                    "error": (
                                        "the workspace in the request does not match this chat's "
                                        "actual workspace; reload the page and approve again"
                                    )
                                },
                            ),
                            runtime,
                        )
            try:
                from core.command_registry.legacy import forward as _cr_forward

                _ab_status, _ab_payload = _cr_forward(
                    "approvals.bypass.activate",
                    {
                        "session_id": mode_session,
                        "project_id": project_id,
                        "task_id": client_turn_id,
                        "scope": str(body.get("scope") or "task"),
                        "duration_seconds": int(body.get("duration_seconds") or 900),
                        "confirmation_id": str(body.get("confirmation_id") or "").strip(),
                        "until_off": body_until_off,
                        # Server-resolved root only: timed scopes are not workspace-bound, and
                        # the client-supplied string never becomes grant authority.
                        "workspace_root": authoritative_root if body_until_off else "",
                    },
                )
                if _ab_status != 200:
                    detail = _ab_payload.get("detail") if isinstance(_ab_payload, dict) else None
                    raise ValueError(str((detail or {}).get("reason") or _ab_payload))
                grant = _ab_payload.get("grant") if isinstance(_ab_payload, dict) else None
            except (PermissionError, ValueError, TypeError) as exc:
                return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
            until_off = bool(grant.get("until_off"))
            emit_runtime_event(
                event_context,
                event_type="bypass_activated",
                message=(
                    f"Bypass permissions activated for {grant['scope']} until you turn it off (this chat only)."
                    if until_off
                    else f"Bypass permissions activated for {grant['scope']} until it expires."
                ),
                details={
                    "active_mode": OperatingMode.BYPASS_PERMISSIONS.value,
                    "bypass_scope": grant["scope"],
                    "expires_at": grant["expires_at"],
                    "until_off": until_off,
                    "project_id": project_id,
                },
            )
            return apply_runtime_headers(json_response(200, {"ok": True, "grant": grant}), runtime)
        if op == "revoke_bypass":
            from core.mode_permission_policy import BypassStoreError

            try:
                revoked = revoke_bypass_grant(str(body.get("token") or ""))
            except BypassStoreError as exc:
                # Revoked for this process, but the durable mirror still holds the grant: after a
                # restart it CAN come back. Never reported as a clean success.
                return apply_runtime_headers(
                    json_response(
                        500,
                        {
                            "ok": False,
                            "error": (
                                "revoked now, but the revocation could not be recorded, so this "
                                f"grant may return after an app restart: {exc}"
                            ),
                        },
                    ),
                    runtime,
                )
            emit_runtime_event(
                event_context,
                event_type="bypass_revoked",
                message="Bypass permissions revoked; Manual mode is active.",
                details={"active_mode": OperatingMode.MANUAL.value, "project_id": project_id},
            )
            return apply_runtime_headers(json_response(200, {"ok": revoked, "mode": OperatingMode.MANUAL.value}), runtime)
        if op in {"grant_chat_workspace", "revoke_chat_workspace"}:
            # The Manual-mode standing approval: "Allow workspace reads and edits in this chat."
            # Server-minted through the internal-authority law (bounded duration, session- and
            # workspace-bound, ordinary reads/writes only); never stored in the browser.
            from core.mode_permission_policy import (
                grant_chat_workspace_authority,
                revoke_chat_workspace_authority,
                session_has_pending_workspace_approval,
            )

            if op == "grant_chat_workspace":
                # The same two laws as bypass above, plus the OPERATOR-ANSWER guard: the standing
                # scope may only be minted while THIS chat shows a pending permission ask in the
                # workspace class -- the button lives on that ask. A bare POST naming a session
                # and any root (plugin, model tool, scripted client) has no ask to answer.
                if not session_has_pending_workspace_approval(mode_session):
                    return apply_runtime_headers(
                        json_response(
                            409,
                            {
                                "error": (
                                    "a chat workspace approval can only be given while this chat is "
                                    "asking about a workspace read or edit"
                                )
                            },
                        ),
                        runtime,
                    )
                from core.context_namespace import authoritative_chat_workspace

                workspace_root, root_reason = authoritative_chat_workspace(mode_session)
                if root_reason != "project":
                    actionable = {
                        "deleted_chat": "this chat was deleted",
                        "missing_chat": "send one message in this chat first",
                        "unbound": "bind this chat to a project folder first (Projects → New project or an existing project)",
                        "project_missing": "the bound project folder no longer exists; re-bind a workspace",
                        "unreadable": "the chat workspace could not be read from server state; retry",
                    }.get(root_reason, f"workspace unavailable ({root_reason})")
                    # `reason` is the stable code the chat UI keys its unbound-chat flow on
                    # (offer the existing folder selection, keep the pending ask). The human
                    # sentence stays the message; the client never parses it.
                    return apply_runtime_headers(
                        json_response(
                            409,
                            {
                                "error": f"the chat workspace is unavailable: {actionable}",
                                "reason": str(root_reason),
                            },
                        ),
                        runtime,
                    )
                claimed_root = str(body.get("workspace_root") or "").strip()
                if claimed_root:
                    try:
                        matches = _Path(claimed_root).expanduser().resolve() == _Path(workspace_root).resolve()
                    except (OSError, RuntimeError):
                        matches = False
                    if not matches:
                        return apply_runtime_headers(
                            json_response(
                                409,
                                {
                                    "error": (
                                        "the workspace in the request does not match this chat's "
                                        "actual workspace; reload the page and approve again"
                                    )
                                },
                            ),
                            runtime,
                        )
                try:
                    scope_info = grant_chat_workspace_authority(
                        session_id=mode_session,
                        workspace_root=workspace_root,
                        duration_seconds=int(body.get("duration_seconds") or 28800),
                    )
                except (PermissionError, ValueError) as exc:
                    return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
                emit_runtime_event(
                    event_context,
                    event_type="chat_workspace_grant",
                    message="Workspace reads and edits approved for this chat.",
                    details={
                        "workspace_root": scope_info["workspace_root"],
                        "actions": scope_info["actions"],
                        "duration_seconds": scope_info["duration_seconds"],
                        "project_id": project_id,
                    },
                )
                return apply_runtime_headers(json_response(200, {"ok": True, "scope": scope_info}), runtime)
            removed = revoke_chat_workspace_authority(mode_session)
            emit_runtime_event(
                event_context,
                event_type="chat_workspace_grant_revoked",
                message="Chat workspace approval removed; ordinary prompts apply again.",
                details={"active_mode": OperatingMode.MANUAL.value, "project_id": project_id},
            )
            return apply_runtime_headers(json_response(200, {"ok": removed}), runtime)
        if op == "resolve_approval":
            from core.command_registry.legacy import forward as _cr_forward

            _ra_status, approval = _cr_forward(
                "approvals.resolve",
                {
                    "approval_id": str(body.get("approval_id") or ""),
                    "decision": str(body.get("decision") or ""),
                    # The operator's chosen scope is the grant's width; dropping it here minted a
                    # once grant for every "Allow all planned changes for this request" click.
                    # `resolve_approval` stays the authority on whether a scope may mint at all.
                    "scope": str(body.get("scope") or "once"),
                },
            )
            if not isinstance(approval, dict) or _ra_status != 200:
                return apply_runtime_headers(json_response(409, {"error": "approval is missing, stale, or already resolved"}), runtime)
            if approval is None:
                return apply_runtime_headers(json_response(409, {"error": "approval is missing, stale, or already resolved"}), runtime)
            allowed = str(approval.get("status") or "") == "approved"
            emit_runtime_event(
                event_context,
                event_type="permission_approved" if allowed else "permission_denied",
                message=("Approved" if allowed else "Denied") + f" {approval.get('intent') or 'action'}.",
                details={
                    "approval_id": str(approval.get("approval_id") or ""),
                    "task_id": str(approval.get("task_id") or ""),
                    "tool_name": str(approval.get("intent") or ""),
                    "approval_scope": str(approval.get("scope") or "once"),
                    "project_id": project_id,
                },
            )
            return apply_runtime_headers(json_response(200, {"ok": True, "approval": approval}), runtime)
        requested_mode = normalize_mode(body.get("mode"))
        if requested_mode is None:
            return apply_runtime_headers(json_response(400, {"error": "unsupported operating mode"}), runtime)
        # Same law as the grant ops: the workspace a bypass token is validated against is the
        # chat's SERVER-OWNED root, never the body's string. The chat-delete revocation path and
        # the mode store itself are unchanged.
        from core.context_namespace import authoritative_chat_workspace

        _set_root, _set_root_reason = authoritative_chat_workspace(mode_session)
        try:
            state = set_active_mode(
                mode_session,
                requested_mode,
                project_id=project_id,
                client_turn_id=client_turn_id,
                bypass_token=str(body.get("bypass_token") or ""),
                workspace_root=(_set_root if _set_root_reason == "project" else ""),
            )
        except (PermissionError, ValueError) as exc:
            return apply_runtime_headers(json_response(409, {"error": str(exc)}), runtime)
        not_durable = str(state.get("bypass_revocation_not_durable") or "").strip()
        if not_durable:
            emit_runtime_event(
                event_context,
                event_type="bypass_revocation_not_durable",
                message=(
                    "Bypass was revoked for this session, but the revocation could not be recorded — "
                    "it may return after an app restart until storage is fixed and it is revoked again."
                ),
                details={"project_id": project_id, "reason": not_durable},
            )
        emit_runtime_event(
            event_context,
            event_type="mode_changed",
            message=f"Mode changed to {state['label']}.",
            details={
                "active_mode": state["mode"],
                "mode_revision": state["revision"],
                "project_id": state["project_id"],
            },
        )
        return apply_runtime_headers(json_response(200, {"ok": True, "state": state}), runtime)

    if normalized_path.startswith("/api/auth/") and normalized_path.endswith("/callback"):
        # OAuth authorization callback delivered by the macOS vool:// scheme handler (the window
        # catches vool://auth/<provider>/callback?code=&state= and POSTs {code, state} here). The
        # desktop owns state-verification + the code->key exchange; the code is held in memory only
        # and never logged. Loopback + same-origin only.
        from core.web.api.runtime import host_header_allowed

        hdr = {str(k).lower(): str(v or "") for k, v in (headers or {}).items()}
        if hdr.get("content-type") and "json" not in hdr["content-type"].lower():
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = hdr.get("origin", "").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "auth callback is local-session only"}), runtime)
        provider = normalized_path[len("/api/auth/"):-len("/callback")].strip("/").lower()
        code = str(body.get("code") or "").strip()
        state = str(body.get("state") or "").strip()
        if not provider or not code or not state:
            return apply_runtime_headers(json_response(400, {"error": "provider, code and state are required"}), runtime)
        from core import oauth_callback

        ack = oauth_callback.record_callback(provider, code, state)
        return apply_runtime_headers(json_response(200, {"ok": True, **ack}), runtime)

    if normalized_path == "/api/runtime/unresolved-effects/resolve":
        # A6 USER/PROVIDER resolution channel (NIA-006): the owner resolves an
        # unresolved effect with typed evidence. This answers "DID it happen?",
        # never "MAY it execute?" — A1 stays the sole widening authority, and a
        # released retry re-enters the full reserve/claim + gate path. The model
        # is refused as a source (never-model-source law). Loopback-only with the
        # same guard suite as the other state-changing POSTs.
        import json as _json

        from core.runtime_continuity import resolve_unresolved_effect
        from core.web.api.runtime import host_header_allowed
        from network.signer import get_local_peer_id

        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)

        def _rhdr(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _rhdr("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _rhdr("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)

        effect_id = str(body.get("logical_effect_id") or "").strip()
        resolution = str(body.get("resolution") or "").strip().upper()
        source = str(body.get("source") or "").strip().lower()
        evidence = str(body.get("evidence") or "").strip()
        if not effect_id:
            return apply_runtime_headers(json_response(400, {"ok": False, "error": "missing logical_effect_id"}), runtime)
        if source not in ("user", "provider"):
            # mechanical resolutions are registered provers, not an HTTP surface;
            # "model" is refused by name in the store and here by whitelist.
            return apply_runtime_headers(json_response(400, {"ok": False, "error": "source must be user or provider"}), runtime)
        if not evidence:
            return apply_runtime_headers(json_response(400, {"ok": False, "error": "a resolution without evidence is unfalsifiable; evidence is required"}), runtime)
        try:
            result = resolve_unresolved_effect(
                logical_effect_id=effect_id,
                resolution=resolution,
                source=source,
                evidence=evidence[:2000],
                resolved_by=f"owner:{get_local_peer_id()}",
            )
        except ValueError as exc:
            return apply_runtime_headers(json_response(400, {"ok": False, "error": str(exc)}), runtime)
        if isinstance(result, dict) and result.get("outcome") == "no_active_effect":
            return apply_runtime_headers(json_response(404, {"ok": False, "error": "no such unresolved effect"}), runtime)
        return apply_runtime_headers(json_response(200, {"ok": True, "resolution": result}), runtime)

    if normalized_path == "/api/chat/queue":
        # Persistent per-session message queue for the composer. One state-changing POST with an
        # "op": enqueue (idempotent), claim (atomic dequeue), complete, cancel. Loopback-only
        # (Host-header guard) with the same content-type/Origin/size guards as /api/chat/session.
        # The TCP-peer gate is the loopback half of that contract: the
        # Host/Origin headers alone do not stop a remote peer from
        # enqueueing, claiming, or completing queue items.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        import json as _json

        from core.runtime_continuity import (
            cancel_queue_item,
            claim_next_message,
            complete_queue_item,
            enqueue_message,
            list_queued_messages,
        )
        from core.web.api.runtime import host_header_allowed

        def _qhdr(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _qhdr("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        q_origin = _qhdr("origin").strip()
        if q_origin and not host_header_allowed(q_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not isinstance(body, dict):
            return apply_runtime_headers(json_response(400, {"error": "body must be a JSON object"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        q_session = str(body.get("session_id") or "").strip()
        if not q_session or len(q_session) > 200 or any(ord(ch) < 32 for ch in q_session):
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid session_id"}), runtime)
        op = str(body.get("op") or "").strip()

        if op == "enqueue":
            text = str(body.get("text") or "").strip()
            if not text:
                return apply_runtime_headers(json_response(400, {"error": "enqueue requires non-empty text"}), runtime)
            if len(text) > 32768:
                return apply_runtime_headers(json_response(413, {"error": "message too long"}), runtime)
            idem = str(body.get("idempotency_key") or "").strip()[:200]
            # A queued message may carry ONLY ids this chat staged and has not spent. A path, an
            # object, or another chat's id is refused here, before anything is stored.
            raw_queue_attachments = body.get("attachments")
            queued_attachments: list[str] = []
            if raw_queue_attachments:
                from core import chat_attachments as _queue_attachments

                try:
                    queued_attachments = _queue_attachments.validate_owned_staged(
                        session_id=q_session,
                        attachment_ids=raw_queue_attachments if isinstance(raw_queue_attachments, list) else [raw_queue_attachments],
                    )
                except _queue_attachments.AttachmentRefused as exc:
                    return apply_runtime_headers(
                        json_response(
                            exc.http_status if exc.http_status not in {400, 422} else 422,
                            {"error": "attachment_rejected", "code": exc.code, "message": exc.message},
                        ),
                        runtime,
                    )
            payload = {"text": text, "attachments": queued_attachments}
            if body.get("price_wait_model"):
                from core.usepod.price_wait import queued_target
                try:
                    payload["price_wait"] = queued_target(str(body["price_wait_model"]))
                except ValueError as exc:
                    return apply_runtime_headers(json_response(409, {"error": str(exc)}), runtime)
            item = enqueue_message(session_id=q_session, payload=payload, idempotency_key=idem)
            position = len(list_queued_messages(q_session, statuses=("pending",)))
            return apply_runtime_headers(json_response(200, {"item": item, "position": position}), runtime)
        if op == "claim":
            pending = list_queued_messages(q_session, statuses=("pending",))
            expected = str(pending[0]["queue_item_id"]) if pending else ""
            if pending and pending[0].get("payload", {}).get("price_wait"):
                from core.usepod.price_wait import check_queued
                gate = check_queued(pending[0]["payload"]["price_wait"])
                if not gate.get("ready"):
                    return apply_runtime_headers(json_response(200, {"item": None, "waiting": gate}), runtime)
            item = claim_next_message(q_session, expected_queue_item_id=expected, lease_owner=str(body.get("lease_owner") or "").strip()[:80], turn_id=str(body.get("turn_id") or "").strip()[:80])
            return apply_runtime_headers(json_response(200, {"item": item}), runtime)
        if op == "complete":
            qid = str(body.get("queue_item_id") or "").strip()
            if not qid:
                return apply_runtime_headers(json_response(400, {"error": "complete requires queue_item_id"}), runtime)
            status = str(body.get("status") or "completed").strip()
            item = complete_queue_item(qid, status=status)
            return apply_runtime_headers(json_response(200, {"item": item}), runtime)
        if op == "cancel":
            qid = str(body.get("queue_item_id") or "").strip()
            if not qid:
                return apply_runtime_headers(json_response(400, {"error": "cancel requires queue_item_id"}), runtime)
            cancelled = cancel_queue_item(qid, session_id=q_session)
            return apply_runtime_headers(json_response(200, {"cancelled": cancelled}), runtime)
        return apply_runtime_headers(json_response(400, {"error": "op must be one of enqueue|claim|complete|cancel"}), runtime)

    if normalized_path == "/api/settings/credentials":
        # GENERATED_ADAPTER: settings.credentials.set owns the action; the verbatim
        # legacy implementation lives in core.web.api.registry_authorities.
        from core.command_registry.legacy import forward as _cr_forward

        content_type = ""
        for key, value in (headers or {}).items():
            if str(key).lower() == "content-type":
                content_type = str(value or "").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        _cr_status, _cr_payload = _cr_forward("settings.credentials.set", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cr_status, _cr_payload), runtime)

    if normalized_path.startswith("/api/cloud/usepod/"):
        # UsePod owner actions from Settings: refresh discovery, save the routing policy, approve or
        # forget a model's price bound, choose the lane's dialect and payment transport. Owner-local;
        # none runs inference or spends, and every refusal carries a stable code and no credential.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        _up_status, _up_payload = _usepod_owner_action(normalized_path, body, headers)
        return apply_runtime_headers(json_response(_up_status, _up_payload), runtime)

    if normalized_path == "/api/cloud/test":
        # Live auth test of the cloud key against the provider ("Test connection" button and the
        # in-chat connection check). Same guard suite as the other state-changing POSTs; the
        # probe itself is rate-limited in cloud_connection_state so this cannot hammer the API.
        # Response carries state/detail only — never any key material.
        import json as _json

        from core.cloud_connection_state import run_auth_probe
        from core.web.api.runtime import host_header_allowed

        def _test_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        content_type = _test_header("content-type").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _test_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"provider"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        _tp = body.get("provider")
        test_provider = str(_tp).strip().lower() if isinstance(_tp, str) and _tp.strip() else None
        return apply_runtime_headers(json_response(200, run_auth_probe(provider=test_provider)), runtime)

    if normalized_path == "/api/discovery/refresh":
        # Refresh one provider's capability discovery: re-verify the stored key through the ONE
        # verification policy, then observe the models list under it. Same guard suite as the
        # other keyed POSTs; the refresh itself refuses without a stored key (nothing is sent),
        # keeps the previous catalogue on an outage and never adopts a public catalogue as key
        # evidence. Response is the resulting assertion — no key material anywhere.
        import json as _json

        from core.credential_intelligence.discovery import discovery_snapshot, refresh_discovery
        from core.web.api.runtime import host_header_allowed

        def _disc_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        content_type = _disc_header("content-type").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _disc_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"provider"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        _dp = body.get("provider")
        if not isinstance(_dp, str) or not _dp.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing provider"}), runtime)
        provider_id = _dp.strip().lower()
        assertion = refresh_discovery(provider_id)
        if assertion is None:
            return apply_runtime_headers(json_response(404, {"error": "unknown provider", "provider": provider_id}), runtime)
        entry = discovery_snapshot([provider_id]).get(provider_id, {})
        return apply_runtime_headers(json_response(200, {"ok": True, "provider": provider_id, **entry}), runtime)

    if normalized_path == "/api/search/test":
        # "Test" for a web-search key. Runs one real minimal search through the same client the
        # search chain uses, so a green result proves the path that actually answers questions —
        # not a cheaper health endpoint that can pass while real searches fail. Rate-limited per
        # provider in search_connection_state so repeated clicks cannot burn a free-tier quota.
        # Response carries state/detail only — never any key material.
        import json as _json

        from core.web.api.runtime import host_header_allowed

        def _search_test_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        content_type = _search_test_header("content-type").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _search_test_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"provider"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        _sp = body.get("provider")
        if not isinstance(_sp, str) or not _sp.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing provider"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _st_status, _st_payload = _cr_forward("search.test", {"provider": _sp.strip().lower()})
        return apply_runtime_headers(json_response(_st_status, _st_payload), runtime)

    if normalized_path == "/api/search/detect":
        # Which provider does this pasted key belong to? Prefix-only, entirely local: nothing is
        # stored and no network call is made, so the UI can label the key the moment it is pasted.
        # The key is never echoed back — only the verdict.
        import json as _json

        from core.search_providers import detect_provider
        from core.web.api.runtime import host_header_allowed

        def _detect_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        content_type = _detect_header("content-type").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _detect_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"value"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        _dv = body.get("value")
        if not isinstance(_dv, str) or not _dv.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid value"}), runtime)
        if len(_dv) > 8192:
            return apply_runtime_headers(json_response(413, {"error": "value too long"}), runtime)
        guess = detect_provider(_dv)
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "provider": guess.provider_id,
                    "confidence": guess.confidence,
                    "candidates": list(guess.candidates),
                },
            ),
            runtime,
        )

    if normalized_path in ("/api/projects", "/api/projects/delete", "/api/projects/reveal", "/api/projects/emoji"):
        # Codex-style projects: create (register a local folder), delete (forget it + purge its memory,
        # never touching the folder), or reveal (open the folder in Finder). Owner-local only; the same
        # guard suite as the other state-changing POSTs. A body field can never grant trust.
        import json as _json

        from core import project_store
        from core.web.api.runtime import host_header_allowed

        def _proj_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _proj_header("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _proj_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "projects can only be changed from your own local session"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)

        if normalized_path == "/api/projects/reveal":
            unknown = set(body.keys()) - {"id"}
            if unknown:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
            pid = body.get("id")
            if not isinstance(pid, str) or not pid.strip():
                return apply_runtime_headers(json_response(400, {"error": "missing or invalid id"}), runtime)
            root = project_store.project_root(pid.strip())  # only the project's own registered, existing folder
            if not root:
                return apply_runtime_headers(json_response(404, {"error": "project folder not found"}), runtime)
            import subprocess as _subprocess

            opened = False
            with contextlib.suppress(Exception):
                _subprocess.Popen(["open", root])  # macOS Finder; path is a validated project root, not user input
                opened = True
            return apply_runtime_headers(json_response(200, {"ok": opened, "id": pid.strip()}), runtime)

        if normalized_path == "/api/projects/emoji":
            # Appearance for a project: emoji marker and/or accent colour (either optional, one required).
            _missing = object()
            unknown = set(body.keys()) - {"id", "emoji", "color"}
            if unknown:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
            pid = body.get("id")
            if not isinstance(pid, str) or not pid.strip():
                return apply_runtime_headers(json_response(400, {"error": "missing or invalid id"}), runtime)
            pid = pid.strip()
            emoji = body.get("emoji", _missing)
            color = body.get("color", _missing)
            if emoji is _missing and color is _missing:
                return apply_runtime_headers(json_response(400, {"error": "emoji or color required"}), runtime)
            ok = True
            applied = {}
            if emoji is not _missing:
                if not isinstance(emoji, str) or len(emoji) > 16:
                    return apply_runtime_headers(json_response(400, {"error": "emoji must be a short string"}), runtime)
                ok = project_store.set_project_emoji(pid, emoji) and ok
                applied["emoji"] = emoji
            if color is not _missing:
                if not isinstance(color, str) or len(color) > 16:
                    return apply_runtime_headers(json_response(400, {"error": "color must be a short string"}), runtime)
                ok = project_store.set_project_color(pid, color) and ok
                applied["color"] = project_store.normalize_color(color)
            return apply_runtime_headers(json_response(200 if ok else 404, {"ok": ok, "id": pid, **applied}), runtime)

        if normalized_path == "/api/projects/delete":
            unknown = set(body.keys()) - {"id"}
            if unknown:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
            pid = body.get("id")
            if not isinstance(pid, str) or not pid.strip():
                return apply_runtime_headers(json_response(400, {"error": "missing or invalid id"}), runtime)
            pid = pid.strip()
            # "Forget its context": unbind the project's chats (they move to General) and purge everything
            # VOOL learned inside them, then drop the project. The chats' transcripts are kept.
            from core.context_namespace import list_chat_namespaces
            from core.persistent_memory import (
                forget_sessions_memory,
                load_session_meta,
                set_session_meta,
            )

            # A chat can be bound to a project in the session meta, in its namespace, or both, so
            # enumerating one store finds only some of the project's chats. Scanning meta alone
            # found 1 of 61 on the reporting runtime -- the other 60 were namespace-bound, so they
            # were neither unbound nor forgotten, and they reappeared the moment a project was
            # recreated on the same folder (the id is derived from the path, so it comes back the
            # same). set_session_meta clears both bindings; this decides who to clear.
            project_sessions = sorted(
                {
                    sid
                    for sid, meta in load_session_meta().items()
                    if str((meta or {}).get("project_id") or "").strip() == pid
                }
                | {
                    namespace.chat_id
                    for namespace in list_chat_namespaces(limit=1_000_000)
                    if str(namespace.project_id or "").strip() == pid
                }
            )
            for sid in project_sessions:
                set_session_meta(sid, project_id="")
            # A8 rebind: the project's chats' governed payloads transition
            # through the ONE availability authority (transcripts themselves
            # stay, per project-delete semantics; the payload bytes are gated
            # everywhere they are served).
            try:
                from core.memory.entries import erase_finalizations_for_session

                for sid in project_sessions:
                    erase_finalizations_for_session(sid)
            except Exception:
                pass
            forgotten = forget_sessions_memory(project_sessions)
            ok = project_store.delete_project(pid)
            return apply_runtime_headers(json_response(200 if ok else 404, {"ok": ok, "id": pid, "forgot": forgotten, "chats": len(project_sessions)}), runtime)

        unknown = set(body.keys()) - {"name", "root"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        name = body.get("name", "")
        root = body.get("root")
        if not isinstance(root, str) or not root.strip():
            return apply_runtime_headers(json_response(400, {"error": "a project folder (root) is required"}), runtime)
        if not isinstance(name, str):
            return apply_runtime_headers(json_response(400, {"error": "name must be a string"}), runtime)
        if len(root) > 4096 or any(ord(ch) < 32 for ch in root):
            return apply_runtime_headers(json_response(400, {"error": "invalid project folder"}), runtime)
        ok, result = project_store.create_project(name, root)
        if not ok:
            return apply_runtime_headers(json_response(400, {"error": str(result)}), runtime)
        return apply_runtime_headers(json_response(200, {"project": result}), runtime)

    if normalized_path == "/api/files/open":
        # Reveal a generated file in Finder. Owner-local; the path MUST resolve under a known Files root
        # (generated_files.path_is_allowed), so this can never be used to reach an arbitrary file.
        import json as _json

        from core import generated_files
        from core.web.api.runtime import host_header_allowed

        def _files_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _files_header("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _files_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "files can only be opened from your own local session"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"path", "reveal"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid path"}), runtime)
        reveal = body.get("reveal", True)
        if not isinstance(reveal, bool):
            return apply_runtime_headers(json_response(400, {"error": "reveal must be a boolean"}), runtime)
        if not generated_files.path_is_allowed(path):
            return apply_runtime_headers(json_response(403, {"error": "that file is not under a known Files folder"}), runtime)
        opened = generated_files.open_file(path, reveal=reveal)
        return apply_runtime_headers(json_response(200 if opened else 500, {"ok": opened}), runtime)

    if normalized_path == "/api/repoops/authorize-push":
        # THE Authorize Push gesture. It exists here and nowhere else: the authorization is minted
        # SERVER-SIDE from an owner-local request, and `repo_push_authorization` is a reserved trust
        # key, so a request body carrying one is stripped before any runtime sees it. A model cannot
        # write its own consent to move a remote ref.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"ok": False, "error": "owner_local_required"}), runtime
            )
        from core.web.api.repoops_api import authorize_push

        payload = authorize_push(
            repo_session_id=str(body.get("repo_session_id") or ""),
            plan_hash=str(body.get("plan_hash") or ""),
            force=bool(body.get("force") or False),
            branch_delete=bool(body.get("branch_delete") or False),
            workspace_root=str(workspace_root_provider() or ""),
        )
        return apply_runtime_headers(json_response(200 if payload.get("ok") else 400, payload), runtime)

    if normalized_path == "/api/repoops/authorize-forge-action":
        # THE forge-action gesture (draft PR create, PR text update, comment), owner-local and
        # server-stamped exactly like Authorize Push: `repo_forge_action_authorization` is a
        # reserved trust key, so a turn cannot write its own consent to put content on a forge.
        # The same route carries the operator's RESOLUTION of an unproven outcome
        # (`resolve: failed_safe_to_retry`), which is the only direction an operator may assert.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"ok": False, "error": "owner_local_required"}), runtime
            )
        from core.web.api.repoops_api import authorize_forge_action

        payload = authorize_forge_action(
            repo_session_id=str(body.get("repo_session_id") or ""),
            action_hash=str(body.get("action_hash") or ""),
            resolve=str(body.get("resolve") or ""),
            workspace_root=str(workspace_root_provider() or ""),
        )
        return apply_runtime_headers(json_response(200 if payload.get("ok") else 400, payload), runtime)

    if normalized_path.startswith("/api/learning/procedures/") and normalized_path.endswith("/invalidate"):
        # PB01 hook 4 — the operator's CORRECTION gesture: a lesson the operator says is wrong
        # stops being reused immediately (demoted; later verified evidence appends but cannot
        # resurrect it). Owner-local only, and the procedure id is validated against the store's
        # own signature-hash shape BEFORE any filesystem access — ids are never raw paths.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.learning import invalidate_procedure
        from core.learning_integration import valid_procedure_id

        procedure_id = normalized_path[len("/api/learning/procedures/"):-len("/invalidate")].strip("/")
        if not valid_procedure_id(procedure_id):
            return apply_runtime_headers(json_response(400, {"ok": False, "error": "invalid_procedure_id"}), runtime)
        reason = str(body.get("reason") or "operator_correction").strip()[:500]
        shard = invalidate_procedure(procedure_id, reason=reason)
        if shard is None:
            return apply_runtime_headers(json_response(404, {"ok": False, "error": "unknown_procedure"}), runtime)
        return apply_runtime_headers(json_response(200, {"ok": True, "procedure_id": procedure_id, "status": shard.status}), runtime)

    if normalized_path.startswith("/api/learning/procedures/") and "/invalidate" not in normalized_path and normalized_path != "/api/learning/procedures":
        # PB01 hook 4 — the operator's FORGET gesture: remove one lesson entirely. Same
        # owner-local law, same id gate before any filesystem access.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.learning import delete_procedure
        from core.learning_integration import valid_procedure_id

        procedure_id = normalized_path[len("/api/learning/procedures/"):].strip("/")
        if not valid_procedure_id(procedure_id):
            return apply_runtime_headers(json_response(400, {"ok": False, "error": "invalid_procedure_id"}), runtime)
        removed = delete_procedure(procedure_id)
        if not removed:
            return apply_runtime_headers(json_response(404, {"ok": False, "error": "unknown_procedure"}), runtime)
        return apply_runtime_headers(json_response(200, {"ok": True, "procedure_id": procedure_id, "deleted": True}), runtime)

    if normalized_path == "/api/plugins/lifecycle":
        # Install / verify / enable / disable / update / revoke / uninstall, owner-local only.
        # Each is a separate act, and the response says what the pack's state IS afterwards --
        # never "installed" as a synonym for "available".
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"ok": False, "error": "owner_local_required"}), runtime
            )
        from core.web.api.repoops_api import plugin_lifecycle_action

        payload = plugin_lifecycle_action(
            action=str(body.get("action") or ""), plugin_id=str(body.get("plugin_id") or "")
        )
        return apply_runtime_headers(json_response(200 if payload.get("ok") else 400, payload), runtime)

    if normalized_path == "/api/plugins/rescan":
        # Owner-local RECOVERY for plugin storage: repeat the bounded folder probe and load the
        # packs it names. This is the one door for "the folder answers now" (a consent dialog
        # answered, a volume back, a permission granted) without a relaunch. It changes no
        # configuration and disables nothing; the response is the storage state as it IS.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(403, {"ok": False, "error": "plugins can only be rescanned from your own local session"}),
                runtime,
            )
        from core.plugin_catalog import RESCAN_PROBE_BUDGET_S, STORAGE_ACCESSIBLE, discover_and_register

        state = discover_and_register(budget_s=RESCAN_PROBE_BUDGET_S, reason="rescan")
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "ok": True,
                    "recovered": str(state.get("state") or "") == STORAGE_ACCESSIBLE,
                    "storage": state,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/plugins/enable":
        # Owner-local toggle of a plugin's enabled state, persisted for the console. Same guard suite
        # as the other state-changing POSTs; a body field can never grant owner-local.
        import json as _json

        from core.plugin_catalog import set_plugin_enabled
        from core.web.api.runtime import host_header_allowed

        def _plugin_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _plugin_header("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _plugin_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "plugins can only be changed from your own local session"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"id", "enabled"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        pid, enabled = body.get("id"), body.get("enabled")
        if not isinstance(pid, str) or not pid.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid id"}), runtime)
        if not isinstance(enabled, bool):
            return apply_runtime_headers(json_response(400, {"error": "enabled must be true/false"}), runtime)
        ok = set_plugin_enabled(pid.strip(), enabled)
        return apply_runtime_headers(json_response(200 if ok else 500, {"ok": ok, "id": pid.strip(), "enabled": enabled}), runtime)

    if normalized_path == "/api/skills/enable":
        # Owner-local toggle of one NATIVE skill's enabled state, same guard suite as
        # /api/plugins/enable: a skill the operator turned off cannot be selected into any
        # turn's context, and the refusal is typed, not silent. An unknown skill id is a 404
        # with the reason, never a fake ok.
        import json as _json

        from core.native_skill_library import set_skill_enabled
        from core.web.api.runtime import host_header_allowed

        def _skill_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ctype = _skill_header("content-type").lower()
        if ctype and "json" not in ctype:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _skill_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"error": "skills can only be changed from your own local session"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"id", "enabled"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        sid, enabled = body.get("id"), body.get("enabled")
        if not isinstance(sid, str) or not sid.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid id"}), runtime)
        if not isinstance(enabled, bool):
            return apply_runtime_headers(json_response(400, {"error": "enabled must be true/false"}), runtime)
        result = set_skill_enabled(sid.strip(), enabled)
        if result.get("status") == "unknown_skill":
            return apply_runtime_headers(json_response(404, {"error": result.get("reason"), "id": sid.strip()}), runtime)
        ok = result.get("status") == "ok"
        return apply_runtime_headers(json_response(200 if ok else 500, {"ok": ok, "id": sid.strip(), "enabled": enabled}), runtime)

    if normalized_path == "/api/cloud/auto-model":
        # VOOL Auto's cloud fallback is independent from the explicit composer pin. It accepts
        # only `auto` or a catalog-verified OpenRouter :free model and is owner-local only.
        import json as _json

        from core.web.api.runtime import host_header_allowed

        def _auto_model_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        content_type = _auto_model_header("content-type").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _auto_model_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"model"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid model"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _am_status, _am_payload = _cr_forward("models.auto", {"model": model.strip()}, client_host=client_host)
        return apply_runtime_headers(json_response(_am_status, _am_payload), runtime)

    if normalized_path == "/api/cloud/model":
        # GENERATED_ADAPTER: models.pin owns the action; the verbatim legacy
        # implementation (council fence, accepted-ceiling law, paid gate) lives in
        # core.web.api.registry_authorities and is called by the registry handler.
        from core.command_registry.legacy import forward as _cr_forward

        _cm_status, _cm_payload = _cr_forward("models.pin", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cm_status, _cm_payload), runtime)

    if normalized_path in {
        "/api/council/convene", "/api/council/stop",
        "/api/council/seat", "/api/council/resume",
    }:
        # Convene or stop a council run. Owner-local plus the standard POST guard suite.
        # The council NEVER self-authorizes spend: seat models the operator has not price-
        # accepted fail typed inside dispatch, and the operator's composer pin is restored
        # when the run ends however it ends.
        from core.web.api.runtime import host_header_allowed as _cc_host_allowed

        def _cc_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        _cc_ct = _cc_header("content-type").lower()
        if _cc_ct and "json" not in _cc_ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        _cc_origin = _cc_header("origin").strip()
        if _cc_origin and not _cc_host_allowed(_cc_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        if normalized_path == "/api/council/stop":
            unknown_cc = set(body.keys()) - {"run_id"}
            if unknown_cc:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown_cc)}"}), runtime)
            council_status, council_payload = _cr_forward("council.stop", {"run_id": str(body.get("run_id") or "")})
            return apply_runtime_headers(json_response(council_status, council_payload), runtime)
        if normalized_path == "/api/council/seat":
            # One operator decision about one failed seat: retry it, point it at another
            # model, or take it out of the bench. Owner-local like every council surface,
            # typed, idempotent, and ledgered by the orchestrator itself.
            _seat_unknown = set(body.keys()) - {"run_id", "seat_id", "action", "model"}
            if _seat_unknown:
                # Silently dropping a field the caller sent would turn a mistyped request
                # into an action on defaults — the same law convene/stop/resume enforce.
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(_seat_unknown)}"}), runtime)
            _seat_in = {"run_id": str(body.get("run_id") or ""), "seat_id": str(body.get("seat_id") or ""), "action": str(body.get("action") or "")}
            if body.get("model"):
                _seat_in["model"] = str(body.get("model"))
            council_status, council_payload = _cr_forward("council.seat", _seat_in)
            return apply_runtime_headers(json_response(council_status, council_payload), runtime)
        if normalized_path == "/api/council/resume":
            unknown_rs = set(body.keys()) - {"run_id"}
            if unknown_rs:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown_rs)}"}), runtime)
            _rs_host = _cc_header("host").strip() or "127.0.0.1:11435"
            council_status, council_payload = _cr_forward(
                "council.resume", {"run_id": str(body.get("run_id") or ""), "base_url": f"http://{_rs_host}"}
            )
            return apply_runtime_headers(json_response(council_status, council_payload), runtime)
        _cc_host = _cc_header("host").strip() or "127.0.0.1:11435"
        council_status, council_payload = _cr_forward(
            "council.convene",
            {"payload": dict(body), "base_url": f"http://{_cc_host}", "workspace_root": str(workspace_root_provider() or "")},
        )
        return apply_runtime_headers(json_response(council_status, council_payload), runtime)

    if normalized_path == "/media-editor" or normalized_path.startswith("/media-editor/"):
        from core.web.api.media_editor_api import handle_media_editor_post

        return handle_media_editor_post(normalized_path, body)

    if normalized_path == "/api/context/pages":
        # C09/C13 turn-context controls (additive delegation): withhold /
        # erase (forget) / supersede (edit) / pin / archive / recall /
        # bump_generation over the pages real turns admit. Owner-local,
        # typed refusals; the guards and actions live in
        # core.web.api.context_pages_api; nothing existing is rewritten.
        from core.web.api.context_pages_api import handle_context_pages_post

        return handle_context_pages_post(body=body, runtime=runtime, client_host=client_host)

    if normalized_path == "/api/memory/forget":
        # Remove exactly one remembered entry by record id. Owner-local + the standard POST
        # guard suite; the precise id-scoped remover exists so this button can never delete
        # more than the row the operator pointed at.
        from core.web.api.runtime import host_header_allowed as _mf_host_allowed

        def _mf_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        _mf_ct = _mf_header("content-type").lower()
        if _mf_ct and "json" not in _mf_ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        _mf_origin = _mf_header("origin").strip()
        if _mf_origin and not _mf_host_allowed(_mf_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        unknown_mf = set(body.keys()) - {"record_id"}
        if unknown_mf:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown_mf)}"}), runtime)
        record_id = body.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid record_id"}), runtime)
        from core.command_registry.legacy import forward as _cr_forward

        _mf_status, _mf_payload = _cr_forward("memory.forget", {"record_id": record_id.strip()})
        return apply_runtime_headers(json_response(_mf_status, _mf_payload), runtime)

    if normalized_path.startswith("/api/bug-report/"):
        # Opt-in privacy-safe bug reporting. Every handler is owner-local + the standard
        # POST guard suite; nothing here can publish anything without the consent-bound
        # approval flow inside core/bug_report (guards re-verified there, not just here).
        from core.web.api.runtime import host_header_allowed as _br_host_allowed

        def _br_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        _br_ct = _br_header("content-type").lower()
        if _br_ct and "json" not in _br_ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        _br_origin = _br_header("origin").strip()
        if _br_origin and not _br_host_allowed(_br_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)

        from core.bug_report import get_service as _br_service
        from core.bug_report.store import is_valid_report_id as _br_valid_id

        def _br_string_list(value, field: str):
            if value is None:
                return []
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                raise ValueError(f"{field} must be a list of strings")
            return value

        def _br_report_id() -> str:
            report_id = body.get("report_id")
            if not isinstance(report_id, str) or not _br_valid_id(report_id):
                raise ValueError("missing or invalid report_id")
            return report_id

        def _br_correlation_id() -> str:
            from core.web.request_ids import resolve_request_id

            return resolve_request_id(headers)

        try:
            if normalized_path == "/api/bug-report/draft":
                unknown_br = set(body.keys()) - {
                    "expected", "actual", "repro_steps", "error_text", "category",
                    "lanes", "tools", "models", "log_sources", "destination_repo", "title",
                }
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                log_sources = body.get("log_sources") or []
                if not isinstance(log_sources, list):
                    raise ValueError("log_sources must be a list")
                draft = _br_service().create_draft(
                    expected=str(body.get("expected", "")),
                    actual=str(body.get("actual", "")),
                    repro_steps=_br_string_list(body.get("repro_steps"), "repro_steps"),
                    error_text=str(body.get("error_text", "")),
                    category=str(body.get("category", "")),
                    lanes=_br_string_list(body.get("lanes"), "lanes"),
                    tools=_br_string_list(body.get("tools"), "tools"),
                    models=_br_string_list(body.get("models"), "models"),
                    log_sources=[s if isinstance(s, dict) else {} for s in log_sources],
                    destination_repo=str(body.get("destination_repo", "")),
                    title=str(body.get("title", "")),
                )
                return apply_runtime_headers(json_response(200, {
                    "ok": True,
                    "report_id": draft.report_id,
                    "fingerprint": draft.fingerprint,
                    "draft": draft.to_dict(),
                }), runtime)

            if normalized_path == "/api/bug-report/update":
                # Edit draft fields after creation. The service CLEARS the consent
                # manifest: an approval binds the exact bytes of a payload that an
                # edit just destroyed, so submission demands fresh approval.
                unknown_br = set(body.keys()) - {"report_id", "expected", "actual", "repro_steps", "title"}
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                report_id = _br_report_id()
                updated_draft = _br_service().update_draft(
                    report_id,
                    expected=body.get("expected"),
                    actual=body.get("actual"),
                    repro_steps=_br_string_list(body.get("repro_steps"), "repro_steps") or None,
                    title=body.get("title"),
                )
                return apply_runtime_headers(json_response(200, {
                    "ok": True,
                    "report_id": updated_draft.report_id,
                    "fingerprint": updated_draft.fingerprint,
                    "consent_cleared": updated_draft.consent is None,
                    "draft": updated_draft.to_dict(),
                }), runtime)

            if normalized_path == "/api/bug-report/preview":
                unknown_br = set(body.keys()) - {"report_id", "remove_fields", "remove_attachments"}
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                report_id = _br_report_id()
                preview = _br_service().preview(
                    report_id,
                    remove_fields=_br_string_list(body.get("remove_fields"), "remove_fields"),
                    remove_attachments=_br_string_list(body.get("remove_attachments"), "remove_attachments"),
                )
                return apply_runtime_headers(json_response(200, {
                    "ok": True,
                    "report_id": preview.report_id,
                    "payload_sha256": preview.payload_sha256,
                    "total_bytes": preview.total_bytes,
                    "issue": preview.issue,
                    "attachments": [p.to_dict() for p in preview.attachments],
                    "fields_included": list(preview.fields_included),
                }), runtime)

            if normalized_path == "/api/bug-report/approve":
                unknown_br = set(body.keys()) - {
                    "report_id", "payload_sha256", "remove_fields", "remove_attachments", "confirm",
                }
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                report_id = _br_report_id()
                payload_sha = body.get("payload_sha256")
                if not isinstance(payload_sha, str) or not payload_sha.strip():
                    raise ValueError("missing payload_sha256")
                confirm = body.get("confirm")
                if confirm is not True:
                    return apply_runtime_headers(
                        json_response(400, {"error": "approval requires confirm: true"}), runtime
                    )
                consent = _br_service().approve(
                    report_id,
                    payload_sha256=payload_sha.strip(),
                    remove_fields=_br_string_list(body.get("remove_fields"), "remove_fields"),
                    remove_attachments=_br_string_list(body.get("remove_attachments"), "remove_attachments"),
                    confirm=True,
                )
                return apply_runtime_headers(json_response(200, {"ok": True, "consent": consent.to_dict()}), runtime)

            if normalized_path == "/api/bug-report/submit":
                unknown_br = set(body.keys()) - {"report_id"}
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                report_id = _br_report_id()
                result = _br_service().submit(report_id)
                payload = {
                    "status": result.status,
                    "issue_url": result.issue_url,
                    "issue_number": result.issue_number,
                    "duplicate_of": result.duplicate_of,
                    "detail": result.detail,
                    # A local sanitized copy is ALWAYS an alternative when the network
                    # path is the blocker -- the UI offers it from this fact, so a user
                    # without GitHub access never loses their report.
                    "local_export_available": result.status == "failed",
                }
                status = _submit_http_status(result)
                if status != 200:
                    submit_message = ""
                    if result.failure_code == "credential_unavailable":
                        submit_message = (
                            "There is no GitHub credential available on this machine, so the report was not sent. "
                            "Your sanitized draft is safe locally; you can save a local copy or add a credential and retry."
                        )
                    elif result.failure_code == "upstream_status" and result.upstream_status == 403:
                        submit_message = (
                            "GitHub refused access to that repository, so the report was not sent. A private repository "
                            "needs a credential with access to it; your sanitized draft is safe locally and can be saved as a local copy."
                        )
                    payload = diagnostics.with_diagnostic(
                        payload,
                        diagnostics.diagnostic_envelope(
                            _submit_condition(result),
                            message=submit_message,
                            detail=result.detail,
                            correlation_id=_br_correlation_id(),
                            http_status=status,
                            upstream_status=result.upstream_status,
                        ),
                    )
                return apply_runtime_headers(json_response(status, payload), runtime)

            if normalized_path == "/api/bug-report/export":
                unknown_br = set(body.keys()) - {"report_id", "remove_fields", "remove_attachments"}
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                report_id = _br_report_id()
                exported = _br_service().export(
                    report_id,
                    remove_fields=_br_string_list(body.get("remove_fields"), "remove_fields"),
                    remove_attachments=_br_string_list(body.get("remove_attachments"), "remove_attachments"),
                )
                return apply_runtime_headers(json_response(200, {
                    "ok": True,
                    "report_id": exported["report_id"],
                    "path": exported["path"],
                    "payload_sha256": exported["payload_sha256"],
                    "total_bytes": exported["total_bytes"],
                }), runtime)

            if normalized_path == "/api/bug-report/revoke":
                unknown_br = set(body.keys()) - {"report_id"}
                if unknown_br:
                    return apply_runtime_headers(
                        json_response(400, {"error": f"unknown fields: {sorted(unknown_br)}"}), runtime
                    )
                report_id = _br_report_id()
                cleared = _br_service().revoke(report_id)
                return apply_runtime_headers(json_response(200, {
                    "ok": True,
                    "report_id": cleared.report_id,
                    "consent": cleared.consent.to_dict() if cleared.consent is not None else None,
                }), runtime)

            return apply_runtime_headers(json_response(404, {"error": "unknown bug-report endpoint"}), runtime)
        except ValueError as _br_exc:
            return apply_runtime_headers(json_response(400, {"error": str(_br_exc)}), runtime)
        except Exception as _br_exc:
            from core.bug_report.schema import ApprovalMismatchError, DraftNotFoundError

            if isinstance(_br_exc, (DraftNotFoundError,)):
                return apply_runtime_headers(json_response(404, {"error": str(_br_exc)}), runtime)
            if isinstance(_br_exc, (ApprovalMismatchError,)):
                return apply_runtime_headers(json_response(409, {"error": str(_br_exc)}), runtime)
            return apply_runtime_headers(json_response(400, {"error": f"{type(_br_exc).__name__}: {_br_exc}"}), runtime)

    # ---- First-Run Pact + provider choice: one POST = one registered command (S-P6) ----
    if normalized_path.startswith("/api/onboarding/") or normalized_path.startswith("/api/intake/"):
        from core.web.api import onboarding_endpoints as _first_run_api

        return _first_run_api.handle_pact_post(normalized_path, body, headers, client_host, runtime)

    if normalized_path.startswith("/api/profile/"):
        # Operator Profile (P1) write surface. Owner-local + the standard POST guard suite. Every
        # action goes through the ONE authority (core.operator_profile): explicit, auditable,
        # reversible, CAS-guarded (409 on a stale revision). Nothing here grants permission.
        from core.web.api.runtime import host_header_allowed as _pf_host_allowed

        def _pf_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        _pf_ct = _pf_header("content-type").lower()
        if _pf_ct and "json" not in _pf_ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        _pf_origin = _pf_header("origin").strip()
        if _pf_origin and not _pf_host_allowed(_pf_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import operator_profile as _op

        _principal = _op.principal_for_request({"_owner_local": True, "surface": "web"})
        action = normalized_path[len("/api/profile/"):]

        def _reply(change, status: int = 200):
            ok = change.kind not in {"refused_secret", "refused_sensitive", "refused_privacy", "refused_binding", "refused_invalid", "no_principal", "paused"}
            if not ok and status == 200:
                status = 400
            return apply_runtime_headers(json_response(status, {
                "ok": ok,
                "change": change.as_dict(),
                "item": change.item.as_dict() if change.item else None,
            }), runtime)

        def _str(key: str, default: str = "") -> str:
            value = body.get(key, default)
            return str(value if value is not None else "").strip()

        def _rev():
            value = body.get("expected_revision")
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("expected_revision must be an integer")
            return int(value)

        try:
            if action == "remember":
                return _reply(_op.remember(_principal, _str("category"), body.get("value"), scope=_str("scope", "global") or "global",
                                           scope_key=_str("scope_key"), session_id=_str("session_id"), origin="explicit",
                                           actor="operator", replace=bool(body.get("replace", False))))
            if action == "item":
                expected = _rev()
                if expected is None:
                    return apply_runtime_headers(json_response(400, {"ok": False, "error": "expected_revision is required for an edit"}), runtime)
                return _reply(_op.edit_item(_str("item_id"), body.get("value"), expected_revision=expected, actor="operator"))
            if action == "forget":
                return _reply(_op.forget_item(_str("item_id"), expected_revision=_rev(), actor="operator"))
            if action == "scope":
                return _reply(_op.move_scope(_str("item_id"), _str("scope"), scope_key=_str("scope_key"), expected_revision=_rev(), actor="operator"))
            if action == "candidate":
                return _reply(_op.decide_candidate(_str("candidate_id"), _str("action"), value=body.get("value"),
                                                   scope=_str("scope", "global") or "global", scope_key=_str("scope_key"), actor="operator"))
            if action == "resolve":
                return _reply(_op.resolve_conflict(_str("candidate_id"), _str("action"), scope=_str("scope"), scope_key=_str("scope_key"), actor="operator"))
            if action == "restore":
                return _reply(_op.restore_previous(_str("item_id"), actor="operator"))
            if action == "pause":
                paused = body.get("paused")
                if not isinstance(paused, bool):
                    return apply_runtime_headers(json_response(400, {"error": "paused must be a boolean"}), runtime)
                _op.set_paused(_principal, paused)
                return apply_runtime_headers(json_response(200, {"ok": True, "paused": _op.is_paused(_principal)}), runtime)
        except _op.RevisionConflict as exc:
            return apply_runtime_headers(json_response(409, {"ok": False, "error": "revision_conflict", "detail": str(exc)}), runtime)
        except ValueError as exc:
            return apply_runtime_headers(json_response(400, {"ok": False, "error": str(exc)}), runtime)
        return apply_runtime_headers(json_response(404, {"ok": False, "error": "unknown profile action"}), runtime)

    if normalized_path == "/api/settings/prefs":
        # GENERATED_ADAPTER: the command registry owns this action (settings.prefs.set);
        # the verbatim legacy implementation lives in core.web.api.registry_authorities.
        from core.command_registry.legacy import forward as _cr_forward

        _pr_status, _pr_payload = _cr_forward("settings.prefs.set", {"prefs": dict(body)}, client_host=client_host, headers=headers)
        if _pr_status == 415 or _pr_status == 413:
            return apply_runtime_headers(json_response(_pr_status, {"error": "request rejected by transport guards"}), runtime)
        return apply_runtime_headers(json_response(_pr_status, _pr_payload), runtime)

    if normalized_path == "/api/contacts/create":
        # OPERATOR: save a contact the owner typed in Contacts. contacts.book.create owns the action (owner-local, JSON,
        # same origin, bounded body); the model's contacts.save tool is a separate path bounded by request provenance.
        from core.command_registry.legacy import forward as _cr_forward

        _cc_status, _cc_payload = _cr_forward("contacts.book.create", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cc_status, _cc_payload), runtime)

    if normalized_path == "/api/contacts/update":
        # OPERATOR: the owner's edit of one contact, bound to the revision the owner was shown.
        from core.command_registry.legacy import forward as _cr_forward

        _cu_status, _cu_payload = _cr_forward("contacts.book.update", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cu_status, _cu_payload), runtime)

    if normalized_path == "/api/contacts/delete":
        # OPERATOR: delete one contact after the owner confirmed; leaves a tombstone, never rewrites other owners' receipts.
        from core.command_registry.legacy import forward as _cr_forward

        _cx_status, _cx_payload = _cr_forward("contacts.book.delete", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cx_status, _cx_payload), runtime)


    if normalized_path == "/api/email/recovery/acknowledge":
        # OPERATOR: release the draft store's send hold for the exact current recovery generation.
        # email.recovery.acknowledge owns the action (owner-local, JSON, same-origin, confirmed,
        # generation-bound; the model has no tool that reaches it). The answer is the store re-read.
        from core.command_registry.legacy import forward as _cr_forward

        _ea_status, _ea_payload = _cr_forward("email.recovery.acknowledge", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ea_status, _ea_payload), runtime)
    if normalized_path == "/api/email/recovery/check":
        # OPERATOR: reconcile one held Message-ID against its account's sent view and record the outcome.
        # email.recovery.check owns the action; nothing is resent and the hold is never released here.
        from core.command_registry.legacy import forward as _cr_forward

        _ec_status, _ec_payload = _cr_forward("email.recovery.check", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ec_status, _ec_payload), runtime)

    if normalized_path == "/api/contacts/suggestions/accept":
        # OPERATOR: the owner confirms a destination that came from untrusted content; only this door applies it.
        from core.command_registry.legacy import forward as _cr_forward

        _ca_status, _ca_payload = _cr_forward("contacts.book.suggestion.accept", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ca_status, _ca_payload), runtime)

    if normalized_path == "/api/contacts/suggestions/dismiss":
        # OPERATOR: the owner dismisses a suggested destination.
        from core.command_registry.legacy import forward as _cr_forward

        _cn_status, _cn_payload = _cr_forward("contacts.book.suggestion.dismiss", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cn_status, _cn_payload), runtime)

    if normalized_path == "/api/contacts/import/connect":
        # OPERATOR: connect one import source after the owner ticked consent; reads nothing yet and never writes the source.
        from core.command_registry.legacy import forward as _cr_forward

        _ic_status, _ic_payload = _cr_forward("contacts.import.connect", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ic_status, _ic_payload), runtime)

    if normalized_path == "/api/contacts/import/preview":
        # OPERATOR: read the source once into a preview run; saves no contact. A failed read returns its recovery step.
        from core.command_registry.legacy import forward as _cr_forward

        _ip_status, _ip_payload = _cr_forward("contacts.import.preview", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ip_status, _ip_payload), runtime)

    if normalized_path == "/api/contacts/import/apply":
        # OPERATOR: import only the preview items the owner selected; entries arrive as imported and not verified.
        from core.command_registry.legacy import forward as _cr_forward

        _ia_status, _ia_payload = _cr_forward("contacts.import.apply", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ia_status, _ia_payload), runtime)

    if normalized_path == "/api/contacts/import/remove":
        # OPERATOR: remove an import source; entries the owner edited or confirmed are kept either way.
        from core.command_registry.legacy import forward as _cr_forward

        _ir_status, _ir_payload = _cr_forward("contacts.import.remove", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ir_status, _ir_payload), runtime)

    if normalized_path == "/api/contacts/operations/confirm":
        # OPERATOR: the owner confirms one reviewed change with the PIN or password; the secret never leaves the door.
        from core.command_registry.legacy import forward as _cr_forward

        _cfc_status, _cfc_payload = _cr_forward("contacts.operations.confirm", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cfc_status, _cfc_payload), runtime)

    if normalized_path == "/api/contacts/operations/commit":
        # OPERATOR: save a change whose confirmation was accepted but not yet saved.
        from core.command_registry.legacy import forward as _cr_forward

        _cfm_status, _cfm_payload = _cr_forward("contacts.operations.commit", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cfm_status, _cfm_payload), runtime)

    if normalized_path == "/api/contacts/operations/cancel":
        # OPERATOR: cancel a pending change; saved contacts stay as they are.
        from core.command_registry.legacy import forward as _cr_forward

        _cfx_status, _cfx_payload = _cr_forward("contacts.operations.cancel", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cfx_status, _cfx_payload), runtime)

    if normalized_path == "/api/contacts/credential/enroll":
        # OPERATOR: set the PIN or password after the operating system's own confirmation.
        from core.command_registry.legacy import forward as _cr_forward

        _cce_status, _cce_payload = _cr_forward("contacts.credential.enroll", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_cce_status, _cce_payload), runtime)

    if normalized_path == "/api/contacts/credential/change":
        # OPERATOR: change the PIN or password with the current one and the system confirmation.
        from core.command_registry.legacy import forward as _cr_forward

        _ccc_status, _ccc_payload = _cr_forward("contacts.credential.change", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ccc_status, _ccc_payload), runtime)

    if normalized_path == "/api/contacts/credential/reset":
        # OPERATOR: reset a forgotten PIN or password with the recovery code and the system confirmation.
        from core.command_registry.legacy import forward as _cr_forward

        _ccr_status, _ccr_payload = _cr_forward("contacts.credential.reset", {"payload": dict(body)}, client_host=client_host, headers=headers)
        return apply_runtime_headers(json_response(_ccr_status, _ccr_payload), runtime)

    if normalized_path == "/api/chat/attachments/remove":
        # Drop one staged-but-unsent attachment of THIS chat. Owner-local, same POST guards as cancel.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core import chat_attachments
        from core.web.api.runtime import host_header_allowed

        rm_ct = _header_value(headers, "content-type").lower()
        if rm_ct and "json" not in rm_ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        rm_origin = _header_value(headers, "origin").strip()
        if rm_origin and not host_header_allowed(rm_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        unknown_rm = set(body.keys()) - {"session_id", "attachment_id"}
        if unknown_rm:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown_rm)}"}), runtime)
        rm_session = str(body.get("session_id") or "").strip()
        rm_id = str(body.get("attachment_id") or "").strip()
        try:
            removed = chat_attachments.remove_staged(session_id=rm_session, attachment_id=rm_id)
            # Not a draft: a SENT document is erased by its owner -- bytes gone, identity kept.
            erased = False if removed else chat_attachments.erase_document(session_id=rm_session, attachment_id=rm_id)
        except chat_attachments.AttachmentRefused as exc:
            return apply_runtime_headers(json_response(exc.http_status, exc.to_dict()), runtime)
        if removed:
            _attachment_event(rm_session, event_type="attachment_removed", message="Removed an attachment before sending", details={"attachment_id": rm_id})
        elif erased:
            _attachment_event(rm_session, event_type="attachment_erased", message="Erased a pasted document from this chat", details={"attachment_id": rm_id})
        return apply_runtime_headers(json_response(200, {"ok": True, "removed": bool(removed or erased)}), runtime)

    if normalized_path in ("/api/session/bundle/preview", "/api/session/bundle/export", "/api/session/bundle/import", "/api/session/bundle/inspect-import"):
        # Session portability (P1): preview / export / import ONE conversation as a typed,
        # integrity-checked bundle. Owner-local + the standard POST guard suite. Every action
        # goes through the ONE seam (core.session_portability.api); a passphrase is accepted
        # for optional authenticated encryption and is NEVER echoed back.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.web.api.runtime import host_header_allowed as _sp_host_allowed

        sp_ct = _header_value(headers, "content-type").lower()
        if sp_ct and "json" not in sp_ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        sp_origin = _header_value(headers, "origin").strip()
        if sp_origin and not _sp_host_allowed(sp_origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        sp_unknown = set(body.keys()) - {"session_id", "path", "passphrase", "confirm_untrusted"}
        if sp_unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(sp_unknown)}"}), runtime)
        from core import runtime_paths
        from core.session_portability import api as session_portability

        sp_passphrase = str(body.get("passphrase") or "")
        try:
            if normalized_path == "/api/session/bundle/preview":
                result = session_portability.preview_export(str(body.get("session_id") or "").strip())
            elif normalized_path == "/api/session/bundle/inspect-import":
                result = session_portability.preview_import(
                    str(body.get("path") or "").strip(), passphrase=sp_passphrase
                )
            elif normalized_path == "/api/session/bundle/export":
                sp_session = str(body.get("session_id") or "").strip()
                sp_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                sp_dir = runtime_paths.data_path("session_bundles")
                sp_out = sp_dir / f"{sp_session.replace(':', '_') or 'session'}-{sp_stamp}.voolsession"
                result = session_portability.export_session(sp_session, sp_out, passphrase=sp_passphrase)
            else:
                result = session_portability.import_bundle(
                    str(body.get("path") or "").strip(),
                    passphrase=sp_passphrase,
                    confirm_untrusted=bool(body.get("confirm_untrusted")),
                )
            return apply_runtime_headers(json_response(200, result), runtime)
        except session_portability.PortabilityRefused as exc:
            return apply_runtime_headers(
                json_response(200, {"ok": False, "code": exc.code, "error": exc.message}),
                runtime,
            )

    if normalized_path == "/api/chat/cancel":
        # Cancel one IN-FLIGHT turn (owner-local, loopback). Keyed by (session_id, turn_id) so it
        # never touches a queued or overlapping turn. Idempotent: cancelled | not_found.
        # The comment above is a gate, not a wish: under the documented
        # non-loopback threat model (core/request_trust) a remote peer could
        # otherwise cancel any in-flight turn.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        from core.web.api.runtime import host_header_allowed
        from core.web.api.turn_cancel import request_cancel

        def _ph_cancel(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ct = _ph_cancel("content-type").lower()
        if ct and "json" not in ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _ph_cancel("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        unknown = set(body.keys()) - {"session_id", "turn_id"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        cancel_session = str(body.get("session_id") or "").strip()
        cancel_turn = str(body.get("turn_id") or "").strip()
        if not cancel_session or not cancel_turn:
            return apply_runtime_headers(json_response(400, {"error": "missing session_id or turn_id"}), runtime)
        state = request_cancel(cancel_session, cancel_turn)
        return apply_runtime_headers(json_response(200, {"ok": True, "state": state}), runtime)

    if normalized_path == "/api/chat/pin":
        # Pin or unpin one message to a chat (owner-local snapshot). Loopback + the same POST guards.
        # Enforced, not assumed: pin/unpin into any session must not be
        # reachable by a remote peer on a non-loopback bind.
        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)
        import json as _json

        from core import message_pins
        from core.web.api.runtime import host_header_allowed

        def _ph(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ct = _ph("content-type").lower()
        if ct and "json" not in ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _ph("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        unknown = set(body.keys()) - {"session_id", "role", "text", "ts", "pin_id", "delete"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid session_id"}), runtime)
        session_id = session_id.strip()
        if body.get("delete") is True:
            pin_id = body.get("pin_id")
            if not isinstance(pin_id, str) or not pin_id.strip():
                return apply_runtime_headers(json_response(400, {"error": "pin_id required to unpin"}), runtime)
            removed = message_pins.unpin_message(session_id, pin_id.strip())
            # A8: every pin echo through the product boundary is the gated
            # representation — WITHHELD/ERASED pins never round-trip.
            return apply_runtime_headers(json_response(200, {"ok": removed, "pins": message_pins.list_servable_pins(session_id)}), runtime)
        role = body.get("role", "assistant")
        text = body.get("text", "")
        if role not in ("assistant", "user"):
            return apply_runtime_headers(json_response(400, {"error": "role must be 'assistant' or 'user'"}), runtime)
        if not isinstance(text, str) or not text.strip():
            return apply_runtime_headers(json_response(400, {"error": "text required"}), runtime)
        if len(text) > 40000:
            return apply_runtime_headers(json_response(400, {"error": "text too long"}), runtime)
        ts = body.get("ts", "")
        ok, pin = message_pins.pin_message(session_id, role, text, ts=ts if isinstance(ts, str) else "")
        # A8 pass-002 (T01): the POST echo carries the privacy-gated listing —
        # a WITHHELD/ERASED payload must not bounce straight back out.
        servable_ids = {p.get("id") for p in message_pins.list_servable_pins(session_id)}
        echoed_pin = pin if ok and pin.get("id") in servable_ids else {}
        return apply_runtime_headers(json_response(200 if ok else 400, {"ok": ok, "pin": echoed_pin, "pins": message_pins.list_servable_pins(session_id)}), runtime)

    if normalized_path in {"/api/model-radar/dismiss", "/api/model-radar/viewed", "/api/model-radar/preferences", "/api/model-radar/try-once"}:
        # One family with the same guard suite as the other owner-local
        # state-changing POSTs. Owner decision FIRST — derived from the real TCP
        # peer (never a body field) and placed ahead of shape work so a non-owner
        # learns nothing about pricing posture before being refused: loopback peer,
        # JSON content-type, same-origin (or no) Origin, bounded body, exact shape.
        # None of these switch models: dismiss/viewed touch notification state,
        # preferences shape future notifications, try-once validates + receipts a
        # ONE-TURN trial whose actual turn still runs the ordinary /api/chat gates.
        import json as _json

        from core import model_radar_service
        from core.model_radar import RadarPreferences
        from core.web.api.runtime import host_header_allowed

        if not is_loopback_host(client_host):
            return apply_runtime_headers(json_response(403, {"ok": False, "error": "owner_local_required"}), runtime)

        def _rh(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        ct = _rh("content-type").lower()
        if ct and "json" not in ct:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _rh("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)
        if not isinstance(body, dict):
            return apply_runtime_headers(json_response(400, {"error": "invalid body"}), runtime)

        if normalized_path == "/api/model-radar/dismiss":
            unknown = set(body.keys()) - {"fingerprint"}
            if unknown:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
            fingerprint = body.get("fingerprint")
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                return apply_runtime_headers(json_response(400, {"error": "missing or invalid fingerprint"}), runtime)
            result = model_radar_service.dismiss(fingerprint.strip(), origin="chat-shell")
            return apply_runtime_headers(
                json_response(200 if result.ok else 404, {"ok": result.ok, "error": result.error or None}), runtime
            )

        if normalized_path == "/api/model-radar/viewed":
            unknown = set(body.keys()) - {"fingerprints"}
            if unknown:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
            fingerprints = body.get("fingerprints")
            if not isinstance(fingerprints, list) or not all(isinstance(f, str) for f in fingerprints):
                return apply_runtime_headers(json_response(400, {"error": "fingerprints must be a list of strings"}), runtime)
            marked = model_radar_service.mark_viewed(fingerprints[:100])
            return apply_runtime_headers(json_response(200, {"ok": True, "marked": marked}), runtime)

        if normalized_path == "/api/model-radar/preferences":
            unknown = set(body.keys()) - {"enabled", "providers", "lane", "required_capabilities", "min_interval_hours"}
            if unknown:
                return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
            try:
                desired = RadarPreferences.from_dict(body)
            except (TypeError, ValueError) as exc:
                return apply_runtime_headers(json_response(400, {"error": f"invalid preferences: {exc}"}), runtime)
            stored = model_radar_service.save_preferences(desired)
            return apply_runtime_headers(json_response(200, {"ok": True, "preferences": stored.to_dict()}), runtime)

        # /api/model-radar/try-once
        unknown = set(body.keys()) - {"fingerprint", "session_id"}
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        fingerprint = body.get("fingerprint")
        session_id = body.get("session_id")
        if not isinstance(fingerprint, str) or not fingerprint.strip() or not isinstance(session_id, str) or not session_id.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid fingerprint/session_id"}), runtime)
        result = model_radar_service.try_once(fingerprint.strip(), session_id=session_id.strip()[:80])
        payload = {"ok": result.ok, "error": result.error or None}
        if result.ok:
            payload.update(
                token=result.token,
                provider_id=result.provider_id,
                model_id=result.model_id,
                display_name=result.display_name,
                expires_at=result.expires_at,
            )
        return apply_runtime_headers(json_response(200 if result.ok else 409, payload), runtime)

    if normalized_path == "/api/chat/session":
        # Sidebar management: set a custom title and/or archive flag for one chat thread. Metadata
        # only — never touches the transcript. Every mutation is bound to the real loopback peer;
        # an allowed Host header alone is not proof of local ownership when the API is exposed.
        # This state-changing POST additionally validates content-type, Origin, body size, and the
        # exact request shape.
        import json as _json

        from core import project_store
        from core.persistent_memory import (
            delete_conversation_session,
            load_session_meta,
            recent_conversation_events,
            set_session_meta,
        )
        from core.web.api.runtime import host_header_allowed

        if not is_loopback_host(client_host):
            return apply_runtime_headers(
                json_response(
                    403,
                    {
                        "error": (
                            "chat lifecycle changes require a local session"
                        )
                    },
                ),
                runtime,
            )

        def _req_header(name: str) -> str:
            for key, value in (headers or {}).items():
                if str(key).lower() == name:
                    return str(value or "")
            return ""

        content_type = _req_header("content-type").lower()
        if content_type and "json" not in content_type:
            return apply_runtime_headers(json_response(415, {"error": "content-type must be application/json"}), runtime)
        origin = _req_header("origin").strip()
        if origin and not host_header_allowed(origin.split("://", 1)[-1]):
            return apply_runtime_headers(json_response(403, {"error": "cross-origin request not allowed"}), runtime)
        try:
            if len(_json.dumps(body)) > 65536:
                return apply_runtime_headers(json_response(413, {"error": "request body too large"}), runtime)
        except (TypeError, ValueError):
            return apply_runtime_headers(json_response(400, {"error": "unserializable body"}), runtime)

        unknown = set(body.keys()) - {
            "session_id",
            "title",
            "archived",
            "emoji",
            "color",
            "delete",
            "operation",
            "new_session_id",
            "through_turn",
            "project_id",
        }
        if unknown:
            return apply_runtime_headers(json_response(400, {"error": f"unknown fields: {sorted(unknown)}"}), runtime)
        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return apply_runtime_headers(json_response(400, {"error": "missing or invalid session_id"}), runtime)
        session_id = session_id.strip()
        if len(session_id) > 200 or any(ord(ch) < 32 for ch in session_id):
            return apply_runtime_headers(json_response(400, {"error": "invalid session_id"}), runtime)

        if body.get("delete") is True and body.get("operation"):
            return apply_runtime_headers(
                json_response(400, {"error": "delete cannot be combined with operation"}),
                runtime,
            )

        from core.context_namespace import (
            clone_chat_namespace,
            ensure_chat_namespace,
            grant_context_import,
            load_chat_namespace,
            set_chat_namespace_project,
            set_chat_namespace_state,
        )

        session_meta = load_session_meta()
        existing_meta = dict(session_meta.get(session_id) or {})
        has_existing_session = bool(
            session_id in session_meta
            or recent_conversation_events(session_id, limit=1)
        )
        operation = str(body.get("operation") or "").strip().lower()
        if operation:
            allowed_operation_fields = {
                "create": {"session_id", "operation", "project_id"},
                "assign_project": {"session_id", "operation", "project_id"},
                "archive": {"session_id", "operation"},
                "restore": {"session_id", "operation"},
                "delete": {"session_id", "operation"},
                "duplicate": {"session_id", "operation", "new_session_id"},
                "branch": {
                    "session_id",
                    "operation",
                    "new_session_id",
                    "through_turn",
                },
            }
            if operation not in allowed_operation_fields:
                return apply_runtime_headers(
                    json_response(400, {"error": "unsupported operation"}),
                    runtime,
                )
            incompatible = set(body) - allowed_operation_fields[operation]
            if incompatible:
                return apply_runtime_headers(
                    json_response(
                        400,
                        {
                            "error": (
                                f"fields not allowed for {operation}: "
                                f"{sorted(incompatible)}"
                            )
                        },
                    ),
                    runtime,
                )

            if operation == "create":
                if load_chat_namespace(session_id) is not None or has_existing_session:
                    return apply_runtime_headers(
                        json_response(409, {"error": "session already exists"}),
                        runtime,
                    )
                project_id = str(body.get("project_id") or "").strip()
                if (
                    len(project_id) > 200
                    or any(ord(char) < 32 for char in project_id)
                ):
                    return apply_runtime_headers(
                        json_response(400, {"error": "invalid project_id"}),
                        runtime,
                    )
                if project_id and project_store.get_project(project_id) is None:
                    return apply_runtime_headers(
                        json_response(404, {"error": "unknown project_id"}),
                        runtime,
                    )
                namespace = ensure_chat_namespace(
                    session_id,
                    project_id=project_id,
                    grant_confirmed_profile=True,
                )
                if project_id:
                    grant_context_import(
                        session_id,
                        scope="project",
                        source_id=f"project:{project_id}",
                        source_project_id=project_id,
                    )
                    set_session_meta(session_id, project_id=project_id)
                return apply_runtime_headers(
                    json_response(
                        201,
                        {
                            "session_id": namespace.chat_id,
                            "lifecycle_state": namespace.lifecycle_state,
                            "project_id": namespace.project_id,
                        },
                    ),
                    runtime,
                )

            namespace = load_chat_namespace(session_id)
            if namespace is None:
                if not has_existing_session:
                    return apply_runtime_headers(
                        json_response(404, {"error": "session not found"}),
                        runtime,
                    )
                namespace = ensure_chat_namespace(
                    session_id,
                    project_id=str(existing_meta.get("project_id") or "").strip(),
                    grant_confirmed_profile=True,
                )

            if operation == "assign_project":
                project_id = str(body.get("project_id") or "").strip()
                if (
                    len(project_id) > 200
                    or any(ord(char) < 32 for char in project_id)
                ):
                    return apply_runtime_headers(
                        json_response(
                            400,
                            {"error": "invalid project_id"},
                        ),
                        runtime,
                    )
                if project_id and project_store.get_project(project_id) is None:
                    return apply_runtime_headers(
                        json_response(404, {"error": "unknown project_id"}),
                        runtime,
                    )
                try:
                    updated_namespace = set_chat_namespace_project(
                        session_id,
                        project_id,
                    )
                except ValueError as exc:
                    return apply_runtime_headers(
                        json_response(409, {"error": str(exc)}),
                        runtime,
                    )
                if project_id:
                    grant_context_import(
                        session_id,
                        scope="project",
                        source_id=f"project:{project_id}",
                        source_project_id=project_id,
                    )
                set_session_meta(session_id, project_id=project_id)
                return apply_runtime_headers(
                    json_response(
                        200,
                        {
                            "session_id": session_id,
                            "project_id": updated_namespace.project_id,
                        },
                    ),
                    runtime,
                )

            if operation in {"archive", "restore", "delete"}:
                state = {
                    "archive": "archived",
                    "restore": "active",
                    "delete": "deleted",
                }[operation]
                try:
                    updated_namespace = set_chat_namespace_state(
                        session_id,
                        state,
                    )
                except ValueError as exc:
                    return apply_runtime_headers(
                        json_response(409, {"error": str(exc)}),
                        runtime,
                    )
                if operation in {"archive", "restore"}:
                    set_session_meta(
                        session_id,
                        archived=operation == "archive",
                    )
                else:
                    deleted = delete_conversation_session(session_id)
                    _erase_session_attachments(session_id)
                    from core import message_pins

                    message_pins.drop_session_pins(session_id)
                    # A deleted chat takes its standing authority with it (same law as the
                    # chat-delete route): grants bound to this session are revoked server-side,
                    # with the durable-mirror failure surfaced as a warning instead of a crash.
                    authority_warning = ""
                    try:
                        from core.mode_permission_policy import (
                            revoke_chat_workspace_authority,
                            revoke_session_bypass_grants,
                        )

                        revoke_session_bypass_grants(session_id)
                        revoke_chat_workspace_authority(session_id)
                    except Exception as exc:
                        authority_warning = (
                            "chat deleted, but its bypass revocation could not be recorded durably "
                            f"({exc}); if the app restarts before storage is fixed, revoke bypass again"
                        )
                    return apply_runtime_headers(
                        json_response(
                            200,
                            {
                                "session_id": session_id,
                                "deleted": bool(deleted),
                                "lifecycle_state": (
                                    updated_namespace.lifecycle_state
                                ),
                                **({"warning": authority_warning} if authority_warning else {}),
                            },
                        ),
                        runtime,
                    )
                return apply_runtime_headers(
                    json_response(
                        200,
                        {
                            "session_id": session_id,
                            "lifecycle_state": (
                                updated_namespace.lifecycle_state
                            ),
                        },
                    ),
                    runtime,
                )

            new_session_id = body.get("new_session_id")
            if (
                not isinstance(new_session_id, str)
                or not new_session_id.strip()
            ):
                return apply_runtime_headers(
                    json_response(
                        400,
                        {"error": "missing or invalid new_session_id"},
                    ),
                    runtime,
                )
            new_session_id = new_session_id.strip()
            if (
                len(new_session_id) > 200
                or any(ord(char) < 32 for char in new_session_id)
            ):
                return apply_runtime_headers(
                    json_response(400, {"error": "invalid new_session_id"}),
                    runtime,
                )
            through_turn = None
            if operation == "branch":
                candidate_turn = body.get("through_turn")
                if (
                    isinstance(candidate_turn, bool)
                    or not isinstance(candidate_turn, int)
                    or candidate_turn < 0
                ):
                    return apply_runtime_headers(
                        json_response(
                            400,
                            {"error": "branch requires through_turn >= 0"},
                        ),
                        runtime,
                    )
                through_turn = candidate_turn
            cloned = clone_chat_namespace(
                session_id,
                new_session_id,
                through_turn=through_turn,
            )
            if cloned.project_id:
                set_session_meta(
                    cloned.chat_id,
                    project_id=cloned.project_id,
                )
            from core.active_context_capsule import rebuild_shadow_capsule

            capsule = rebuild_shadow_capsule(cloned.chat_id)
            return apply_runtime_headers(
                json_response(
                    201,
                    {
                        "session_id": cloned.chat_id,
                        "lifecycle_state": cloned.lifecycle_state,
                        "project_id": cloned.project_id,
                        "parent_chat_id": cloned.parent_chat_id,
                        "through_turn": cloned.branch_turn,
                        "capsule_version": (
                            capsule.version_id if capsule is not None else "none"
                        ),
                    },
                ),
                runtime,
            )

        # Permanent delete preserves a namespace tombstone while dropping transcript, learned
        # memory, summaries, metadata, and pins. Missing sessions retain the historical idempotent
        # response rather than creating a namespace solely to delete it.
        if body.get("delete") is True:
            namespace = load_chat_namespace(session_id)
            if namespace is None and has_existing_session:
                namespace = ensure_chat_namespace(
                    session_id,
                    project_id=str(existing_meta.get("project_id") or "").strip(),
                    grant_confirmed_profile=True,
                )
            if namespace is not None and namespace.lifecycle_state != "deleted":
                set_chat_namespace_state(session_id, "deleted")
            deleted = delete_conversation_session(session_id)
            _erase_session_attachments(session_id)
            from core import message_pins

            message_pins.drop_session_pins(session_id)
            # A deleted chat takes its standing authority with it: every bypass grant bound to
            # this session (including an until-off chat grant) and the chat's workspace approval
            # are revoked server-side, so no stale page or restored token can act for a chat that
            # no longer exists.
            authority_warning = ""
            try:
                from core.mode_permission_policy import (
                    revoke_chat_workspace_authority,
                    revoke_session_bypass_grants,
                )

                revoke_session_bypass_grants(session_id)
                revoke_chat_workspace_authority(session_id)
            except Exception as exc:
                # The in-memory revocation happened; the durable mirror write failed, so an
                # until-off grant CAN return with the profile after a restart. Deleting the chat
                # still succeeds -- the answer says what did not.
                authority_warning = (
                    "chat deleted, but its bypass revocation could not be recorded durably "
                    f"({exc}); if the app restarts before storage is fixed, revoke bypass again"
                )
            return apply_runtime_headers(
                json_response(
                    200,
                    {
                        "session_id": session_id,
                        "deleted": bool(deleted),
                        "lifecycle_state": (
                            "deleted" if namespace is not None else "missing"
                        ),
                    },
                ),
                runtime,
            )

        _missing = object()
        title = body.get("title", _missing)
        archived = body.get("archived", _missing)
        project = body.get("project_id", _missing)
        emoji = body.get("emoji", _missing)
        color = body.get("color", _missing)
        if title is _missing and archived is _missing and project is _missing and emoji is _missing and color is _missing:
            return apply_runtime_headers(json_response(400, {"error": "no supported change (title, archived, project_id, emoji, or color required)"}), runtime)
        title_arg = None
        if title is not _missing:
            if not isinstance(title, str):
                return apply_runtime_headers(json_response(400, {"error": "title must be a string"}), runtime)
            if any(ord(ch) < 32 for ch in title):  # control chars incl. newlines/tabs
                return apply_runtime_headers(json_response(400, {"error": "title contains control characters"}), runtime)
            if len(title) > 200:
                return apply_runtime_headers(json_response(400, {"error": "title too long"}), runtime)
            title_arg = title  # blank/whitespace clears the override in set_session_meta
        archived_arg = None
        if archived is not _missing:
            if not isinstance(archived, bool):
                return apply_runtime_headers(json_response(400, {"error": "archived must be a boolean"}), runtime)
            archived_arg = archived
        project_arg = None
        if project is not _missing:
            if not isinstance(project, str):
                return apply_runtime_headers(json_response(400, {"error": "project_id must be a string"}), runtime)
            project_arg = project.strip()  # blank -> unbind
            if project_arg and project_store.get_project(project_arg) is None:
                return apply_runtime_headers(json_response(404, {"error": "unknown project_id"}), runtime)
        emoji_arg = None
        if emoji is not _missing:
            if not isinstance(emoji, str):
                return apply_runtime_headers(json_response(400, {"error": "emoji must be a string"}), runtime)
            if any(ord(ch) < 32 for ch in emoji):  # control chars incl. newlines/tabs
                return apply_runtime_headers(json_response(400, {"error": "emoji contains control characters"}), runtime)
            if len(emoji) > 32:  # generous ceiling; set_session_meta trims to a couple of code points
                return apply_runtime_headers(json_response(400, {"error": "emoji too long"}), runtime)
            emoji_arg = emoji  # blank clears the marker in set_session_meta
        color_arg = None
        if color is not _missing:
            if not isinstance(color, str):
                return apply_runtime_headers(json_response(400, {"error": "color must be a string"}), runtime)
            if len(color) > 16:
                return apply_runtime_headers(json_response(400, {"error": "color too long"}), runtime)
            color_arg = color  # blank/invalid clears the accent in set_session_meta (strict #rrggbb)

        # Title/archive/emoji act on an existing chat; a project may be bound to a brand-new chat (picked
        # at New-Chat time, before its first message), so binding does not require a prior transcript.
        binding_project = project is not _missing
        if not binding_project and not has_existing_session:
            return apply_runtime_headers(json_response(404, {"error": "session not found"}), runtime)
        namespace = load_chat_namespace(session_id)
        if namespace is None:
            namespace = ensure_chat_namespace(
                session_id,
                project_id=(
                    project_arg
                    if binding_project
                    else str(existing_meta.get("project_id") or "").strip()
                ),
                grant_confirmed_profile=True,
            )
        if namespace.lifecycle_state == "deleted":
            return apply_runtime_headers(
                json_response(409, {"error": "deleted chat namespaces cannot be changed"}),
                runtime,
            )
        try:
            # Restore before assigning a project; archive after assignment. This keeps project
            # mutation restricted to active namespaces while supporting one atomic metadata POST.
            if archived_arg is False and namespace.lifecycle_state == "archived":
                namespace = set_chat_namespace_state(session_id, "active")
            if binding_project:
                namespace = set_chat_namespace_project(session_id, project_arg)
                if project_arg:
                    grant_context_import(
                        session_id,
                        scope="project",
                        source_id=f"project:{project_arg}",
                        source_project_id=project_arg,
                    )
            if archived_arg is True and namespace.lifecycle_state == "active":
                namespace = set_chat_namespace_state(session_id, "archived")
        except ValueError as exc:
            return apply_runtime_headers(
                json_response(409, {"error": str(exc)}),
                runtime,
            )
        updated = set_session_meta(session_id, title=title_arg, archived=archived_arg, project_id=project_arg, emoji=emoji_arg, color=color_arg)
        return apply_runtime_headers(json_response(200, {"session_id": session_id, "meta": updated}), runtime)

    if normalized_path in {"/api/null", "/v1/null"}:
        uri_str = str(body.get("uri") or "").strip()
        prompt = str(body.get("prompt") or body.get("task") or "").strip()
        if not uri_str:
            return apply_runtime_headers(json_response(400, {"error": "missing 'uri' field"}), runtime)
        try:
            from core.null_protocol import NullResponse, parse_null_uri
        except Exception as exc:
            return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)

        # When remote dial is opted in, resolve the .null name carried by the URI
        # (the service segment) so the quote shows the REAL recipient wallet (the
        # on-chain owner) instead of the default. A resolution miss or any error
        # leaves recipient + record unset and the path behaves as before.
        from core import policy_engine

        null_record = None
        recipient_wallet = "stub-wallet"
        # Owner resolution does a live on-chain read, so it ONLY runs when remote
        # dial is opted in — with the flag off this route stays fully local (no
        # network), byte-identical to before this feature.
        if policy_engine.null_dial_enabled():
            resolve_domain = resolve_null_domain_provider
            if resolve_domain is None:
                from core.null_resolver import resolve_null_domain as resolve_domain
            try:
                parsed_uri = parse_null_uri(uri_str)
                null_record = resolve_domain(parsed_uri.service)
                if null_record is not None and getattr(null_record, "owner", ""):
                    recipient_wallet = null_record.owner
            except Exception:
                null_record = None

        try:
            from core.null_protocol import resolve_null_request
            null_req = resolve_null_request(uri_str, recipient_wallet=recipient_wallet)
        except Exception as exc:
            return apply_runtime_headers(json_response(400, {"error": str(exc)}), runtime)
        task_text = prompt or null_req.uri.path or null_req.uri.service

        # Remote dial is opt-in and off by default. When enabled and the resolved
        # record carries a safe x402 endpoint, reach the named agent; otherwise
        # fall through to the unchanged local run.
        dial_result = None
        try:
            if policy_engine.null_dial_enabled() and null_record is not None:
                dial_fn = try_dial_provider
                if dial_fn is None:
                    from core.null_dial import try_dial as dial_fn
                dial_result = dial_fn(
                    uri_str,
                    task_text,
                    record=null_record,
                    wallet=None,
                    allow_spend=False,
                )
        except Exception:
            dial_result = None

        if dial_result is not None:
            null_resp = NullResponse(
                session_id=null_req.session_id,
                service=null_req.uri.service,
                path=null_req.uri.path,
                result=dial_result,
            )
            dial_payload: dict[str, Any] = {
                "session_id": null_resp.session_id,
                "service":    null_resp.service,
                "path":       null_resp.path,
                "result":     null_resp.result,
                "receipt_id": None,
                "zk_proof":   null_resp.zk_proof,
                "dialed":     True,
                "quote": {
                    "amount_usdc":      null_req.quote.amount_usdc,
                    "recipient_wallet": null_req.quote.recipient_wallet,
                } if null_req.quote else None,
            }
            return apply_runtime_headers(json_response(200, dial_payload), runtime)

        try:
            null_result = run_agent_provider(
                runtime,
                task_text,
                session_id=null_req.session_id,
                source_context={"surface": "null_protocol", "null_uri": uri_str, "service": null_req.uri.service},
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": safe_error_text(exc)}), runtime)
        # A7 W5 (S12 family): the local fallback serves the FINALIZED answer
        # bytes — the same committed truth every other surface consumes — and
        # the work receipt references the finalized content identity, never raw
        # unfinalized text (M18-class at transport scale).
        null_commit = (
            null_result.get("vool_response_commit")
            if isinstance(null_result.get("vool_response_commit"), dict)
            else None
        )
        if null_commit is None:
            # A-1/R-5: admit BEFORE finalize — the null fallback previously
            # finalized with sr='', evading NO_ADMISSION_ROW enforcement.
            from core.semantic.semantic_result_seam import (
                admit_semantic_result,
                reset_admission,
            )

            reset_admission()
            admit_semantic_result(dict(null_result or {}))
            from core.finalization import finalize_answer

            null_commit = finalize_answer(
                turn_id=str(null_req.session_id or ""),
                canonical_content=strip_provenance_footer_str(str(null_result.get("response") or "")),
                source_context={"surface": "null_protocol"},
                display_metadata={"route": "null_protocol_local"},
            )
        response_text = str(null_commit.get("canonical_content") or "")
        null_receipt = None
        try:
            import os

            from core.web0_work_receipt import issue_work_receipt
            null_receipt = issue_work_receipt(
                task_id=null_req.session_id,
                result=null_commit.get("content_hash") or response_text or task_text,
                worker_id=str(os.environ.get("VOOL_WORKER_ID") or "vool"),
            )
        except Exception:
            pass
        null_resp = NullResponse(
            session_id=null_req.session_id,
            service=null_req.uri.service,
            path=null_req.uri.path,
            result=response_text,
            receipt_id=null_receipt.receipt_id if null_receipt else None,
        )
        null_payload: dict[str, Any] = {
            "session_id": null_resp.session_id,
            "service":    null_resp.service,
            "path":       null_resp.path,
            "result":     null_resp.result,
            "receipt_id": null_resp.receipt_id,
            "zk_proof":   null_resp.zk_proof,
            "vool_response_commit": null_commit,
            "quote": {
                "amount_usdc":      null_req.quote.amount_usdc,
                "recipient_wallet": null_req.quote.recipient_wallet,
            } if null_req.quote else None,
        }
        return apply_runtime_headers(json_response(200, null_payload), runtime)

    if normalized_path == "/gate/unlock":
        from core.product_edition import edition_allows

        web0_ok, web0_reason = edition_allows("web0")
        if not web0_ok:
            return apply_runtime_headers(
                json_response(404, {"error": "not_found", "reason": web0_reason}), runtime
            )
        from core.web0_gated_html import VoolGateHandler, gate_cors_headers
        from core.web0_tools import web0_gate_key_store

        result = VoolGateHandler(web0_gate_key_store()).handle(body)
        status = 200 if "aes_key" in result else 403
        if result.get("error") in {"missing_fields", "invalid_wallet_pubkey", "invalid_nonce"}:
            status = 400
        return apply_runtime_headers(json_response(status, result, headers=gate_cors_headers()), runtime)

    if normalized_path in {"/api/chat", "/v1/chat/completions"}:
        messages = list(body.get("messages", []) or [])
        client_history = normalize_chat_history_provider(messages)
        user_text = extract_user_message_provider(messages)
        if not user_text:
            return apply_runtime_headers(json_response(400, {"error": "no user message found"}), runtime)

        model = body.get("model", model_name)
        stream = body.get("stream", False)
        # Served on the typed `vool_event` channel (A7 W4 leaves no other lawful carrier), so it
        # shares the /v1 guard below: extra lines would corrupt the OpenAI SSE transcript.
        include_runtime_events = bool(
            body.get("stream_runtime_events") or body.get("include_runtime_events")
        ) and not normalized_path.startswith("/v1/")
        # Typed live-status channel for the /chat UI (dedicated `vool_event` NDJSON lines).
        # Never on the OpenAI /v1 path, where extra lines would corrupt the SSE transcript.
        emit_task_events = bool(body.get("stream_task_events")) and not normalized_path.startswith("/v1/")
        # Strip caller-forgeable policy fields before deriving any request trust. Surface labels are
        # retained for routing/compatibility, but never grant owner or context privileges.
        inbound_source_context = strip_reserved_trust_keys(
            _inbound_source_context(body)
        )
        resolved_surface = (
            str(
                inbound_source_context.get("surface")
                or body.get("surface")
                or "api"
            ).strip()
            or "api"
        )
        owner_local = (
            is_loopback_host(client_host)
            and resolved_surface != "channel"
        )
        session_id = stable_openclaw_session_id_provider(
            body=body,
            history=client_history,
            headers=headers,
            # Canonical chat ids are local resume handles. A remotely reachable
            # API may create/reuse its own hashed id, but cannot select an
            # existing desktop namespace by presenting a guessed canonical id.
            allow_canonical_resume=owner_local,
        )
        history = _augment_history_from_session_log(
            client_history,
            session_id=session_id,
            user_text=user_text,
        )
        # The client sends a per-turn id it also uses to cancel; stamp it so stream_agent_with_events
        # registers a cancel Event under (session_id, turn_id) for this turn.
        inbound_source_context["price_wait_queue_id"] = str(body.get("queue_item_id") or "").strip()[:200] if owner_local else ""
        inbound_source_context["cancel_turn_id"] = str(body.get("turn_id") or "").strip()[:80]
        requested_workspace = str(
            body.get("workspace")
            or body.get("workspace_root")
            or body.get("cwd")
            or body.get("projectRoot")
            or inbound_source_context.get("workspace")
            or inbound_source_context.get("workspace_root")
            or inbound_source_context.get("cwd")
            or inbound_source_context.get("projectRoot")
            or ""
        ).strip()
        default_workspace = workspace_root_provider()
        # A chat bound to a project operates in that project's folder — this is what isolates one
        # project's files from another's. The server owns this binding (session meta), so it can't be
        # forged from the body; an explicit caller-sent workspace still wins for programmatic callers.
        from core import project_store
        from core.context_namespace import (
            ensure_chat_namespace,
            load_chat_namespace,
        )
        from core.persistent_memory import load_session_meta

        existing_namespace = load_chat_namespace(session_id)
        persisted_project_id = str(
            (load_session_meta().get(session_id) or {}).get("project_id") or ""
        ).strip()
        namespace_project_id = (
            existing_namespace.project_id
            if existing_namespace is not None
            else persisted_project_id
        )
        context_namespace = ensure_chat_namespace(
            session_id,
            project_id=namespace_project_id,
            grant_confirmed_profile=owner_local,
        )
        if context_namespace.lifecycle_state != "active":
            return apply_runtime_headers(
                json_response(
                    409,
                    {
                        "error": (
                            "chat is not active; restore it before sending messages"
                        ),
                        "lifecycle_state": (
                            context_namespace.lifecycle_state
                        ),
                    },
                ),
                runtime,
            )
        bound_project_id = context_namespace.project_id
        project_workspace = (
            project_store.project_root(bound_project_id) or ""
            if bound_project_id and not requested_workspace
            else ""
        )
        effective_workspace = requested_workspace or project_workspace or default_workspace
        workspace_binding = (
            "explicit"
            if requested_workspace
            else ("project" if project_workspace else "default")
        )
        # Owner-local trust is derived from the real TCP peer (loopback == the owner's own
        # session), never from the caller-supplied surface. A channel surface is remote by
        # definition, so it is never owner-local even over loopback. Privileged gates (cloud
        # spend policy, brake) read this stamped flag, not `surface`.
        # DECLARED INGRESS GUARANTEE — this dispatch is one of the two lanes named by the
        # canonical KAS→VOOL channel ingress contract
        # (core/channel_gateway.process_channel_request). For surface="channel" callers it
        # enforces the same deny-only remote stamp the gateway applies at ingress; external
        # bridges using this lane (telegram_chat_bridge) are declared shims to THIS contract,
        # not independent execution authorities. A7 alone mints finalization identity.
        source_context = {
            # The transcript row of this turn is written at the response-commit boundary (the
            # seal, or the request-end flush) -- see core.persistent_memory.append_conversation_event.
            "transcript_commit_boundary": True,
            **inbound_source_context,
            "surface": resolved_surface,
            "platform": str(inbound_source_context.get("platform") or body.get("platform") or "api").strip() or "api",
            OWNER_LOCAL_KEY: owner_local,
            "client_conversation_history": client_history,
            "client_history_message_count": len(client_history),
            "conversation_history": history,
            "history_message_count": len(history),
            "workspace": effective_workspace,
            "workspace_root": effective_workspace,
            "project_id": bound_project_id,
            "workspace_binding": workspace_binding,
            "request_id": str(request_id or "").strip(),
        }
        source_context["chat_id"] = context_namespace.chat_id
        if context_namespace.project_id:
            source_context["_trusted_project_id"] = context_namespace.project_id
        requested_model = str(model or "").strip()
        if requested_model and requested_model not in {str(model_name or "").strip(), f"{str(model_name or '').strip()}:latest"}:
            source_context["requested_model"] = requested_model
            # Advisory only, and only ever loosening: "sticky" tells the router this concrete id
            # came from Auto's per-chat stickiness, not an operator pin, so an unresolvable name
            # degrades to auto routing instead of the pinned refusal (MF-22). A forged value can
            # never grant anything -- "pin" is already the default reading.
            model_selection = str(body.get("model_selection") or "").strip().lower()
            if model_selection in {"sticky", "pin", "auto"}:
                source_context["model_selection"] = model_selection
        # Bind the Auto lane for this turn BEFORE any routing runs. The flag is stamped into
        # `source_context`, which is what survives the shallow copies and the thread-pool hops the
        # provider calls take — see `core.auto_local_only_mode`. Stamped server-side from the
        # composer selection, so a caller cannot set the provenance key itself.
        from core.auto_local_only_mode import bind_turn as bind_local_only_turn

        source_context.pop("_auto_local_only_selected", None)
        bind_local_only_turn(source_context, requested_model)
        # ---- VOOL School ingress gate -------------------------------------
        # A SCHOOL-edition chat turn requires a verified school session; the
        # resolved lesson/class/assignment policy is stamped server-side under
        # the reserved `school_policy` key (a caller copy was stripped at
        # intake). Students are forced onto the buffered lane so the
        # assistance publication gate sees the whole response.
        from core.school.ingress import SCHOOL_POLICY_KEY, force_buffered, ingress_gate

        _school_error, _school_ctx = ingress_gate(
            headers=headers,
            source_context=source_context,
            client_host=client_host,
        )
        if _school_error is not None:
            return apply_runtime_headers(
                json_response(int(_school_error.pop("status", 403)), _school_error), runtime
            )
        if _school_ctx:
            source_context[SCHOOL_POLICY_KEY] = _school_ctx
            if force_buffered(_school_ctx):
                stream = False
        # Composer state is normalized and registered with the controller before the task begins.
        # Every tool call then resolves the live session state again, so a mode change during a task
        # takes effect before its next action rather than remaining frozen in prompt text.
        raw_operating_mode = str(body.get("mode") or "").strip().lower()
        operating_mode = normalize_mode(raw_operating_mode) if raw_operating_mode in _SUPPORTED_OPERATING_MODES else None
        # EVERY chat turn is stamped with the controller-owned effective mode, not just the turns
        # that named one. A turn used to leave `operating_mode` unset whenever the field was
        # missing or unrecognised, and unset was the permission bypass downstream. The authority
        # resolves an unnamed mode to the session's existing selection, and to MANUAL when there is
        # none — so an omitted field costs an approval prompt, never a silent grant.
        # An UNRECOGNISED value is not dropped to "omitted" either: the raw value is handed to the
        # one authority, which fails the turn closed to MANUAL and names the refusal, answered
        # here with a typed 400 before the turn runs. (A legacy alias such as "bypass" still
        # normalizes inside the authority, so the explicit-bypass 409 path below is unchanged.)
        mode_state = resolve_effective_mode(
            session_id=session_id,
            requested_mode=(operating_mode if operating_mode is not None else (str(body.get("mode") or "").strip() or None)),
            project_id=bound_project_id,
            client_turn_id=str(body.get("turn_id") or "").strip()[:80],
            bypass_token=str(body.get("bypass_token") or ""),
        )
        refused_mode = str(mode_state.get("invalid_mode_refused") or "").strip()
        if refused_mode:
            return apply_runtime_headers(
                json_response(
                    400,
                    {"error": f"Unknown operating mode '{refused_mode}'. The turn did not run; nothing was executed."},
                ),
                runtime,
            )
        if operating_mode is OperatingMode.BYPASS_PERMISSIONS and mode_state.get("downgraded_from"):
            # An EXPLICIT bypass request that cannot be honoured is refused out loud, exactly as
            # before. Silently serving Manual would leave the operator believing bypass is live.
            return apply_runtime_headers(
                json_response(409, {"error": "Bypass permissions needs a current, explicitly confirmed grant."}),
                runtime,
            )
        source_context["operating_mode"] = mode_state["mode"]
        source_context["operating_mode_revision"] = mode_state["revision"]
        if operating_mode is not None:
            source_context["bypass_token"] = str(body.get("bypass_token") or "").strip()
        source_context["runtime_session_id"] = session_id
        # ---- Attachments: spend what THIS chat staged, on THIS turn, and hand it over as bounded
        # evidence. The ids are the only thing a client may name; the bytes, the kinds and the
        # text all come from the authority. A client-stamped turn id or delivery record is a forgery
        # and is dropped before anything is read.
        from core import chat_attachments as _chat_attachments

        source_context.pop("attachment_turn_id", None)
        source_context.pop("attachment_delivery", None)
        _attachment_turn = ""
        _raw_attachment_ids = body.get("attachments")
        if _raw_attachment_ids not in (None, [], ()):
            _attachment_turn = (
                str(body.get("turn_id") or "").strip()[:80]
                or ("req:" + str(request_id or uuid.uuid4().hex).strip())[:80]
            )
            try:
                if not isinstance(_raw_attachment_ids, list):
                    raise _chat_attachments.AttachmentRefused("invalid_id", "attachments must be a list of attachment ids.", http_status=400)
                _bound_attachments = _chat_attachments.bind_to_turn(
                    session_id=session_id, turn_id=_attachment_turn, attachment_ids=_raw_attachment_ids
                )
                source_context["external_evidence"] = _chat_attachments.evidence_items_for_turn(
                    session_id=session_id, turn_id=_attachment_turn, question=str(user_text or "")
                )
            except _chat_attachments.AttachmentRefused as exc:
                _attachment_event(
                    session_id,
                    event_type="attachment_refused",
                    message=f"Attachments refused for this message — {exc.message}",
                    details={"code": exc.code},
                    turn_id=_attachment_turn,
                )
                return apply_runtime_headers(
                    json_response(422, {"error": "attachment_rejected", "code": exc.code, "message": exc.message}),
                    runtime,
                )
            source_context["attachment_turn_id"] = _attachment_turn
            for _bound in _bound_attachments:
                _attachment_event(
                    session_id,
                    event_type="attachment_bound",
                    message=f"Sending {_bound['name']} ({_bound['kind']}, {_human_size(_bound['size_bytes'])}) with this message",
                    details={
                        "attachment_id": _bound["id"],
                        "kind": _bound["kind"],
                        "media_type": _bound["media_type"],
                        "size_bytes": _bound["size_bytes"],
                    },
                    turn_id=_attachment_turn,
                )
        elif isinstance(source_context.get("external_evidence"), list):
            # A client cannot name a staged attachment by hand: evidence items that claim the
            # attachment origin, id or reference are stripped, never honoured.
            source_context["external_evidence"] = [
                item
                for item in source_context["external_evidence"]
                if not (
                    isinstance(item, dict)
                    and (
                        item.get("attachment_id")
                        or item.get("origin") == "chat_attachment"
                        or str(item.get("reference") or "").startswith("attachment:")
                    )
                )
            ]
        approval_token = str(body.get("approval_token") or "").strip()
        if approval_token and len(approval_token) <= 128:
            source_context["mode_approval_token"] = approval_token
        # Per-turn autonomy override (see core.execution_gate). Auto raises the legacy execution
        # gate only for actions the central permission controller has already allowed; exact
        # approval tokens remain fingerprinted and are consumed separately above.
        requested_autonomy = str(body.get("autonomy") or "").strip().lower()
        if requested_autonomy in {"auto", "balanced", "strict", "hands_off"}:
            source_context["autonomy_override"] = requested_autonomy
        elif operating_mode is OperatingMode.AUTO:
            source_context["autonomy_override"] = "auto"
        # `effort` is accepted only for the one step that reaches real machinery: "smarter" sets the
        # autopilot hook local_inference_autopilot._explicit_heavy_requested reads. The level itself
        # is not recorded -- nothing consumes it, and a stored key nobody reads is not a setting.
        effort = str(body.get("effort") or "").strip().lower()
        if effort == "smarter":
            source_context["autopilot_allow_heavy_model"] = True

        model_status_result = runtime_version_response(user_text, runtime) or runtime_model_status_response(
            user_text,
            runtime,
            requested_model=requested_model,
        )
        if model_status_result is not None:
            result = apply_exact_response_control(dict(model_status_result), user_text)
            if stream:
                stream_iter = ollama_stream_chunks(result, str(model))
                if normalized_path.startswith("/v1/"):
                    return apply_runtime_headers(
                        stream_response(
                            200,
                            openai_sse_stream_from_ollama_chunks(stream_iter, str(model)),
                            content_type="text/event-stream; charset=utf-8",
                            headers={"Cache-Control": "no-cache"},
                        ),
                        runtime,
                    )
                return apply_runtime_headers(
                    stream_response(200, stream_iter, content_type="application/x-ndjson"),
                    runtime,
                )
            payload = openai_chat_response(result, model, source_context) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime, source_context)
            payload = _attach_work_receipt(payload, result=result, session_id=session_id)
            payload = _attach_profile_frame(payload, result=result)
            # R-7 (A-6): buffered lanes mark ATTEMPTED_UNKNOWN before the bytes
            # leave - the handoff row asserts what actually happened.
            try:
                from core.finalization import (
                    DELIVERY_ATTEMPTED_UNKNOWN as _DELIVERY_ATTEMPTED_UNKNOWN,
                )
                from core.finalization import (
                    set_delivery_status as _buf_sds,
                )

                _buf_fid = str(
                    (result.get("vool_response_commit") or {}).get("finalization_id") or ""
                )
                if _buf_fid:
                    _buf_sds(_buf_fid, _DELIVERY_ATTEMPTED_UNKNOWN)
            except Exception:
                pass
            return apply_runtime_headers(json_response(200, payload), runtime)

        # In-chat self-update offer / apply (proactive on greetings, or "update now").
        # A no-op unless a verified update is cached; applying launches the detached,
        # auto-rollback updater — it never runs unverified code or touches user data.
        # Emergency brake first: /stopx402 freezes the wallet (blocks every x402 spend)
        # and /startx402 unfreezes it. Checked before anything else so a stop is never
        # intercepted by another handler.
        brake_result = maybe_handle_agent_brake(user_text, session_id, owner_local=owner_local)
        update_result = brake_result if brake_result is not None else maybe_handle_update_offer(user_text, session_id)
        # The scripted web0/.null topic-answer interception that lived here is REMOVED by the
        # owner's instruction (2026-09-16): `maybe_handle_null_registration` and
        # `web0_null_project_response` preempted real questions on keyword evidence far weaker
        # than their authority -- a provider-health checker asking to "capture the returned
        # model CLAIM" and "calculate the request COST" was answered, wholesale, with the
        # `.null` registration fee boilerplate ("claim" is on the registration-verb list,
        # "cost" on the fee list). The brake and update controls above are exact commands,
        # not topic matchers, and stay; every other message now reaches the ordinary lanes.
        grounded_result = update_result
        if grounded_result is not None:
            result = apply_exact_response_control(dict(grounded_result), user_text)
            # A7 product decision (X-FAST closure): a route that emits semantic
            # assistant answer bytes to the user MUST converge through A2
            # admission + A7 finality — no permanent fast-path exception. The
            # deterministic answer is admitted through the seam and finalized
            # by the canonical authority, so its truth is durably bound under
            # the same law as ordinary answer-present turns.
            from core.finalization import finalize_answer
            from core.semantic.semantic_result_seam import (
                admit_semantic_result,
                reset_admission,
            )

            reset_admission()
            fast_turn_id = f"fast:{session_id}:{abs(hash(user_text)) % 10**16}"

            # K-08: ALL semantic transforms BEFORE A2 admission — the admitted
            # bytes ARE the committed bytes.
            result["response"] = strip_provenance_footer_str(str(result.get("response") or ""))
            admit_semantic_result(dict(result))
            result["vool_response_commit"] = finalize_answer(
                turn_id=fast_turn_id,
                canonical_content=str(result.get("response") or ""),
                source_context=None,
                display_metadata={"route": "fast_grounding"},
            )
            # K-10 DELIVERED=EVIDENCE: the handoff row already exists
            # (NOT_ATTEMPTED, created at finalization — before any transport
            # byte). A synchronous HTTP return is NOT proven recipient-class
            # evidence: the honest pre-wire mark is ATTEMPTED_UNKNOWN. The
            # startup sweep converts unknowns into DELIVERY_RETRY (resend of
            # committed bytes); nothing may claim DELIVERED pre-socket.
            from core.finalization import DELIVERY_ATTEMPTED_UNKNOWN as _DELIV_TRY
            from core.finalization import set_delivery_status as _sds

            _sds(result["vool_response_commit"]["finalization_id"], _DELIV_TRY)
            if stream:
                stream_iter = ollama_stream_chunks(result, str(model))
                if normalized_path.startswith("/v1/"):
                    return apply_runtime_headers(
                        stream_response(
                            200,
                            openai_sse_stream_from_ollama_chunks(stream_iter, str(model)),
                            content_type="text/event-stream; charset=utf-8",
                            headers={"Cache-Control": "no-cache"},
                        ),
                        runtime,
                    )
                return apply_runtime_headers(
                    stream_response(200, stream_iter, content_type="application/x-ndjson"),
                    runtime,
                )
            payload = openai_chat_response(result, model, source_context) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime, source_context)
            payload = _attach_work_receipt(payload, result=result, session_id=session_id)
            payload = _attach_profile_frame(payload, result=result)
            # R-7 (A-6): buffered lanes mark ATTEMPTED_UNKNOWN before the bytes
            # leave - the handoff row asserts what actually happened.
            try:
                from core.finalization import (
                    DELIVERY_ATTEMPTED_UNKNOWN as _DELIVERY_ATTEMPTED_UNKNOWN,
                )
                from core.finalization import (
                    set_delivery_status as _buf_sds,
                )

                _buf_fid = str(
                    (result.get("vool_response_commit") or {}).get("finalization_id") or ""
                )
                if _buf_fid:
                    _buf_sds(_buf_fid, _DELIVERY_ATTEMPTED_UNKNOWN)
            except Exception:
                pass
            return apply_runtime_headers(json_response(200, payload), runtime)

        if stream:
            # F-01 (independent-proof repair): the A0/A2 ContextVar binding set
            # by the accept door is RESET when dispatch_post returns -- long
            # before this StreamingResponse body is pulled -- and the worker
            # thread inside stream_agent_with_events starts a fresh context
            # besides. Capture the ingress context HERE, while the binding is
            # live, and hand it to the streaming lane so the turn executes
            # under the canonical request/turn/execution identity established
            # at ingress. No downstream identity fabrication.
            import contextvars as _ctxvars

            _ingress_context = _ctxvars.copy_context()
            stream_iter = stream_agent_with_events_provider(
                runtime,
                user_text,
                session_id=session_id,
                source_context=source_context,
                model=model,
                include_runtime_events=include_runtime_events,
                emit_task_events=emit_task_events,
                ingress_context=_ingress_context,
            )
            with _update_turn_guard(source_context, session_id) as _update_work_id:
                stream_iter = _stream_under_update_work(stream_iter, _update_work_id)
            # The StreamingResponse body is pulled AFTER dispatch_post's finally
            # reset the accept-door token, and finalization runs in the
            # generator frame (_response_commit). Every pull of the wrapped
            # iterator therefore executes under the ingress context, keeping
            # A0/A2 binding live through commit and delivery marking.
            stream_iter = _iterate_under_ingress_context(_ingress_context, stream_iter)
            # S1: the single task.finalizing event, emitted only in front of the real
            # response-commit frame, only when the typed channel is on for this turn.
            stream_iter = _inject_task_finalizing_event(stream_iter, emit_task_events)
            if _attachment_turn:
                # Outermost on purpose: the turn's attachments are released only once every
                # byte has left (or the client has), never while a frame is still being built.
                stream_iter = _release_attachments_after(stream_iter, session_id=session_id, turn_id=_attachment_turn)
            if normalized_path.startswith("/v1/"):
                return apply_runtime_headers(
                    stream_response(
                        200,
                        openai_sse_stream_from_ollama_chunks(stream_iter, str(model)),
                        content_type="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-cache"},
                    ),
                    runtime,
                )
            return apply_runtime_headers(
                stream_response(200, stream_iter, content_type="application/x-ndjson"),
                runtime,
            )

        # The buffered lane registers its turn for cancellation exactly like the streaming
        # worker does: an API client that asked for a non-streamed answer can still press stop,
        # and the per-turn cancel signal must reach the in-flight tool (a long command in a code
        # task) rather than only the visible reply. Registered before the call, released in the
        # finally, on every exit path.
        from core.live_turns import register_turn as _buf_register_turn
        from core.live_turns import unregister_turn as _buf_unregister_turn

        _buf_cancel_turn = str(source_context.get("cancel_turn_id") or "").strip()
        if _buf_cancel_turn:
            source_context["cancel_event"] = _buf_register_turn(session_id, _buf_cancel_turn)
        try:
            with _update_turn_guard(source_context, session_id):
                result = run_agent_provider(
                    runtime,
                    user_text,
                    session_id=session_id,
                    source_context=source_context,
                    workspace_root_provider=workspace_root_provider,
                )
        except Exception as exc:
            # A served turn that dies as a bare 500 leaves no trace anywhere -- measured
            # 2026-09-03: a concurrent-edit journey lost one turn to a FileNotFoundError and
            # the daemon log recorded nothing, so the flake looked environmental for a day.
            # The traceback is operator-side only; the client still gets the safe text.
            logging.getLogger("vool.api").exception("served chat turn failed (session=%s)", session_id)
            return apply_runtime_headers(json_response(500, {"error": safe_error_text(exc)}), runtime)
        finally:
            if _buf_cancel_turn:
                _buf_unregister_turn(session_id, _buf_cancel_turn)
            # The buffered lane's turn is over here, answered or failed: release its attachments.
            if _attachment_turn:
                _release_turn_attachments(session_id, _attachment_turn)
        # K-08 ORDERING-FROZEN verify-only assert (R-0/A-11): transforms ran
        # inside the sealing context BEFORE admission; the boundary re-derives
        # them against the UNFOOTERED canonical bytes — `_finalize_turn_usage`
        # appends the provenance footer after the seal, so comparing raw
        # `result["response"]` here crashes every exact-contract turn.
        from core.response_provenance import strip_provenance_footer as _r0_strip

        _canonical_served = _r0_strip(str((result or {}).get("response") or ""))
        commit = (result or {}).get("vool_response_commit") or {}
        if commit.get("canonical_content"):
            _canonical_served = str(commit["canonical_content"])
        _controlled = apply_exact_response_control(
            {**dict(result or {}), "response": _canonical_served}, user_text
        )
        if str(_controlled.get("response") or "") != _canonical_served:
            raise RuntimeError(
                "K-08 violation: exact-response-control would mutate post-admission bytes"
            )
        # Honesty seal verify-only against the same unfootered canonical bytes.
        _guarded = enforce_final_action_honesty(
            {**dict(result or {}), "response": _canonical_served},
            user_input=user_text,
            effective_input=user_text,
            session_id=session_id,
            source_context=source_context,
        )
        if str(_guarded.get("response") or "") != _canonical_served:
            raise RuntimeError(
                "K-08 violation: final-action-honesty would mutate post-admission bytes"
            )
        # ---- VOOL School post-turn gate (buffered student lane) ------------
        # Assistance publication gate + quota settle + ledger events, BEFORE
        # the response is shaped for the wire. May replace the response text
        # with the fixed guidance refusal (recorded as a violation event).
        if SCHOOL_POLICY_KEY in source_context:
            try:
                from core.school.ingress import post_turn as _school_post_turn

                _school_post_turn(
                    school_ctx=source_context[SCHOOL_POLICY_KEY],
                    result=result,
                    user_text=user_text,
                )
            except Exception:
                logging.getLogger("vool.api").exception(
                    "school post-turn gate failed (session=%s)", session_id
                )
        payload = openai_chat_response(result, model, source_context) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime, source_context)
        payload = _attach_work_receipt(payload, result=result, session_id=session_id)
        payload = _attach_profile_frame(payload, result=result)
        # R-7 (A-6): buffered lanes mark ATTEMPTED_UNKNOWN before the bytes
        # leave - the handoff row asserts what actually happened.
        try:
            from core.finalization import (
                DELIVERY_ATTEMPTED_UNKNOWN as _DELIVERY_ATTEMPTED_UNKNOWN,
            )
            from core.finalization import (
                set_delivery_status as _buf_sds,
            )

            _buf_commit_obj = (
                result.get("vool_response_commit")
                if isinstance(result.get("vool_response_commit"), dict)
                else (payload.get("vool_response_commit") if isinstance(payload, dict) else {})
            )
            _buf_fid = str((_buf_commit_obj or {}).get("finalization_id") or "")
            if _buf_fid:
                _buf_sds(
                    _buf_fid,
                    _DELIVERY_ATTEMPTED_UNKNOWN,
                    execution_identity=(
                        result.get("_execution_identity")
                        if isinstance(result.get("_execution_identity"), dict)
                        else None
                    ),
                )
        except Exception:
            pass
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/generate":
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            return apply_runtime_headers(json_response(400, {"error": "no prompt"}), runtime)
        model = body.get("model", model_name)
        try:
            result = run_agent_provider(
                runtime,
                prompt,
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": safe_error_text(exc)}), runtime)
        # R-8 (H-6/K-11, S12): /api/generate serves COMMIT bytes via the same
        # authority as every other surface — admit -> finalize -> serve sealed
        # canonical content. No raw .strip() serving.
        from core.semantic.semantic_result_seam import (
            admit_semantic_result,
            reset_admission,
        )

        reset_admission()
        result = apply_exact_response_control(dict(result or {}), prompt)
        result = enforce_final_action_honesty(
            dict(result or {}),
            user_input=prompt,
            effective_input=prompt,
            session_id=None,
            source_context=None,
        )
        admit_semantic_result(dict(result or {}))
        from core.finalization import finalize_answer
        from core.response_provenance import strip_provenance_footer as _gen_strip

        _gen_commit = result.get("vool_response_commit")
        if not isinstance(_gen_commit, dict) or not _gen_commit:
            _gen_commit = finalize_answer(
                turn_id=str(body.get("turn_id") or "")[:80],
                canonical_content=_gen_strip(str(result.get("response") or "")),
            )
        _gen_gate = _hashlast_serve_gate(_gen_commit)
        if _gen_gate:
            raise RuntimeError(f"HASH-LAST per-serve gate: {_gen_gate}")
        response_text = str(_gen_commit.get("canonical_content") or "")
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "model": model,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "response": response_text,
                    "done": True,
                    "vool_response_commit": _gen_commit,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/show":
        name = str(body.get("name") or body.get("model") or "").strip()
        if name and name not in {model_name, f"{model_name}:latest"}:
            return apply_runtime_headers(json_response(404, {"error": f"model '{name}' not found"}), runtime)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = {
            "modelfile": f"# VOOL runtime model\nFROM {model_name}",
            "parameters": "stop <|im_end|>",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "vool",
                "family": "qwen",
                "families": ["qwen"],
                "parameter_size": runtime.runtime_parameter_size,
                "quantization_level": "runtime",
            },
            "model_info": {
                "general.architecture": "qwen2",
                "general.parameter_count": parameter_count_for_model(runtime.runtime_model_tag),
                "general.file_type": 0,
            },
            "modified_at": now,
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/adaptation/loop/tick":
        return apply_runtime_headers(json_response(200, schedule_adaptation_autopilot_tick(force=True, wait=True)), runtime)

    if normalized_path in {"/v1/credits/settle", "/api/credits/settle"}:
        try:
            from core.credit_ledger import reconcile_ledger
            from network.signer import get_local_peer_id
            peer_id = str(body.get("peer_id") or get_local_peer_id())
            result = reconcile_ledger(peer_id)
            resp_payload: dict[str, Any] = {
                "peer_id": result.peer_id,
                "balance": result.balance,
                "entries": result.entries,
                "mode": result.mode,
            }
        except Exception as exc:
            resp_payload = {"error": str(exc)}
        return apply_runtime_headers(json_response(200, resp_payload), runtime)

    return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)
