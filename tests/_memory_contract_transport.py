"""Purpose-aware offline provider controls; never a quality scorer or product fallback."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class ReplyControl:
    content: str
    required_source_fragments: tuple[str, ...] = ()
    malformed_auxiliary: bool = False
    malformed_final: bool = False
    finish_reason: str = "stop"

def call_contract(payload: dict[str, Any]) -> tuple[str, str]:
    messages = payload.get("messages") or []
    systems = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    # Remove attributed retrieval blocks before inspecting actual instructions.
    system = "\n".join(re.sub(r"<retrieved_context>.*?</retrieved_context>", "", s, flags=re.S) for s in systems)
    if any(s.lstrip().startswith("You split a user's message into the separate requests") for s in systems):
        return "auxiliary_decomposition", "request_array"
    if re.search(r"Return valid JSON only in the form.*?[\"']summary[\"'].*?[\"']steps[\"']", system, flags=re.S):
        return "answer", "action_plan"
    if (payload.get("response_format") or {}).get("type") in {"json_object", "json_schema"}:
        return "answer", "explicit_json"
    return "answer", "plain_text"

def strict_judge_label(raw: str) -> str | None:
    """New future-control parser. Old labels/parser are preserved elsewhere."""
    value = raw.strip().casefold()
    return value if value in {"yes", "no"} else None

def reply_for(payload: dict[str, Any], control: ReplyControl) -> dict[str, Any]:
    purpose, mode = call_contract(payload)
    texts = [str(m.get("content") or "") for m in payload.get("messages", [])]
    required = control.required_source_fragments
    source_present = all(any(fragment in text for text in texts) for fragment in required)
    if purpose == "auxiliary_decomposition":
        # Auxiliary splitting has no memory-delivery obligation and gets no gold.
        users = [str(m.get("content") or "") for m in payload.get("messages", []) if m.get("role") == "user"]
        request = users[-1] if users else ""
        content = "deliberately malformed decomposition" if control.malformed_auxiliary else json.dumps([
            {"request": request, "operation": "factual_explanation", "depends_on": []}
        ], ensure_ascii=False)
    else:
        content = control.content if source_present else "I could not find sufficient source evidence in the supplied context."
        if mode == "action_plan" and not control.malformed_final:
            try:
                obj = json.loads(content)
            except (TypeError, ValueError):
                lines = [line for line in content.splitlines() if line.strip()]
                obj = {"summary": lines[0] if lines else "Source evidence unavailable", "steps": lines[1:] or lines}
            content = json.dumps(obj, ensure_ascii=False)
        elif mode == "explicit_json" and not control.malformed_final:
            # This control requires an authored valid JSON fixture; no coercion
            # can conceal a bad router or invent a different requested payload.
            if source_present:
                json.loads(content)
            else:
                content = json.dumps({"error": "source_unavailable"})
    return {
        "purpose": purpose, "output_contract": mode,
        "source_required_at_this_call": bool(required) and purpose == "answer",
        "source_present": source_present if purpose == "answer" else None,
        "content": content, "finish_reason": control.finish_reason if purpose == "answer" else "stop",
        "usage_measurement": "synthetic_not_measured",
        "max_output_tokens_wire": payload.get("max_tokens"),
        "provider_spending_usd": 0,
    }


def launcher_context_defaults(repo: Path, environment: dict[str, str]) -> dict[str, Any]:
    """Resolve only the two source-defined startup exports, without executing installer."""
    path = repo / "installer" / "install_vool.sh"
    body = path.read_bytes()
    lines = body.decode().splitlines()
    resolved = {}
    definitions = []
    for key in ("VOOL_ADAPTIVE_CONTEXT", "VOOL_CONTEXT_CAPSULE_V2"):
        matches = [(n, line) for n, line in enumerate(lines, 1) if line.startswith("export " + key + "=") and key + ":-1}" in line]
        if len(matches) != 1:
            raise ValueError("Source-defined launcher default is absent or ambiguous: " + key)
        n, line = matches[0]
        resolved[key] = environment.get(key) or "1"
        definitions.append({"name": key, "line": n, "source_text": line, "resolved_value": resolved[key], "explicit_nonempty_override": bool(environment.get(key))})
    return {"configuration_kind": "source_defined_installer_launcher_defaults", "source_path": "installer/install_vool.sh", "source_sha256": hashlib.sha256(body).hexdigest(), "definitions": definitions, "resolved": resolved, "installed_binary_correspondence": "unknown"}


def fulfillment_is_failed(outcome: Any, response_control: Any = None) -> bool:
    if outcome == "failed":
        return True
    return any(isinstance(value, dict) and value.get("fulfillment_status") == "failed" for value in (outcome, response_control))
