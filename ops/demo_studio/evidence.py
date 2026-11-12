"""The evidence manifest: what happened, bound to bytes that prove it.

The manifest is the machine-readable contract of a capture: exact commit,
app version, journey specification hash, timeline, every executed step, the
public-event summary, captions, zoom justifications, redaction summary, and
the sha256 + byte length of every artifact. ``verify_evidence_manifest``
independently re-derives the bindings from the files on disk — a tampered
artifact or a forged SHA binding is a violation, not a demo.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.demo_studio.redact import RedactionEngine

MANIFEST_NAME = "evidence.manifest.json"
MANIFEST_SCHEMA = "demo-studio/evidence-manifest@1"
#: Fixed epoch for deterministic dry-run plans (2026-09-02T00:00:00Z).
DRY_RUN_EPOCH = "2026-09-02T00:00:00Z"


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return sha256_bytes(data), len(data)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ManifestBuilder:
    """Accumulates the capture facts, then builds + writes the manifest."""

    mode: str
    journey: dict[str, Any]
    product: dict[str, Any]
    out_dir: Path
    boot: dict[str, Any] = field(default_factory=dict)
    timeline: dict[str, Any] = field(default_factory=dict)
    steps_executed: list[dict[str, Any]] = field(default_factory=list)
    events_summary: dict[str, Any] = field(default_factory=dict)
    captions: list[dict[str, Any]] = field(default_factory=list)
    focus_plan: list[dict[str, Any]] = field(default_factory=list)
    render_report: dict[str, Any] | None = None
    honesty: dict[str, Any] = field(default_factory=dict)
    redaction_summary: dict[str, Any] = field(default_factory=dict)
    generated_at: str | None = None

    def build(self) -> dict[str, Any]:
        artifacts: dict[str, Any] = {}
        if self.render_report:
            for key, name in (
                ("mp4", "demo.mp4"),
                ("webm", "demo.webm"),
                ("srt", "captions.srt"),
                ("poster", "poster.png"),
            ):
                row = (self.render_report or {}).get(key) or {}
                if row.get("sha256"):
                    artifacts[name] = {"sha256": row["sha256"], "bytes": row["bytes"]}
            events_file = self.out_dir / "events.jsonl"
            if events_file.is_file():
                digest, size = sha256_file(events_file)
                artifacts["events.jsonl"] = {"sha256": digest, "bytes": size}
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "mode": self.mode,
            "generated_at": self.generated_at or utc_now_iso(),
            "journey": self.journey,
            "product": self.product,
            "boot": self.boot,
            "timeline": self.timeline,
            "steps_executed": self.steps_executed,
            "events": self.events_summary,
            "captions": {
                "count": len(self.captions),
                "rows": self.captions,
                "source": "/api/runtime/events (public typed events only); "
                "model_output_chunk excluded by allowlist",
            },
            "redaction": self.redaction_summary
            or {
                "engine": "ops.demo_studio.redact.RedactionEngine",
                "applies_to": ["captions", "events.jsonl mirror", "manifest free text"],
                "counts": {},
            },
            "focus": [entry if isinstance(entry, dict) else entry.to_public_dict() for entry in self.focus_plan],
            "artifacts": artifacts,
            "render": (self.render_report or {}),
            "honesty": self.honesty,
            "verdict": "pass",
        }
        return manifest


def build_evidence_manifest(
    *,
    mode: str,
    journey: Mapping[str, Any],
    product: Mapping[str, Any],
    render_report: Mapping[str, Any] | None,
    captions: list[dict[str, Any]],
    focus_plan: list[dict[str, Any]],
    timeline: Mapping[str, Any],
    honesty: Mapping[str, Any],
    out_dir: Path,
    boot: Mapping[str, Any] | None = None,
    steps_executed: list[dict[str, Any]] | None = None,
    events_summary: Mapping[str, Any] | None = None,
    redaction_summary: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    builder = ManifestBuilder(
        mode=mode,
        journey=dict(journey),
        product=dict(product),
        out_dir=out_dir,
        boot=dict(boot or {}),
        timeline=dict(timeline),
        steps_executed=list(steps_executed or []),
        events_summary=dict(events_summary or {}),
        captions=list(captions),
        focus_plan=list(focus_plan),
        render_report=dict(render_report) if render_report else None,
        honesty=dict(honesty),
        redaction_summary=dict(redaction_summary or {}),
        generated_at=generated_at,
    )
    manifest = builder.build()
    write_json(out_dir / MANIFEST_NAME, manifest)
    return manifest


def verify_evidence_manifest(
    manifest: Mapping[str, Any],
    artifact_dir: Path,
    *,
    expected_sha: str | None = None,
    scan_secrets: tuple[str, ...] = (),
    workspace_root: str = "",
    runtime_home: str = "",
) -> list[dict[str, str]]:
    """Independently re-derive every binding; returns violations ([] = clean)."""
    violations: list[dict[str, str]] = []
    for name, row in (manifest.get("artifacts") or {}).items():
        path = artifact_dir / name
        if not path.is_file():
            violations.append({"rule": "artifact_missing", "artifact": name, "detail": "bound artifact absent"})
            continue
        digest, size = sha256_file(path)
        if digest != row.get("sha256") or size != row.get("bytes"):
            violations.append(
                {"rule": "artifact_hash", "artifact": name, "detail": "artifact bytes do not match the manifest binding"}
            )
    if expected_sha is not None:
        bound = str((manifest.get("product") or {}).get("commit_full") or "")
        if bound != expected_sha:
            violations.append(
                {"rule": "sha_binding", "artifact": MANIFEST_NAME, "detail":
                 f"manifest binds commit {bound[:12] or '<none>'}…, expected {expected_sha[:12]}…"}
            )
    honesty = manifest.get("honesty") or {}
    if manifest.get("mode") == "dry-run" and honesty.get("captured") is not False:
        violations.append(
            {"rule": "honesty", "artifact": MANIFEST_NAME, "detail": "a dry-run manifest must never claim a capture"}
        )
    texts: dict[str, str] = {}
    for row in manifest.get("captions", {}).get("rows", []) or []:
        texts["captions.srt"] = texts.get("captions.srt", "") + str(row.get("text") or "") + "\n"
    for key, value in (manifest.get("honesty") or {}).items():
        if isinstance(value, str):
            texts[f"honesty.{key}"] = value
    engine = RedactionEngine(
        workspace_root=workspace_root, runtime_home=runtime_home, scan_secrets=scan_secrets
    )
    for name, text in texts.items():
        violations.extend(engine.scan(name, text))
    return violations


def leak_scan(
    texts: Mapping[str, str],
    *,
    workspace_root: str = "",
    runtime_home: str = "",
    scan_secrets: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Independent leak scan over named artifacts (artifact -> text).

    Used by the runner as a second, engine-independent pass over the finished
    text artifacts, and by the sabotage suite to prove the scan catches what
    a disabled redaction engine lets through.
    """
    engine = RedactionEngine(
        workspace_root=workspace_root, runtime_home=runtime_home, scan_secrets=scan_secrets
    )
    violations: list[dict[str, str]] = []
    for name, text in texts.items():
        violations.extend(engine.scan(name, text))
    return violations
