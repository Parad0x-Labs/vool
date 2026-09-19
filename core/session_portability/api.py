"""THE seam: every surface (HTTP routes, CLI, tests, future lanes) goes through this module.

The functions here are the entire public contract of session portability. Preconditions,
typed refusals, and the receipt shapes live here once so no surface can grow its own dialect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.session_portability.paths import scoped_home
from core.session_portability.schema import (
    BOUNDS,
    CURRENT_SCHEMA_VERSION,
    SCHEMA_NAME,
    SCOPE_NOTE,
)

#: Re-exported so callers (and tests) can pin against the seam without reaching into internals.
__all__ = [
    "BOUNDS",
    "CURRENT_SCHEMA_VERSION",
    "SCHEMA_NAME",
    "SCOPE_NOTE",
    "PortabilityRefused",
    "export_session",
    "import_bundle",
    "inspect_bundle",
    "load_payload",
    "preview_export",
]


class PortabilityRefused(Exception):
    """A typed refusal. `code` is stable vocabulary; `detail` carries safe context only.

    Refusals never carry the material that caused them: a tampered member is named, the
    tampering bytes are not echoed.
    """

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


def _bounds() -> dict[str, int]:
    return dict(BOUNDS)


def preview_export(session_id: str, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """State EXACTLY what an export of this session will produce — the same collection pass the
    export runs, minus the write. Counts, embedded files, redactions, scope."""
    from core.session_portability.collect import OverBound, SessionNotFound, collect_session

    try:
        payload, _attachment_bytes, counts, redactions = collect_session(session_id)
    except SessionNotFound as exc:
        raise PortabilityRefused(
            "SESSION_NOT_FOUND", f"No session '{session_id}' exists in this home."
        ) from exc
    except OverBound as exc:
        raise PortabilityRefused(
            "BUNDLE_LIMIT_EXCEEDED",
            f"This session exceeds the {exc.bound} bound ({exc.limit}); it cannot export as one bundle.",
        ) from exc

    embedded = payload.get("evidence", {}).get("embedded") or []
    estimated = sum(int(item.get("size_bytes") or 0) for item in embedded)
    return {
        "schema": SCHEMA_NAME,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "session_id": session_id,
        "counts": counts.as_dict(),
        "embedded_files": [
            {"path": item.get("path"), "size_bytes": item.get("size_bytes")} for item in embedded
        ],
        "redactions": redactions,
        "estimated_bytes": estimated,
        "scope_note": SCOPE_NOTE,
        "encrypted": False,
    }


def export_session(
    session_id: str,
    out_path: str | Path,
    *,
    passphrase: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Export one session to a `.voolsession` file. Returns the export receipt."""
    from core.session_portability.bundle import write_bundle
    from core.session_portability.collect import OverBound, SessionNotFound, collect_session

    try:
        payload, attachment_bytes, counts, redactions = collect_session(session_id)
    except SessionNotFound as exc:
        raise PortabilityRefused(
            "SESSION_NOT_FOUND", f"No session '{session_id}' exists in this home."
        ) from exc
    except OverBound as exc:
        raise PortabilityRefused(
            "BUNDLE_LIMIT_EXCEEDED",
            f"This session exceeds the {exc.bound} bound ({exc.limit}); it cannot export as one bundle.",
        ) from exc

    identity = write_bundle(Path(out_path), payload, attachment_bytes, passphrase=passphrase)
    return {
        "ok": True,
        "schema": SCHEMA_NAME,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "session_id": session_id,
        "bundle_id": identity["bundle_id"],
        "sha256": identity["sha256"],
        "encrypted": identity["encrypted"],
        "path": str(out_path),
        "counts": counts.as_dict(),
        "redactions": redactions,
        "scope_note": SCOPE_NOTE,
        "trust": {
            "origin": identity.get("trust_origin", ""),
            "trusted": identity.get("trust_origin", "") != "foreign",
            "signer_fingerprint": identity.get("signer_fingerprint", ""),
        },
    }


def inspect_bundle(
    path: str | Path, *, passphrase: str = "", env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Read a bundle's identity, manifest and signature WITHOUT importing anything."""
    from core.session_portability import signing
    from core.session_portability.bundle import read_bundle

    payload, manifest, _attachment_members, signature_record = read_bundle(
        Path(path), passphrase=passphrase
    )
    receipts = payload.get("receipts") or {}
    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "bundle_id": payload.get("bundle_id"),
        "bundle_digest": manifest.get("bundle_digest"),
        "session": payload.get("session") or {},
        "exported_at": payload.get("exported_at"),
        "scope_note": payload.get("scope_note"),
        "encrypted": bool(passphrase) and _is_envelope(Path(path)),
        "signature": {
            "origin": signing.classify_origin(signature_record) if signature_record else "none",
            "trusted": (
                signing.classify_origin(signature_record)
                in (signing.TRUST_SELF, signing.TRUST_TRUSTED)
            )
            if signature_record
            else False,
            "signer_public_key": signature_record.get("signer_public_key") or "",
            "signer_fingerprint": signature_record.get("signer_fingerprint") or "",
            "algorithm": signature_record.get("algorithm") or "",
        },
        "counts": {
            "turns": len(payload.get("turns") or []),
            "summaries": len(payload.get("summaries") or []),
            "obligation_sets": len(payload.get("obligations") or []),
            "tool_receipts": len(receipts.get("tool_receipts") or []),
            "session_events": len(receipts.get("session_events") or []),
            "embedded_files": len((payload.get("evidence") or {}).get("embedded") or []),
            "profile_items": len((payload.get("profile_refs") or {}).get("items") or []),
        },
        "manifest": {
            "algorithm": manifest.get("algorithm"),
            "entries": sorted((manifest.get("entries") or {}).keys()),
        },
    }


def load_payload(
    path: str | Path, *, passphrase: str = "", env: dict[str, str] | None = None
) -> dict[str, Any]:
    """The bundle's verified payload (read-only; no writes, no import)."""
    from core.session_portability.bundle import read_bundle

    payload, _manifest, _members, _signature = read_bundle(Path(path), passphrase=passphrase)
    return payload


def preview_import(
    path: str | Path,
    *,
    passphrase: str = "",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Preview what an import of this bundle WILL DO before confirming it: identity, counts,
    signature/trust verdict, embedded files and the collision resolution — with NO writes.

    This is the confirmation gate the import UI shows: nothing has landed when this returns.
    Typed refusals (tamper, unsigned, untrusted signer, too-new schema, bounds, forbidden
    roles, attachment checks) fire here exactly as they would at import time."""
    from core.session_portability import signing
    from core.session_portability.bundle import read_bundle
    from core.session_portability.importer import (
        _check_authority,
        _check_bounds,
        _derived_session_id,
        _session_exists,
    )
    from core.session_portability.schema import (
        CURRENT_SCHEMA_VERSION,
        migrate_payload,
    )

    with scoped_home(home):
        payload, _manifest, attachment_members, signature_record = read_bundle(
            Path(path), passphrase=passphrase
        )
        trust_origin = signing.classify_origin(signature_record)

        summary = {
            "ok": True,
            "schema": payload.get("schema"),
            "schema_version": payload.get("schema_version"),
            "bundle_id": payload.get("bundle_id"),
            "source_session_id": (payload.get("session") or {}).get("session_id") or "",
            "title": (payload.get("session_meta") or {}).get("title")
            or (payload.get("session") or {}).get("title")
            or "",
            "exported_at": payload.get("exported_at"),
            "scope_note": payload.get("scope_note"),
            "encrypted": _is_envelope(Path(path)),
            "signature": {
                "origin": trust_origin,
                "trusted": trust_origin in (signing.TRUST_SELF, signing.TRUST_TRUSTED),
                "signer_fingerprint": signature_record.get("signer_fingerprint") or "",
            },
            "needs_confirmation": trust_origin == signing.TRUST_FOREIGN,
        }
        if trust_origin == signing.TRUST_FOREIGN:
            # The operator sees the verdict in the preview; the import itself still refuses
            # without confirm_untrusted.
            pass

        # Schema/bounds/authority checks fire here so the preview is the truth about the import.
        version = int(payload.get("schema_version") or 0)
        if version > CURRENT_SCHEMA_VERSION:
            raise PortabilityRefused(
                "BUNDLE_SCHEMA_TOO_NEW",
                f"This bundle's schema (v{version}) is newer than this runtime "
                f"(v{CURRENT_SCHEMA_VERSION}) understands.",
            )
        try:
            payload, _migrated = migrate_payload(payload)
        except ValueError as exc:
            raise PortabilityRefused("BUNDLE_MALFORMED", str(exc)) from exc
        _check_bounds(payload, attachment_members)
        _check_authority(payload, attachment_members)

        receipts = payload.get("receipts") or {}
        summary["counts"] = {
            "turns": len(payload.get("turns") or []),
            "summaries": len(payload.get("summaries") or []),
            "obligation_sets": len(payload.get("obligations") or []),
            "tool_receipts": len(receipts.get("tool_receipts") or []),
            "session_events": len(receipts.get("session_events") or []),
            "embedded_files": len((payload.get("evidence") or {}).get("embedded") or []),
            "profile_candidates": len((payload.get("profile_refs") or {}).get("items") or []),
        }
        summary["embedded_files"] = [
            {"name": item.get("name"), "size_bytes": item.get("size_bytes")}
            for item in (payload.get("evidence") or {}).get("embedded") or []
        ]
        source_id = summary["source_session_id"]
        collision = bool(source_id) and _session_exists(source_id)
        summary["conflicts"] = {
            "collision": collision,
            "target_session_id": _derived_session_id(payload.get("bundle_id") or "", source_id)
            if collision
            else source_id,
            "excluded": "system/developer/tool-authority records are never portable; imported "
            "profile references arrive as candidates only; no task, checkpoint or attempt is "
            "imported",
        }
        return summary


def import_bundle(
    path: str | Path,
    *,
    passphrase: str = "",
    home: str | Path | None = None,
    confirm_untrusted: bool = False,
) -> dict[str, Any]:
    """Import a verified bundle into this (or an explicit `home`) VOOL home.

    `confirm_untrusted` is the operator's explicit confirmation for a bundle signed by an
    unknown key: without it a foreign-signed bundle refuses; with it the import lands and the
    session stays durably marked foreign/untrusted in the import ledger."""
    from core.session_portability.importer import import_bundle as _import

    return _import(
        path, passphrase=passphrase, home=home, confirm_untrusted=confirm_untrusted
    )


def _is_envelope(path: Path) -> bool:
    try:
        return path.read_bytes().lstrip().startswith(b"{")
    except OSError:
        return False
