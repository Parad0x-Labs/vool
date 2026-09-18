"""The code-task journal's schema: versions, forward migration and the rollback downgrade.

The task journal (one JSON file per task under ``VOOL_CODE_TASK_DIR``) is the coding
assistant's only execution state. Its shape has changed:

* **1** -- the pinned integration base ``df49f096``: stage machine, steps, proposals and the
  narrow/cumulative outcomes. No version field.
* **2** -- coding revision 2 (``e06407eb``): adds the ``diagnoses`` and ``verifications``
  histories. Still no version field; the presence of either list identifies it.
* **3** -- this revision: every approval binds the REVIEWED BASE of each canonical target it
  changes; landed mutations advance a workspace ``revision``; every narrow/cumulative outcome
  carries the revision and repaired-file identity it verified, and only the current revision's
  outcomes are current; the journal records the task's own view of each file (``snapshots``),
  durable approval claims, and a ``journal_version`` that makes every journal write a
  compare-and-swap.

A newer runtime reads every older journal through :func:`upgrade_payload`, deriving the new
state ONLY from evidence the journal already recorded (read hashes, write results, the
arguments an approval was granted over). It never invents a reviewed base: an unexecuted
approval whose base cannot be established from recorded evidence is invalidated -- fail
closed -- and must be proposed again.

An older runtime cannot read a schema-3 journal (its dataclasses reject unknown keys, so it
answers "no such task": fail closed, with the work stranded). :func:`downgrade_payload` is the
rollback path: it produces the older shape and REVOKES every approval that has not executed,
because the older runtime cannot enforce the reviewed-base binding under which it was granted.
The CLI never rewrites journals in place::

    python -m core.code_assistant.journal_schema downgrade --source DIR --dest DIR --target-schema 2
    python -m core.code_assistant.journal_schema upgrade   --source DIR --dest DIR
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import posixpath
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3

#: Mutation intents whose physical writer enforces an ``expected_hash`` precondition over ONE
#: ``path``. For these the reviewed base of that path IS the hash the writer compares.
CONTENT_HASH_INTENTS = frozenset({"workspace.write_file", "workspace.replace_in_file"})

PATH_KEYS = ("path", "source", "destination", "target", "from_path", "to_path")

#: What each record gained in schema 3, with defaults. A downgrade removes exactly these.
TASK_V3_FIELDS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "journal_version": 0,
    "revision": 0,
    "snapshots": {},
    "schema_migrations": [],
    "checkpoint": {},
    "pr_description_history": [],
}
STEP_V3_FIELDS: dict[str, Any] = {"revision_at": 0, "bytes_at": {}, "unit": ""}
#: Keys schema 3 adds inside narrow/cumulative outcomes and verification records.
OUTCOME_V3_KEYS = ("revision", "bytes", "current", "stale_reason", "legacy", "at")
PROPOSAL_V3_FIELDS: dict[str, Any] = {
    "targets": [],
    "base": {},
    "reserved_by": "",
    "reserved_instance": "",
    "reserved_at": "",
    "invalidated": {},
    "unit": "",
}
#: What schema 2 added over schema 1.
TASK_V2_FIELDS: dict[str, Any] = {"diagnoses": [], "verifications": []}

LEGACY_UNBOUND = "legacy_approval_without_reviewed_base"
#: An unexecuted legacy approval whose recorded path now resolves somewhere other than the path it recorded.
LEGACY_DESTINATION_MOVED = "legacy_destination_resolves_elsewhere"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_schema(payload: dict[str, Any]) -> int:
    version = payload.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 1:
        return version
    return 2 if any(key in payload for key in TASK_V2_FIELDS) else 1


def state_token(state: dict[str, Any] | None) -> str:
    """One comparable token for what is (or was) at a path: ``file:<sha256>``, ``absent``,
    ``directory``... The writer's precondition, a read's hash and a snapshot all reduce to it."""
    record = state if isinstance(state, dict) else {}
    kind = str(record.get("kind") or "")
    if kind == "file":
        return "file:" + str(record.get("sha256") or "")
    return kind or "unknown"


def mutation_target_paths(intent: str, arguments: dict[str, Any]) -> list[str]:
    """The raw workspace paths a mutation names, in argument order, de-duplicated."""
    raws = [str(arguments.get(key) or "").strip() for key in PATH_KEYS if str(arguments.get(key) or "").strip()]
    if str(intent or "") == "workspace.apply_unified_diff":
        patch = str(arguments.get("patch") or "")
        for match in re.finditer(r"^(?:\+\+\+ b/|--- a/)(.+)$", patch, flags=re.MULTILINE):
            raws.append(match.group(1).split("\t", 1)[0].strip())
    seen: list[str] = []
    for raw in raws:
        if raw and raw not in seen:
            seen.append(raw)
    return seen


def canonical_target(workspace_root: str, raw: str) -> str | None:
    """The workspace-relative identity of ``raw`` as the physical writer resolves it (symlinks
    followed, confinement enforced), or None when it escapes the workspace or names the root."""
    from core.execution.workspace_tools import relative_path, resolve_workspace_path

    clean = str(raw or "").strip()
    if not clean or not str(workspace_root or "").strip():
        return None
    root = Path(workspace_root)
    try:
        resolved = resolve_workspace_path(clean, workspace_root=root)
    except (ValueError, OSError):
        return None
    if resolved == root:
        return None
    return relative_path(resolved, workspace_root=root)


def recorded_target(workspace_root: str, raw: str) -> str | None:
    """The workspace-relative path a journal RECORDED, normalized as text and never resolved against today's
    filesystem, where a link made since could name another file. None when the recorded path leaves the recorded
    workspace: a `..` escape, a home path, or an absolute path outside the recorded root."""
    clean = str(raw or "").strip().replace("\\", "/")
    if not clean or clean.startswith("~"):
        return None
    if clean.startswith("/"):
        root = posixpath.normpath(str(workspace_root or "").strip().replace("\\", "/")).rstrip("/")
        if not root or root == "." or not clean.startswith(root + "/"):
            return None
        clean = clean[len(root) + 1:]
    normal = posixpath.normpath(clean)
    if normal in {".", ".."} or normal.startswith("../"):
        return None
    return normal


def _resolves_elsewhere_today(root: str, intent: str, arguments: dict[str, Any]) -> tuple[str, str, str] | None:
    """Today's filesystem may WITHDRAW a legacy approval, never bind one: the first path the approval names that now
    resolves somewhere other than the path its journal recorded (a link made since, or out of the workspace), as
    (raw, recorded, resolved or ""). None when every path still resolves to itself, or when the recorded workspace
    is not on this machine to ask -- nothing can execute there anyway."""
    if not root or not Path(root).is_dir():
        return None
    for raw in mutation_target_paths(intent, arguments):
        recorded = recorded_target(root, raw)
        today = canonical_target(root, raw)
        if recorded is not None and today != recorded:
            return raw, recorded, today or ""
    return None


def _fill_v3_defaults(data: dict[str, Any]) -> None:
    for key, default in TASK_V3_FIELDS.items():
        data.setdefault(key, copy.deepcopy(default))
    for step in dict(data.get("steps") or {}).values():
        for key, default in STEP_V3_FIELDS.items():
            step.setdefault(key, copy.deepcopy(default))
    for proposal in dict(data.get("proposals") or {}).values():
        for key, default in PROPOSAL_V3_FIELDS.items():
            proposal.setdefault(key, copy.deepcopy(default))


def upgrade_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Any recorded journal shape -> schema 3, derived only from what the journal recorded: paths as recorded text,
    bases from recorded hashes. Today's filesystem is consulted for one thing only -- to WITHDRAW an unexecuted
    approval whose recorded path now resolves elsewhere; it never binds one."""
    data = copy.deepcopy(dict(payload or {}))
    version = detect_schema(data)
    for key, default in TASK_V2_FIELDS.items():
        data.setdefault(key, copy.deepcopy(default))
    if version >= SCHEMA_VERSION:
        _fill_v3_defaults(data)
        return data

    stamp = _utcnow()
    root = str(data.get("workspace_root") or "")
    steps: dict[str, Any] = dict(data.get("steps") or {})
    order = [sid for sid in list(data.get("step_order") or []) if sid in steps]
    revision = 0
    snapshots: dict[str, dict[str, Any]] = {}
    evidence: list[tuple[str, str, dict[str, Any]]] = []

    def _target(raw: str) -> str | None:
        # The path as the journal recorded it. Resolved through today's filesystem, a link made since would hand
        # this evidence to a file the task never read.
        return recorded_target(root, raw)

    for sid in order:
        step = steps[sid]
        step["revision_at"] = revision
        result = dict(step.get("result") or {})
        intent = str(step.get("intent") or "")
        raw_path = str(result.get("path") or (list(step.get("paths") or [""]) or [""])[0])
        at = str(step.get("completed_at") or "")
        target = _target(raw_path)
        if step.get("proposal_id") and step.get("executed") and step.get("ok"):
            # Only an admitted mutation carries a proposal id; an executed, ok one landed bytes.
            revision += 1
            if intent in CONTENT_HASH_INTENTS and str(result.get("after_hash") or "") and target:
                snap = {"kind": "file", "sha256": str(result["after_hash"]), "source": "mutation",
                        "step_id": sid, "revision": revision, "at": at}
                snapshots[target] = snap
                evidence.append((at, target, snap))
        elif (intent == "workspace.read_file" and step.get("executed") and step.get("ok")
              and str(result.get("hash") or "") and target):
            snap = {"kind": "file", "sha256": str(result["hash"]), "source": "read",
                    "step_id": sid, "revision": revision, "at": at}
            snapshots[target] = snap
            evidence.append((at, target, snap))
    for sid, step in steps.items():
        step.setdefault("revision_at", revision if sid not in order else step.get("revision_at", 0))
    data["revision"] = revision
    data["snapshots"] = snapshots

    def _revision_of(step_id: Any) -> int:
        recorded = steps.get(str(step_id or ""))
        return int(recorded.get("revision_at", 0)) if isinstance(recorded, dict) else 0

    history = list(data.get("verifications") or [])
    for record in history:
        record.setdefault("revision", _revision_of(record.get("step_id")))
        record.setdefault("bytes", {})
        record.setdefault("current", True)
        record.setdefault("stale_reason", "")
        record["legacy"] = True
    recorded_ids = {str(record.get("step_id") or "") for record in history}
    for key, stage in (("narrow", "narrow_test"), ("cumulative", "cumulative")):
        outcome = data.get(key)
        if not isinstance(outcome, dict) or not outcome:
            continue
        outcome.setdefault("revision", _revision_of(outcome.get("step_id")))
        outcome.setdefault("bytes", {})
        superseded = outcome["revision"] != revision
        if version == 1 and str(outcome.get("step_id") or "") not in recorded_ids:
            # A base journal kept only the latest outcome; it is history before it is retired.
            history.append({**outcome, "stage": stage, "current": not superseded, "legacy": True,
                            "stale_reason": "superseded by a later mutation" if superseded else ""})
        if superseded:
            # It verified bytes a later mutation replaced: history only, never current evidence.
            data[key] = None
    data["verifications"] = history

    for proposal_id, proposal in dict(data.get("proposals") or {}).items():
        intent = str(proposal.get("intent") or "")
        arguments = dict(proposal.get("arguments") or {})
        targets: list[str] = []
        escaped = False
        for raw in mutation_target_paths(intent, arguments):
            target = recorded_target(root, raw)
            if target is None:
                # A recorded path that leaves the recorded workspace never binds, whether or not that workspace
                # exists on the machine migrating the journal.
                escaped = True
                continue
            if target not in targets:
                targets.append(target)
        base: dict[str, dict[str, Any]] = {}
        explicit = str(arguments.get("expected_hash") or "").strip().lower()
        if explicit and intent in CONTENT_HASH_INTENTS and len(targets) == 1:
            # The operator approved arguments that NAMED this base: it is recorded evidence.
            base[targets[0]] = {"kind": "file", "sha256": explicit, "source": "legacy_expected_hash"}
        else:
            created = str(proposal.get("created_at") or "")
            for target in targets:
                prior = [snap for at, path, snap in evidence if path == target and at and created and at <= created]
                if prior:
                    last = prior[-1]
                    base[target] = {"kind": last["kind"], "sha256": last["sha256"],
                                    "source": f"legacy_{last['source']}", "step_id": last["step_id"]}
        proposal["targets"] = targets
        proposal["base"] = base
        for key, default in PROPOSAL_V3_FIELDS.items():
            proposal.setdefault(key, copy.deepcopy(default))
        bound = bool(targets) and not escaped and len(base) == len(targets)
        if not proposal.get("consumed_by") and not bound:
            proposal["invalidated"] = {
                "reason": LEGACY_UNBOUND,
                "detail": (
                    "recorded before approvals bound a reviewed base, and the journal holds no "
                    "evidence of the content this proposal was reviewed against"
                ),
                "at": stamp,
            }
        elif not proposal.get("consumed_by"):
            moved = _resolves_elsewhere_today(root, intent, arguments)
            if moved is not None:
                raw, recorded, today = moved
                where = f"`{today}`" if today else "a place outside the workspace"
                proposal["invalidated"] = {
                    "reason": LEGACY_DESTINATION_MOVED,
                    "path": recorded,
                    "resolves_to": today,
                    "detail": (
                        f"recorded before approvals bound a reviewed destination; `{raw}` now resolves to {where}, "
                        "and the journal cannot show that this is the file that was reviewed"
                    ),
                    "at": stamp,
                }
        proposal.setdefault("proposal_id", proposal_id)
        proposal["unit"] = str(proposal.get("unit") or proposal_id)

    if revision and str(data.get("stage") or "") in {"narrow_test", "cumulative", "inspect_diff", "report"}:
        # The older runtimes validated the whole batch at once: the checkpoint they were at belongs
        # to the change that landed last.
        index = {sid: position for position, sid in enumerate(order)}
        landed = [(index.get(str(p.get("consumed_by") or ""), -1), pid)
                  for pid, p in dict(data.get("proposals") or {}).items() if p.get("consumed_by")]
        data["checkpoint"] = {"unit": max(landed)[1] if landed else "", "revision": revision, "legacy": True}
    data["journal_version"] = int(data.get("journal_version") or 0)
    migrations = list(data.get("schema_migrations") or [])
    migrations.append({"from": version, "to": SCHEMA_VERSION, "at": stamp})
    data["schema_migrations"] = migrations
    data["schema_version"] = SCHEMA_VERSION
    _fill_v3_defaults(data)
    return data


def downgrade_payload(payload: dict[str, Any], target_schema: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Schema-3 (or any) journal -> the shape ``target_schema`` (1 or 2) runtimes load.

    Every approval that has not executed is REVOKED: the older runtime would execute it without
    the reviewed-base precondition it was granted under. Executed history is kept verbatim."""
    if target_schema not in (1, 2):
        raise ValueError(f"unsupported downgrade target schema {target_schema!r}")
    data = upgrade_payload(payload)
    revoked: list[dict[str, Any]] = []
    for proposal_id, proposal in dict(data.get("proposals") or {}).items():
        if proposal.get("approved") and not proposal.get("consumed_by"):
            proposal["approved"] = False
            proposal["approved_at"] = ""
            revoked.append({
                "proposal_id": proposal_id,
                "invalidated": dict(proposal.get("invalidated") or {}),
                "reason": "an older runtime cannot enforce the reviewed-base binding this approval was granted under",
            })
        for key in PROPOSAL_V3_FIELDS:
            proposal.pop(key, None)
    for step in dict(data.get("steps") or {}).values():
        for key in STEP_V3_FIELDS:
            step.pop(key, None)
    for key in ("narrow", "cumulative"):
        if isinstance(data.get(key), dict):
            for field_name in OUTCOME_V3_KEYS:
                data[key].pop(field_name, None)
    for record in list(data.get("verifications") or []):
        for field_name in OUTCOME_V3_KEYS:
            record.pop(field_name, None)
    if isinstance(data.get("pr_description"), dict):
        for field_name in ("revision", "bytes"):
            data["pr_description"].pop(field_name, None)
    for key in TASK_V3_FIELDS:
        data.pop(key, None)
    if target_schema == 1:
        for key in TASK_V2_FIELDS:
            data.pop(key, None)
    report = {"task_id": data.get("task_id"), "target_schema": target_schema, "revoked_approvals": revoked}
    return data, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.code_assistant.journal_schema")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("upgrade", "downgrade"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True)
        command.add_argument("--dest", required=True)
        if name == "downgrade":
            command.add_argument("--target-schema", type=int, choices=(1, 2), required=True)
    args = parser.parse_args(argv)
    source = Path(args.source)
    dest = Path(args.dest)
    if dest.exists() and any(dest.iterdir()):
        print(f"refusing to write into non-empty {dest}; journals are never rewritten in place", file=sys.stderr)
        return 2
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"command": args.command, "source": str(source.resolve()), "at": _utcnow(), "tasks": []}
    for path in sorted(source.glob("ct-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if args.command == "upgrade":
            out = upgrade_payload(payload)
            report: dict[str, Any] = {"task_id": out.get("task_id"), "from_schema": detect_schema(payload),
                                      "invalidated": sorted(pid for pid, p in out["proposals"].items() if p.get("invalidated"))}
        else:
            out, report = downgrade_payload(payload, args.target_schema)
        (dest / path.name).write_text(json.dumps(out, sort_keys=True, indent=1, default=str), encoding="utf-8")
        report["sha256"] = hashlib.sha256((dest / path.name).read_bytes()).hexdigest()
        manifest["tasks"].append(report)
    (dest / "JOURNAL_SCHEMA_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"tasks": len(manifest["tasks"]), "dest": str(dest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
