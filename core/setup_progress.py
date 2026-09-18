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
- ``name``         → an active ``preferred_name`` Operator Profile item.

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

STEP_IDS: tuple[str, ...] = ("thinking", "folder", "permissions", "name")


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
        sentence="On my Mac keeps everything private. Online with a key is smarter, but it costs money.",
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
        later="Settings → Privacy & Permissions → Autonomy",
    ),
    StepSpec(
        id="name",
        title="What should it call you?",
        sentence="Optional. A first name or nickname is enough, and it stays on this Mac.",
        later="Settings → Memory & Personalisation",
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

def snapshot() -> dict[str, Any]:
    """Everything the setup window, the Settings entry and the chat line need, read fresh."""
    from core.user_preferences import load_preferences

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
            "title": spec.title,
            "sentence": spec.sentence,
            "later": spec.later,
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
    }


def step_spec(step_id: str) -> StepSpec | None:
    return _SPEC_BY_ID.get(str(step_id or "").strip())
