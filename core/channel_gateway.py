from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.agent_runtime.request_authority import bounded_evidence_items

_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


@dataclass(frozen=True)
class ChannelRequest:
    platform: str
    user_id: str
    text: str
    channel_id: str | None = None
    persona_id: str = "default"
    device_hint: str = "channel"
    surface: str = "channel"
    allow_cold_context: bool = False
    allow_remote_fetch: bool = False
    attachments: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class ChannelOutputPolicy:
    platform: str
    max_chars: int
    compact: bool
    metadata_first: bool


@dataclass(frozen=True)
class ChannelGatewayResult:
    task_id: str
    platform: str
    session_id: str
    response_text: str
    truncated: bool
    mode: str
    confidence: float
    prompt_assembly_report: dict[str, Any]
    source_context: dict[str, Any]
    finalization_id: str = ""


def _safe_segment(value: str, *, fallback: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip("-").lower()
    return cleaned or fallback


def channel_session_id(
    *,
    platform: str,
    user_id: str,
    channel_id: str | None,
    persona_id: str,
    device_hint: str = "channel",
) -> str:
    safe_platform = _safe_segment(platform, fallback="platform")
    safe_user = _safe_segment(user_id, fallback="user")
    safe_channel = _safe_segment(channel_id or "direct", fallback="direct")
    safe_persona = _safe_segment(persona_id, fallback="default")
    safe_device = _safe_segment(device_hint, fallback="channel")
    return f"{safe_device}:{safe_platform}:{safe_channel}:{safe_user}:{safe_persona}"


def channel_output_policy(platform: str) -> ChannelOutputPolicy:
    normalized = _safe_segment(platform, fallback="channel")
    if normalized == "telegram":
        return ChannelOutputPolicy(platform=normalized, max_chars=3200, compact=True, metadata_first=True)
    if normalized == "discord":
        return ChannelOutputPolicy(platform=normalized, max_chars=1800, compact=True, metadata_first=True)
    if normalized in {"web", "web-companion", "web_companion"}:
        return ChannelOutputPolicy(platform="web_companion", max_chars=6000, compact=False, metadata_first=True)
    return ChannelOutputPolicy(platform=normalized, max_chars=2200, compact=True, metadata_first=True)


def build_source_context(request: ChannelRequest) -> dict[str, Any]:
    policy = channel_output_policy(request.platform)
    from core.request_trust import OWNER_LOCAL_KEY

    return {
        # A channel request is a REMOTE surface by definition, so stamp owner-local False rather
        # than leaving the key absent. Without it request_is_owner_local falls back to the
        # `surface` string, and a ChannelRequest carrying surface="cli"/"local"/"desktop"/"" would
        # hand a remote user the owner's privileges (cloud key set/clear, model switch, and any
        # future paid spend). Stamped here it can only ever deny.
        OWNER_LOCAL_KEY: False,
        "surface": request.surface,
        "platform": policy.platform,
        "channel_id": request.channel_id,
        "source_user_id": request.user_id,
        "allow_cold_context": bool(request.allow_cold_context),
        "allow_remote_fetch": bool(request.allow_remote_fetch),
        # Bounded AT INGRESS, not later. `list(request.attachments or [])` copied every item a
        # remote sender chose to send, and every downstream consumer then paid for all of them --
        # a slice further down is a display cap, not a work bound. See
        # `core/agent_runtime/request_authority.py:bounded_evidence_items`.
        "external_evidence": bounded_evidence_items(request.attachments),
        "output_policy": {
            "max_chars": policy.max_chars,
            "compact": policy.compact,
            "metadata_first": policy.metadata_first,
        },
    }


def render_channel_response(text: str, *, platform: str) -> tuple[str, bool]:
    policy = channel_output_policy(platform)
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(normalized) <= policy.max_chars:
        return normalized, False
    clipped = normalized[: max(0, policy.max_chars - 20)].rstrip()
    return f"{clipped}\n\n[truncated]", True


def process_channel_request(agent: Any, request: ChannelRequest) -> ChannelGatewayResult:
    """THE canonical KAS→VOOL channel ingress contract.

    Every external channel integration (KAS-owned bridges) reaches the agent
    through THIS function or through an explicit shim that enforces these same
    guarantees. Two lanes are live today, both enumerable here and nowhere else:

      * relay/bridge_workers/discord_bridge.py     — calls this function directly.
      * relay/bridge_workers/telegram_chat_bridge.py
        → POST {local}/api/chat with surface="channel"
        (core/web/api/service.py chat dispatch) — a cross-process HTTP shim whose
        owner-local guarantee is the DENY-equivalent stamp
        `OWNER_LOCAL_KEY = loopback AND surface != "channel"`.

    Result / finality identity semantics:
      * The reply served to any channel is the A7-FINALIZED canonical bytes,
        never raw result["response"].
      * `finalization_id` is MINTED by core/finalization (A7) only; channel code
        carries it through (`ChannelGatewayResult.finalization_id`) and never
        re-mints it. There is no second finalization owner.

    Ingress trust semantics: this gateway stamps OWNER_LOCAL_KEY=False for every
    channel request. Remote surfaces can chat and read; they can never inherit
    owner-gated privileges regardless of the surface string they present.
    """
    session_id = channel_session_id(
        platform=request.platform,
        user_id=request.user_id,
        channel_id=request.channel_id,
        persona_id=request.persona_id,
        device_hint=request.device_hint,
    )
    source_context = build_source_context(request)
    result = agent.run_once(
        request.text,
        session_id_override=session_id,
        source_context=source_context,
    )
    # A7 W5 (M09/M05): channels consume the finalized canonical bytes, never
    # raw result["response"]. Platform limits apply as DECLARED framing only:
    # exact-prefix splitting with the ``truncated`` flag — no strip/collapse,
    # no invented marker text inside the answer body.
    commit = result.get("vool_response_commit")
    if isinstance(commit, dict) and commit.get("type") == "response.commit":
        canonical = str(commit.get("canonical_content") or "")
        policy = channel_output_policy(request.platform)
        if len(canonical) <= policy.max_chars:
            response_text, truncated = canonical, False
        else:
            response_text, truncated = canonical[: max(0, policy.max_chars)], True
    else:
        # H-6/R-8 (K-11): the raw path is DELETED. A commitless channel turn
        # mints through the ONE finalization authority (mirroring
        # _response_commit): admit -> finalize -> serve sealed bytes with
        # declared platform framing only.
        from core.finalization import finalize_answer
        from core.response_provenance import strip_provenance_footer
        from core.semantic.semantic_result_seam import (
            admit_semantic_result,
            current_admission,
            reset_admission,
        )

        reset_admission()
        admitted = admit_semantic_result(dict(result or {}))
        _sr = ""
        _rec = current_admission()
        if _rec is not None:
            _sr = str(_rec.semantic_result_id or "")
        _minted = finalize_answer(
            turn_id=str(source_context.get("_canonical_user_turn_id") or session_id),
            canonical_content=strip_provenance_footer(str((result or {}).get("response") or "")),
        )
        policy = channel_output_policy(request.platform)
        _canonical = str(_minted.get("canonical_content") or "")
        if len(_canonical) <= policy.max_chars:
            response_text, truncated = _canonical, False
        else:
            response_text, truncated = _canonical[: max(0, policy.max_chars)], True
        commit = _minted
        result = {**dict(result or {}), "vool_response_commit": _minted}
    return ChannelGatewayResult(
        task_id=str(result["task_id"]),
        platform=channel_output_policy(request.platform).platform,
        session_id=session_id,
        response_text=response_text,
        truncated=truncated,
        mode=str(result.get("mode") or "unknown"),
        confidence=float(result.get("confidence") or 0.0),
        prompt_assembly_report=dict(result.get("prompt_assembly_report") or {}),
        source_context=source_context,
        finalization_id=str(commit.get("finalization_id") or "") if isinstance(commit, dict) else "",
    )
