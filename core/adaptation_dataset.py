from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.persistent_memory import conversation_log_path, ensure_memory_files
from core.runtime_paths import data_path
from storage.adaptation_store import get_adaptation_corpus, update_corpus_build
from storage.db import get_connection
from storage.useful_output_store import list_useful_outputs, sync_useful_outputs

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(password|passphrase|secret|api[_ -]?key|private[_ -]?key|seed phrase|mnemonic)\b", re.IGNORECASE),
    re.compile(r"\b(sk|ghp|xoxb|xoxp|rk)_[A-Za-z0-9_-]{16,}\b"),
)

_LOW_SIGNAL_INSTRUCTION_PATTERNS = (
    re.compile(r"^(gm|hi|hello|hey|ok|okay|cool|nice|thanks?|thx)[!. ]*$", re.IGNORECASE),
    re.compile(r"^/(new|reset|trace|rail|task-rail)$", re.IGNORECASE),
)

_LOW_SIGNAL_OUTPUT_PATTERNS = (
    re.compile(r"\bi won't fake it\b", re.IGNORECASE),
    re.compile(r"\binvalid tool payload\b", re.IGNORECASE),
    re.compile(r"\bi'm here and ready to help\b", re.IGNORECASE),
    re.compile(r"say\s+\"pull hive tasks\"", re.IGNORECASE),
    re.compile(r"^real steps completed:\s*- unknown", re.IGNORECASE),
)

_TASK_LIST_OUTPUT_RE = re.compile(r"available hive tasks right now", re.IGNORECASE)
_RESEARCH_START_OUTPUT_RE = re.compile(r"started hive research on", re.IGNORECASE)
_RESEARCH_STATUS_OUTPUT_RE = re.compile(r"\bis still [`']?researching[`']?", re.IGNORECASE)
_GENERIC_SUGGEST_RE = re.compile(r"^here'?s what i'?d suggest", re.IGNORECASE)
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+\b")


@dataclass
class CorpusBuildResult:
    corpus_id: str
    output_path: str
    example_count: int
    source_stats: dict[str, int]


@dataclass
class AdaptationExample:
    instruction: str
    output: str
    source: str
    metadata: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "instruction": self.instruction,
                "output": self.output,
                "source": self.source,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass
class CuratedCorpusResult:
    rows: list[dict[str, Any]]
    details: dict[str, Any]


def build_adaptation_corpus(corpus_id: str) -> CorpusBuildResult:
    spec = get_adaptation_corpus(corpus_id)
    if not spec:
        raise ValueError(f"Unknown adaptation corpus: {corpus_id}")
    source_config = dict(spec.get("source_config") or {})
    filters = dict(spec.get("filters") or {})
    if bool(source_config.get("imported")):
        out_path = str(spec.get("output_path") or "").strip()
        if not out_path:
            raise RuntimeError("Imported adaptation corpus is missing output_path.")
        path = Path(out_path)
        if not path.exists():
            raise FileNotFoundError(f"Imported adaptation corpus is missing: {path}")
        rows = load_adaptation_examples(path)
        source_stats = {"imported": len(rows)}
        update_corpus_build(corpus_id, output_path=str(path), example_count=len(rows), source_stats=source_stats)
        return CorpusBuildResult(
            corpus_id=corpus_id,
            output_path=str(path),
            example_count=len(rows),
            source_stats=source_stats,
        )
    limit_per_source = max(1, int(source_config.get("limit_per_source") or 250))
    examples: list[AdaptationExample] = []
    source_stats: dict[str, int] = {
        "useful_outputs": 0,
        "conversations": 0,
        "final_responses": 0,
        "hive_posts": 0,
        "task_results": 0,
        "proof_backed_examples": 0,
        "finalized_examples": 0,
        "commons_reviewed_examples": 0,
        "skipped_sensitive": 0,
        "skipped_short": 0,
        "deduped": 0,
    }

    include_useful_outputs = bool(source_config.get("include_useful_outputs", True))
    structured_sources = {
        "task_result": bool(source_config.get("include_task_results", True)),
        "final_response": bool(source_config.get("include_final_responses", True)),
        "hive_post": bool(source_config.get("include_hive_posts", True)),
    }

    if include_useful_outputs and any(structured_sources.values()):
        useful_output_examples = _useful_output_examples(
            limit=limit_per_source,
            filters=filters,
            include_sources=tuple(source for source, enabled in structured_sources.items() if enabled),
        )
        source_stats["useful_outputs"] = len(useful_output_examples)
        yielded_sources = {item.source for item in useful_output_examples}
        if useful_output_examples:
            source_stats["task_results"] = sum(1 for item in useful_output_examples if item.source == "task_result")
            source_stats["final_responses"] = sum(1 for item in useful_output_examples if item.source == "final_response")
            source_stats["hive_posts"] = sum(1 for item in useful_output_examples if item.source == "hive_post")
            examples.extend(useful_output_examples)
        else:
            source_stats["useful_outputs"] = 0
            yielded_sources = set()
        fallback_sources = 0
        if bool(source_config.get("include_final_responses", True)) and "final_response" not in yielded_sources:
            final_examples = _final_response_examples(limit=limit_per_source, filters=filters)
            source_stats["final_responses"] += len(final_examples)
            examples.extend(final_examples)
            fallback_sources += 1
        if bool(source_config.get("include_hive_posts", True)) and "hive_post" not in yielded_sources:
            hive_examples = _hive_post_examples(limit=limit_per_source, filters=filters)
            source_stats["hive_posts"] += len(hive_examples)
            examples.extend(hive_examples)
            fallback_sources += 1
        if bool(source_config.get("include_task_results", True)) and "task_result" not in yielded_sources:
            task_result_examples = _task_result_examples(limit=limit_per_source, filters=filters)
            source_stats["task_results"] += len(task_result_examples)
            examples.extend(task_result_examples)
            fallback_sources += 1
        if fallback_sources:
            source_stats["fallback_to_raw_sources"] = fallback_sources
    else:
        if bool(source_config.get("include_final_responses", True)):
            final_examples = _final_response_examples(limit=limit_per_source, filters=filters)
            source_stats["final_responses"] = len(final_examples)
            examples.extend(final_examples)
        if bool(source_config.get("include_hive_posts", True)):
            hive_examples = _hive_post_examples(limit=limit_per_source, filters=filters)
            source_stats["hive_posts"] = len(hive_examples)
            examples.extend(hive_examples)
        if bool(source_config.get("include_task_results", True)):
            task_result_examples = _task_result_examples(limit=limit_per_source, filters=filters)
            source_stats["task_results"] = len(task_result_examples)
            examples.extend(task_result_examples)

    if bool(source_config.get("include_conversations", True)):
        conversation_examples = _conversation_examples(limit=limit_per_source, filters=filters)
        source_stats["conversations"] = len(conversation_examples)
        examples.extend(conversation_examples)

    unique_examples: list[AdaptationExample] = []
    seen: set[str] = set()
    for item in examples:
        digest = hashlib.sha256(f"{item.source}\n{item.instruction}\n{item.output}".encode()).hexdigest()
        if digest in seen:
            source_stats["deduped"] += 1
            continue
        seen.add(digest)
        unique_examples.append(item)

    curated = curate_adaptation_rows(
        [json.loads(item.to_json()) for item in unique_examples],
        filters=filters,
    )
    source_stats["curated_examples"] = len(curated.rows)
    source_stats["filtered_out"] = max(0, len(unique_examples) - len(curated.rows))
    source_stats["structured_examples"] = int(curated.details.get("structured_examples") or 0)
    source_stats["conversation_examples"] = int(curated.details.get("conversation_examples") or 0)
    source_stats["high_signal_examples"] = int(curated.details.get("high_signal_examples") or 0)
    source_stats["archive_candidate_examples"] = int(curated.details.get("archive_candidate_examples") or 0)
    source_stats["training_eligible_examples"] = int(curated.details.get("training_eligible_examples") or 0)
    source_stats["proof_backed_examples"] = int(curated.details.get("proof_backed_examples") or 0)
    source_stats["finalized_examples"] = int(curated.details.get("finalized_examples") or 0)
    source_stats["commons_reviewed_examples"] = int(curated.details.get("commons_reviewed_examples") or 0)
    source_stats["conversation_ratio"] = float(curated.details.get("conversation_ratio") or 0.0)

    out_path = str(spec.get("output_path") or "").strip()
    if not out_path:
        out_path = str(data_path("adaptation", "corpora", f"{corpus_id}.jsonl"))
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in curated.rows:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    update_corpus_build(corpus_id, output_path=str(path), example_count=len(curated.rows), source_stats=source_stats)
    return CorpusBuildResult(
        corpus_id=corpus_id,
        output_path=str(path),
        example_count=len(curated.rows),
        source_stats=source_stats,
    )


def load_adaptation_examples(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidate = Path(path)
    if not candidate.exists():
        return rows
    for raw in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _normalize_text_for_example(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _useful_output_examples(
    *,
    limit: int,
    filters: dict[str, Any],
    include_sources: tuple[str, ...],
) -> list[AdaptationExample]:
    sync_useful_outputs()
    rows = list_useful_outputs(
        source_types=include_sources,
        eligibility_state="eligible",
        limit=max(1, limit * 4),
    )
    out: list[AdaptationExample] = []
    for row in rows:
        instruction = _normalize_text_for_example(str(row.get("instruction_text") or ""))
        output = _normalize_text_for_example(str(row.get("output_text") or ""))
        normalized = _sanitize_example(instruction, output, filters=filters)
        if normalized is None:
            continue
        instruction, output = normalized
        metadata = dict(row.get("metadata") or {})
        metadata.update(
            {
                "useful_output_id": str(row.get("useful_output_id") or ""),
                "task_id": str(row.get("task_id") or ""),
                "topic_id": str(row.get("topic_id") or ""),
                "claim_id": str(row.get("claim_id") or ""),
                "result_id": str(row.get("result_id") or ""),
                "archive_state": str(row.get("archive_state") or ""),
                "eligibility_state": str(row.get("eligibility_state") or ""),
                "durability_reasons": list(row.get("durability_reasons") or []),
                "eligibility_reasons": list(row.get("eligibility_reasons") or []),
                "artifact_ids": list(row.get("artifact_ids") or []),
                "quality_score": float(row.get("quality_score") or 0.0),
                "source_created_at": str(row.get("source_created_at") or ""),
                "source_updated_at": str(row.get("source_updated_at") or ""),
            }
        )
        out.append(
            AdaptationExample(
                instruction=instruction,
                output=output,
                source=str(row.get("source_type") or "unknown"),
                metadata=metadata,
            )
        )
        if len(out) >= limit:
            break
    return out


def _conversation_examples(*, limit: int, filters: dict[str, Any]) -> list[AdaptationExample]:
    ensure_memory_files()
    path = conversation_log_path()
    if not path.exists():
        return []
    rows: list[AdaptationExample] = []
    for raw in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if len(rows) >= limit:
            break
        line = raw.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        normalized = _sanitize_example(str(item.get("user") or ""), str(item.get("assistant") or ""), filters=filters)
        if normalized is None:
            continue
        instruction, output = normalized
        rows.append(
            AdaptationExample(
                instruction=instruction,
                output=output,
                source="conversation",
                metadata={
                    "session_id": str(item.get("session_id") or ""),
                    # A8 pass-001 payload lineage passthrough ('' on legacy
                    # rows — never fabricated) for erasure traversal.
                    "request_id": str(item.get("request_id") or ""),
                    "share_scope": str(item.get("share_scope") or "local_only"),
                    "surface": str(item.get("surface") or ""),
                    "platform": str(item.get("platform") or ""),
                    "ts": str(item.get("ts") or ""),
                },
            )
        )
    rows.reverse()
    return rows


def _final_response_examples(*, limit: int, filters: dict[str, Any]) -> list[AdaptationExample]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT lt.task_id, lt.task_summary, lt.task_class, fr.rendered_persona_text, fr.status_marker, fr.created_at
            FROM finalized_responses fr
            JOIN local_tasks lt ON lt.task_id = fr.parent_task_id
            ORDER BY fr.created_at DESC
            LIMIT ?
            """,
            (limit * 2,),
        ).fetchall()
    finally:
        conn.close()
    out: list[AdaptationExample] = []
    for row in rows:
        normalized = _sanitize_example(str(row["task_summary"] or ""), str(row["rendered_persona_text"] or ""), filters=filters)
        if normalized is None:
            continue
        instruction, output = normalized
        out.append(
            AdaptationExample(
                instruction=instruction,
                output=output,
                source="final_response",
                metadata={
                    "task_id": str(row["task_id"] or ""),
                    "task_class": str(row["task_class"] or ""),
                    "status_marker": str(row["status_marker"] or ""),
                    "created_at": str(row["created_at"] or ""),
                },
            )
        )
        if len(out) >= limit:
            break
    return out


def _hive_post_examples(*, limit: int, filters: dict[str, Any]) -> list[AdaptationExample]:
    conn = get_connection()
    try:
        hive_post_columns = _table_columns(conn, "hive_posts")
        moderation_filter = ""
        if "moderation_state" in hive_post_columns:
            moderation_filter = "WHERE COALESCE(hp.moderation_state, 'approved') = 'approved'"
        rows = conn.execute(
            f"""
            SELECT hp.post_id, hp.post_kind, hp.stance, hp.body, hp.created_at,
                   ht.topic_id, ht.title, ht.summary
            FROM hive_posts hp
            JOIN hive_topics ht ON ht.topic_id = hp.topic_id
            {moderation_filter}
            ORDER BY hp.created_at DESC
            LIMIT ?
            """,
            (limit * 2,),
        ).fetchall()
    finally:
        conn.close()
    out: list[AdaptationExample] = []
    for row in rows:
        instruction = (
            f"Hive topic: {str(row['title'] or '').strip()}\n"
            f"Topic summary: {str(row['summary'] or '').strip()}\n"
            f"Post kind: {str(row['post_kind'] or '').strip()}\n"
            f"Stance: {str(row['stance'] or '').strip()}\n"
            "Write the next useful Hive contribution."
        ).strip()
        normalized = _sanitize_example(instruction, str(row["body"] or ""), filters=filters)
        if normalized is None:
            continue
        instruction, output = normalized
        out.append(
            AdaptationExample(
                instruction=instruction,
                output=output,
                source="hive_post",
                metadata={
                    "post_id": str(row["post_id"] or ""),
                    "topic_id": str(row["topic_id"] or ""),
                    "post_kind": str(row["post_kind"] or ""),
                    "stance": str(row["stance"] or ""),
                    "created_at": str(row["created_at"] or ""),
                },
            )
        )
        if len(out) >= limit:
            break
    return out


def _task_result_examples(*, limit: int, filters: dict[str, Any]) -> list[AdaptationExample]:
    conn = get_connection()
    try:
        if not _table_columns(conn, "task_results"):
            return []
        rows = conn.execute(
            """
            SELECT tr.result_id, tr.task_id, tr.result_type, tr.summary, tr.confidence, tr.status,
                   tr.risk_flags_json, tr.created_at, tr.updated_at,
                   toff.summary AS task_summary, toff.task_type,
                   rv.outcome, rv.helpfulness_score, rv.quality_score, rv.harmful_flag
            FROM task_results tr
            LEFT JOIN task_offers toff ON toff.task_id = tr.task_id
            LEFT JOIN task_reviews rv
              ON rv.task_id = tr.task_id
             AND rv.helper_peer_id = tr.helper_peer_id
            WHERE tr.status IN ('accepted', 'reviewed', 'partial')
              AND COALESCE(rv.harmful_flag, 0) = 0
            ORDER BY tr.updated_at DESC
            LIMIT ?
            """,
            (limit * 2,),
        ).fetchall()
    finally:
        conn.close()
    out: list[AdaptationExample] = []
    for row in rows:
        instruction = (
            f"Task type: {str(row['task_type'] or row['result_type'] or '').strip()}\n"
            f"Task summary: {str(row['task_summary'] or '').strip()}\n"
            "Write the kind of accepted worker result VOOL should produce."
        ).strip()
        normalized = _sanitize_example(instruction, str(row["summary"] or ""), filters=filters)
        if normalized is None:
            continue
        instruction, output = normalized
        out.append(
            AdaptationExample(
                instruction=instruction,
                output=output,
                source="task_result",
                metadata={
                    "result_id": str(row["result_id"] or ""),
                    "task_id": str(row["task_id"] or ""),
                    "result_type": str(row["result_type"] or ""),
                    "status": str(row["status"] or ""),
                    "confidence": float(row["confidence"] or 0.0),
                    "review_outcome": str(row["outcome"] or ""),
                    "helpfulness_score": float(row["helpfulness_score"] or 0.0),
                    "quality_score": float(row["quality_score"] or 0.0),
                    "risk_flags": _json_loads(row["risk_flags_json"], fallback=[]),
                    "created_at": str(row["created_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
                },
            )
        )
        if len(out) >= limit:
            break
    return out


def _sanitize_example(instruction: str, output: str, *, filters: dict[str, Any]) -> tuple[str, str] | None:
    min_instruction = max(1, int(filters.get("min_instruction_chars") or 12))
    min_output = max(1, int(filters.get("min_output_chars") or 24))
    max_instruction = max(min_instruction, int(filters.get("max_instruction_chars") or 6000))
    max_output = max(min_output, int(filters.get("max_output_chars") or 12000))
    clean_instruction = " ".join(str(instruction or "").strip().split())
    clean_output = " ".join(str(output or "").strip().split())
    if len(clean_instruction) < min_instruction or len(clean_output) < min_output:
        return None
    if len(clean_instruction) > max_instruction:
        clean_instruction = clean_instruction[:max_instruction]
    if len(clean_output) > max_output:
        clean_output = clean_output[:max_output]
    if _looks_sensitive(clean_instruction) or _looks_sensitive(clean_output):
        return None
    return clean_instruction, clean_output


def _looks_sensitive(text: str) -> bool:
    candidate = str(text or "")
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in _SECRET_PATTERNS)


def _table_columns(conn: Any, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    return {str(row["name"]) for row in rows if row and row["name"]}


def curate_adaptation_rows(
    rows: list[dict[str, Any]],
    *,
    filters: dict[str, Any] | None = None,
    max_examples: int | None = None,
) -> CuratedCorpusResult:
    effective_filters = dict(filters or {})
    if not rows:
        return CuratedCorpusResult(rows=[], details={"kept": 0, "dropped": 0})
    scored: list[tuple[int, float, dict[str, Any], str, str]] = []
    dropped_low_signal = 0
    for index, row in enumerate(rows):
        normalized = _normalize_loaded_row(row)
        score = _example_signal_score(normalized)
        minimum = _minimum_signal_for_source(normalized, effective_filters)
        if score < minimum:
            dropped_low_signal += 1
            continue
        output_fingerprint = _normalized_fingerprint(str(normalized.get("output") or ""))
        scored.append((index, score, normalized, output_fingerprint, str(normalized.get("source") or "unknown")))
    scored.sort(key=lambda item: (-item[1], _example_sort_key(item[2]), item[0]))
    structured_present = any(source != "conversation" for _, _, _, _, source in scored)
    max_conv_share = float(effective_filters.get("max_conversation_share_when_structured_present", 0.55) or 0.55)
    conversation_cap = None
    if structured_present:
        conversation_cap = max(2, int(max(1, max_examples or len(scored)) * max_conv_share))
    duplicate_cap = max(1, int(effective_filters.get("max_duplicate_output_fingerprint", 2) or 2))
    duplicate_cap_conversation = max(1, int(effective_filters.get("max_duplicate_output_fingerprint_conversation", 1) or 1))
    kept: list[tuple[int, dict[str, Any]]] = []
    source_counts: dict[str, int] = {}
    fingerprint_counts: dict[str, int] = {}
    dropped_duplicate = 0
    dropped_source_cap = 0
    high_signal_examples = 0
    archive_candidate_examples = 0
    training_eligible_examples = 0
    proof_backed_examples = 0
    finalized_examples = 0
    commons_reviewed_examples = 0
    for index, _score, row, fingerprint, source in scored:
        if max_examples is not None and len(kept) >= max(1, int(max_examples)):
            break
        if conversation_cap is not None and source == "conversation" and source_counts.get(source, 0) >= conversation_cap:
            dropped_source_cap += 1
            continue
        allowed_duplicates = duplicate_cap_conversation if source == "conversation" else duplicate_cap
        if fingerprint and fingerprint_counts.get(fingerprint, 0) >= allowed_duplicates:
            dropped_duplicate += 1
            continue
        kept.append((index, row))
        source_counts[source] = source_counts.get(source, 0) + 1
        metadata = dict(row.get("metadata") or {})
        if _example_signal_score(row) >= 0.7:
            high_signal_examples += 1
        if str(metadata.get("archive_state") or "").strip().lower() in {"candidate", "approved"}:
            archive_candidate_examples += 1
        if str(metadata.get("eligibility_state") or "").strip().lower() == "eligible":
            training_eligible_examples += 1
        finality_state = str(metadata.get("finality_state") or "").strip().lower()
        if bool(metadata.get("proof_backed")) or finality_state in {"confirmed", "finalized"}:
            proof_backed_examples += 1
        if finality_state == "finalized":
            finalized_examples += 1
        if source == "hive_post" and str(metadata.get("promotion_review_state") or "").strip().lower() == "approved":
            commons_reviewed_examples += 1
        if fingerprint:
            fingerprint_counts[fingerprint] = fingerprint_counts.get(fingerprint, 0) + 1
    kept.sort(key=lambda item: item[0])
    curated_rows = [row for _, row in kept]
    conversation_examples = int(source_counts.get("conversation", 0))
    structured_examples = max(0, len(curated_rows) - conversation_examples)
    return CuratedCorpusResult(
        rows=curated_rows,
        details={
            "kept": len(curated_rows),
            "dropped": max(0, len(rows) - len(curated_rows)),
            "dropped_low_signal": dropped_low_signal,
            "dropped_duplicate": dropped_duplicate,
            "dropped_source_cap": dropped_source_cap,
            "source_counts": source_counts,
            "structured_present": structured_present,
            "structured_examples": structured_examples,
            "conversation_examples": conversation_examples,
            "conversation_ratio": round(conversation_examples / max(1, len(curated_rows)), 4) if curated_rows else 0.0,
            "high_signal_examples": high_signal_examples,
            "archive_candidate_examples": archive_candidate_examples,
            "training_eligible_examples": training_eligible_examples,
            "proof_backed_examples": proof_backed_examples,
            "finalized_examples": finalized_examples,
            "commons_reviewed_examples": commons_reviewed_examples,
        },
    )


def score_adaptation_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "quality_score": 0.0,
            "details": {
                "volume_score": 0.0,
                "diversity_score": 0.0,
                "uniqueness_score": 0.0,
                "substance_score": 0.0,
                "recency_score": 0.0,
                "signal_score": 0.0,
                "source_mix_score": 0.0,
                "source_counts": {},
                "average_output_chars": 0.0,
                "average_signal": 0.0,
                "dated_examples": 0,
                "recent_examples": 0,
                "low_signal_examples": 0,
                "high_signal_examples": 0,
                "proof_backed_examples": 0,
                "finalized_examples": 0,
                "commons_reviewed_examples": 0,
            },
        }
    source_counts: dict[str, int] = {}
    instruction_hashes: set[str] = set()
    output_fingerprints: set[str] = set()
    output_lengths: list[int] = []
    signal_scores: list[float] = []
    recent_hits = 0
    dated_examples = 0
    low_signal_examples = 0
    high_signal_examples = 0
    for row in rows:
        normalized = _normalize_loaded_row(row)
        source = str(normalized.get("source") or "unknown").strip() or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
        instruction_hashes.add(hashlib.sha256(str(normalized.get("instruction") or "").encode("utf-8")).hexdigest())
        output_fingerprints.add(_normalized_fingerprint(str(normalized.get("output") or "")))
        output_lengths.append(len(str(normalized.get("output") or "").strip()))
        signal = _example_signal_score(normalized)
        signal_scores.append(signal)
        if signal < 0.35:
            low_signal_examples += 1
        if signal >= 0.7:
            high_signal_examples += 1
        ts = _example_timestamp(normalized)
        if ts is not None:
            dated_examples += 1
            if (datetime.now(timezone.utc) - ts).days <= 30:
                recent_hits += 1
    average_output_len = sum(output_lengths) / max(1, len(output_lengths))
    average_signal = sum(signal_scores) / max(1, len(signal_scores))
    conversation_ratio = source_counts.get("conversation", 0) / max(1, len(rows))
    proof_backed_count = sum(
        1
        for row in rows
        if bool(dict(row.get("metadata") or {}).get("proof_backed"))
        or str(dict(row.get("metadata") or {}).get("finality_state") or "").strip().lower() in {"confirmed", "finalized"}
    )
    commons_reviewed_count = sum(
        1
        for row in rows
        if str(row.get("source") or "").strip().lower() == "hive_post"
        and str(dict(row.get("metadata") or {}).get("promotion_review_state") or "").strip().lower() == "approved"
    )
    premium_signal_ratio = min(1.0, (proof_backed_count + commons_reviewed_count) / max(1, len(rows)))
    volume_score = min(1.0, len(rows) / 24.0)
    diversity_score = min(1.0, len(source_counts) / 4.0)
    uniqueness_score = min(1.0, (len(instruction_hashes) + len(output_fingerprints)) / max(1, len(rows) * 2))
    substance_score = min(1.0, average_output_len / 320.0)
    recency_score = (recent_hits / dated_examples) if (dated_examples and recent_hits) else 0.55
    signal_score = min(1.0, average_signal)
    source_mix_score = max(0.0, 1.0 - max(0.0, conversation_ratio - 0.35))
    # Proof-backed and commons-reviewed examples represent the highest-quality training signal;
    # reward corpora that are dense with such examples even when overall volume is small.
    premium_signal_bonus = round(0.10 * premium_signal_ratio, 4)
    quality_score = round(
        (0.18 * volume_score)
        + (0.10 * diversity_score)
        + (0.16 * uniqueness_score)
        + (0.12 * substance_score)
        + (0.10 * recency_score)
        + (0.24 * signal_score)
        + (0.10 * source_mix_score)
        + premium_signal_bonus,
        4,
    )
    return {
        "quality_score": quality_score,
        "details": {
            "volume_score": round(volume_score, 4),
            "diversity_score": round(diversity_score, 4),
            "uniqueness_score": round(uniqueness_score, 4),
            "substance_score": round(substance_score, 4),
            "recency_score": round(recency_score, 4),
            "signal_score": round(signal_score, 4),
            "source_mix_score": round(source_mix_score, 4),
            "source_counts": source_counts,
            "average_output_chars": round(average_output_len, 2),
            "average_signal": round(average_signal, 4),
            "dated_examples": dated_examples,
            "recent_examples": recent_hits,
            "low_signal_examples": low_signal_examples,
            "high_signal_examples": high_signal_examples,
            "conversation_ratio": round(conversation_ratio, 4),
            "structured_examples": max(0, len(rows) - int(source_counts.get("conversation", 0))),
            "conversation_examples": int(source_counts.get("conversation", 0)),
            "task_result_examples": int(source_counts.get("task_result", 0)),
            "final_response_examples": int(source_counts.get("final_response", 0)),
            "hive_post_examples": int(source_counts.get("hive_post", 0)),
            "training_eligible_examples": sum(
                1
                for row in rows
                if str(dict(row.get("metadata") or {}).get("eligibility_state") or "").strip().lower() == "eligible"
            ),
            "archive_candidate_examples": sum(
                1
                for row in rows
                if str(dict(row.get("metadata") or {}).get("archive_state") or "").strip().lower() in {"candidate", "approved"}
            ),
            "proof_backed_examples": sum(
                1
                for row in rows
                if bool(dict(row.get("metadata") or {}).get("proof_backed"))
                or str(dict(row.get("metadata") or {}).get("finality_state") or "").strip().lower() in {"confirmed", "finalized"}
            ),
            "finalized_examples": sum(
                1
                for row in rows
                if str(dict(row.get("metadata") or {}).get("finality_state") or "").strip().lower() == "finalized"
            ),
            "commons_reviewed_examples": sum(
                1
                for row in rows
                if str(row.get("source") or "").strip().lower() == "hive_post"
                and str(dict(row.get("metadata") or {}).get("promotion_review_state") or "").strip().lower() == "approved"
            ),
        },
    }


def _normalize_loaded_row(row: dict[str, Any]) -> dict[str, Any]:
    output = " ".join(str(row.get("output") or "").strip().split())
    instruction = " ".join(str(row.get("instruction") or "").strip().split())
    metadata = dict(row.get("metadata") or {})
    return {
        "instruction": instruction,
        "output": output,
        "source": str(row.get("source") or "unknown").strip() or "unknown",
        "metadata": metadata,
    }


def _minimum_signal_for_source(row: dict[str, Any], filters: dict[str, Any]) -> float:
    source = str(row.get("source") or "unknown").strip().lower()
    if source == "conversation":
        return float(filters.get("conversation_min_signal_score", 0.38) or 0.38)
    return float(filters.get("min_signal_score", 0.34) or 0.34)


def _example_signal_score(row: dict[str, Any]) -> float:
    instruction = str(row.get("instruction") or "").strip()
    output = str(row.get("output") or "").strip()
    source = str(row.get("source") or "unknown").strip().lower()
    metadata = dict(row.get("metadata") or {})
    score = {
        "task_result": 0.82,
        "final_response": 0.76,
        "hive_post": 0.7,
        "conversation": 0.38,
    }.get(source, 0.4)
    if any(pattern.search(instruction) for pattern in _LOW_SIGNAL_INSTRUCTION_PATTERNS):
        score -= 0.45
    if any(pattern.search(output) for pattern in _LOW_SIGNAL_OUTPUT_PATTERNS):
        score -= 0.6
    if _GENERIC_SUGGEST_RE.search(output):
        score -= 0.22
    if _TASK_LIST_OUTPUT_RE.search(output):
        score -= 0.18
    if _RESEARCH_START_OUTPUT_RE.search(output):
        score += 0.18
    if _RESEARCH_STATUS_OUTPUT_RE.search(output):
        score += 0.1
    output_len = len(output)
    if output_len >= 120:
        score += 0.08
    if output_len >= 220:
        score += 0.06
    if output_len >= 420:
        score += 0.04
    share_scope = str(metadata.get("share_scope") or "").strip().lower()
    if share_scope in {"hive_mind", "shared_pack"}:
        score += 0.08
    if source == "final_response":
        score += min(0.18, max(0.0, float(metadata.get("confidence_score") or 0.0)) * 0.2)
        status_marker = str(metadata.get("status_marker") or "").strip().lower()
        if status_marker in {"success", "finalized"}:
            score += 0.08
    if source == "hive_post":
        if str(metadata.get("moderation_state") or "approved").strip().lower() == "approved":
            score += 0.08
        post_kind = str(metadata.get("post_kind") or "").strip().lower()
        if post_kind in {"result", "summary"}:
            score += 0.1
        elif post_kind in {"analysis", "progress"}:
            score += 0.06
        promotion_review_state = str(metadata.get("promotion_review_state") or "").strip().lower()
        promotion_status = str(metadata.get("promotion_status") or "").strip().lower()
        support_weight = max(0.0, float(metadata.get("support_weight") or 0.0))
        challenge_weight = max(0.0, float(metadata.get("challenge_weight") or 0.0))
        downstream_use_count = max(0, int(metadata.get("downstream_use_count") or 0))
        training_signal_count = max(0, int(metadata.get("training_signal_count") or 0))
        if promotion_review_state == "approved":
            score += 0.14
        elif promotion_review_state == "pending":
            score -= 0.08
        if promotion_status in {"approved", "promoted"}:
            score += 0.08
        score += min(0.1, support_weight * 0.03)
        score -= min(0.12, challenge_weight * 0.04)
        score += min(0.08, downstream_use_count * 0.02)
        score += min(0.06, training_signal_count * 0.02)
    if source == "task_result":
        status = str(metadata.get("status") or "").strip().lower()
        if status in {"accepted", "reviewed"}:
            score += 0.15
        elif status == "partial":
            score += 0.06
        score += min(0.12, max(0.0, float(metadata.get("confidence") or 0.0)) * 0.12)
        score += min(0.12, max(0.0, float(metadata.get("quality_score") or 0.0)) * 0.12)
        score += min(0.1, max(0.0, float(metadata.get("helpfulness_score") or 0.0)) * 0.1)
        review_support_score = max(0.0, float(metadata.get("review_support_score") or 0.0))
        reviewer_count = max(0, int(metadata.get("reviewer_count") or 0))
        finality_state = str(metadata.get("finality_state") or "").strip().lower()
        score += min(0.1, review_support_score * 0.1)
        if reviewer_count >= 2:
            score += min(0.05, reviewer_count * 0.02)
        if finality_state == "confirmed":
            score += 0.12
        elif finality_state == "finalized":
            score += 0.18
        elif finality_state == "pending":
            score -= 0.18
        elif finality_state in {"rejected", "slashed"}:
            score -= 0.5
        if list(metadata.get("risk_flags") or []):
            score -= 0.3
    if str(metadata.get("eligibility_state") or "").strip().lower() == "eligible":
        score += 0.08
    archive_state = str(metadata.get("archive_state") or "").strip().lower()
    if archive_state in {"candidate", "approved"}:
        score += 0.05
    durability_reasons = {str(item or "").strip().lower() for item in list(metadata.get("durability_reasons") or []) if str(item or "").strip()}
    if "artifact_backed" in durability_reasons:
        score += 0.05
    if "evidence_backed" in durability_reasons:
        score += 0.04
    return round(max(0.0, min(1.0, score)), 4)


def _normalized_fingerprint(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = _HEX_RE.sub("#hex", normalized)
    normalized = _NUMBER_RE.sub("#num", normalized)
    normalized = " ".join(normalized.split())
    return normalized


def _example_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    ts = _example_timestamp(row)
    if ts is None:
        return (1, "")
    return (0, ts.isoformat())


def _example_timestamp(row: dict[str, Any]) -> datetime | None:
    metadata = dict(row.get("metadata") or {})
    for key in ("ts", "created_at", "updated_at"):
        raw = str(metadata.get(key) or row.get(key) or "").strip()
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _json_loads(raw: Any, *, fallback: Any) -> Any:
    try:
        loaded = json.loads(str(raw or ""))
    except Exception:
        return fallback
    if isinstance(fallback, list):
        return loaded if isinstance(loaded, list) else fallback
    if isinstance(fallback, dict):
        return loaded if isinstance(loaded, dict) else fallback
    return loaded
