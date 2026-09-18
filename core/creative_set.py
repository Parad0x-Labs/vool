"""Creative sets - locked character + world continuity for a series of videos/pictures.

A "set" is a saved, named bundle that pins a character's canonical look, wardrobe, and voice, the
surroundings/background, the visual style/palette, and the story/world. Once saved, the user cooks
continuing content by naming the set plus a short script for THIS shot - "video from SET1: she raises
the staff" - and VOOL composes a full generation prompt that re-injects the set's exact character and
world every time, so the face, outfit, voice, and world stay identical across clips and stills. The
canonical appearance repeated verbatim is what keeps a generative model on-model across shots.

Sets persist per-machine in the runtime data dir (git-ignored). Persistence + composition are the
core here (deterministic, offline-testable); a natural-language "create a set" step and the runtime
tools are wired on top.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from core.runtime_paths import data_path

SETS_FILE = "creative_sets.json"


@dataclass(frozen=True)
class Character:
    name: str
    appearance: str = ""     # canonical, detailed physical description - the consistency anchor
    wardrobe: str = ""
    voice: str = ""          # voice for voiceover/dialogue
    personality: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("name", "appearance", "wardrobe", "voice", "personality")}

    @classmethod
    def from_dict(cls, d: dict) -> Character:
        d = d or {}
        return cls(
            name=str(d.get("name", "")), appearance=str(d.get("appearance", "")),
            wardrobe=str(d.get("wardrobe", "")), voice=str(d.get("voice", "")),
            personality=str(d.get("personality", "")),
        )


@dataclass(frozen=True)
class CreativeSet:
    name: str
    characters: tuple[Character, ...] = ()
    setting: str = ""        # surroundings / location
    background: str = ""     # the world backdrop behind the action
    style: str = ""          # visual style
    palette: str = ""        # colour palette
    aspect: str = "16:9"
    mood: str = ""
    story: str = ""          # world / lore / ongoing-story context
    negative: str = ""       # set-specific things to avoid

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "characters": [c.to_dict() for c in self.characters],
            "setting": self.setting, "background": self.background, "style": self.style,
            "palette": self.palette, "aspect": self.aspect, "mood": self.mood,
            "story": self.story, "negative": self.negative,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CreativeSet:
        d = d or {}
        return cls(
            name=str(d.get("name", "")),
            characters=tuple(Character.from_dict(c) for c in (d.get("characters") or [])),
            setting=str(d.get("setting", "")), background=str(d.get("background", "")),
            style=str(d.get("style", "")), palette=str(d.get("palette", "")),
            aspect=str(d.get("aspect", "") or "16:9"), mood=str(d.get("mood", "")),
            story=str(d.get("story", "")), negative=str(d.get("negative", "")),
        )


def _norm(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _sets_path():
    return data_path(SETS_FILE)


def _raw_load() -> dict:
    try:
        path = _sets_path()
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(raw: dict) -> None:
    path = _sets_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_sets() -> dict[str, CreativeSet]:
    """All saved sets, keyed by normalized name."""
    return {key: CreativeSet.from_dict(value) for key, value in _raw_load().items() if isinstance(value, dict)}


def get_set(name: str) -> CreativeSet | None:
    return load_sets().get(_norm(name))


def list_set_names() -> list[str]:
    return sorted(s.name or key for key, s in load_sets().items())


def save_set(cset: CreativeSet) -> CreativeSet:
    """Persist a set (overwrites one with the same normalized name)."""
    raw = _raw_load()
    raw[_norm(cset.name)] = cset.to_dict()
    _write(raw)
    return cset


def delete_set(name: str) -> bool:
    raw = _raw_load()
    key = _norm(name)
    if key in raw:
        del raw[key]
        _write(raw)
        return True
    return False


_CONSISTENCY_NEGATIVE = (
    "inconsistent character, changed or different face, altered outfit, restyled, off-model, "
    "wrong hair, age drift, different actor, continuity error"
)


def compose_scene_prompt(
    cset: CreativeSet, script: str, *, medium: str = "video", camera: str = "", extra_negative: str = "",
) -> str:
    """Compose a full, on-model generation prompt for one shot: the set's LOCKED character(s) + world
    re-injected verbatim, plus this shot's short script. ``medium`` is 'video' or 'image'."""
    char_segments = []
    for c in cset.characters:
        seg = f"{c.name} - {c.appearance}".strip(" -")
        if c.wardrobe:
            seg += f", wearing {c.wardrobe}"
        char_segments.append(seg)
    char_block = "; ".join(s for s in char_segments if s) or "the established character(s)"
    voice_block = "; ".join(f"{c.name}'s voice: {c.voice}" for c in cset.characters if c.voice)
    world = ". ".join(x for x in (cset.setting.strip(), cset.background.strip()) if x) or "the established setting"
    style = ", ".join(x for x in (cset.style.strip(), (f"palette {cset.palette}" if cset.palette else ""),
                                  cset.aspect, cset.mood.strip()) if x)
    negative = "; ".join(x for x in (cset.negative.strip(), _CONSISTENCY_NEGATIVE, extra_negative.strip()) if x)
    script = str(script or "").strip() or "continue the scene"

    if str(medium).lower().startswith(("image", "still", "photo", "pic")):
        return (
            f"Consistent character(s), identical to the established set: {char_block}. "
            f"Setting: {world}. Style: {style}. Scene: {script}. "
            f"Keep the character design, wardrobe, and world EXACTLY on-model - do not restyle. "
            f"Negative prompt: {negative}"
        )
    cam = camera.strip() or "cinematic camera with intentional, motivated motion"
    lines = [
        f"Continuing scene in the same world, matching all prior shots. Consistent character(s), "
        f"identical face/hair/build to earlier shots: {char_block}.",
        f"Setting and background: {world}.",
        f"Style: {style}. Camera: {cam}.",
        f"Action this shot: {script}.",
    ]
    if voice_block:
        lines.append(f"Voice/dialogue: {voice_block}.")
    if cset.story.strip():
        lines.append(f"Story context: {cset.story.strip()}.")
    lines.append(
        f"Keep the character design, wardrobe, palette, and world EXACTLY consistent with the set - "
        f"do not restyle or re-cast. Negative prompt: {negative}"
    )
    return " ".join(lines)


@dataclass
class _SetBuilder:
    """Convenience for assembling a set field-by-field before saving (used by the NL/tool layer)."""
    name: str
    fields: dict = field(default_factory=dict)
    characters: list = field(default_factory=list)

    def build(self) -> CreativeSet:
        base = CreativeSet(name=self.name, characters=tuple(self.characters))
        return replace(base, **{k: v for k, v in self.fields.items() if hasattr(base, k)})


# ---------------------------------------------------------------------------
# Natural-language "create a set from this paragraph" parser.
#
# Deterministic, offline, and lossy-by-design: it turns a free-form paragraph into a first-draft
# CreativeSet the user then edits or overrides. Two cues drive it - explicit "Label: value" segments
# (Setting:, Palette:, Voice:, Story: ...) which are authoritative, and, for loose prose, keyword
# heuristics that pull a character name + appearance, wardrobe ("wearing ..."), voice ("... voice"),
# aspect ratio, and colour palette, then route the remaining scene/story sentences to setting/story.
# ---------------------------------------------------------------------------

_LABEL_ALIASES = {
    "name": "name", "set": "name", "set name": "name", "title": "name", "named": "name",
    "character": "character", "char": "character", "subject": "character",
    "protagonist": "character", "lead": "character", "hero": "character", "heroine": "character",
    "appearance": "appearance", "looks": "appearance", "description": "appearance",
    "wardrobe": "wardrobe", "outfit": "wardrobe", "clothing": "wardrobe", "wearing": "wardrobe",
    "costume": "wardrobe", "attire": "wardrobe",
    "voice": "voice",
    "personality": "personality", "demeanor": "personality", "demeanour": "personality",
    "temperament": "personality",
    "setting": "setting", "location": "setting", "place": "setting", "scene": "setting",
    "background": "background", "backdrop": "background", "surroundings": "background",
    "environment": "background",
    "style": "style", "aesthetic": "style", "art style": "style",
    "palette": "palette", "colors": "palette", "colours": "palette",
    "color palette": "palette", "colour palette": "palette", "color": "palette", "colour": "palette",
    "aspect": "aspect", "aspect ratio": "aspect", "ratio": "aspect", "format": "aspect",
    "mood": "mood", "tone": "mood", "atmosphere": "mood", "vibe": "mood",
    "story": "story", "lore": "story", "plot": "story", "premise": "story", "backstory": "story",
    "negative": "negative", "avoid": "negative", "exclude": "negative",
}

_LABEL_RE = re.compile(r"^([A-Za-z][A-Za-z ]{1,24}?)\s*[:\-]\s+(.*\S)$")
_SENT_SPLIT = re.compile(r"[\n;]+|(?<=[.!?])\s+")
_ASPECT_RE = re.compile(r"\b(\d{1,2}(?:\.\d{1,2})?)\s*:\s*(\d{1,2})\b")
_WEAR_RE = re.compile(r"\b(?:wearing|dressed in|clad in|dressed as|wears)\s+(?:a |an |the )?([^.,;]+)", re.I)
_WEAR_NOUN_RE = re.compile(
    r"\b(?:in|sporting)\s+(?:a |an |the )?"
    r"([^.,;]*?(?:coat|jacket|dress|suit|gown|armou?r|robe|uniform|hoodie|shirt|cloak|outfit|"
    r"attire|hat|boots|gloves|cape|kimono|scarf|tunic|vest)[^.,;]*)", re.I)
_VOICE_LEAD_RE = re.compile(
    r"\b(?:has|have|with|in|is|of|speaks?(?:\s+in|\s+with)?)\b", re.I)
_VOICE_PRONOUN_RE = re.compile(r"^(?:her|his|their|its|the|a|an)?$", re.I)
_NAME_RE = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?:\s+(?:is|has|wears|stands|appears)\b|,)")
_WEAR_STRIP_RE = re.compile(r",?\s*(?:wearing|dressed in|clad in|wears)\b", re.I)
# A wardrobe value is a noun phrase, not a clause: cut it at the first finite verb / new-subject marker
# so "in a silk kimono walks the market" yields "silk kimono", not the trailing action.
_WARDROBE_CUT_RE = re.compile(
    r"\b(?:walk|walks|walking|walked|run|runs|running|ran|stand|stands|standing|stood|sit|sits|"
    r"sitting|sat|hold|holds|holding|held|carry|carries|carrying|wield|wields|enter|enters|step|"
    r"steps|move|moves|turn|turns|raise|raises|draw|draws|leap|leaps|stride|strides|gaze|gazes|"
    r"beside|next to|who|which|while|and then)\b", re.I)
# Physical / character-type cues used to locate a character's own description sentence.
_PHYSICAL_RE = re.compile(
    r"\b(hair|eyes|tall|short|skin|beard|build|years old|woman|man|boy|girl|lady|gentleman|"
    r"in (?:her|his|their) (?:teens|twenties|thirties|forties|fifties|sixties)|"
    r"freckl|muscular|slender|slim|athletic|scarr?|tattoo|complexion|face|figure|"
    r"knight|warrior|mage|wizard|witch|pilot|samurai|soldier|hunter|robot|android|cyborg|"
    r"king|queen|prince|princess|creature|dragon|elf|orc)\b", re.I)
# Real aspect-ratio denominators; used to reject clock times like "at 3:30" being read as a ratio.
_ASPECT_DENOMS = frozenset({1, 2, 3, 4, 5, 8, 9, 10, 16})

_STYLE_WORDS = (
    "cinematic", "photorealistic", "photoreal", "hyperrealistic", "realistic", "anime", "manga",
    "cartoon", "watercolor", "watercolour", "oil painting", "film noir", "noir", "cyberpunk",
    "steampunk", "surreal", "minimalist", "vaporwave", "retro", "vintage", "monochrome",
    "black and white", "3d render", "claymation", "pixel art", "comic book", "impressionist",
    "documentary", "stop motion", "low poly", "painterly",
)
_MOOD_WORDS = (
    "moody", "dreamy", "gritty", "ethereal", "tense", "serene", "ominous", "whimsical",
    "melancholic", "vibrant", "romantic", "epic", "cozy", "eerie", "upbeat", "somber",
    "playful", "dramatic", "nostalgic", "haunting",
)
_PLACE_WORDS = (
    "alley", "street", "city", "forest", "room", "house", "castle", "bridge", "valley", "mountain",
    "ocean", "sea", "desert", "village", "town", "station", "ship", "spaceship", "lab", "office",
    "bar", "cafe", "rooftop", "field", "cave", "temple", "market", "garden", "beach", "jungle",
    "tower", "dungeon", "at night", "at dawn", "at dusk", "indoors", "outdoors", "neon", "rain",
    "snow", "skyline", "landscape", "set in", "takes place",
)
_STORY_WORDS = (
    "hunting", "quest", "seeks", "searching for", "must ", "war", "betray", "secret", "mission",
    "courier", "detective", "journey", "revenge", "escape", "survive", "rebellion", "prophecy",
    "heist", "she's a", "he's a", "they're", "on the run", "last of", "hunts", "chasing",
)


def _clean(value: str) -> str:
    return " ".join(str(value or "").strip().strip("-,. ").split())


def _clip_clause(value: str) -> str:
    """Trim a captured phrase at the first finite verb / new-subject marker (keeps a noun phrase)."""
    return _clean(_WARDROBE_CUT_RE.split(str(value or ""), maxsplit=1)[0])


def _is_plausible_aspect(num: str, den: str) -> bool:
    """A real aspect ratio, not a clock time: denominator is a known ratio denom (rejects :30/:45)."""
    try:
        return int(den) in _ASPECT_DENOMS and 0.2 <= float(num) <= 32.0
    except ValueError:
        return False


def _voice_from_prose(parts: list[str]) -> str:
    """The adjective phrase qualifying a '... voice' mention (e.g. 'calm, low').

    Handles both orders: '<adj> voice' (descriptor before) and 'voice is <adj>' (descriptor after),
    falling through to the post-modifier when the pre-modifier is only a bare pronoun/article.
    """
    for sent in parts:
        m = re.search(r"\bvoice\b", sent, re.I)
        if not m:
            continue
        before = _VOICE_LEAD_RE.split(sent[: m.start()])[-1]
        before = _clean(re.sub(r"^\s*(?:a|an|the)\s+", "", before.strip(), flags=re.I))
        if before and not _VOICE_PRONOUN_RE.match(before):
            return before
        after = re.match(r"\s*(?:is|was|sounds?|sounded)?\s*([^.;!?]+)", sent[m.end():], re.I)
        if after and _clean(after.group(1)):
            return _clean(after.group(1))
        return before
    return ""


def _palette_from_prose(parts: list[str]) -> str:
    """A colour phrase bound to a 'palette' mention, kept short (no spill across a sentence).

    Gated on the keyword's presence and using a de-overlapped pattern so an ordinary long paragraph
    with no 'palette' word cannot trigger quadratic backtracking.
    """
    text = " ".join(parts)
    if "palette" not in text.lower():
        return ""
    m = re.search(r"palette of\s+([^.;!?]+)", text, re.I)
    if m:
        return _clean(m.group(1))
    m = re.search(r"([A-Za-z][A-Za-z&/-]*(?:[ +][A-Za-z&/-]+)*)\s+(?:colou?r[- ])?palette\b", text, re.I)
    return _clean(m.group(1)) if m else ""


def _appearance_from_prose(parts: list[str], name: str) -> tuple[str, str]:
    """Return (appearance_text, raw_sentence) describing the chosen character.

    Prefers the sentence led by ``name`` so a two-character paragraph never grafts one person's face
    onto another's name; otherwise the first physical sentence not led by a *different* proper name.
    """
    def strip_lead(sent: str) -> str:
        text = sent.strip().rstrip(".")
        if name:
            text = re.sub(rf"^{re.escape(name)}\s+(?:is|,)\s*", "", text).strip()
        return _clean(text)

    if name:
        for sent in parts:
            lead = _NAME_RE.match(sent.strip())
            if lead and _clean(lead.group(1)) == _clean(name):
                return strip_lead(sent), sent
    for sent in parts:
        lead = _NAME_RE.match(sent.strip())
        if name and lead and _clean(lead.group(1)) != _clean(name):
            continue                        # this sentence describes a different named character
        if _PHYSICAL_RE.search(sent):
            return strip_lead(sent), sent
    return "", ""


def _first_sentence(parts: list[str], keywords: tuple[str, ...], skip: str = "") -> str:
    for sent in parts:
        if skip and sent == skip:
            continue
        low = sent.lower()
        if any(k in low for k in keywords):
            return sent.strip().rstrip(".")
    return ""


def set_from_description(text: str, name: str = "") -> CreativeSet:
    """Best-effort parse of a natural-language paragraph into a ``CreativeSet``.

    Never raises: on empty or unparseable input it returns a set carrying whatever name was given
    (or "Untitled set"). Explicit ``Label: value`` segments win over prose heuristics for the same
    field. Intended as a first draft the caller can override field-by-field.
    """
    text = str(text or "").strip()
    set_name = _clean(name)

    # A leading "NAME:" / "SET1:" prefix that is not itself a known field label becomes the set name.
    head = re.match(r"^([A-Za-z0-9][\w -]{0,23})\s*:\s+(.+)$", text, re.S)
    if head and _norm(head.group(1)) not in _LABEL_ALIASES:
        if not set_name:
            set_name = head.group(1).strip()
        text = head.group(2).strip()

    fields: dict[str, str] = {}
    prose_parts: list[str] = []
    for clause in _SENT_SPLIT.split(text):
        clause = clause.strip()
        if not clause:
            continue
        match = _LABEL_RE.match(clause)
        key = _LABEL_ALIASES.get(_norm(match.group(1))) if match else None
        if key:
            val = _clean(match.group(2))
            fields[key] = (fields[key] + " " + val).strip() if key in fields else val
        else:
            prose_parts.append(clause)              # keep terminal punctuation as regex boundaries
    prose = " ".join(prose_parts)

    def cue(field_name: str, value: str) -> None:
        if value and not fields.get(field_name):
            fields[field_name] = _clean(value)

    # aspect ratio (accepts decimals like 2.39:1; rejects clock times like "at 3:30")
    for am in _ASPECT_RE.finditer(fields.get("aspect", "") or prose):
        if _is_plausible_aspect(am.group(1), am.group(2)):
            fields["aspect"] = f"{am.group(1)}:{am.group(2)}"
            break

    wm = _WEAR_RE.search(prose) or _WEAR_NOUN_RE.search(prose)
    if wm:
        cue("wardrobe", _clip_clause(wm.group(1)))

    cue("voice", _voice_from_prose(prose_parts))
    cue("palette", _palette_from_prose(prose_parts))

    low = prose.lower()
    styles = [w for w in _STYLE_WORDS if w in low]
    if styles:
        cue("style", ", ".join(dict.fromkeys(styles)))
    moods = [w for w in _MOOD_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]
    if moods:
        cue("mood", ", ".join(dict.fromkeys(moods)))

    # character name + appearance
    char_name = ""
    appearance = fields.get("appearance", "")
    if fields.get("character"):
        cparts = re.split(r",| is | - ", fields["character"], maxsplit=1)
        char_name = _clean(cparts[0])
        if len(cparts) > 1 and not appearance:
            appearance = _clean(cparts[1])
    if not char_name:
        nm = _NAME_RE.search(prose)
        if nm:
            char_name = _clean(nm.group(1))
    appearance_raw = ""
    if not appearance:
        appearance, appearance_raw = _appearance_from_prose(prose_parts, char_name)
    if appearance and not fields.get("wardrobe"):
        wa = _WEAR_RE.search(appearance) or _WEAR_NOUN_RE.search(appearance)
        if wa:
            fields["wardrobe"] = _clip_clause(wa.group(1))
    if appearance and fields.get("wardrobe"):
        appearance = _clean(_WEAR_STRIP_RE.split(appearance, maxsplit=1)[0])

    if not set_name and fields.get("name"):
        set_name = _clean(fields["name"])

    if not fields.get("setting"):
        cue("setting", _first_sentence(prose_parts, _PLACE_WORDS, skip=appearance_raw))
    if not fields.get("story"):
        cue("story", _first_sentence(prose_parts, _STORY_WORDS, skip=appearance_raw))

    characters: tuple[Character, ...] = ()
    if char_name or appearance or fields.get("wardrobe") or fields.get("voice") or fields.get("personality"):
        characters = (Character(
            name=char_name or "Character", appearance=appearance,
            wardrobe=fields.get("wardrobe", ""), voice=fields.get("voice", ""),
            personality=fields.get("personality", "")),)

    return CreativeSet(
        name=set_name or "Untitled set",
        characters=characters,
        setting=fields.get("setting", ""), background=fields.get("background", ""),
        style=fields.get("style", ""), palette=fields.get("palette", ""),
        aspect=fields.get("aspect", "") or "16:9", mood=fields.get("mood", ""),
        story=fields.get("story", ""), negative=fields.get("negative", ""),
    )
