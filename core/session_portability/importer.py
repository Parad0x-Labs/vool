"""The importer: land a verified bundle in a home without ever overwriting what lives there,
never powering up what it carries, and never leaving half a session behind.

Laws, in enforcement order:
1. verify the FILE (archive safety, manifest hashes, Ed25519 signature) before any write;
2. enforce imported authority: system/developer/tool-authority records refuse outright,
   attachments pass type/size/secret checks as inert data, profile references land ONLY as
   candidates, no checkpoint or attempt is ever created (nothing can auto-execute);
3. migrate the payload forward through registered steps only; a newer schema refuses closed;
4. enforce the cumulative bounds;
5. resolve identity collisions by DERIVING a new canonical session id — a resident session's
   rows are never touched; the same bundle twice is a typed refusal, not a silent second copy;
6. imported evidence is stamped `fresh: False` + `lifecycle: "restored"` so every production
   reader treats it as history, never as fresh current evidence.

Atomicity: the import is one RECOVERABLE operation — attachments stage to temp names, a
PREPARED ledger row is the durable intent, publication/appends are idempotent, every SQLite
write commits in ONE transaction, and a COMPLETE ledger row is the finish line. A crash at any
point followed by a retry yields the complete import exactly once; a crash before the intent
row leaves no import at all.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from core.session_portability.paths import scoped_home
from core.session_portability.schema import (
    CURRENT_SCHEMA_VERSION,
    IMPORTED_FRESH_MARK,
    IMPORTED_LIFECYCLE_MARK,
    migrate_payload,
)

#: Test-only crash-injection seams. Production never populates this; a test sets
#: CRASH_POINTS["before_db_commit"] = True and the importer raises ImportCrash at that seam.
CRASH_POINTS: dict[str, bool] = {}


class ImportCrash(RuntimeError):
    """Raised ONLY by an injected crash seam (tests); never by production code."""


def _crash(name: str) -> None:
    if CRASH_POINTS.get(name):
        raise ImportCrash(f"injected crash at {name}")


def _bounds() -> dict[str, int]:
    from core.session_portability import schema

    return dict(schema.BOUNDS)


def import_bundle(
    path: str | Path,
    *,
    passphrase: str = "",
    home: str | Path | None = None,
    confirm_untrusted: bool = False,
) -> dict[str, Any]:
    from core.session_portability import bundle as bundle_format
    from core.session_portability import signing
    from core.session_portability.api import PortabilityRefused

    with scoped_home(home):
        payload, manifest, attachment_members, signature_record = bundle_format.read_bundle(
            Path(path), passphrase=passphrase
        )
        # Authenticity + trust BEFORE any import write. read_bundle already verified the
        # signature over the manifest; here we only classify the signer.
        trust_origin = signing.classify_origin(signature_record)
        if trust_origin == signing.TRUST_FOREIGN and not confirm_untrusted:
            raise PortabilityRefused(
                "BUNDLE_UNTRUSTED_SIGNER",
                "This bundle is signed by an unknown key "
                f"({signature_record.get('signer_fingerprint')}). Confirm explicitly to import "
                "it as untrusted, or add the fingerprint to the home's trusted signers.",
                detail={"signer_fingerprint": signature_record.get("signer_fingerprint")},
            )
        return _import_verified(
            payload,
            manifest,
            attachment_members,
            signature_record,
            trust_origin,
            resumed_has_state=None,
        )


def _import_verified(
    payload: dict[str, Any],
    manifest: dict[str, Any],
    attachment_members: dict[str, bytes],
    signature_record: dict[str, Any],
    trust_origin: str,
    *,
    resumed_has_state: str | None,
) -> dict[str, Any]:
    from core.memory.learning import _ensure_memory_files
    from core.runtime_paths import ensure_runtime_dirs
    from core.session_portability import signing
    from core.session_portability.api import PortabilityRefused
    from storage import dialogue_memory
    from storage.migrations import run_migrations

    bundle_id = str(payload.get("bundle_id") or manifest.get("bundle_digest") or "")

    # The target home may be brand new: bring its storage up to the runtime's schema head
    # before any write, exactly as a booting daemon would.
    ensure_runtime_dirs()
    run_migrations()
    dialogue_memory._init_tables()

    # 2. Schema migration (forward-only; newer refused closed).
    version = int(payload.get("schema_version") or 0)
    if version > CURRENT_SCHEMA_VERSION:
        raise PortabilityRefused(
            "BUNDLE_SCHEMA_TOO_NEW",
            f"This bundle's schema (v{version}) is newer than this runtime (v{CURRENT_SCHEMA_VERSION}) "
            "understands. Upgrade VOOL and try again; nothing was imported.",
        )
    try:
        payload, migrated_from = migrate_payload(payload)
    except ValueError as exc:
        raise PortabilityRefused("BUNDLE_MALFORMED", str(exc)) from exc

    _check_bounds(payload, attachment_members)
    _check_authority(payload, attachment_members)

    source_session_id = str((payload.get("session") or {}).get("session_id") or "")
    if not source_session_id:
        raise PortabilityRefused("BUNDLE_MALFORMED", "The bundle names no session.")

    _ensure_memory_files()

    # 5. Identity collision resolution + the durable intent row.
    ledger = _load_ledger()
    prior = ledger.get(bundle_id)
    if prior is not None and prior.get("state") == "COMPLETE":
        raise PortabilityRefused(
            "BUNDLE_ALREADY_IMPORTED",
            f"This exact bundle was already imported into this home as session {prior.get('imported_session_id')}. "
            "Importing it twice would fork its history; nothing was changed.",
        )
    resumed = prior is not None and prior.get("state") == "PREPARED"
    if resumed:
        target_id = str(prior.get("imported_session_id"))
        collision = target_id != source_session_id
    else:
        resident = _session_exists(source_session_id)
        if resident:
            target_id = _derived_session_id(bundle_id, source_session_id)
            if _session_exists(target_id):
                raise PortabilityRefused(
                    "BUNDLE_ALREADY_IMPORTED",
                    "A session derived from this bundle already exists here; nothing was changed.",
                )
            collision = True
        else:
            target_id = source_session_id
            collision = False

    # Stage attachments to temp names: publication is a rename away, and idempotent.
    staged = _stage_attachments(payload, attachment_members, target_id)
    _crash("after_stage")

    if not resumed:
        _record_ledger(
            bundle_id,
            target_id,
            source_session_id,
            state="PREPARED",
            trust=trust_origin,
            signer_fingerprint=str(signature_record.get("signer_fingerprint") or ""),
        )
    _crash("after_intent")

    # Publish attachments (tmp -> final, skip-if-exists).
    _publish_attachments(staged)
    _crash("after_publish")

    counts = {
        "turns": _import_turns(payload, target_id),
        "summaries": _import_summaries(payload, target_id),
        "tool_receipts": 0,
        "session_events": 0,
        "obligation_sets": 0,
        "dialogue_turns": 0,
    }
    _import_session_header(payload, target_id, collision, trust_origin)
    _import_namespace(payload, target_id)
    evidence_marked_stale = _mark_evidence_stale(payload, target_id, bundle_id)
    profile_imports = _import_profile_items(payload, target_id)

    _crash("before_db_commit")
    db_counts = _import_db_records(payload, target_id)
    counts.update(db_counts)
    _crash("after_commit")

    _record_ledger(
        bundle_id,
        target_id,
        source_session_id,
        state="COMPLETE",
        trust=trust_origin,
        signer_fingerprint=str(signature_record.get("signer_fingerprint") or ""),
    )

    obligation_refs = [
        {"set_id": row.get("set_id"), "version": row.get("version")}
        for row in payload.get("obligations") or []
    ]
    return {
        "ok": True,
        "source_session_id": source_session_id,
        "imported_session_id": target_id,
        "bundle_id": bundle_id,
        "collision": collision,
        "resumed": resumed,
        "migrated_from": migrated_from,
        "evidence_marked_stale": evidence_marked_stale,
        "counts": counts,
        "obligation_sets": obligation_refs,
        "schema_version": payload.get("schema_version"),
        "trust": {
            "origin": trust_origin,
            "trusted": trust_origin in (signing.TRUST_SELF, signing.TRUST_TRUSTED),
            "signer_fingerprint": signature_record.get("signer_fingerprint") or "",
        },
        "profile_imports": profile_imports,
    }


# --------------------------------------------------------------------------- bounds


def _check_bounds(payload: dict[str, Any], attachment_members: dict[str, bytes]) -> None:
    from core.session_portability.api import PortabilityRefused

    bounds = _bounds()
    receipts = payload.get("receipts") or {}

    def over(label: str, count: int, key: str) -> None:
        if count > bounds.get(key, 0):
            raise PortabilityRefused(
                "BUNDLE_LIMIT_EXCEEDED", f"Bundle exceeds the {key} bound."
            )

    over("turns", len(payload.get("turns") or []), "turns")
    over("dialogue", len(payload.get("dialogue_turns") or []), "dialogue_turns")
    over("summaries", len(payload.get("summaries") or []), "summaries")
    over("obligations", len(payload.get("obligations") or []), "obligation_sets")
    over("receipts", len(receipts.get("tool_receipts") or []), "tool_receipts")
    over("events", len(receipts.get("session_events") or []), "session_events")
    over(
        "profile",
        len((payload.get("profile_refs") or {}).get("items") or []),
        "profile_items",
    )
    embedded = (payload.get("evidence") or {}).get("embedded") or []
    over("embedded", len(embedded), "embedded_files")
    total = sum(int(item.get("size_bytes") or 0) for item in embedded)
    if total > bounds["embedded_total_bytes"] or len(attachment_members) > bounds["embedded_files"]:
        raise PortabilityRefused(
            "BUNDLE_LIMIT_EXCEEDED", "Bundle exceeds the embedded-evidence bounds."
        )


# --------------------------------------------------------------------------- imported authority

_FORBIDDEN_ROLES = frozenset({"system", "developer", "tool", "tool_authority", "function"})


def _check_authority(payload: dict[str, Any], attachment_members: dict[str, bytes]) -> None:
    """Refuse any record that would arrive as system/developer/tool authority, and any
    attachment that is not inert, in-bounds, type-honest, secret-free data."""
    from core.session_portability.api import PortabilityRefused

    for row in payload.get("dialogue_turns") or []:
        role = str(row.get("speaker_role") or "").strip().lower()
        if role and role not in ("user", "assistant"):
            raise PortabilityRefused(
                "BUNDLE_FORBIDDEN_ROLE",
                f"The bundle carries a dialogue record with authority role '{role}'. "
                "Imported sessions may hold user/assistant history only; nothing was imported.",
            )
    for row in payload.get("turns") or []:
        role = str(row.get("role") or row.get("authority") or "").strip().lower()
        if role in _FORBIDDEN_ROLES:
            raise PortabilityRefused(
                "BUNDLE_FORBIDDEN_ROLE",
                f"The bundle carries a transcript record with authority role '{role}'. "
                "Imported sessions may hold user/assistant history only; nothing was imported.",
            )

    from core.chat_attachments import MAX_BYTES_PER_FILE
    from core.privacy_guard import secret_material_present
    from core.secret_redaction import contains_secret

    for item in (payload.get("evidence") or {}).get("embedded") or []:
        member = str(item.get("path") or "")
        data = attachment_members.get(member)
        if data is None:
            raise PortabilityRefused(
                "BUNDLE_MALFORMED",
                f"Bundle references embedded evidence '{member}' that is not in the file.",
            )
        if len(data) > MAX_BYTES_PER_FILE:
            raise PortabilityRefused(
                "BUNDLE_ATTACHMENT_REJECTED",
                f"Embedded attachment '{item.get('name')}' exceeds the per-file size bound.",
            )
        kind = str(item.get("kind") or "")
        media_type = str(item.get("media_type") or "")
        if kind not in ("text", "image") or not media_type.startswith(("text/", "image/")):
            raise PortabilityRefused(
                "BUNDLE_ATTACHMENT_REJECTED",
                f"Embedded attachment '{item.get('name')}' has an unsupported type "
                f"({kind or 'unknown'}/{media_type or 'unknown'}).",
            )
        if kind == "image":
            if media_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise PortabilityRefused(
                    "BUNDLE_ATTACHMENT_REJECTED",
                    f"Embedded attachment '{item.get('name')}' claims PNG but its bytes are not PNG.",
                )
        else:
            if b"\x00" in data:
                raise PortabilityRefused(
                    "BUNDLE_ATTACHMENT_REJECTED",
                    f"Embedded attachment '{item.get('name')}' is declared text but is binary.",
                )
            try:
                text = data[:65536].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PortabilityRefused(
                    "BUNDLE_ATTACHMENT_REJECTED",
                    f"Embedded attachment '{item.get('name')}' is declared text but does not decode.",
                ) from exc
            if contains_secret(text) or secret_material_present(text):
                raise PortabilityRefused(
                    "BUNDLE_ATTACHMENT_REJECTED",
                    f"Embedded attachment '{item.get('name')}' carries credential material "
                    "and was refused.",
                )


# --------------------------------------------------------------------------- existence/collision


def _session_exists(session_id: str) -> bool:
    from core.context_namespace import load_chat_namespace
    from core.memory.entries import list_conversation_sessions

    namespace = load_chat_namespace(session_id)
    if namespace is not None:
        return True
    return any(
        row.get("session_id") == session_id for row in list_conversation_sessions(limit=1_000_000)
    )


def _derived_session_id(bundle_id: str, source_session_id: str) -> str:
    digest = hashlib.sha256(
        f"session-portability:{bundle_id}:{source_session_id}".encode()
    ).hexdigest()
    return f"openclaw:{digest[:20]}"


def _ensure_ledger_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_bundle_imports (
            bundle_id TEXT PRIMARY KEY,
            imported_session_id TEXT NOT NULL,
            source_session_id TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'COMPLETE',
            trust TEXT NOT NULL DEFAULT 'trusted',
            signer_fingerprint TEXT NOT NULL DEFAULT ''
        )
        """
    )


def _load_ledger() -> dict[str, dict[str, Any]]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        _ensure_ledger_table(conn)
        conn.commit()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_bundle_imports)")}
        for column in ("state", "trust", "signer_fingerprint"):
            if column not in columns:
                conn.execute(f"ALTER TABLE session_bundle_imports ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        conn.commit()
        rows = conn.execute(
            "SELECT bundle_id, imported_session_id, state, trust, signer_fingerprint "
            "FROM session_bundle_imports"
        ).fetchall()
        return {
            row[0]: {
                "imported_session_id": row[1],
                "state": row[2],
                "trust": row[3],
                "signer_fingerprint": row[4],
            }
            for row in rows
        }
    finally:
        conn.close()


def import_ledger_state(home: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Read-only view of the import ledger (test/diagnostic seam): bundle_id -> row."""
    with scoped_home(home):
        return _load_ledger()


def _record_ledger(
    bundle_id: str,
    target_id: str,
    source_session_id: str,
    *,
    state: str,
    trust: str,
    signer_fingerprint: str,
) -> None:
    from core.session_portability.collect import _utcnow
    from storage.db import get_connection

    conn = get_connection()
    try:
        _ensure_ledger_table(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(session_bundle_imports)")}
        for column in ("state", "trust", "signer_fingerprint"):
            if column not in columns:
                conn.execute(f"ALTER TABLE session_bundle_imports ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "INSERT OR REPLACE INTO session_bundle_imports "
            "(bundle_id, imported_session_id, source_session_id, imported_at, state, trust, signer_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (bundle_id, target_id, source_session_id, _utcnow(), state, trust, signer_fingerprint),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- section writers


def _existing_event_ids(target_id: str) -> set[str]:
    from core.memory.files import conversation_log_path

    ids: set[str] = set()
    path = conversation_log_path()
    if not path.is_file():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("session_id") == target_id:
            if row.get("event_id"):
                ids.add(str(row["event_id"]))
    return ids


def _import_turns(payload: dict[str, Any], target_id: str) -> int:
    """Idempotent transcript append: rows already present (by event_id) are skipped, so a
    crashed-then-resumed import never duplicates history."""
    from core.memory.files import append_jsonl, conversation_log_path

    existing = _existing_event_ids(target_id)
    count = 0
    for index, row in enumerate(payload.get("turns") or []):
        record = dict(row)
        record["session_id"] = target_id
        record.setdefault("event_id", f"event-{uuid.uuid4().hex}")
        record.setdefault("event_sequence", index + 1)
        if record["event_id"] in existing:
            continue
        append_jsonl(conversation_log_path(), record)
        count += 1
    return count


def _existing_summary_keys(target_id: str) -> set[tuple[str, str]]:
    from core.memory.files import session_summaries_path

    keys: set[tuple[str, str]] = set()
    path = session_summaries_path()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("session_id") == target_id:
            keys.add((str(row.get("created_at") or ""), str(row.get("source_id") or "")))
    return keys


def _import_summaries(payload: dict[str, Any], target_id: str) -> int:
    from core.memory.files import append_jsonl, session_summaries_path

    existing = _existing_summary_keys(target_id)
    count = 0
    for row in payload.get("summaries") or []:
        record = dict(row)
        record["session_id"] = target_id
        key = (str(record.get("created_at") or ""), str(record.get("source_id") or ""))
        if key in existing:
            continue
        append_jsonl(session_summaries_path(), record)
        count += 1
    return count


def _import_session_header(payload: dict[str, Any], target_id: str, collision: bool, trust_origin: str) -> None:
    from core.memory.entries import set_session_meta

    meta = dict(payload.get("session_meta") or {})
    session_header = dict(payload.get("session") or {})
    title = str(meta.get("title") or session_header.get("title") or "") or None
    archived = meta.get("archived")
    project_id = str(meta.get("project_id") or session_header.get("project_id") or "") or None
    emoji = str(meta.get("emoji") or "") or None
    color = str(meta.get("color") or "") or None
    try:
        set_session_meta(
            target_id,
            title=title,
            archived=archived if isinstance(archived, bool) else None,
            project_id=project_id,
            emoji=emoji,
            color=color,
        )
        _stamp_import_trust(target_id, trust_origin)
    except Exception:
        # The header is cosmetic; a meta schema drift must not fail the import of history.
        pass


def _stamp_import_trust(target_id: str, trust_origin: str) -> None:
    """Record the import's trust verdict on the session's meta entry (custom key survives the
    meta authority's own updates)."""
    import json as _json

    from core.runtime_paths import active_data_dir

    path = Path(active_data_dir()) / "chat_session_meta.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = _json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
    entry = data.get(target_id)
    if isinstance(entry, dict):
        entry["import_trust"] = trust_origin
        data[target_id] = entry
        path.write_text(_json.dumps(data, indent=1) + "\n")


def _import_namespace(payload: dict[str, Any], target_id: str) -> None:
    from core.context_namespace import ensure_chat_namespace

    namespace = dict(payload.get("namespace") or {})
    project_id = str(namespace.get("project_id") or "") or None
    ensure_chat_namespace(target_id, project_id=project_id)


# --------------------------------------------------------------------------- attachments


def _stage_attachments(
    payload: dict[str, Any], attachment_members: dict[str, bytes], target_id: str
) -> list[tuple[Path, Path, dict[str, Any], bytes]]:
    """Copy embedded attachment bytes + manifests to .tmp sidecars. Publication is later a
    rename — idempotent, crash-safe, never a partial file at the final name."""
    from core.runtime_paths import active_data_dir

    embedded = (payload.get("evidence") or {}).get("embedded") or []
    stage_dir = Path(active_data_dir()) / "chat_attachments"
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path, dict[str, Any], bytes]] = []
    for item in embedded:
        member = str(item.get("path") or "")
        data = attachment_members.get(member)
        if data is None:
            from core.session_portability.api import PortabilityRefused

            raise PortabilityRefused(
                "BUNDLE_MALFORMED",
                f"Bundle references embedded evidence '{member}' that is not in the file.",
            )
        attachment_id = str(item.get("attachment_id") or "")
        sha = str(item.get("sha256") or "")
        manifest = {
            "id": attachment_id,
            "session_id": target_id,
            "name": str(item.get("name") or "attachment"),
            "kind": str(item.get("kind") or "text"),
            "media_type": str(item.get("media_type") or "text/plain"),
            "size_bytes": len(data),
            "sha256": sha,
            "state": "bound",
            "turn_id": str(item.get("turn_id") or ""),
            "created_at": "",
            # THE portability law, stamped where every production reader looks:
            "fresh": IMPORTED_FRESH_MARK,
            "lifecycle": IMPORTED_LIFECYCLE_MARK,
            "imported_bundle_id": bundle_id_ref(payload),
        }
        bytes_tmp = stage_dir / f"{attachment_id}.bin.tmp-{bundle_id_ref(payload)[:8]}"
        manifest_tmp = stage_dir / f"{attachment_id}.json.tmp-{bundle_id_ref(payload)[:8]}"
        if not bytes_tmp.exists():
            bytes_tmp.write_bytes(data)
            with contextlib.suppress(OSError):
                bytes_tmp.chmod(0o600)
        if not manifest_tmp.exists():
            manifest_tmp.write_text(json.dumps(manifest, sort_keys=True))
        staged.append(
            (bytes_tmp, manifest_tmp, manifest, data)
        )
    return staged


def bundle_id_ref(payload: dict[str, Any]) -> str:
    return str(payload.get("bundle_id") or "nobundle")


def _publish_attachments(staged: list[tuple[Path, Path, dict[str, Any], bytes]]) -> int:
    published = 0
    for bytes_tmp, manifest_tmp, manifest, _data in staged:
        bytes_final = bytes_tmp.with_name(f"{manifest['id']}.bin")
        manifest_final = manifest_tmp.with_name(f"{manifest['id']}.json")
        if not bytes_final.exists():
            bytes_tmp.rename(bytes_final)
        if not manifest_final.exists():
            manifest_tmp.rename(manifest_final)
        published += 1
    return published


def _mark_evidence_stale(payload: dict[str, Any], target_id: str, bundle_id: str) -> int:
    return len((payload.get("evidence") or {}).get("embedded") or [])


# --------------------------------------------------------------------------- profile


def _import_profile_items(payload: dict[str, Any], target_id: str) -> dict[str, int]:
    """Imported profile references are CANDIDATES ONLY: they can never silently become active
    preferences or permissions. `remember` (the ACTIVE writer) is deliberately not used."""
    from core.operator_profile import propose_candidate

    items = (payload.get("profile_refs") or {}).get("items") or []
    result = {"candidates": 0, "activated": 0, "skipped": 0}
    for item in items:
        category = str(item.get("category") or "")
        if not category:
            continue
        value = item.get("value", item.get("value_text"))
        try:
            change = propose_candidate(
                "owner_local",
                category,
                value,
                session_id=target_id,
                reason="imported from a session bundle",
            )
            if str(getattr(change, "kind", "")).startswith("refused") or str(
                getattr(change, "kind", "")
            ) in ("no_principal", "paused"):
                result["skipped"] += 1
            else:
                result["candidates"] += 1
        except Exception:
            result["skipped"] += 1
    return result


# --------------------------------------------------------------------------- database


def _import_db_records(payload: dict[str, Any], target_id: str) -> dict[str, int]:
    """Every SQLite write of one import commits in ONE transaction (INSERT OR IGNORE
    throughout), so a crash before the commit leaves no partial database state."""
    from storage.db import get_connection

    receipts = (payload.get("receipts") or {}).get("tool_receipts") or []
    events = (payload.get("receipts") or {}).get("session_events") or []
    obligations = payload.get("obligations") or []
    dialogue_turns = payload.get("dialogue_turns") or []
    dialogue_session = payload.get("dialogue_session") or {}

    def _json_field(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value or {})

    conn = get_connection()
    try:
        for row in receipts:
            record = dict(row)
            conn.execute(
                """
                INSERT OR IGNORE INTO runtime_tool_receipts (
                    receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                    arguments_json, execution_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("receipt_key") or f"rk-{uuid.uuid4().hex[:16]}"),
                    target_id,
                    str(record.get("checkpoint_id") or ""),
                    str(record.get("tool_name") or ""),
                    str(record.get("idempotency_key") or ""),
                    _json_field(record.get("arguments") or record.get("arguments_json")),
                    _json_field(record.get("execution") or record.get("execution_json")),
                    str(record.get("created_at") or ""),
                    str(record.get("updated_at") or ""),
                ),
            )
        for row in events:
            conn.execute(
                """
                INSERT OR IGNORE INTO runtime_session_events (
                    session_id, seq, event_type, message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id,
                    int(row.get("seq") or 0),
                    str(row.get("event_type") or ""),
                    str(row.get("message") or ""),
                    _json_field(row.get("details") or row.get("details_json")),
                    str(row.get("created_at") or ""),
                ),
            )
        snapshot_fields = ("obligations", "request_text", "request_id", "consumption")
        for row in obligations:
            snapshot = {k: row.get(k) for k in snapshot_fields if k in row}
            conn.execute(
                """
                INSERT OR IGNORE INTO obligation_sets (
                    set_id, version, status, snapshot_json, closure_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("set_id") or ""),
                    str(row.get("version") or ""),
                    str(row.get("status") or "OPEN"),
                    json.dumps(snapshot),
                    row.get("closure_hash"),
                    str(row.get("created_at") or ""),
                ),
            )
        for row in dialogue_turns:
            conn.execute(
                """
                INSERT OR IGNORE INTO dialogue_turns (
                    turn_id, session_id, raw_input, normalized_input, reconstructed_input,
                    speaker_role, topic_hints_json, reference_targets_json,
                    understanding_confidence, quality_flags_json, created_at, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("turn_id") or f"turn-{uuid.uuid4().hex}"),
                    target_id,
                    str(row.get("raw_input") or ""),
                    str(row.get("normalized_input") or ""),
                    str(row.get("reconstructed_input") or ""),
                    str(row.get("speaker_role") or "user"),
                    row.get("topic_hints_json") or "[]",
                    row.get("reference_targets_json") or "{}",
                    row.get("understanding_confidence"),
                    row.get("quality_flags_json") or "[]",
                    str(row.get("created_at") or ""),
                    str(row.get("request_id") or ""),
                ),
            )
        if dialogue_session:
            conn.execute(
                """
                INSERT OR IGNORE INTO dialogue_sessions (
                    session_id, last_subject, updated_at
                ) VALUES (?, ?, ?)
                """,
                (
                    target_id,
                    str(dialogue_session.get("last_subject") or ""),
                    str(dialogue_session.get("updated_at") or ""),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "tool_receipts": len(receipts),
        "session_events": len(events),
        "obligation_sets": len(obligations),
        "dialogue_turns": len(dialogue_turns),
    }
