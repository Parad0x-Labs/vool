"""The narrow seam where a turn's tool offer is assembled: bounded specs plus skill guidance.

Two production callers build what a model sees of the toolbelt — the prompt catalog in
`core.prompt_normalizer` (every surface, the text catalog a local model reads) and the native
tool definitions in `core.memory_first_router` (cloud function-calling). Before this module each
called `capability_graph.model_visible_specs` on its own, with different arguments: the prompt
seam passed only a family hint, so a local model saw a fixed family set whatever the user's words
asked for, and neither seam had any way to carry a SKILL.md — `core.plugin_skills` could rank and
render skills and had no caller (measured 2026-09-02 at 96c2fb96).

Now both call `assemble_tool_offer`. It:

- builds the SAME bounded, adaptive offer for both seams (user words → explicit seats, mixed
  families, follow-up inheritance, turn expansions — all of `model_visible_specs`);
- ranks installed skills against the user's words and renders the matched bodies with
  **provenance** (plugin id, file) under hard **bounds** (`MAX_SKILLS`, `MAX_SKILL_CHARS`);
- lets a matched skill's ``allowed-tools`` NARROW the ranked fill, never widen it: reserved seats
  and explicitly demanded intents stay, an unavailable or unoffered tool is never seated because a
  skill named it;
- stamps the offer's provenance on the turn context (``_tool_offer``) so an executed turn can be
  audited against what was offered.

**A skill grants nothing.** Its text is instructions to the model; the permission controller never
reads it, and nothing here can change a contract, a mode, or a decision. That property is pinned
by test, not asserted in prose.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Slots PER LANE (see core.native_skill_library: doctrine and task skills no longer
#: compete for the same seats).
MAX_SKILLS = 3
MAX_DOCTRINE_SKILLS = 2

#: Character budgets per lane. These are DERIVED FROM THE COMPOSED LIBRARY'S REAL
#: BODIES, not guessed. The task lane's worst real composition is the served
#: archaeology turn -- a debugging request that also asks for file history --
#: which needs root-cause-repair (1760) + bug-reproduction (1690) +
#: project-archaeology (3735) = 7185, and the doctrine lane's is
#: answer-presentation (1481) + the widest lens (1082) = 2563; each block adds one
#: ~150-char header.
#:
#: Three task slots for the same reason: that turn genuinely needs all three, and
#: the previous two-slot budget is what made archaeology and the repair doctrine
#: substitutes for each other.
#:
#: The old single 4000 was not a ceiling on content. It was a TRUNCATION POINT: the
#: packer cut whatever did not fit and shipped it anyway, so the real behaviour was
#: an unbounded-quality loss inside a bounded character count. Nothing here can grow
#: silently -- a body that does not fit is dropped and recorded, never trimmed -- so
#: adding a skill to the library cannot quietly enlarge the prompt.
MAX_SKILL_CHARS = 7800
MAX_DOCTRINE_CHARS = 3200

#: The TOOL-INTENT lane's tighter share.
#:
#: That lane's prompt already carries the whole tool catalog, so it has far less room
#: for guidance than an advisory turn does. Measured on the served repair journey:
#: at ~3.9k of skill text the turn completes, and at ~5.8k it stops producing a usable
#: closure. This is not a guess at a token cost -- it is the observed boundary on the
#: lane that has to share its window with the catalog.
#:
#: Under complete-or-absent this means a constrained turn carries FEWER skills, each
#: whole, with the rest recorded as dropped. That is the deliberate trade: a partial
#: doctrine is a different doctrine, and a silent one is worse than a named absence.
MAX_TOOL_LANE_SKILL_CHARS = 4200
MAX_TOOL_LANE_DOCTRINE_CHARS = 1800
#: The worst case a single turn can contribute, for tests and budget accounting.
MAX_TOTAL_SKILL_CHARS = MAX_SKILL_CHARS + MAX_DOCTRINE_CHARS
_MIN_SKILL_CHARS = 200  # a body that would be cut below this is dropped rather than mangled

PROVENANCE = "core.tool_offer_assembly"

_lock = threading.RLock()
# (skill path, mtime) -> parsed Skill. Parsing is cheap; the cache exists so repeated turns do not
# re-read the same files, and `reset_skill_cache` exists so a test can prove the seam re-reads.
_skill_cache: dict[tuple[str, float], Any] = {}


@dataclass(frozen=True)
class SkillGuidance:
    text: str = ""
    skills: tuple[dict[str, Any], ...] = ()
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolOffer:
    """One model round's offer: what its prompt catalog lists and its native tool definitions carry.

    Materialized once per round by `assemble_tool_offer` and rendered by both consumers, so the text
    a model reads and the tools it can call cannot diverge. `specs` are the offer's own deep copies,
    so a later registry change cannot alter a materialized offer, and `fingerprint` digests them so a
    rendering can show which offer it rendered. `expanded_families` is the turn navigation set the
    offer was built from (core.tool_offer_state).
    """

    specs: tuple[dict[str, Any], ...] = ()
    skill_guidance: SkillGuidance = field(default_factory=SkillGuidance)
    families: tuple[str, ...] = ()
    expanded_families: tuple[str, ...] = ()
    fingerprint: str = ""

    @property
    def intents(self) -> tuple[str, ...]:
        return tuple(str(spec.get("intent") or "") for spec in self.specs if spec.get("intent"))


def reset_skill_cache() -> None:
    with _lock:
        _skill_cache.clear()
    # The canonical skill authority owns the MCP-sourced contracts (server subprocesses make
    # those expensive to rebuild); its caches reset with this one so tests and config edits
    # have ONE reset entry point.
    with contextlib.suppress(Exception):
        from core.native_skill_library import reset_contract_caches

        reset_contract_caches()


def _plugin_dirs() -> tuple[tuple[str, Path], ...]:
    """(plugin_id, directory) for every enabled installed plugin. Fail-soft, never raises."""
    try:
        from core.plugin_catalog import _disabled_ids, discovered_plugin_dirs

        disabled = _disabled_ids()
        found: list[tuple[str, Path]] = []
        # The bounded probe's listing, never a directory walk on the turn's thread: a plugin
        # folder that stalls after boot cannot stall a tool offer (measured 2026-09-10, the
        # boot-time twin of this walk hung the packaged app; see core.plugin_catalog).
        for entry in discovered_plugin_dirs():
            plugin_id = entry.name
            manifest = entry / ".codex-plugin" / "plugin.json"
            if manifest.is_file():
                try:
                    import json

                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    plugin_id = str(payload.get("name") or entry.name).strip() or entry.name
                except Exception:
                    plugin_id = entry.name
            if plugin_id in disabled:
                continue
            found.append((plugin_id, entry))
        return tuple(found)
    except Exception:
        return ()


def loaded_skills() -> tuple[Any, ...]:
    """Every skill under every enabled installed plugin, parsed by the real loader."""
    from core.plugin_skills import parse_skill

    skills: list[Any] = []
    for plugin_id, plugin_dir in _plugin_dirs():
        skills_dir = plugin_dir / "skills"
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            candidate = entry / "SKILL.md" if entry.is_dir() else entry
            if candidate.name != "SKILL.md" or not candidate.is_file():
                continue
            try:
                mtime = os.stat(candidate).st_mtime
            except OSError:
                continue
            key = (str(candidate), mtime)
            with _lock:
                skill = _skill_cache.get(key)
            if skill is None:
                skill = parse_skill(candidate, plugin_id=plugin_id)
                if skill is None:
                    continue
                with _lock:
                    _skill_cache[key] = skill
            skills.append(skill)
    return tuple(skills)


def _skill_header(skill: Any) -> str:
    """The attribution line that rides with every skill body.

    The path is repo-RELATIVE. It used to be absolute, which meant ~5% of the budget
    was a string that differs per checkout and the assembled prompt was not
    byte-reproducible across machines.
    """
    raw = str(getattr(skill, "path", "") or "")
    shown = raw
    with contextlib.suppress(Exception):
        shown = str(Path(raw).resolve().relative_to(Path(__file__).resolve().parents[1]))
    return (
        f"[skill '{skill.name}' from '{skill.plugin_id or 'unknown'}' ({shown}) "
        "— guidance only; it grants no permissions]"
    )


def render_complete_block(skill: Any, budget: int) -> str | None:
    """The skill's WHOLE body with its header, or ``None`` if it does not fit.

    Never truncates. A partial instruction set is not a smaller instruction set --
    it is a different one, and the doctrine tokens a contract is validated against
    at load live throughout the body, not in its first paragraph.

    The header is charged against the same budget it rides in. The old renderer
    truncated the BODY to the budget and then prepended an uncounted header, so the
    assembled guidance overshot its own ceiling by one header on every turn.
    """
    body = str(getattr(skill, "body", "") or "").strip()
    if not body:
        return None
    header = _skill_header(skill)
    block = f"{header}\n{body}"
    if len(block) > int(budget):
        return None
    return block


def _render_block(skill: Any, budget: int) -> tuple[str, int]:
    """Legacy truncating renderer, kept ONLY for the lexical plugin lane.

    That lane already drops a body it would cut below ``_MIN_SKILL_CHARS``; the typed
    lanes use ``render_complete_block`` and never truncate at all.
    """
    body = str(getattr(skill, "body", "") or "").strip()
    if not body:
        return "", 0
    header = _skill_header(skill)
    if len(body) > budget:
        body = body[: max(0, budget - 1)].rstrip() + "…"
    return f"{header}\n{body}", len(body)


def skill_provenance_rows(rows: Any) -> list[dict[str, Any]]:
    """The ONE durable shape for "which skill influenced this turn".

    Every emitter used to build this dict inline, and all of them recorded only
    name/origin/version/plugin_id. Under complete-or-absent packing that is a
    dangerous omission: a skill DROPPED for budget would be recorded
    indistinguishably from one whose whole body reached the provider, so the
    ledger could claim an influence the wire never carried. ``complete`` and the
    character counts travel with the row so a reader can tell the difference, and
    ``dropped`` names why when it did not make it.
    """
    out: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        entry = {
            "name": str(row.get("name") or ""),
            "origin": str(row.get("origin") or "plugin"),
            "version": str(row.get("version") or ""),
            "plugin_id": str(row.get("plugin_id") or ""),
            "lane": str(row.get("lane") or ""),
            "kind": str(row.get("kind") or ""),
            "chars": int(row.get("chars") or 0),
            "body_chars": int(row.get("body_chars") or 0),
            "complete": bool(row.get("complete", True)),
        }
        if row.get("dropped"):
            entry["dropped"] = str(row.get("dropped"))
        out.append(entry)
    return out


def skill_guidance_for(
    user_text: str,
    *,
    task_class: str = "",
    limit: int = MAX_SKILLS,
    task_chars: int | None = None,
    doctrine_chars: int | None = None,
) -> SkillGuidance:
    """The bounded, attributed instructions the matched skills contribute for this turn.

    Two sources, one budget. NATIVE skills (the repo's own library) are selected by the typed
    law in ``core.native_skill_library`` — the turn's task class and its demand signals, never
    user-text tokens, because a second phrase-regex pile is the defect class this seam exists
    to avoid. PLUGIN skills keep the loader's lexical ranking. Native selections take the
    budget first: a typed decision outranks a lexical one, and the total stays bounded.
    """
    from core import plugin_skills

    budget = max(1, min(int(limit), MAX_SKILLS))
    blocks: list[str] = []
    provenance: list[dict[str, Any]] = []
    allowed: list[str] = []
    # The lexical plugin lane draws on the SAME task-lane budget the typed lanes use,
    # including the tool-intent lane's tighter share. Leaving it on the module constant
    # let a caller that had asked for a smaller budget still receive the full one.
    remaining = int(MAX_SKILL_CHARS if task_chars is None else task_chars)

    if str(task_class or "").strip() or str(user_text or "").strip():
        from core.native_skill_library import guidance_for_selection, select_native_skills

        selection = select_native_skills(
            task_class=str(task_class or ""), user_text=str(user_text or ""), limit=budget
        )
        native_text, native_provenance, _native_permitted = guidance_for_selection(
            selection.selected, task_chars=task_chars, doctrine_chars=doctrine_chars
        )
        if native_text:
            blocks.append(native_text)
            remaining -= sum(
                int(row.get("chars") or 0)
                for row in native_provenance
                if row.get("complete", True)
            )
            provenance.extend(native_provenance)
            # A native skill's permitted-tools deliberately do NOT narrow the offer. Narrowing
            # was built for PLUGIN skills, which match only on strong lexical overlap with the
            # user's own words; a native match rests on the task class alone, and shrinking the
            # standard seat set on that weak a signal starved the bounded offer on every
            # debugging-classified turn (caught by test_offer_stays_bounded_at_every_seam).
            # Skills teach workflows; they never resize the model's toolbelt.

    # Count only what actually reached the prompt: a row recorded as dropped for
    # budget is provenance, not a filled slot.
    native_slots = sum(1 for row in provenance if row.get("complete", True))
    if native_slots < budget and str(user_text or "").strip():
        skills = loaded_skills()
        if skills:
            # A skill disabled in the ONE disable store is not a candidate — typed contracts are
            # filtered inside the library's selection, so the lexical path filters through the
            # same store rather than keeping a second opinion about "off".
            with contextlib.suppress(Exception):
                from core.native_skill_library import disabled_skill_ids

                disabled = disabled_skill_ids()
                skills = tuple(s for s in skills if str(s.name or "") not in disabled)
            if skills:
                ranked = plugin_skills.rank_skills(skills, str(user_text), limit=budget - native_slots)
                for skill in ranked:
                    if remaining < _MIN_SKILL_CHARS:
                        break
                    block, used = _render_block(skill, remaining)
                    if not block:
                        continue
                    blocks.append(block)
                    remaining -= used
                    # The recorded head version of an installed skill rides the provenance row,
                    # so "which skill VERSION influenced this turn" is answerable for
                    # user-authored skills too (typed contracts carry their own declared version).
                    version = 0
                    with contextlib.suppress(Exception):
                        from core.skill_tools import installed_skill_version_for_path

                        version = installed_skill_version_for_path(
                            str(getattr(skill, "path", "") or "")
                        )
                    provenance.append(
                        {
                            "name": str(skill.name),
                            "plugin_id": str(skill.plugin_id or ""),
                            "origin": "plugin",
                            "path": str(skill.path),
                            "chars": int(used),
                            **({"version": int(version)} if version else {}),
                        }
                    )
                    for name in getattr(skill, "allowed_tools", ()) or ():
                        if name not in allowed:
                            allowed.append(str(name))
    return SkillGuidance(text="\n\n".join(blocks), skills=tuple(provenance), allowed_tools=tuple(allowed))


def offer_fingerprint(specs: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """A stable digest of an offer's specs, so any rendering can show which offer it rendered."""
    canonical = json.dumps(list(specs), sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _narrow(specs: list[dict[str, Any]], *, allowed: tuple[str, ...], keep: set[str]) -> list[dict[str, Any]]:
    """Apply a skill's `allowed-tools` as a budget lever: keep reserved + explicit + allowed.

    Never widens: a name that is not already in the offer is ignored. Never empties: when the
    narrowing would leave only reserved seats, the ranked fill stays as it was.
    """
    if not allowed:
        return specs
    offered = {str(s.get("intent") or "") for s in specs}
    wanted = (set(allowed) & offered) | keep
    narrowed = [s for s in specs if str(s.get("intent") or "") in wanted]
    if {str(s.get("intent") or "") for s in narrowed} <= keep:
        return specs
    return narrowed


def assemble_tool_offer(
    *,
    user_text: str = "",
    task_class: str = "",
    family_hint: str | None = None,
    family_hints: tuple[str, ...] = (),
    toolset_hints: tuple[str, ...] = (),
    source_context: dict[str, Any] | None = None,
    max_candidates: int | None = None,
) -> ToolOffer:
    """Build this turn's bounded offer and its skill guidance from ONE call.

    `family_hint` defaults to the task-class vocabulary's family when a `task_class` is given, so a
    caller that only knows the classification gets the same seat as before; `user_text` is what
    makes the offer adaptive. `source_context`, when a dict, receives the provenance stamp.
    """
    from core.capability_graph import _ALWAYS_VISIBLE, _DEFAULT_MAX_CANDIDATES, model_visible_specs
    from core.tool_demand_signals import resolve_demand_signals

    resolved_hint = family_hint
    if resolved_hint is None and task_class:
        from core.capability_graph import family_hint_from_task_class

        resolved_hint = family_hint_from_task_class(str(task_class))

    # Offered by task capability: a code task open in this session seats the control-plane tools
    # its current stage needs, as caller-typed explicit seats the family ranking cannot evict.
    # Read from the task journal (core.code_assistant), never from the conversation.
    try:
        from core.code_assistant.task_runtime import active_task_control_intents

        task_seats = tuple(active_task_control_intents(source_context))
    except Exception:
        task_seats = ()
    # Offered by repo capability: an OPEN RepoOps session seats the control-plane tools its
    # current stage needs, exactly as an open code task seats its stage's tools. Read from the
    # session journal, so a follow-up turn in a repository workflow keeps its tools without
    # re-deriving them from the wording of every short message.
    try:
        from core.repoops.plane import active_repo_session_intents

        repo_seats = tuple(active_repo_session_intents(source_context))
    except Exception:
        repo_seats = ()
    # Offered by session work the same way: the email tools this session's CURRENT email stage needs
    # (core.email_work_state, read from the execution ledger and the draft store).
    try:
        from core.email_work_state import active_email_work_intents

        email_seats = tuple(active_email_work_intents(source_context))
    except Exception:
        email_seats = ()
    work_seats = tuple(dict.fromkeys((*task_seats, *email_seats)))
    # ONE read of the turn's navigation set (core.tool_offer_state): the offer is built from it and
    # records it, so every rendering of this offer agrees on which expansions it carries.
    from core.tool_offer_state import turn_family_expansions

    expansions = turn_family_expansions(source_context) if source_context is not None else ()
    specs = list(
        model_visible_specs(
            family_hint=resolved_hint,
            family_hints=tuple(family_hints),
            toolset_hints=tuple(toolset_hints),
            explicit_intents=(*task_seats, *repo_seats, *work_seats),
            user_text=str(user_text or ""),
            source_context=source_context,
            expanded_families=expansions,
            max_candidates=int(max_candidates or _DEFAULT_MAX_CANDIDATES),
        )
    )
    if task_seats and "code.task.open" not in task_seats:
        # Continue the journal's active task instead of offering a second opening
        # merely because the original repair words still demand code.task.open.
        specs = [spec for spec in specs if spec.get("intent") != "code.task.open"]
    guidance = skill_guidance_for(
        str(user_text or ""),
        task_class=str(task_class or ""),
        task_chars=MAX_TOOL_LANE_SKILL_CHARS,
        doctrine_chars=MAX_TOOL_LANE_DOCTRINE_CHARS,
    )
    if guidance.allowed_tools:
        explicit = set(resolve_demand_signals(str(user_text or "")).explicit_intents)
        specs = _narrow(specs, allowed=guidance.allowed_tools, keep=set(_ALWAYS_VISIBLE) | explicit | set(work_seats))

    # The recorded families are the families the offer ACTUALLY seated -- derived from the
    # seated intents' own namespace (the vocabulary's convention: `repo.*` -> "repo",
    # `code.task.*` -> "code"), not just the task-class hint. A family seated by the user's
    # words (demand signals) or by an open control-plane session (task/repo seats) is exactly
    # as real as one derived from a task class, and a contextual follow-up must inherit it.
    from core.capability_graph import _CANONICAL_FAMILIES

    seated_families = tuple(
        dict.fromkeys(
            family
            for family in (
                str(spec.get("intent") or "").partition(".")[0]
                for spec in specs
                if str(spec.get("intent") or "").strip()
            )
            if family in _CANONICAL_FAMILIES
        )
    )
    families = tuple(
        dict.fromkeys(
            f
            for f in ([resolved_hint] if resolved_hint else []) + list(family_hints) + list(seated_families)
            if f
        )
    )
    offer = ToolOffer(specs=tuple(specs), skill_guidance=guidance, families=families)
    if isinstance(source_context, dict):
        source_context["_tool_offer"] = {
            "provenance": PROVENANCE,
            "intents": list(offer.intents),
            "families": list(families),
            "expanded_families": list(offer.expanded_families),
            "fingerprint": offer.fingerprint,
            "skills": [dict(s) for s in guidance.skills],
        }
        # Audit trail from THE offer seam itself: every consumer path that binds skill guidance
        # (router offer, express chat lanes) records WHICH skills and versions entered the turn's
        # context. Consumers of the event tolerate repeats; a turn whose guidance went unrecorded
        # could not answer for its own context.
        if guidance.skills and source_context.get("runtime_session_id"):
            try:
                from core.runtime_task_events import emit_runtime_event

                emit_runtime_event(
                    source_context,
                    event_type="tool_offer_skills",
                    message="skill guidance joined the turn's context (tool offer)",
                    details={
                        "recorded_by": "tool_offer",
                        "skills": skill_provenance_rows(guidance.skills),
                    },
                )
            except Exception:
                pass  # the audit trail must never break the offer it records
    return offer


__all__ = [
    "MAX_SKILLS",
    "MAX_SKILL_CHARS",
    "MAX_TOOL_LANE_SKILL_CHARS",
    "MAX_TOTAL_SKILL_CHARS",
    "PROVENANCE",
    "SkillGuidance",
    "ToolOffer",
    "assemble_tool_offer",
    "loaded_skills",
    "offer_fingerprint",
    "reset_skill_cache",
    "skill_guidance_for",
    "skill_provenance_rows",
]
