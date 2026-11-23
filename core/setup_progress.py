"""First-run setup progress: a projection over the authorities that already own each value.

The guided setup window (``/setup``) and the "Complete setup" entry in Settings both read ONE
snapshot from here. Nothing in this module stores "done": a step is done when the underlying
setting really exists, read fresh from its owner on every call —

- ``thinking``     → ``core.first_run`` (Local Only / Connected chosen) or a stored ``llm.cloud.*``
                     key NAME in the credential index files (never a Keychain call);
- ``folder``       → ``core.project_store`` (a registered folder that still exists) or an explicit
                     ``VOOL_WORKSPACE_ROOT`` that exists;
- ``permissions``  → ``autonomy_mode`` with provenance (``autonomy_chosen_at`` is stamped by every
                     door that writes autonomy; the default with no stamp is not a choice);
- ``name``         → an active ``preferred_name`` Operator Profile item;
- ``tryit``        → the first three real tries (remember, a reminder, find a file), each read from
                     the execution-fact ledger of what ran, or the store the try writes.

So a value configured directly in Settings ticks its step here without any sync code, and the
flow itself persists only two things, both ordinary preferences: which steps the user skipped
and whether the reminder was dismissed. A skipped step is never a done step.

A predicate that raises reports the step as not done AND names the failure (``check_failed``);
the projection never breaks the page that asked for it.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

STEP_IDS: tuple[str, ...] = ("thinking", "folder", "permissions", "name", "tryit")


@dataclass(frozen=True)
class StepSpec:
    id: str
    title: str      # ≤ 5 words, a question a first-time computer user understands
    sentence: str   # ≤ 18 words: what it is and why, no jargon
    later: str      # where the same thing lives in Settings ("later, in Settings")


STEPS: tuple[StepSpec, ...] = (
    StepSpec(
        id="thinking",
        title="Where should it think?",
        sentence="On this computer keeps everything private. Online with a key is smarter, but it costs money.",
        later="Settings → Models & Providers, and Settings → API Keys",
    ),
    StepSpec(
        id="folder",
        title="Pick a folder",
        sentence="It can only read and change files inside the one folder you choose.",
        later="the “+ Project” button in the chat sidebar",
    ),
    StepSpec(
        id="permissions",
        title="What may it do alone?",
        sentence="Choose how often it should stop and ask you before it changes anything.",
        later="Settings → Privacy & Permissions → What VOOL may do alone",
    ),
    StepSpec(
        id="name",
        title="What should it call you?",
        sentence="Optional. A first name or nickname is enough, and it stays on this computer.",
        later="Settings → Memory & Personalisation, in the name field",
    ),
    StepSpec(
        id="tryit",
        title="Try three things",
        sentence="Ask it to remember something, set a reminder and find a file. Each one really runs.",
        later="the chat, any time",
    ),
)

_SPEC_BY_ID = {spec.id: spec for spec in STEPS}


# --- the predicates: one per step, each reading its owning authority ------------------------

def cloud_key_present() -> bool:
    """True when a model-provider key NAME is indexed on disk. Names only, no secret, no Keychain.

    ``credential_store.has_credential`` may reconcile with the macOS Keychain when the sidecar is
    empty; a Settings boot read must never do that, so this reads the two index files directly.
    """
    from core.runtime_paths import data_path

    names: set[str] = set()
    for filename in ("credentials.meta.json", "credentials.enc.json"):
        path = data_path(filename)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            names.update(str(k) for k in data)
    return any(name.startswith("llm.cloud.") for name in names)


def thinking_done() -> bool:
    from core import first_run
    from core.local_model_presence import local_model_running

    if not local_model_running():
        # Nothing local can answer here (no Ollama, or local models off). A pasted key or a
        # "Local only" choice used to tick this step while Auto kept refusing every turn; here
        # the step is done only once the owner answered the cloud-only question in setup.
        from core import cloud_only_default

        return bool(cloud_only_default.load()["decision"])
    state = str(first_run.load().get("state") or "")
    if state in {first_run.STATE_LOCAL_ONLY_DONE, first_run.STATE_CONNECTED_DONE}:
        return True
    return cloud_key_present()


def folder_done() -> bool:
    from core import project_store

    if any(bool(p.get("exists")) for p in project_store.list_projects()):
        return True
    override = str(os.environ.get("VOOL_WORKSPACE_ROOT") or "").strip()
    return bool(override) and os.path.isdir(os.path.expanduser(override))


def permissions_done() -> bool:
    from core.user_preferences import load_preferences

    prefs = load_preferences()
    return bool(str(prefs.autonomy_chosen_at or "").strip()) and bool(str(prefs.autonomy_mode or "").strip())


def name_done() -> bool:
    from core import operator_profile

    items = operator_profile.list_items(operator_profile.OWNER_PRINCIPAL)
    return any(i.category == "preferred_name" and i.status == "active" and str(i.value_text or "").strip() for i in items)


_PREDICATES: dict[str, Callable[[], bool]] = {
    "thinking": thinking_done,
    "folder": folder_done,
    "permissions": permissions_done,
    "name": name_done,
}


# --- the flow's own two records -------------------------------------------------------------

def parse_skipped(raw: str) -> list[str]:
    """Known step ids only, in step order, without duplicates."""
    wanted = {part.strip() for part in str(raw or "").split(",") if part.strip()}
    return [step for step in STEP_IDS if step in wanted]


def fresh_profile() -> bool:
    """The first-boot verdict the pact seed recorded: an existing user is ``not_applicable``.

    Read from the pact state file directly (a durable, one-time verdict); a missing or unreadable
    file counts as fresh, because the only cost of being wrong is one dismissible line.
    """
    try:
        from core import first_run_pact

        data = first_run_pact.load_state(quarantine_corrupt=False)
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    return str(data.get("state") or "") != first_run_pact.STATE_NOT_APPLICABLE


# --- the projection ---------------------------------------------------------------------------

def snapshot(locale: str = "en") -> dict[str, Any]:
    """Everything the setup window, the Settings entry and the chat line need, read fresh.

    ``locale`` resolves the step presentation through the deterministic catalog
    (``setup.step.<id>.<field>``); the StepSpec English stays the authority and a
    missing key falls back to it visibly (the engine's own law). The projection
    itself — done/skipped/failed — never depends on the locale.
    """
    from core.i18n.catalog import catalog_for
    from core.user_preferences import load_preferences

    catalog = catalog_for(locale) if locale and locale != "en" else None

    def present(spec_id: str, field: str, english: str) -> str:
        if not english or catalog is None:
            return english
        return catalog.text(f"setup.step.{spec_id}.{field}")

    autonomy_mode = ""
    try:
        prefs = load_preferences()
        skipped = parse_skipped(prefs.setup_skipped_steps)
        dismissed = bool(prefs.setup_dismissed)
        autonomy_mode = str(prefs.autonomy_mode or "")
    except Exception:
        skipped, dismissed = [], False

    steps: list[dict[str, Any]] = []
    for spec in STEPS:
        done = False
        failed = ""
        try:
            done = bool(_PREDICATES[spec.id]())
        except Exception as exc:  # a broken authority read is reported, never hidden
            failed = type(exc).__name__
        entry: dict[str, Any] = {
            "id": spec.id,
            "title": present(spec.id, "title", spec.title),
            "sentence": present(spec.id, "sentence", spec.sentence),
            "later": present(spec.id, "later", spec.later),
            "done": done,
            "skipped": (spec.id in skipped) and not done,
        }
        if failed:
            entry["check_failed"] = failed
        steps.append(entry)

    done_count = sum(1 for s in steps if s["done"])
    complete = done_count == len(steps)
    undone = [s["id"] for s in steps if not s["done"]]
    first_undone = next((sid for sid in undone if sid not in skipped), undone[0] if undone else None)
    show_entry = not complete and not dismissed
    return {
        "ok": True,
        "steps": steps,
        "total": len(steps),
        "done_count": done_count,
        "skipped": [s["id"] for s in steps if s["skipped"]],
        "complete": complete,
        "dismissed": dismissed,
        "show_entry": show_entry,
        "show_chat_line": show_entry and fresh_profile(),
        "first_undone": first_undone,
        # the stored value, shown by the page only when the permissions step is DONE (a default
        # with no provenance is not presented as a choice)
        "autonomy_mode": autonomy_mode,
        "cloud_default": _cloud_default_snapshot(),
        "first_tries": _first_tries_snapshot(),
        # A model choice was cleared because its service left VOOL: the chat page says that in
        # place of the plain no-model line, until the owner closes the notice.
        "retired_model_notice": _retired_model_notice(),
    }


def _first_tries_snapshot() -> dict[str, Any]:
    try:
        return first_tries()
    except Exception as exc:  # reported, never hidden, never breaking the page
        return {"check_failed": type(exc).__name__, "tries": [], "needs": [], "done_count": 0}


def _retired_model_notice() -> bool:
    try:
        from core.plugin_migration import migrate_selections, selection_notice

        migrate_selections()
        return selection_notice() is not None
    except Exception:
        return False


def _cloud_default_snapshot() -> dict[str, Any]:
    """What the "thinking" step needs on a machine where no local model runs (read-only)."""
    try:
        from core import cloud_only_default

        return cloud_only_default.snapshot()
    except Exception as exc:  # reported, never hidden, never breaking the page
        return {"check_failed": type(exc).__name__, "local_model_running": None, "candidates": []}


# --- the first three tries: read from what really ran ----------------------------------------

#: One real thing per try, named by the tools that do it. A try is done when one of its tools ran and
#: said it worked (the execution-fact ledger every runtime tool event is recorded in), or when the
#: store the try writes already holds a row of it.
FIRST_TRIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("remember", ("profile.remember",)),
    ("reminder", ("operator.schedule_reminder",)),
    ("find", ("machine.find_file", "machine.find_folder", "workspace.list_files", "workspace.search_text")),
)


def _latest_facts(names: tuple[str, ...], limit: int = 40) -> list[dict[str, Any]]:
    from core.execution_truth import _conn, _ensure_table

    conn = _conn()
    try:
        if not _ensure_table(conn):
            return []
        marks = ",".join("?" for _ in names)
        rows = conn.execute(
            f"SELECT name, ok, status, detail_json, created_at FROM execution_facts WHERE name IN ({marks}) "
            "ORDER BY created_at DESC LIMIT ?",
            (*names, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        try:
            detail = json.loads(row["detail_json"] or "{}")
        except Exception:
            detail = {}
        out.append({"name": str(row["name"]), "ok": bool(row["ok"]), "status": str(row["status"] or ""),
                    "summary": str((detail or {}).get("summary") or "") if isinstance(detail, dict) else "",
                    "at": str(row["created_at"] or "")})
    return out


def _remembered() -> bool:
    """Something the person asked VOOL to keep: a saved memory (the memory lane, any language), or a profile
    setting other than the name, which has a step of its own."""
    from core import operator_profile
    from core.memory.files import _load_jsonl, memory_entries_path
    from core.memory.revocation import authorize_payload

    items = operator_profile.list_items(operator_profile.OWNER_PRINCIPAL)
    if any(i.status == "active" and i.category != "preferred_name" and str(i.value_text or "").strip() for i in items):
        return True
    path = memory_entries_path()
    if not path.exists():
        return False
    # Memories live per chat; any chat counts. A memory the person later had forgotten does not.
    return any(str(row.get("text") or "").strip() and authorize_payload(row, chat_id=str(row.get("chat_id") or row.get("session_id") or row.get("origin_chat_id") or "") or None)
               for row in _load_jsonl(path))


def _reminder_rows() -> bool:
    from storage.db import get_connection

    with get_connection() as conn:
        try:
            return conn.execute("SELECT 1 FROM reminder_requests LIMIT 1").fetchone() is not None
        except Exception:
            return False


_STORE_CHECKS: dict[str, Callable[[], bool]] = {"remember": _remembered, "reminder": _reminder_rows}


def first_tries() -> dict[str, Any]:
    """Each try: ``done`` (it really ran and worked), or the reason the last attempt did not, plus
    what the tries need that is missing right now (a model to answer). Finding a file needs no
    project folder: it looks in the person's own home folders, and a folder the computer keeps
    private comes back as that try's failure, in the tool's own words.
    Read fresh; a read that fails says so instead of reading as "not tried"."""
    tries: list[dict[str, Any]] = []
    for try_id, names in FIRST_TRIES:
        entry: dict[str, Any] = {"id": try_id, "done": False, "last_failure": ""}
        try:
            facts = _latest_facts(names)
            entry["done"] = any(f["ok"] for f in facts)
            if not entry["done"] and try_id in _STORE_CHECKS:
                entry["done"] = bool(_STORE_CHECKS[try_id]())
            if not entry["done"] and facts:
                last = facts[0]
                entry["last_failure"] = (last["summary"] or last["status"])[:220]
        except Exception as exc:
            entry["check_failed"] = type(exc).__name__
        tries.append(entry)
    needs: list[str] = []
    try:
        if not _something_answers():
            needs.append("model")
    except Exception:
        needs.append("model")
    return {"tries": tries, "needs": needs, "done_count": sum(1 for t in tries if t["done"])}


def _something_answers() -> bool:
    """A model can answer the tries right now: a local one runs, or the owner chose a cloud one for Auto.
    Not the "thinking" step's tick: a running local model answers before anyone picked "On this computer"."""
    from core.local_model_presence import local_model_running

    if local_model_running():
        return True
    from core import cloud_only_default

    state = cloud_only_default.load()
    return str(state.get("decision") or "") == "use_cloud" and bool(state.get("model"))


def tryit_done() -> bool:
    return all(t["done"] for t in first_tries()["tries"])


_PREDICATES["tryit"] = tryit_done


def step_spec(step_id: str) -> StepSpec | None:
    return _SPEC_BY_ID.get(str(step_id or "").strip())
