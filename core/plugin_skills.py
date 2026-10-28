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
    return parse_skill_content(text, path=path, plugin_id=plugin_id)


def parse_skill_content(text: str, *, path: Path, plugin_id: str = "") -> Skill | None:
    """Parse an already captured instruction snapshot without re-opening its source."""
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

# Words that name the KIND of help wanted rather than a topic. A request sharing only these
# with a skill ("explain ...", "help me ...") says nothing about WHICH skill: every authoring
# skill "writes", every explainer "explains". They never carry a match alone; they only stand
# behind a topic word that is itself specific — "explain the bill" reaches the bill skill,
# "write a short poem" must not reach every skill mentioning "short" (measured 2026-10-28
# against probe/browse-skills-53: "short" appears in 11 of 53 descriptions).
_KIND_WORDS = frozenset(
    {
        "explain", "write", "make", "help", "plan", "check", "list", "table",
        "summarise", "summarize", "draft", "fix",
    }
)

# Light suffix rules, applied until none fits. Word forms must meet: politely/polite,
# receipts/receipt, organise/organize, translate/translation, planner/planning. This is a
# morphology rule, never a vocabulary list — nothing here knows any individual request.
_SUFFIXES = (
    "ions", "ion", "ising", "izing", "ised", "ized", "ings", "ing", "edly", "ed",
    "ly", "ise", "ize", "er", "es", "s",
)


def _stem(token: str) -> str:
    """Fold one word form onto its stem: strip a light suffix, drop a trailing 'e' (only when
    what remains is 5+ letters, so "plane" does not fold onto "plan"), halve a doubled final
    consonant ("planning" and "planner" both reach "plan")."""

    word = token
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                changed = True
                break
    if word.endswith("e") and len(word) - 1 >= 5:
        word = word[:-1]
    if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "aeiouy":
        word = word[:-1]
    return word


_KIND_STEMS = frozenset(_stem(word) for word in _KIND_WORDS)
_RARE = 1  # a stem no other candidate skill carries
_SCARCE = 3  # carried by at most a handful of candidates


def _raw_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if t]


def _content_stems(text: str) -> list[str]:
    """The ordered, stemmed content words — function words removed, word forms folded."""

    return [
        _stem(t)
        for t in _raw_tokens(text)
        if len(t) > 2 and t not in _STOPWORDS and not t.isdigit()
    ]


def _tokens(text: str) -> set[str]:
    return set(_content_stems(text))


def _contains_phrase(ask_stems: list[str], phrase_stems: list[str]) -> bool:
    """A whole trigger, in the ask: its content stems appear in order and adjacent, or every
    one of them is present however the ask arranges them ("compress and archive my workspace
    into a vault" matches the trigger "compress archive vault workspace"). One word out of a
    multi-word trigger is never a match on its own."""

    if not phrase_stems:
        return False
    span = len(phrase_stems)
    if span == 1:
        return phrase_stems[0] in ask_stems
    if set(phrase_stems) <= set(ask_stems):
        return True
    return any(ask_stems[start : start + span] == phrase_stems for start in range(len(ask_stems) - span + 1))


def rank_skills(skills: tuple[Skill, ...], user_text: str, *, limit: int = 2) -> tuple[Skill, ...]:
    """The skills this request is actually about, best first.

    Overlap only — nothing here decides whether a tool is available. A request that matches no
    skill still reaches the model with the full catalog; it just gets no extra instructions.

    Measured 2026-10-28 against 53 real Browse skills: one shared ordinary word was enough to
    inject a skill ("what rows of seats are best on a plane" seated the database skill), so a
    match now needs one of:

    * the ask names the skill — its name verbatim, or every stem of a multi-word name;
    * the ask contains a whole declared trigger (a multi-word trigger must appear as a phrase,
      in order — one word out of it is not a match);
    * the ask shares at least two distinct content stems with the skill's name, description and
      triggers, at least one of which is a topic word. Kind-of-help words (explain, write,
      make, help, plan, check, list, table, summarise, draft, fix) never carry a match alone,
      and may only stand behind a topic word no other candidate skill carries or that only a
      few do: "fix the spelling" reaches the proofreading skill, "explain how it works" must
      not reach whichever skill happens to say "worked example";
    * the ask uses one word that is BOTH distinctive (no other candidate skill contains it) and
      part of this skill's own name — "which chart should I use" is about the chart skill even
      though "chart" is its only overlap.
    """

    ask_stems = _content_stems(user_text)
    if not ask_stems:
        return ()
    ask_stem_set = set(ask_stems)
    # "use the release-notes skill" names the skill outright. That is the strongest statement of
    # intent there is, and it must not lose to a skill whose description merely shares more words.
    spoken = " " + " ".join(_raw_tokens(user_text)) + " "

    # How many candidate skills carry each stem: a word many skills carry says little about
    # which one this ask means, so rarity decides how much a shared word is worth.
    stem_owners: dict[str, set[int]] = {}
    per_skill: list[tuple[set[str], set[str], list[list[str]]]] = []
    for index, skill in enumerate(skills):
        name_stems = set(_content_stems(skill.name))
        desc_stems = set(_content_stems(skill.description))
        # A word that only ever appears INSIDE a multi-word trigger is evidence as part of that
        # phrase and never on its own ("index" counts through "add an index"; "the index at the
        # back of my cookbook" must not seat the database skill — measured 2026-10-28).
        trigger_stems: set[str] = set()
        phrase_stems: list[list[str]] = []
        for trigger in skill.triggers:
            stems = _content_stems(trigger)
            if not stems:
                continue
            phrase_stems.append(stems)
            if len(stems) == 1:
                trigger_stems.update(stems)
        per_skill.append((name_stems, desc_stems | trigger_stems, phrase_stems))
        for stem in name_stems | desc_stems | trigger_stems:
            stem_owners.setdefault(stem, set()).add(index)

    def _owners(stem: str) -> int:
        return len(stem_owners.get(stem, ()))

    scored: list[tuple[int, int, Skill]] = []
    for index, skill in enumerate(skills):
        name_stems, corpus_stems, phrase_stems = per_skill[index]
        # Naming the skill: the words of its name, verbatim and adjacent...
        name_words = " ".join(_raw_tokens(skill.name)).strip()
        named = bool(name_words) and len(name_words) > 2 and f" {name_words} " in spoken
        # ...or every stem of a multi-word name in the ask, provided the name is specific
        # enough to be naming ("explain what this sql query does" names the sql-explainer;
        # "explain this in plain words" only borrows two generic words of plain-explainer).
        name_covered = (
            len(name_stems) >= 2
            and name_stems <= ask_stem_set
            and any(stem not in _KIND_STEMS and _owners(stem) <= _SCARCE for stem in name_stems)
        )
        hits = [stems for stems in phrase_stems if _contains_phrase(ask_stems, stems)]
        shared = ask_stem_set & (name_stems | corpus_stems)
        topic = shared - _KIND_STEMS
        # A kind word may only complete a pair whose topic word is itself specific.
        carried_by_topic = any(_owners(stem) <= 2 for stem in topic)
        qualified = (
            named
            or name_covered
            or bool(hits)
            or len(topic) >= 2
            or (topic and len(shared) >= 2 and carried_by_topic)
            or any(
                stem in name_stems and _owners(stem) == _RARE for stem in topic
            )
        )
        if not qualified:
            continue
        # An explicit trigger is a stronger statement of intent than a word in prose, so a
        # whole trigger outweighs description overlap; without that, a long description
        # outranks a precise trigger list on volume alone. Rarer shared words count more:
        # "contract" pins one skill, "short" appears in eleven descriptions.
        distinctive_name = any(stem in name_stems and _owners(stem) == _RARE for stem in topic)
        score = (
            (100 if named else 0)
            + (70 if name_covered else 0)
            + sum(20 + 10 * len(stems) for stems in hits)
            + (25 if distinctive_name else 0)
            + sum((12 if _owners(stem) == _RARE else 6 if _owners(stem) <= _SCARCE else 3) for stem in topic)
            + 2 * len(shared - topic)
            + 3 * len(ask_stem_set & corpus_stems)
        )
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
