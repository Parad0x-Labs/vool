from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib import parse, request


def _read_json(url: str, *, data: dict[str, Any] | None = None, timeout: float = 180.0) -> dict[str, Any]:
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot_observation(spec: dict[str, Any]) -> dict[str, Any]:
    kind = str(spec.get("kind") or "").strip()
    path = Path(str(spec.get("path") or "")).expanduser()
    payload: dict[str, Any] = {
        "observation_id": str(spec.get("observation_id") or ""),
        "kind": kind,
        "path": str(path),
        "exists": path.exists(),
    }
    if kind == "file":
        if path.exists() and path.is_file():
            text = _read_text(path)
            payload.update(
                {
                    "text": text,
                    "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
        else:
            payload.update({"text": "", "size_bytes": 0, "sha256": ""})
        return payload
    if kind == "directory":
        entries: list[str] = []
        if path.exists() and path.is_dir():
            entries = sorted(child.name for child in path.iterdir())
        payload["entries"] = entries
        return payload
    return payload


def _apply_fixtures(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for fixture in list(fixtures or []):
        path = Path(str(fixture.get("path") or "")).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(fixture.get("content") or "")
        path.write_text(content, encoding="utf-8")
        created.append({"path": str(path), "content": content})
    return created


def _collect_runtime_events(base_url: str, conversation_id: str) -> dict[str, Any]:
    try:
        query = parse.urlencode({"session": conversation_id, "limit": 200})
        payload = _read_json(f"{base_url.rstrip('/')}/api/runtime/events?{query}", timeout=10.0)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "events": [],
        }
    events = list(payload.get("events") or [])
    return {
        "ok": True,
        "events": events,
        "next_after": int(payload.get("next_after") or 0),
    }


def _collect_operator_snapshot(
    base_url: str,
    *,
    conversation_id: str,
    query_text: str,
    topic_hints: list[str] | None = None,
) -> dict[str, Any]:
    try:
        query = parse.urlencode(
            [
                ("session", conversation_id),
                ("query", query_text),
                *[("topic_hint", str(item)) for item in list(topic_hints or []) if str(item).strip()],
            ]
        )
        payload = _read_json(f"{base_url.rstrip('/')}/api/runtime/operator-snapshot?{query}", timeout=10.0)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }
    return {"ok": True, **dict(payload or {})}


def run_procedural_pack(
    *,
    base_url: str,
    pack: dict[str, Any],
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    scenarios_out: list[dict[str, Any]] = []
    turns_latency_rows: list[dict[str, Any]] = []

    for scenario in list(pack.get("scenarios") or []):
        workspace = Path(str(scenario.get("workspace") or "")).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        messages: list[dict[str, str]] = []
        created_fixtures = _apply_fixtures(list(scenario.get("fixtures") or []))
        turns_out: list[dict[str, Any]] = []
        conversation_id = str(scenario.get("conversation_id") or scenario.get("scenario_id") or "").strip()
        source_context = dict(scenario.get("source_context") or {})

        for turn in list(scenario.get("turns") or []):
            prompt = str(turn.get("prompt") or "")
            body = {
                "model": "vool",
                "messages": [*messages, {"role": "user", "content": prompt}],
                "stream": False,
                "workspace": str(workspace),
                "conversationId": conversation_id,
                "source_context": source_context,
            }
            turn_started = time.perf_counter()
            payload: dict[str, Any]
            error = ""
            try:
                payload = _read_json(f"{base_url.rstrip('/')}/api/chat", data=body, timeout=timeout_seconds)
            except Exception as exc:  # pragma: no cover - live transport defense
                payload = {"error": str(exc), "message": {"role": "assistant", "content": ""}}
                error = str(exc)
            latency = round(time.perf_counter() - turn_started, 3)
            response_text = str(dict(payload.get("message") or {}).get("content") or "").strip()
            turns_out.append(
                {
                    "turn_id": str(turn.get("turn_id") or ""),
                    "prompt": prompt,
                    "response_text": response_text,
                    "latency_seconds": latency,
                    "raw_payload": payload,
                    "error": error,
                }
            )
            messages.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": response_text},
                ]
            )
            turns_latency_rows.append(
                {
                    "request_type": str(scenario.get("family") or "procedural"),
                    "latency_seconds": latency,
                }
            )

        observations = {
            str(spec.get("observation_id") or ""): _snapshot_observation(spec)
            for spec in list(scenario.get("observations") or [])
        }
        runtime_events = _collect_runtime_events(base_url, conversation_id)
        operator_snapshot_query = str(
            scenario.get("operator_snapshot_query")
            or (turns_out[-1]["prompt"] if turns_out else "")
            or ""
        ).strip()
        operator_snapshot = _collect_operator_snapshot(
            base_url,
            conversation_id=conversation_id,
            query_text=operator_snapshot_query,
            topic_hints=[
                str(item).strip()
                for item in list(scenario.get("operator_snapshot_topic_hints") or [])
                if str(item).strip()
            ],
        )
        scenarios_out.append(
            {
                "scenario_id": str(scenario.get("scenario_id") or ""),
                "family": str(scenario.get("family") or ""),
                "title": str(scenario.get("title") or ""),
                "description": str(scenario.get("description") or ""),
                "workspace": str(workspace),
                "conversation_id": conversation_id,
                "source_context": source_context,
                "created_fixtures": created_fixtures,
                "turns": turns_out,
                "observations": observations,
                "runtime_events": runtime_events,
                "operator_snapshot": operator_snapshot,
            }
        )

    return {
        "seed": int(pack.get("seed") or 0),
        "generated_at_utc": str(pack.get("generated_at_utc") or ""),
        "base_url": base_url.rstrip("/"),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "turn_latency_rows": turns_latency_rows,
        "scenarios": scenarios_out,
    }
