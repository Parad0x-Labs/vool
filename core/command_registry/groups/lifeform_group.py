"""The lifeform group — /egg and /lifeform inspect (VOOLemon foundation).

Boundary laws for this adapter (the ONLY VOOLemon code that touches the
runtime):
- reads execution-truth views and the fact ledger READ-ONLY (a bare SELECT on
  execution_facts; never record_execution, never any write);
- never emits onto the vool_event channel (the typed event allowlist is not
  this feature's surface);
- writes only through storage.lifeform_store (its own isolated table);
- no timer, no background loop, no fake activity: everything here happens
  inside an operator-invoked command.

UX laws (wave-2 review): an early /egg NEVER errors — it renders the evidence
prospectus and promises automatic minting once the gate is met. The hatch and
all reveals queue behind operator invocation; no notifications exist.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from core.command_registry.registry import CommandRegistry
from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerOk,
    OperatorAuthority,
)

_SIGNING_KEY_ID = "lifeform-local-v1"


def _gate_operator(inp, ctx) -> AuthorityDecision:
    from core.command_registry.spec import AuthorityDecision as _AuthorityDecision

    if str(getattr(ctx, "principal", "") or "") != "operator":
        return _AuthorityDecision(granted=False,
                   reason="only the local operator may mint or change a lifeform")
    return _AuthorityDecision(granted=True)


def _probe_lifeform_store(context: dict) -> tuple[bool, str]:
    """Real availability evidence: the active data dir must exist and accept writes."""
    from core.runtime_paths import active_data_dir

    try:
        data_dir = active_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".lifeform_availability_probe"
        probe.write_text("ok")
        ok = probe.read_text() == "ok"
        probe.unlink(missing_ok=True)
        return ok, "lifeform store writable" if ok else "data dir rejected the probe write"
    except Exception as exc:
        return False, f"data dir unavailable: {exc}"


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _owner_id() -> str:
    from core.runtime_paths import active_data_dir

    return "op-" + hashlib.sha256(str(active_data_dir()).encode("utf-8")).hexdigest()[:16]


def _signing_key() -> bytes:
    """Local HMAC key, file-backed in the runtime home (0600). Stage-0
    tamper-EVIDENCE only — see core.companion.lifeform.progression docstring."""
    from pathlib import Path

    from core.runtime_paths import active_data_dir

    key_path = Path(active_data_dir()) / "lifeform_signing.key"
    if key_path.exists():
        material = key_path.read_bytes().strip()
        if material:
            return material
    key_path.parent.mkdir(parents=True, exist_ok=True)
    material = uuid.uuid4().bytes + uuid.uuid4().bytes
    key_path.write_bytes(material)
    key_path.chmod(0o600)
    return material


def _witness_evidence() -> tuple[int, int]:
    """READ-ONLY census of the fact ledger (never written by this feature)."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT turn_key) AS t "
            "FROM execution_facts"
        ).fetchone()
    except Exception:
        return 0, 0  # ledger absent: a fresh home, honestly empty
    finally:
        conn.close()
    return int(row["n"] or 0), int(row["t"] or 0)


def _fact_resolver(turn_key: str, fact_id: str, signal_type: str) -> bool:
    from core.execution_truth import facts_for_turn

    try:
        for fact in facts_for_turn(turn_key, limit=200):
            if fact.fact_id == fact_id:
                return True
    except Exception:
        return False
    return False


def _prospectus(detail: dict) -> str:
    return (
        "No egg yet. VOOLemon hatches life from verified work — no fake starts.\n\n"
        f"Evidence witnessed:   {detail['facts_witnessed']} / {detail['facts_required']} execution facts\n"
        f"Distinct sessions:    {detail['turn_keys_witnessed']} / {detail['turn_keys_required']}\n\n"
        "Keep working. The gate mints the egg automatically once met — run /egg again "
        "whenever you like.\n"
        "(witness is read-only: contents never seen, only that verified work happened)"
    )


def _genome_card(doc: dict, state: dict, manifest: str) -> str:
    genome = state.get("genome") or {}
    lines = [
        f"{doc.get('display_name') or 'lifeform'} — stage: {state['stage']}",
        f"archetype: {genome.get('archetype') or '-'}  lineage: {genome.get('lineage') or '-'}",
        "growth rings: " + (", ".join(doc.get("lineage", {}).get("growth_rings") or []) or "none yet"),
        f"visual manifest: {manifest[:24]}…",
        "every stat traces to witnessed, signed events — inspect the ledger anytime.",
    ]
    return "\n".join(lines)


def _handle_egg(inp, ctx):
    from core.companion.lifeform import (
        evidence_gate,
        genesis_seed,
        new_lifeform_v1,
        progression,
        to_json,
    )
    from storage import lifeform_store

    owner = _owner_id()
    existing = lifeform_store.load_for_owner(owner)
    if existing:
        return HandlerOk(
            data={"lifeform_id": existing["doc"]["lifeform_id"], "existing": True},
            summary="Your lifeform already exists — run /lifeform to inspect it.")
    fact_count, turn_keys = _witness_evidence()
    allowed, detail = evidence_gate(fact_count, turn_keys)
    if not allowed:
        return HandlerOk(data={"gate": detail}, summary=_prospectus(detail))
    lifeform_id = "lf-" + uuid.uuid4().hex[:20]
    seed = genesis_seed(owner, lifeform_id)
    doc = new_lifeform_v1(lifeform_id, owner, seed, _utc_today())
    doc_json = to_json(doc)
    snapshot = progression.sign_snapshot([], _signing_key(), _SIGNING_KEY_ID, seed)
    lifeform_store.save_lifeform(doc_json, [], snapshot, _utcnow_iso())
    return HandlerOk(
        data={"lifeform_id": lifeform_id, "genesis_seed": seed, "existing": False},
        summary="A Seed forms around the work you have already done. "
                "It grows as you do — run /lifeform to meet it.")


def _handle_inspect(inp, ctx):
    from core.companion.lifeform import appearance, progression
    from core.companion.lifeform.schema import from_json
    from storage import lifeform_store

    owner = _owner_id()
    existing = lifeform_store.load_for_owner(owner)
    if not existing:
        return HandlerOk(
            data={"exists": False},
            summary="No lifeform on this home yet — run /egg (it mints from "
                    "evidence of real work, never from nothing).")
    doc = from_json(json.dumps(existing["doc"]))
    state = progression.reduce_events(existing["event_log"], doc.genesis_seed)
    genome = state.get("genome") or {}
    spec = appearance.render_spec(
        doc, stage=state["stage"], genesis_seed=doc.genesis_seed,
        archetype=genome.get("archetype"), lineage=genome.get("lineage"),
        rings=len(doc.lineage.get("growth_rings") or []))
    manifest = appearance.manifest(spec)
    ok, problems = (True, [])
    if existing.get("snapshot"):
        ok, problems = progression.verify_snapshot(
            existing["snapshot"], existing["event_log"], {_SIGNING_KEY_ID: _signing_key()},
            doc.genesis_seed)
    status_line = "ledger verified" if ok else "snapshot discarded — rebuilt from the log: " + "; ".join(problems)
    return HandlerOk(
        data={"state": state, "manifest": manifest, "verify_ok": ok},
        summary=_genome_card({"display_name": doc.display_name, "lineage": doc.lineage},
                             state, manifest) + f"\n{status_line}")


@dataclass(frozen=True)
class _EmptyInput:
    pass


def register(reg: CommandRegistry) -> None:
    reg.add_group(GroupSpec(group_id="lifeform",
                            description="VOOLemon digital-life companion (grows from verified work)"))
    egg_permission = OperatorAuthority(
        kind="lifeform.mint", verifier="core.command_registry.groups.lifeform_group:_gate_operator")
    reg.add(
        CommandSpec(
            command_id="lifeform.egg",
            group="lifeform",
            description="Mint your lifeform seed — gated on witnessed, verified work",
            aliases=("egg",),
            input_schema=_EmptyInput,
            effects="idempotent_write",
            permission=egg_permission,
            availability=Availability(
                probe="core.command_registry.groups.lifeform_group:_probe_lifeform_store"),
            fault_bindings=(FaultBinding(when="store_write_refused",
                                         fault_code="conflict",
                                         remediation=("check the data dir",)),),
            handler=Handler("core.command_registry.groups.lifeform_group:_handle_egg"),
            exit_codes=(0, 2, 30),
        )
    )
    reg.add(
        CommandSpec(
            command_id="lifeform.inspect",
            group="lifeform",
            description="Inspect your lifeform: stage, genome, growth rings, ledger state",
            aliases=("lifeform",),
            input_schema=_EmptyInput,
            effects="read_only",
            handler=Handler("core.command_registry.groups.lifeform_group:_handle_inspect"),
            exit_codes=(0, 2),
        )
    )
