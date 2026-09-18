"""Skills: the instructions a plugin ships for how to use its tools.

A tool tells the model *what it can do*. A skill tells it *how this user wants it done* — search
before filing, quote the issue key, keep the summary under 80 characters. Claude Code's format is
markdown with YAML frontmatter, and both plugins already installed on this machine are written that
way, so this reads the shape that is already on disk rather than inventing another.

Two things change versus `core/plugin_catalog._skill_frontmatter`:

**Real YAML.** That parser is `line.split(":", 1)` per line, which turns `allowed-tools: [a, b]`
into the *string* `"[a, b]"` and truncates any value at its first colon. A description reading
"Use when: the user pastes a stack trace" silently loses everything after "when".

**The body is used.** Today it is parsed and thrown away — only `name` and `description` survive —
so the actual instructions a skill author wrote never reach the model. Here the body is what gets
injected when a skill matches.

Matching is a ranking signal and never a gate. A skill that does not match simply does not add its
instructions; it never removes a tool from the catalog. `allowed-tools` narrows the catalog *for a
turn where that skill matched*, which is a budget lever, not a permission decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_MAX_BODY_CHARS = 8000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str = ""
    body: str = ""
    allowed_tools: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    plugin_id: str = ""
    path: str = ""
    # The package contract (P1 native skill library). Every field defaults empty, because the two
    # packages that predate the contract declare only some of them — surfacing these must never
    # turn a legacy package into an unparseable one.
    version: str = ""
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    recovery: tuple[str, ...] = ()
    cumulative_test_law: tuple[str, ...] = ()

    @property
    def match_corpus(self) -> str:
        """Everything an author wrote that says when this skill applies.

        `description` carries the trigger sentence by convention ("Use when the user ..."), and the
        installed plugins already write it that way, so it is part of the corpus rather than
        requiring authors to duplicate themselves into `triggers`.
        """

        return " ".join([self.name, self.description, " ".join(self.triggers)]).lower()


def _as_tuple(value: Any) -> tuple[str, ...]:
    """Accept a YAML list or a comma-separated string; authors write both."""

    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    text = text.strip("[]")
    return tuple(part.strip().strip("'\"") for part in text.split(",") if part.strip())


def parse_skill(path: Path, *, plugin_id: str = "") -> Skill | None:
    """Read one SKILL.md. Returns None when there is no frontmatter to read."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None

    front: dict[str, Any] = {}
    try:
        import yaml

        loaded = yaml.safe_load(match.group(1))
        if isinstance(loaded, dict):
            front = loaded
    except Exception:
        # A malformed skill must not take down the plugin that ships it. Falling through with an
        # empty header still yields a usable skill: the body is the valuable part.
        front = {}

    body = (match.group(2) or "").strip()[:_MAX_BODY_CHARS]
    return Skill(
        name=str(front.get("name") or Path(path).parent.name).strip(),
        description=str(front.get("description") or "").strip(),
        body=body,
        allowed_tools=_as_tuple(front.get("allowed-tools") or front.get("allowed_tools")),
        triggers=_as_tuple(front.get("triggers")),
        plugin_id=str(plugin_id or ""),
        path=str(path),
        version=str(front.get("version") or "").strip(),
        capabilities=_as_tuple(front.get("capabilities")),
        permissions=_as_tuple(front.get("permissions")),
        effects=_as_tuple(front.get("effects")),
        inputs=_as_tuple(front.get("inputs")),
        outputs=_as_tuple(front.get("outputs")),
        stop_conditions=_as_tuple(front.get("stop-conditions") or front.get("stop_conditions")),
        recovery=_as_tuple(front.get("recovery")),
        cumulative_test_law=_as_tuple(
            front.get("cumulative-test-law") or front.get("cumulative_test_law")
        ),
    )


def load_skills(plugin_root: Path, *, plugin_id: str = "") -> tuple[Skill, ...]:
    """Every skill under one plugin's `skills/` directory."""

    skills_dir = Path(plugin_root) / "skills"
    if not skills_dir.is_dir():
        return ()
    found: list[Skill] = []
    for entry in sorted(skills_dir.iterdir()):
        candidate = entry / "SKILL.md" if entry.is_dir() else entry
        if candidate.name != "SKILL.md" or not candidate.is_file():
            continue
        skill = parse_skill(candidate, plugin_id=plugin_id)
        if skill is not None:
            found.append(skill)
    return tuple(found)


# Function words that appear in every description and every request. Counting them as overlap
# made "what is the capital of France" match a skill whose description contained "the" (measured
# 2026-09-02): a skill would then be injected into turns that had nothing to do with it.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "when", "user", "asks", "use", "from", "into",
        "your", "you", "are", "was", "were", "will", "can", "has", "have", "had", "not", "but", "its",
        "any", "all", "one", "two", "how", "what", "which", "who", "why", "where", "then", "than",
        "also", "just", "only", "some", "them", "they", "their", "there", "here", "about", "after",
        "before", "over", "under", "each", "per", "via", "get", "got", "make", "made", "does", "did",
        "should", "would", "could", "may", "might", "please", "want", "wants", "need", "needs",
    }
)


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.split(r"[^a-z0-9]+", str(text or "").lower())
        if len(t) > 2 and t not in _STOPWORDS
    }


def rank_skills(skills: tuple[Skill, ...], user_text: str, *, limit: int = 2) -> tuple[Skill, ...]:
    """The skills whose declared triggers overlap the request, best first.

    Overlap only — nothing here decides whether a tool is available. A request that matches no
    skill still reaches the model with the full catalog; it just gets no extra instructions.
    """

    wanted = _tokens(user_text)
    if not wanted:
        return ()
    scored: list[tuple[int, int, Skill]] = []
    for index, skill in enumerate(skills):
        # An explicit trigger is a stronger statement of intent than a word in prose, so it counts
        # double; without that, a long description outranks a precise trigger list on volume alone.
        explicit = len(wanted & _tokens(" ".join(skill.triggers))) * 2
        described = len(wanted & _tokens(skill.description))
        score = explicit + described
        if score:
            scored.append((-score, index, skill))
    scored.sort()
    return tuple(item[2] for item in scored[:limit])


def instructions_for(skills: tuple[Skill, ...]) -> str:
    """The prompt fragment a matched skill contributes: its body, as its author wrote it."""

    blocks = [f"## {skill.name}\n{skill.body}".strip() for skill in skills if skill.body]
    return "\n\n".join(blocks)


def narrowed_tools(skills: tuple[Skill, ...]) -> tuple[str, ...]:
    """The union of `allowed-tools` across matched skills, or empty for "do not narrow".

    Empty means the full catalog, never an empty catalog — a skill that forgot to list its tools
    must not silently leave the model with nothing to call.
    """

    names: list[str] = []
    for skill in skills:
        names.extend(skill.allowed_tools)
    return tuple(dict.fromkeys(names))


# The native skill library lives in core.native_skill_library — the ONE contract, selection,
# configuration and projection authority for repo-native, plugin-typed and MCP-sourced skills.
# This module is the SKILL.md parser and the plugin-tree lexical ranker only. The former
# select_native_skill/natives block (lexical, capability-gated) was REMOVED at reconciliation:
# its only callers were tests, now re-pointed, and two selection authorities is the defect
# this file will not grow back.


__all__ = [
    "Skill",
    "instructions_for",
    "load_skills",
    "narrowed_tools",
    "parse_skill",
    "rank_skills",
]
