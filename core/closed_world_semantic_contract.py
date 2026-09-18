"""Closed-world reasoning for explicit fictional rules and stipulated phase thresholds.

The inputs admitted here are not questions about the real world.  The user supplies all rules that
may determine the answer and explicitly asks for an inference inside that frame.  This module owns
both admission and rendering so the planner bypass cannot drift from the answer contract.

No entity registry or prompt-to-answer table lives here.  Fictional rules are parsed into one of a
few causal forms (transformation, consumption, or described-agent action), while temperature phase
thresholds are evaluated as intervals.  Missing rules, mismatched subjects and extra requests fail
closed to the normal model path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_FICTION_MARKER = re.compile(
    r"\b(?:assume|suppose|imagine)\b.*\b(?:fictional|fantasy|sci-fi|tabletop|novel|script|game)\b"
    r"|\bbased\s+only\s+on\s+this\s+fictional\s+definition\b",
    re.IGNORECASE | re.DOTALL,
)
_LOOKUP_TAIL = re.compile(
    r"(?:\s+(?:do\s+not|don't)\s+(?:use|search|look\s+up|check|access|read|browse|fetch|"
    r"trigger|call|invoke)\b[^.?!]*[.?!]?\s*)+$",
    re.IGNORECASE,
)
_FICTION_FRAME = re.compile(
    r'^\s*(?:assume|suppose|imagine)\b(?P<preamble>[^.]*?)\bthat\s+["“](?P<term>[^"”]{1,80})["”]\s+'
    r"is\s+(?P<definition>[^.?!]+)[.?!]\s*"
    r"(?:based\s+only\s+on\s+this\s+fictional\s+definition\s*,?\s*)?"
    r"(?P<question>what\s+(?:happens|would\s+happen)\s+if\s+.+?[?])\s*"
    r"(?P<constraints>.*)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TRANSFORM_RULE = re.compile(
    r"\b(?:turns?|transforms?|changes?|converts?)\s+(?P<source>.+?)\s+into\s+(?P<target>.+)$",
    re.IGNORECASE,
)
_CONSUME_RULE = re.compile(r"\b(?:eats?|devours?|consumes?|swallows?)\s+(?P<object>.+)$", re.IGNORECASE)
_APPLY_ACTION = re.compile(
    r"^what\s+(?:happens|would\s+happen)\s+if\s+i\s+"
    r'(?:cast|apply|use|pour|spray)\s+["“]?(?P<term>[^"”]+?)["”]?\s+'
    r"(?:on|onto|to|at)\s+(?P<target>.+?)\?\s*$",
    re.IGNORECASE,
)
_PLACE_ACTION = re.compile(
    r"^what\s+(?:happens|would\s+happen)\s+if\s+i\s+"
    r'(?:put|place|set|leave)\s+["“]?(?P<term>[^"”]+?)["”]?\s+'
    r"(?:in|inside|into|within)\s+(?P<container>.+?)\?\s*$",
    re.IGNORECASE,
)
_ATTACK_ACTION = re.compile(
    r"^what\s+(?:happens|would\s+happen)\s+if\s+"
    r'["“]?(?P<term>[^"”]+?)["”]?\s+(?P<verb>attacks?|strikes?|assaults?)\s+'
    r"(?P<target>.+?)\?\s*$",
    re.IGNORECASE,
)
_INSTRUMENT = re.compile(r"\bwith\s+(?P<instrument>[^,;]+)$", re.IGNORECASE)

_FICTIONAL_BIO_MARKER = re.compile(
    r"\b(?:assume|suppose|imagine|pretend)\b[^.?!]{0,180}"
    r"\b(?:fictional|made[- ]?up|imaginary|fake|fantasy|sci[- ]?fi|story|novel|game|"
    r"world|setting)\b"
    r"|\b(?:in|for)\s+(?:my|this|a)\s+"
    r"(?:fictional|made[- ]?up|imaginary|fake|fantasy|sci[- ]?fi)\b"
    r"|\bfictional\s+(?:story|novel|game|world|setting)\s+(?:rule|definition)\b",
    re.IGNORECASE,
)
_FICTIONAL_BIO_DEFINITION = re.compile(
    r'["“](?P<term>[^"”]{1,80})["”]\s*'
    r"(?:is|means|is\s+defined\s+as|=|:)\s*"
    r"(?P<definition>[^.?!;]{3,300})[.?!;]",
    re.IGNORECASE,
)
_FICTIONAL_CURE_QUESTION = re.compile(
    r"(?P<question>"
    r"how\s+(?:(?:do|does|would|could|can|should)\s+)?"
    r"(?:(?:you|we|one|u|ya|d'?ya)\s+)?(?:cure|treat|heal|get\s+rid\s+of)\s+.+?"
    r"|how(?:'s|s|\s+is)\s+.+?\s+(?:cured|treated|healed)"
    r"|what(?:'s|\s+is)?\s+(?:the\s+)?(?:cure|treatment|antidote|remedy)\s+for\s+.+?"
    r"|what\s+(?:cures|treats|heals)\s+.+?"
    r"|is\s+there\s+(?:a|any)\s+(?:cure|treatment|antidote|remedy)\s+for\s+.+?"
    r"|(?:cure|treatment|antidote|remedy)\s+for\s+.+?"
    r")\s*\?*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FICTIONAL_CURE_CONSTRAINT_TAIL = re.compile(
    r"(?:\s+(?:(?:do\s*not|don'?t|no)\s+"
    r"(?:use|search|look\s*up|check|browse|google|web|tools?|medical\s+advice)\b"
    r"[^.?!]*[.?!]?\s*))+$",
    re.IGNORECASE,
)
_BIOLOGICAL_ENTITY = re.compile(
    r"\b(?:virus|viral\s+infection|pathogen|disease|infection|plague|parasite|"
    r"bacteri(?:um|a)|germ|microbe|fungus|fungal\s+infection|spore|contagion|bug)\b",
    re.IGNORECASE,
)
_BIOLOGICAL_HARM = re.compile(
    r"\b(?:contagious|infectious|flesh[- ]eating|deadly|lethal|harmful|dangerous|virulent|"
    r"toxic|nasty|spreads?|infects?|devours?|eats?|attacks?|destroys?|rots?|kills?|"
    r"causes?\s+(?:illness|sickness|fever|pain|rash|damage))\b",
    re.IGNORECASE,
)
_PERSONAL_MEDICAL_CONTEXT = re.compile(
    r"\b(?:in\s+real\s+life|real[- ]world\s+(?:symptom|illness|infection)|"
    r"i\s+(?:have|feel|am\s+sick|was\s+diagnosed)|i'?ve\s+got|"
    r"my\s+(?:symptoms?|body|skin|doctor|medication|prescription|child|baby)|"
    r"should\s+i\s+(?:take|drink|use|stop)|diagnose\s+me|medical\s+advice\s+for\s+me)\b",
    re.IGNORECASE,
)
_FICTIONAL_MIXED_ACTION = re.compile(
    r"\b(?:delete|remove|open|read|write|create|send|deploy|install|run|execute|"
    r"search|browse|google|look\s+up)\b",
    re.IGNORECASE,
)
_EXPLICIT_NO_CURE = re.compile(
    r"\b(?:has|have|there\s+is|there's)\s+(?:absolutely\s+)?no\s+"
    r"(?:known\s+)?(?:cure|treatment|antidote|remedy)\b"
    r"|\b(?:incurable|untreatable)\b",
    re.IGNORECASE,
)
_CURE_MECHANISM_PATTERNS = (
    re.compile(
        r"\b(?:is|can\s+(?:only\s+)?be|must\s+(?:only\s+)?be|gets?)\s+(?:only\s+)?"
        r"(?:cured|treated|healed|neutralized)\s+(?:by|with|using|through)\s+"
        r"(?P<mechanism>[^.?!;]{1,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the\s+)?(?:cure|treatment|antidote|remedy)\s+"
        r"(?:is|equals|requires)\s+(?P<mechanism>[^.?!;]{1,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.?!;]\s*)(?:in\s+(?:this|that)\s+(?:world|story|game|setting)\s*,?\s*)?"
        r"(?P<mechanism>[^.?!;]{1,100}?)\s+"
        r"(?:cures|treats|heals|neutralizes|eradicates)\s+"
        r"(?:it|the\s+(?:infection|disease)|[A-Za-z][A-Za-z0-9 -]{0,60}\s+infection)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the\s+)?(?:infection|disease|it)\s+"
        r"(?:clears?|ends?|vanishes?|is\s+cured|is\s+treated)\s+when\s+"
        r"(?P<mechanism>[^.?!;]{1,120})",
        re.IGNORECASE,
    ),
)

_RPG_FRAME = re.compile(
    r"^\s*(?:assume|suppose|imagine)\b(?P<frame>[^.?!]*\b(?:rpg|game|campaign|adventure)\b[^.?!]*)"
    r"[.?!:;]\s*[\"“](?P<term>[^\"”]{1,80})[\"”]\s+is\s+(?:the\s+name\s+of\s+)?"
    r"(?P<definition>[^.?!;]+)[.?!;]\s*"
    r"if\s+i\s+(?P<action>give|use|apply|cast|attack|strike)\s+(?P<application>[^?]+),?\s*"
    r"what\s+(?:happens|would\s+happen)(?:\s+in\s+(?:the\s+)?(?:game|rpg|campaign))?\s*\?\s*"
    r"(?P<constraints>.*)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_RESTORE_EFFECT = re.compile(
    rf"\b(?:restores?|heals?|recovers?|replenishes?)\s+(?P<amount>{_NUMBER})\s*"
    r"(?P<unit>hp|health|hit\s+points?)\b",
    re.IGNORECASE,
)
_DAMAGE_EFFECT = re.compile(
    rf"\b(?:deals?|inflicts?|causes?)\s+(?:(?P<amount>{_NUMBER})\s+)?"
    r"(?:(?P<area>aoe|area[- ]of[- ]effect|area)\s+)?"
    r"(?:(?P<element>fire|ice|frost|lightning|electric|poison|acid|shadow|radiant)\s+)?"
    r"damage\b",
    re.IGNORECASE,
)
_GIVE_APPLICATION = re.compile(r"^[\"“]?(?P<term>[^\"”]+?)[\"”]?\s+to\s+(?P<target>.+?)\s*$", re.IGNORECASE)
_USE_APPLICATION = re.compile(
    r"^[\"“]?(?P<term>[^\"”]+?)[\"”]?\s+(?:on|to|at)\s+(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
_CAST_APPLICATION = re.compile(
    r"^[\"“]?(?P<term>[^\"”]+?)[\"”]?\s+(?:in|into|on|onto|at)\s+(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
_ATTACK_APPLICATION = re.compile(r"^(?P<target>.+?)\s+with\s+[\"“]?(?P<term>[^\"”]+?)[\"”]?\s*$", re.IGNORECASE)

_PHASE_FRAME = re.compile(
    rf"^\s*(?:assume|suppose|imagine)\b(?P<preamble>.+?)\b"
    rf"(?P<material>[a-z][a-z -]{{0,50}}?)\s+boils?\s+at\s+(?P<boil>{_NUMBER})\s*"
    rf"degrees?\s*(?:celsius|c|°c)?\s+and\s+freezes?\s+at\s+(?P<freeze>{_NUMBER})\s*"
    rf"degrees?\s*(?:celsius|c|°c)?[.?!]\s*"
    rf"if\s+(?:it|the\s+(?:outside\s+)?temperature)\s+is\s+(?P<query>{_NUMBER})\s*"
    rf"degrees?(?:\s*(?:celsius|c|°c))?\s*(?:outside\s*)?,?\s*"
    rf"is\s+(?P<subject>.+?)\s+(?:a\s+)?liquid\s*,\s*(?:a\s+)?gas\s*,\s*or\s*(?:a\s+)?solid\s*\?\s*"
    rf"(?P<constraints>.*)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_PHASE_FRAME_REVERSED = re.compile(
    rf"^\s*(?:assume|suppose|imagine)\b(?P<preamble>.+?)\b"
    rf"(?P<material>[a-z][a-z -]{{0,50}}?)\s+freezes?\s+at\s+(?P<freeze>{_NUMBER})\s*"
    rf"degrees?\s*(?:celsius|c|°c)?\s+and\s+boils?\s+at\s+(?P<boil>{_NUMBER})\s*"
    rf"degrees?\s*(?:celsius|c|°c)?[.?!]\s*"
    rf"if\s+(?:it|the\s+(?:outside\s+)?temperature)\s+is\s+(?P<query>{_NUMBER})\s*"
    rf"degrees?(?:\s*(?:celsius|c|°c))?\s*(?:outside\s*)?,?\s*"
    rf"is\s+(?P<subject>.+?)\s+(?:a\s+)?liquid\s*,\s*(?:a\s+)?gas\s*,\s*or\s*(?:a\s+)?solid\s*\?\s*"
    rf"(?P<constraints>.*)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_DIRECTION = (
    r"left(?:wards?)?|right(?:wards?)?|up(?:wards?)?|down(?:wards?)?|"
    r"north(?:wards?)?|south(?:wards?)?|east(?:wards?)?|west(?:wards?)?|forward|backward"
)
_DIRECTIONAL_GRAVITY_FRAME = re.compile(
    rf"^\s*(?:assume|suppose|imagine)\b(?P<preamble>[^.?!]*?)\b"
    rf"(?:effective\s+)?gravity\b[^.?!]{{0,80}}?\b(?:pulls?|points?|acts?)\s+"
    rf"(?:(?:to|toward|towards|in)\s+)?(?:the\s+)?(?P<gravity>{_DIRECTION})\b"
    rf"(?:\s+instead\s+of\s+(?:the\s+)?(?:usual\s+)?(?:{_DIRECTION}))?\s*[.?!]\s*"
    rf"if\s+(?:i|you)\s+(?:let\s+go\s+of|release)\s+"
    rf"(?P<subject>(?:a|the)\s+(?:helium|lighter-than-air)\s+balloon)\s*,?\s*"
    rf"which\s+direction\s+does\s+(?:it|the\s+balloon)\s+(?:travel|move|go|accelerate)"
    rf"(?:\s+relative\s+to\s+(?:me|you|the\s+observer))?\s*\?\s*"
    rf"(?P<constraints>.*)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_ACCELERATION_UNIT = (
    r"(?:m(?:eters?)?\s*/\s*s(?:econds?)?\s*(?:\^\s*2|²)"
    r"|m(?:eters?)?\s+per\s+seconds?\s+squared)"
)
_DIRECTIONAL_FALLING_BODY_FRAME = re.compile(
    rf"^\s*(?:assume|suppose|imagine)\b(?P<preamble>[^.?!]*?)\b"
    rf"(?:effective\s+)?gravity\b[^.?!]{{0,80}}?\b(?:pulls?|points?|acts?)\s+"
    rf"(?:(?:to|toward|towards|in)\s+)?(?:the\s+)?(?P<gravity>{_DIRECTION})\b"
    rf"(?:\s+instead\s+of\s+(?:the\s+)?(?:usual\s+)?(?:{_DIRECTION}))?"
    rf"(?:\s+at\s+(?P<magnitude>{_NUMBER})\s*(?P<unit>{_ACCELERATION_UNIT}))?\s*[.?!]\s*"
    rf"if\s+(?:i|you)\s+(?:drop|release|let\s+go\s+of)\s+"
    rf"(?P<subject>(?:an?|the)\s+[^,?]{{1,80}}?)"
    rf"(?:\s+from\s+[^,?]{{1,80}})?\s*,?\s*"
    rf"which\s+direction\s+(?:does|will)\s+(?:it|the\s+(?:object|body))\s+"
    rf"(?:travel|move|go|accelerate|fall)(?:\s+and\s+why)?\s*\?\s*"
    rf"(?P<constraints>.*)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NON_ORDINARY_FALLING_SUBJECT_RE = re.compile(
    r"\b(?:balloons?|lighter-than-air|helium|bubbles?|smoke|gas|vapou?r|steam|flames?|"
    r"liquids?|water|oil|clouds?)\b",
    re.IGNORECASE,
)

_CLOCK_OFFSET_RELATION_RE = re.compile(
    rf"(?P<target>[A-Za-z][A-Za-z0-9'’ -]{{0,80}}?)\s+"
    rf"(?P<standard>standard\s+)?time\s+"
    rf"(?:is|runs?)\s+(?:permanently\s+)?(?:fixed\s+to\s+be\s+)?(?:exactly\s+)?"
    rf"(?P<offset>{_NUMBER})\s*(?P<unit>hours?|hrs?|h|minutes?|mins?|min)\s+"
    rf"(?P<direction>ahead|behind)\s+(?:of\s+)?(?P<source>[^.?!]{{1,80}})",
    re.IGNORECASE,
)
_CLOCK_TIME = (
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
    r"(?P<meridiem>a\.?m\.?|p\.?m\.?)?"
)
_CLOCK_OFFSET_QUESTION_RE = re.compile(
    rf"\bif\s+it\s+is\s+{_CLOCK_TIME}\s+(?P<source>[^,?]{{1,80}})\s*,\s*"
    rf"what\s+time\s+is\s+it\s+(?:on|in|at)\s+(?P<target>[^?]{{1,80}})\?",
    re.IGNORECASE,
)
_CLOCK_FRAME_START_RE = re.compile(
    r"^\s*(?:assum(?:e|ing)|suppos(?:e|ing)|imagin(?:e|ing))\b",
    re.IGNORECASE,
)
_CLOCK_LABEL_STOPWORDS = frozenset(
    {"at", "clock", "in", "of", "on", "standard", "the", "time"}
)

_STIPULATED_NUMBER = r"[+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+]?\.\d+"
_STIPULATED_UNIT_RATIO = re.compile(
    rf"^\s*(?:assume|suppose|given|using)\b(?:\s+that)?\s*:?\s*"
    rf"(?P<left_quantity>{_STIPULATED_NUMBER})\s*[\"'“‘](?P<left_unit>[A-Za-z][A-Za-z0-9'’ -]{{0,60}})[\"'”’]\s*"
    rf"(?:=|equals?|is\s+worth)\s*"
    rf"(?P<right_quantity>{_STIPULATED_NUMBER})\s*[\"'“‘](?P<right_unit>[A-Za-z][A-Za-z0-9'’ -]{{0,60}})[\"'”’]\s*"
    rf"[.?!;]\s*",
    re.IGNORECASE,
)
_STIPULATED_UNIT_HOLDING = re.compile(
    rf"(?:i|we)\s+(?:have|hold|own|start\s+with|currently\s+have|(?:have\s+)?got)\s+"
    rf"(?P<quantity>{_STIPULATED_NUMBER})\s+(?P<unit>[A-Za-z][A-Za-z0-9'’ -]{{0,80}}?)\s*[.?!;]",
    re.IGNORECASE,
)
_STIPULATED_UNIT_PURCHASE = re.compile(
    rf"(?:i|we)\s+(?:(?:buy|bought|purchase|purchased|acquire|acquired|get|got)\s+"
    rf"(?P<item>[A-Za-z0-9][A-Za-z0-9'’ -]{{0,100}}?)\s+"
    rf"(?:for|costing|at\s+(?:a\s+)?cost\s+of)\s+"
    rf"(?P<quantity>{_STIPULATED_NUMBER})\s+(?P<unit>[A-Za-z][A-Za-z0-9'’ -]{{0,80}}?)"
    rf"|(?:spend|spent|pay|paid)\s+(?P<spend_quantity>{_STIPULATED_NUMBER})\s+"
    rf"(?P<spend_unit>[A-Za-z][A-Za-z0-9'’ -]{{0,80}}?)\s+(?:on|for)\s+"
    rf"(?P<spend_item>[A-Za-z0-9][A-Za-z0-9'’ -]{{0,100}}?))\s*[.?!;]",
    re.IGNORECASE,
)
_STIPULATED_UNIT_QUESTION = re.compile(
    r"(?:how\s+many\s+(?P<unit>[A-Za-z][A-Za-z0-9'’ -]{0,80}?)\s+"
    r"(?:(?:(?:do|will|would)\s+(?:i|we)\s+have\s+)?"
    r"(?:(?:are|will\s+be|would\s+be)\s+)?(?:remain|remaining|left|left\s+over)"
    r"|(?:do|will|would)\s+(?:i|we)\s+have\s+left(?:\s+over)?)"
    r"|what\s+is\s+(?:(?:my|our)\s+)?remaining\s+balance\s+in\s+"
    r"(?P<balance_unit>[A-Za-z][A-Za-z0-9'’ -]{0,80}?))\s*\?",
    re.IGNORECASE,
)
_STIPULATED_UNIT_EFFECTFUL = re.compile(
    r"\b(?:send|transfer|withdraw|deposit|place\s+(?:an?\s+)?order|charge\s+(?:my|our)|"
    r"use\s+(?:my|our)\s+(?:wallet|account|card)|execute\s+(?:the\s+)?(?:purchase|trade))\b"
    r"|\b(?:please|now)\s+(?:buy|purchase|pay|order)\b"
    r"|\b(?:buy|purchase|order)\b[^.?!]{0,40}\bfor\s+me\b",
    re.IGNORECASE,
)
_STIPULATED_UNIT_BETWEEN = re.compile(
    r"^\s*(?:(?:then|next|after\s+that|afterwards)\s*,?\s*)?$",
    re.IGNORECASE,
)

_OPPOSITE_DIRECTION = {
    "left": "right",
    "right": "left",
    "up": "down",
    "down": "up",
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "forward": "backward",
    "backward": "forward",
}
_DIRECTION_CANONICAL = {
    f"{base}{suffix}": base
    for base in ("left", "right", "up", "down", "north", "south", "east", "west")
    for suffix in ("ward", "wards")
}


def _canonical_direction(value: str) -> str:
    direction = str(value or "").casefold()
    return _DIRECTION_CANONICAL.get(direction, direction)


class FictionalRuleKind(str, Enum):
    TRANSFORM = "transform"
    CONSUME = "consume"
    DESCRIBED_ATTACK = "described_attack"


class RpgEffectKind(str, Enum):
    RESTORE = "restore"
    DAMAGE = "damage"


class FictionalTreatmentKind(str, Enum):
    UNSPECIFIED = "unspecified"
    STIPULATED_MECHANISM = "stipulated_mechanism"
    STIPULATED_INCURABLE = "stipulated_incurable"


@dataclass(frozen=True)
class FictionalCausalContract:
    term: str
    description: str
    kind: FictionalRuleKind
    action_target: str
    rule_source: str = ""
    rule_target: str = ""
    instrument: str = ""
    action_verb: str = ""


@dataclass(frozen=True)
class FictionalBiologicalTreatmentContract:
    """A cure question whose entire biological premise exists only in a fictional frame."""

    term: str
    description: str
    treatment_kind: FictionalTreatmentKind
    mechanism: str = ""


class PhaseAssessmentKind(str, Enum):
    CONSISTENT = "consistent"
    CONTRADICTORY_OVERLAP = "contradictory_overlap"
    UNDERDETERMINED_BOUNDARY = "underdetermined_boundary"


@dataclass(frozen=True)
class PhaseThresholdContract:
    material: str
    subject: str
    freeze_at: Decimal
    boil_at: Decimal
    query_at: Decimal
    assessment: PhaseAssessmentKind
    phase: str = ""


@dataclass(frozen=True)
class DirectionalBuoyancyContract:
    """A lighter-than-air object's direction under one stipulated effective-gravity vector."""

    subject: str
    gravity_direction: str
    buoyant_direction: str


@dataclass(frozen=True)
class DirectionalFallingBodyContract:
    """An ordinary released body following one stipulated effective-gravity vector."""

    subject: str
    gravity_direction: str
    acceleration_m_s2: Decimal | None = None


@dataclass(frozen=True)
class StipulatedClockOffsetContract:
    """A user-supplied fixed relation between two clocks, evaluated without host time."""

    source_clock: str
    target_clock: str
    source_minutes: int
    offset_minutes: int
    target_minutes: int


@dataclass(frozen=True)
class StipulatedUnitInventoryContract:
    """One closed inventory purchase evaluated from a user-authored unit conversion."""

    left_unit: str
    right_unit: str
    left_quantity: Decimal
    right_quantity: Decimal
    holding_unit: str
    holding_quantity: Decimal
    cost_unit: str
    cost_quantity: Decimal
    requested_unit: str
    item: str


@dataclass(frozen=True)
class RpgEffectContract:
    """One user-stipulated game item/spell effect applied to one matching target."""

    term: str
    description: str
    effect_kind: RpgEffectKind
    action: str
    target: str
    amount: Decimal | None = None
    unit: str = ""
    element: str = ""
    area_of_effect: bool = False


ClosedWorldContract = (
    FictionalCausalContract
    | FictionalBiologicalTreatmentContract
    | PhaseThresholdContract
    | DirectionalBuoyancyContract
    | DirectionalFallingBodyContract
    | StipulatedClockOffsetContract
    | StipulatedUnitInventoryContract
    | RpgEffectContract
)


def _flat(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _same_term(candidate: str, expected: str) -> bool:
    return _flat(candidate).strip('"“” ') == _flat(expected).strip('"“” ')


def _constraints_are_closed(value: str) -> bool:
    trailing = str(value or "").strip()
    return not trailing or not _LOOKUP_TAIL.sub("", f" {trailing}").strip()


def parse_fictional_causal_contract(user_text: str) -> FictionalCausalContract | None:
    """Parse a complete fictional causal turn without knowing any particular fictional entity."""

    raw = str(user_text or "").strip()
    if not _FICTION_MARKER.search(raw):
        return None
    match = _FICTION_FRAME.fullmatch(raw)
    if match is None or not _constraints_are_closed(match.group("constraints")):
        return None

    term = " ".join(match.group("term").split())
    description = " ".join(match.group("definition").split())
    question = " ".join(match.group("question").split())
    transform = _TRANSFORM_RULE.search(description)
    apply_action = _APPLY_ACTION.fullmatch(question)
    if transform is not None and apply_action is not None and _same_term(apply_action.group("term"), term):
        return FictionalCausalContract(
            term=term,
            description=description,
            kind=FictionalRuleKind.TRANSFORM,
            action_target=apply_action.group("target").strip(),
            rule_source=transform.group("source").strip(),
            rule_target=transform.group("target").strip(),
        )

    consume = _CONSUME_RULE.search(description)
    place_action = _PLACE_ACTION.fullmatch(question)
    if consume is not None and place_action is not None and _same_term(place_action.group("term"), term):
        return FictionalCausalContract(
            term=term,
            description=description,
            kind=FictionalRuleKind.CONSUME,
            action_target=place_action.group("container").strip(),
            rule_source=consume.group("object").strip(),
        )

    attack_action = _ATTACK_ACTION.fullmatch(question)
    if attack_action is not None and _same_term(attack_action.group("term"), term):
        instrument_match = _INSTRUMENT.search(description)
        return FictionalCausalContract(
            term=term,
            description=description,
            kind=FictionalRuleKind.DESCRIBED_ATTACK,
            action_target=attack_action.group("target").strip(),
            instrument=(instrument_match.group("instrument").strip() if instrument_match else ""),
            action_verb=attack_action.group("verb").strip(),
        )
    return None


def _harmful_biological_definition(definition: str) -> bool:
    return bool(
        _BIOLOGICAL_ENTITY.search(definition)
        and _BIOLOGICAL_HARM.search(definition)
    )


def _question_refers_to_fictional_infection(question: str, term: str) -> bool:
    flat_question = _flat(question)
    flat_term = _flat(term).strip('"“” ')
    if flat_term and re.search(rf"(?<!\w){re.escape(flat_term)}(?!\w)", flat_question):
        return True
    return bool(
        re.search(
            r"\b(?:it|this\s+(?:infection|disease|virus|pathogen)|the\s+"
            r"(?:infection|disease|virus|pathogen)|that\s+(?:infection|disease))\b",
            flat_question,
            re.IGNORECASE,
        )
    )


def _fictional_cure_mechanism(rule_text: str) -> str:
    for pattern in _CURE_MECHANISM_PATTERNS:
        match = pattern.search(rule_text)
        if match is None:
            continue
        mechanism = " ".join(match.group("mechanism").strip(" ,:-").split())
        mechanism = re.sub(
            r"^(?:only\s+|the\s+use\s+of\s+)",
            "",
            mechanism,
            flags=re.IGNORECASE,
        ).strip()
        if mechanism and not _FICTIONAL_MIXED_ACTION.search(mechanism):
            return mechanism
    return ""


def parse_fictional_biological_treatment_contract(
    user_text: str,
) -> FictionalBiologicalTreatmentContract | None:
    """Resolve cure questions only when the user authored a closed fictional biology system.

    Harm properties never imply a treatment. A cure is applied only when the same fictional
    premise states one. Personal symptoms, real medical decisions, mixed actions, mismatched
    entities, and open lookup requests fail closed to the normal runtime.
    """

    raw = str(user_text or "").strip()
    if not raw or not _FICTIONAL_BIO_MARKER.search(raw):
        return None
    if _PERSONAL_MEDICAL_CONTEXT.search(raw):
        return None
    closed = _FICTIONAL_CURE_CONSTRAINT_TAIL.sub("", raw).strip()
    question_match = _FICTIONAL_CURE_QUESTION.search(closed)
    if question_match is None:
        return None
    prefix = closed[: question_match.start()].strip()
    if not prefix or _FICTIONAL_MIXED_ACTION.search(prefix):
        return None
    definition_match = _FICTIONAL_BIO_DEFINITION.search(prefix)
    if definition_match is None:
        return None
    term = " ".join(definition_match.group("term").split())
    description = " ".join(definition_match.group("definition").split())
    question = " ".join(question_match.group("question").split())
    if not _harmful_biological_definition(description):
        return None
    if not _question_refers_to_fictional_infection(question, term):
        return None

    # Only declarative premise text can authorize a cure. The question itself is intentionally
    # excluded so "what cure?" cannot be mistaken for a supplied mechanism.
    rule_text = f"{description}. {prefix[definition_match.end():].strip()}"
    if _EXPLICIT_NO_CURE.search(rule_text):
        return FictionalBiologicalTreatmentContract(
            term=term,
            description=description,
            treatment_kind=FictionalTreatmentKind.STIPULATED_INCURABLE,
        )
    mechanism = _fictional_cure_mechanism(rule_text)
    return FictionalBiologicalTreatmentContract(
        term=term,
        description=description,
        treatment_kind=(
            FictionalTreatmentKind.STIPULATED_MECHANISM
            if mechanism
            else FictionalTreatmentKind.UNSPECIFIED
        ),
        mechanism=mechanism,
    )


def _decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _clock_label_tokens(value: str) -> frozenset[str]:
    normalized = re.sub(r"[’']s\b", "", str(value or "").casefold())
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in _CLOCK_LABEL_STOPWORDS
    )


def _clock_minutes(hour: str, minute: str, meridiem: str) -> int | None:
    hour_value = int(hour)
    minute_value = int(minute or "0")
    if not 0 <= minute_value <= 59:
        return None
    marker = re.sub(r"[^apm]", "", str(meridiem or "").casefold())
    if marker:
        if not 1 <= hour_value <= 12:
            return None
        hour_value %= 12
        if marker == "pm":
            hour_value += 12
    elif not 0 <= hour_value <= 23:
        return None
    return hour_value * 60 + minute_value


def parse_stipulated_clock_offset_contract(
    user_text: str,
) -> StipulatedClockOffsetContract | None:
    """Parse one complete, fixed clock-offset premise and its in-frame question."""

    raw = str(user_text or "").strip()
    if _CLOCK_FRAME_START_RE.search(raw) is None:
        return None
    relations = list(_CLOCK_OFFSET_RELATION_RE.finditer(raw))
    questions = list(_CLOCK_OFFSET_QUESTION_RE.finditer(raw))
    if len(relations) != 1 or len(questions) != 1:
        return None
    relation = relations[0]
    question = questions[0]
    if relation.end() > question.start() or not _constraints_are_closed(raw[question.end() :]):
        return None

    relation_source = _clock_label_tokens(relation.group("source"))
    relation_target = _clock_label_tokens(relation.group("target"))
    question_source = _clock_label_tokens(question.group("source"))
    question_target = _clock_label_tokens(question.group("target"))
    if (
        not relation_source
        or not relation_target
        or relation_source != question_source
        or relation_target != question_target
    ):
        return None

    source_minutes = _clock_minutes(
        question.group("hour"),
        question.group("minute") or "0",
        question.group("meridiem") or "",
    )
    offset = _decimal(relation.group("offset"))
    if source_minutes is None or offset is None or offset < 0:
        return None
    unit = relation.group("unit").casefold()
    offset_decimal_minutes = offset * (Decimal("60") if unit.startswith("h") else Decimal("1"))
    if offset_decimal_minutes != offset_decimal_minutes.to_integral_value():
        return None
    offset_minutes = int(offset_decimal_minutes)
    if relation.group("direction").casefold() == "behind":
        offset_minutes *= -1
    target_minutes = (source_minutes + offset_minutes) % (24 * 60)
    target_clock = " ".join(relation.group("target").split())
    if relation.group("standard"):
        target_clock += " Standard Time"
    return StipulatedClockOffsetContract(
        source_clock=" ".join(question.group("source").split()),
        target_clock=target_clock,
        source_minutes=source_minutes,
        offset_minutes=offset_minutes,
        target_minutes=target_minutes,
    )


def _unit_label(value: str) -> str:
    return " ".join(str(value or "").strip(' "“”.,;:!?').split())


def _unit_key(value: str) -> str:
    return " ".join(_unit_label(value).casefold().split())


def _unit_forms(value: str) -> frozenset[str]:
    """Bounded singular/plural forms for an arbitrary user-authored unit label."""

    key = _unit_key(value)
    words = key.split()
    if not words:
        return frozenset()
    forms = {key}
    stem = words[-1]
    prefixes = words[:-1]
    candidates: set[str] = set()
    if stem.endswith("ies") and len(stem) > 3:
        candidates.add(stem[:-3] + "y")
    if stem.endswith("es") and len(stem) > 3:
        candidates.update((stem[:-2], stem[:-1]))
    if stem.endswith("s") and len(stem) > 2:
        candidates.add(stem[:-1])
    forms.update(" ".join((*prefixes, candidate)) for candidate in candidates if candidate)
    return frozenset(forms)


def _unit_matches(candidate: str, reference: str) -> bool:
    return bool(_unit_forms(candidate) & _unit_forms(reference))


def _ratio_unit_key(candidate: str, left_unit: str, right_unit: str) -> str:
    matches = [
        _unit_key(reference)
        for reference in (left_unit, right_unit)
        if _unit_matches(candidate, reference)
    ]
    return matches[0] if len(set(matches)) == 1 else ""


def parse_stipulated_unit_inventory_contract(
    user_text: str,
) -> StipulatedUnitInventoryContract | None:
    """Parse one user-stipulated conversion, holding, purchase, and balance question.

    Every number and unit must come from the current turn.  The bounded grammar intentionally
    declines real transaction commands, missing or repeated facts, unrelated story text, and any
    request that cannot be solved by exactly one conversion and one subtraction.
    """

    raw = str(user_text or "").strip()
    if not raw or _STIPULATED_UNIT_EFFECTFUL.search(raw):
        return None
    ratio_matches = list(_STIPULATED_UNIT_RATIO.finditer(raw))
    holding_matches = list(_STIPULATED_UNIT_HOLDING.finditer(raw))
    purchase_matches = list(_STIPULATED_UNIT_PURCHASE.finditer(raw))
    question_matches = list(_STIPULATED_UNIT_QUESTION.finditer(raw))
    if not (
        len(ratio_matches)
        == len(holding_matches)
        == len(purchase_matches)
        == len(question_matches)
        == 1
    ):
        return None
    ratio = ratio_matches[0]
    holding = holding_matches[0]
    purchase = purchase_matches[0]
    question = question_matches[0]
    if ratio.start() != 0 or not (
        ratio.end() <= holding.start() < purchase.start() < question.start()
    ):
        return None
    gaps = (
        raw[ratio.end() : holding.start()],
        raw[holding.end() : purchase.start()],
        raw[purchase.end() : question.start()],
    )
    if any(_STIPULATED_UNIT_BETWEEN.fullmatch(gap) is None for gap in gaps):
        return None
    if not _constraints_are_closed(raw[question.end() :]):
        return None

    left_quantity = _decimal(ratio.group("left_quantity"))
    right_quantity = _decimal(ratio.group("right_quantity"))
    holding_quantity = _decimal(holding.group("quantity"))
    cost_quantity = _decimal(purchase.group("quantity") or purchase.group("spend_quantity"))
    if (
        left_quantity is None
        or right_quantity is None
        or holding_quantity is None
        or cost_quantity is None
        or left_quantity <= 0
        or right_quantity <= 0
        or holding_quantity < 0
        or cost_quantity < 0
    ):
        return None

    left_unit = _unit_label(ratio.group("left_unit"))
    right_unit = _unit_label(ratio.group("right_unit"))
    holding_unit = _unit_label(holding.group("unit"))
    cost_unit = _unit_label(purchase.group("unit") or purchase.group("spend_unit"))
    requested_unit = _unit_label(question.group("unit") or question.group("balance_unit"))
    item = _unit_label(purchase.group("item") or purchase.group("spend_item"))
    left_key = _unit_key(left_unit)
    right_key = _unit_key(right_unit)
    if not left_key or not right_key or _unit_matches(left_unit, right_unit):
        return None
    holding_key = _ratio_unit_key(holding_unit, left_unit, right_unit)
    cost_key = _ratio_unit_key(cost_unit, left_unit, right_unit)
    requested_key = _ratio_unit_key(requested_unit, left_unit, right_unit)
    if not holding_key or not cost_key or not requested_key:
        return None
    if holding_key == cost_key:
        return None

    # A negative balance can be represented, but "left over/remain" asserts a non-negative
    # inventory. Decline insufficient-funds questions to the ordinary lane rather than silently
    # changing the requested relation into debt or deficit semantics.
    if holding_key == left_key:
        holding_in_cost = holding_quantity * right_quantity / left_quantity
    else:
        holding_in_cost = holding_quantity * left_quantity / right_quantity
    if holding_in_cost < cost_quantity:
        return None
    return StipulatedUnitInventoryContract(
        left_unit=left_unit,
        right_unit=right_unit,
        left_quantity=left_quantity,
        right_quantity=right_quantity,
        holding_unit=holding_unit,
        holding_quantity=holding_quantity,
        cost_unit=cost_unit,
        cost_quantity=cost_quantity,
        requested_unit=requested_unit,
        item=item,
    )


def _rpg_application(action: str, application: str) -> tuple[str, str] | None:
    if action.casefold() == "give":
        match = _GIVE_APPLICATION.fullmatch(application)
    elif action.casefold() in {"use", "apply"}:
        match = _USE_APPLICATION.fullmatch(application)
    elif action.casefold() == "cast":
        match = _CAST_APPLICATION.fullmatch(application)
    else:
        match = _ATTACK_APPLICATION.fullmatch(application)
    if match is None:
        return None
    return match.group("term").strip(" ,"), match.group("target").strip(" ,")


def _element_from_description(description: str) -> str:
    elements = re.findall(
        r"\b(fire|ice|frost|lightning|electric|poison|acid|shadow|radiant)\b",
        description,
        re.IGNORECASE,
    )
    unique = {item.casefold() for item in elements}
    return elements[0].casefold() if len(unique) == 1 else ""


def parse_rpg_effect_contract(user_text: str) -> RpgEffectContract | None:
    """Parse a complete stipulated RPG effect without a registry of item or spell names."""

    raw = str(user_text or "").strip()
    match = _RPG_FRAME.fullmatch(raw)
    if match is None or not _constraints_are_closed(match.group("constraints")):
        return None
    term = " ".join(match.group("term").split())
    description = " ".join(match.group("definition").split())
    action = match.group("action").casefold()
    application = _rpg_application(action, " ".join(match.group("application").split()))
    if application is None or not _same_term(application[0], term):
        return None
    target = application[1]

    restore = _RESTORE_EFFECT.search(description)
    if restore is not None and action in {"give", "use", "apply"}:
        amount = _decimal(restore.group("amount"))
        if amount is None or amount <= 0:
            return None
        unit = " ".join(restore.group("unit").upper().split())
        if unit == "HEALTH":
            unit = "health"
        elif unit.startswith("HIT "):
            unit = "hit points"
        return RpgEffectContract(
            term=term,
            description=description,
            effect_kind=RpgEffectKind.RESTORE,
            action=action,
            target=target,
            amount=amount,
            unit=unit,
        )

    damage = _DAMAGE_EFFECT.search(description)
    if damage is None or action not in {"cast", "attack", "strike", "use", "apply"}:
        return None
    amount = _decimal(damage.group("amount")) if damage.group("amount") else None
    if amount is not None and amount <= 0:
        return None
    element = (damage.group("element") or "").casefold() or _element_from_description(description)
    return RpgEffectContract(
        term=term,
        description=description,
        effect_kind=RpgEffectKind.DAMAGE,
        action=action,
        target=target,
        amount=amount,
        unit="damage",
        element=element,
        area_of_effect=bool(damage.group("area")),
    )


def parse_phase_threshold_contract(user_text: str) -> PhaseThresholdContract | None:
    """Parse and validate a closed temperature-phase threshold system."""

    raw = str(user_text or "").strip()
    match = _PHASE_FRAME.fullmatch(raw) or _PHASE_FRAME_REVERSED.fullmatch(raw)
    if match is None or not _constraints_are_closed(match.group("constraints")):
        return None
    freeze_at = _decimal(match.group("freeze"))
    boil_at = _decimal(match.group("boil"))
    query_at = _decimal(match.group("query"))
    if freeze_at is None or boil_at is None or query_at is None:
        return None

    if boil_at <= query_at <= freeze_at:
        assessment = PhaseAssessmentKind.CONTRADICTORY_OVERLAP
        phase = ""
    elif query_at in {freeze_at, boil_at}:
        assessment = PhaseAssessmentKind.UNDERDETERMINED_BOUNDARY
        phase = ""
    elif query_at < freeze_at:
        assessment = PhaseAssessmentKind.CONSISTENT
        phase = "solid"
    elif query_at > boil_at:
        assessment = PhaseAssessmentKind.CONSISTENT
        phase = "gas"
    elif freeze_at < query_at < boil_at:
        assessment = PhaseAssessmentKind.CONSISTENT
        phase = "liquid"
    else:
        # This branch is reachable only for a malformed ordering not covered by the overlap test.
        return None
    return PhaseThresholdContract(
        material=" ".join(match.group("material").split()),
        subject=" ".join(match.group("subject").split()),
        freeze_at=freeze_at,
        boil_at=boil_at,
        query_at=query_at,
        assessment=assessment,
        phase=phase,
    )


def parse_directional_buoyancy_contract(user_text: str) -> DirectionalBuoyancyContract | None:
    """Parse a complete stipulated gravity-vector question about a lighter-than-air balloon."""

    raw = str(user_text or "").strip()
    match = _DIRECTIONAL_GRAVITY_FRAME.fullmatch(raw)
    if match is None or not _constraints_are_closed(match.group("constraints")):
        return None
    gravity = _canonical_direction(match.group("gravity"))
    opposite = _OPPOSITE_DIRECTION.get(gravity)
    if opposite is None:
        return None
    subject = re.sub(r"^(?:an?|the)\s+", "", " ".join(match.group("subject").split()), flags=re.IGNORECASE)
    return DirectionalBuoyancyContract(
        subject=subject,
        gravity_direction=gravity,
        buoyant_direction=opposite,
    )


def parse_directional_falling_body_contract(user_text: str) -> DirectionalFallingBodyContract | None:
    """Parse an ordinary released body under a complete stipulated gravity vector."""

    raw = str(user_text or "").strip()
    match = _DIRECTIONAL_FALLING_BODY_FRAME.fullmatch(raw)
    if match is None or not _constraints_are_closed(match.group("constraints")):
        return None
    subject = re.sub(r"^(?:an?|the)\s+", "", " ".join(match.group("subject").split()), flags=re.IGNORECASE)
    if _NON_ORDINARY_FALLING_SUBJECT_RE.search(subject):
        return None
    gravity = _canonical_direction(match.group("gravity"))
    magnitude = _decimal(match.group("magnitude")) if match.group("magnitude") else None
    if magnitude is not None and magnitude <= 0:
        return None
    return DirectionalFallingBodyContract(
        subject=subject,
        gravity_direction=gravity,
        acceleration_m_s2=magnitude,
    )


def parse_closed_world_contract(user_text: str) -> ClosedWorldContract | None:
    return (
        parse_fictional_biological_treatment_contract(user_text)
        or parse_rpg_effect_contract(user_text)
        or parse_stipulated_clock_offset_contract(user_text)
        or parse_stipulated_unit_inventory_contract(user_text)
        or parse_directional_buoyancy_contract(user_text)
        or parse_directional_falling_body_contract(user_text)
        or parse_phase_threshold_contract(user_text)
        or parse_fictional_causal_contract(user_text)
    )


def _temperature(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered or '0'} °C"


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _clock_text(total_minutes: int) -> tuple[str, str]:
    hour_24, minute = divmod(total_minutes % (24 * 60), 60)
    hour_12 = hour_24 % 12 or 12
    meridiem = "AM" if hour_24 < 12 else "PM"
    return f"{hour_12}:{minute:02d} {meridiem}", f"{hour_24:02d}:{minute:02d}"


def render_closed_world_contract(contract: ClosedWorldContract) -> str:
    if isinstance(contract, FictionalBiologicalTreatmentContract):
        premise = (
            f'In the stipulated fictional setting, “{contract.term}” is '
            f"{contract.description}. "
        )
        if contract.treatment_kind is FictionalTreatmentKind.UNSPECIFIED:
            return (
                premise
                + "The premise describes how it harms or spreads, but supplies no cure or "
                "treatment rule. Therefore its cure is unspecified: there is not enough "
                "fictional information to determine one. No real-world treatment should be "
                "inferred, and no medical or web lookup is needed."
            )
        if contract.treatment_kind is FictionalTreatmentKind.STIPULATED_INCURABLE:
            return (
                premise
                + "The fictional rules explicitly say that it has no cure, so within that "
                "setting the infection is incurable. This is a conclusion about the story only, "
                "not real-world medical advice; no lookup is needed."
            )
        return (
            premise
            + f"The stated fictional cure mechanism is {contract.mechanism}. Therefore, within "
            f"that setting, {contract.mechanism} cures the {contract.term} infection. This applies "
            "only inside the stipulated story and is not real-world medical advice; no lookup is needed."
        )
    if isinstance(contract, RpgEffectContract):
        amount = _decimal_text(contract.amount) if contract.amount is not None else ""
        if contract.effect_kind is RpgEffectKind.RESTORE:
            return (
                f"In the stipulated game, {contract.term} is {contract.description}. "
                f"Using {contract.term} on {contract.target} restores {amount} {contract.unit} "
                f"to {contract.target}. No real-world lookup or tool is needed; this outcome "
                "follows only from the game's stated rule."
            )
        element = f"{contract.element} " if contract.element else ""
        quantity = f"{amount} " if amount else ""
        area = "area-of-effect (AoE) " if contract.area_of_effect else ""
        consequence = f"It harms {contract.target}."
        if contract.element == "fire" and re.search(r"\b(?:ice|frost|frozen)\b", contract.target, re.IGNORECASE):
            consequence = f"It damages {contract.target} and can melt their ice."
        limitation = (
            " The premise supplies no numeric damage total."
            if contract.amount is None
            else " The premise does not specify resistance, weakness, or remaining health."
        )
        return (
            f"In the stipulated game, {contract.term} is {contract.description}. Applying "
            f"{contract.term} to {contract.target} deals {quantity}{area}{element}damage. "
            f"{consequence}{limitation} No real-world lookup or tool is needed."
        )
    if isinstance(contract, DirectionalBuoyancyContract):
        return (
            f"The {contract.subject} moves {contract.buoyant_direction} relative to you. "
            f"In the stipulated frame, effective gravity points {contract.gravity_direction}, "
            f"and buoyancy in the surrounding air acts opposite effective gravity: "
            f"{contract.buoyant_direction}. This assumes the atmosphere shares that effective-"
            "gravity field and no stronger airflow dominates. No calculator or web lookup is needed."
        )
    if isinstance(contract, DirectionalFallingBodyContract):
        acceleration = (
            f" at {_decimal_text(contract.acceleration_m_s2)} m/s²"
            if contract.acceleration_m_s2 is not None
            else ""
        )
        return (
            f"The {contract.subject} accelerates {contract.gravity_direction}{acceleration} in the "
            "stipulated frame. Releasing it removes its support, so an ordinary body follows the "
            f"stated effective-gravity vector: {contract.gravity_direction}. The word “drop” does "
            "not impose the usual downward direction when the premise explicitly changes gravity. "
            "No calculator or web lookup is needed."
        )
    if isinstance(contract, StipulatedClockOffsetContract):
        source_12, source_24 = _clock_text(contract.source_minutes)
        target_12, target_24 = _clock_text(contract.target_minutes)
        direction = "ahead" if contract.offset_minutes >= 0 else "behind"
        absolute_offset = abs(contract.offset_minutes)
        if absolute_offset % 60 == 0:
            quantity = absolute_offset // 60
            offset_text = f"{quantity} hour{'s' if quantity != 1 else ''}"
        else:
            offset_text = f"{absolute_offset} minute{'s' if absolute_offset != 1 else ''}"
        operator = "+" if contract.offset_minutes >= 0 else "−"
        return (
            f"Under the stipulated fixed offset, {contract.target_clock} is {target_12} "
            f"({target_24}). {source_12} ({source_24}) on the source clock "
            f"({contract.source_clock}) {operator} "
            f"{offset_text} = {target_12} ({target_24}); the target clock is {offset_text} "
            f"{direction}. No host clock, live-time lookup, timezone database, or tool is used."
        )
    if isinstance(contract, StipulatedUnitInventoryContract):
        unit_quantities = {
            _unit_key(contract.left_unit): contract.left_quantity,
            _unit_key(contract.right_unit): contract.right_quantity,
        }
        holding_key = _ratio_unit_key(
            contract.holding_unit, contract.left_unit, contract.right_unit
        )
        cost_key = _ratio_unit_key(contract.cost_unit, contract.left_unit, contract.right_unit)
        requested_key = _ratio_unit_key(
            contract.requested_unit, contract.left_unit, contract.right_unit
        )
        holding_ratio = unit_quantities[holding_key]
        cost_ratio = unit_quantities[cost_key]
        requested_ratio = unit_quantities[requested_key]
        holding_in_cost = contract.holding_quantity * cost_ratio / holding_ratio
        remainder_in_cost = holding_in_cost - contract.cost_quantity
        remainder_requested = remainder_in_cost * requested_ratio / cost_ratio
        return (
            f"Using only the stipulated conversion from {contract.holding_unit} to "
            f"{contract.cost_unit}: {_decimal_text(contract.holding_quantity)} × "
            f"{_decimal_text(cost_ratio / holding_ratio)} = "
            f"{_decimal_text(holding_in_cost)} {contract.cost_unit}; "
            f"{_decimal_text(holding_in_cost)} − {_decimal_text(contract.cost_quantity)} = "
            f"{_decimal_text(remainder_in_cost)} {contract.cost_unit}; "
            f"{_decimal_text(remainder_in_cost)} ÷ {_decimal_text(cost_ratio / requested_ratio)} = "
            f"{_decimal_text(remainder_requested)} {contract.requested_unit}. You have "
            f"{_decimal_text(remainder_requested)} {contract.requested_unit} left after the "
            f"purchase of {contract.item}. No web lookup, live rate, model inference, or tool is used."
        )
    if isinstance(contract, FictionalCausalContract):
        if contract.kind is FictionalRuleKind.TRANSFORM:
            conclusion = (
                f"Under the stipulated rule, applying {contract.term} to {contract.action_target} "
                f"turns its {contract.rule_source} into {contract.rule_target}."
            )
        elif contract.kind is FictionalRuleKind.CONSUME:
            conclusion = (
                f"Under the stipulated rule, {contract.term} is {contract.description}. Putting it "
                f"in {contract.action_target} puts any {contract.rule_source} there at risk: the "
                f"stipulated creature eats and may destroy them. "
                f"The premise does not establish that {contract.rule_source} are present, so that "
                "part of the outcome is conditional."
            )
        else:
            instrument = f" with its {contract.instrument}" if contract.instrument else ""
            conclusion = (
                f"Under the fictional definition, {contract.term} is {contract.description}. "
                f"It {contract.action_verb} {contract.action_target}"
                f"{instrument}. The premise does not specify the exact damage or whether the target "
                "survives, so no stronger outcome follows."
            )
        return conclusion + " No lookup is needed; this follows only from the user's fictional definition."

    freeze = _temperature(contract.freeze_at)
    boil = _temperature(contract.boil_at)
    query = _temperature(contract.query_at)
    if contract.assessment is PhaseAssessmentKind.CONTRADICTORY_OVERLAP:
        return (
            f"The stipulated thresholds are internally contradictory at {query}: freezing at "
            f"{freeze} makes that temperature satisfy the solid range, while boiling at {boil} "
            f"makes it satisfy the gas range. Those ranges overlap from {boil} through {freeze}, "
            "so both criteria apply. The premises do not uniquely determine liquid, gas, or solid; "
            "an additional conflict-resolution rule is required. No weather API or calculator is needed."
        )
    if contract.assessment is PhaseAssessmentKind.UNDERDETERMINED_BOUNDARY:
        return (
            f"The query is exactly on a stipulated transition threshold at {query}. A threshold alone "
            "does not say which side of the transition owns equality or whether phases coexist there, "
            "so the phase is underdetermined without an additional boundary rule. No external data is needed."
        )
    return (
        f"Under the stipulated thresholds, {contract.subject} is {contract.phase} at {query}: "
        f"the freezing point is {freeze} and the boiling point is {boil}. "
        "No weather API or calculator is needed."
    )


def closed_world_semantic_response(user_text: str) -> str | None:
    contract = parse_closed_world_contract(user_text)
    return render_closed_world_contract(contract) if contract is not None else None


__all__ = [
    "ClosedWorldContract",
    "DirectionalBuoyancyContract",
    "DirectionalFallingBodyContract",
    "FictionalBiologicalTreatmentContract",
    "FictionalCausalContract",
    "FictionalRuleKind",
    "FictionalTreatmentKind",
    "PhaseAssessmentKind",
    "PhaseThresholdContract",
    "RpgEffectContract",
    "RpgEffectKind",
    "StipulatedClockOffsetContract",
    "StipulatedUnitInventoryContract",
    "closed_world_semantic_response",
    "parse_closed_world_contract",
    "parse_directional_buoyancy_contract",
    "parse_directional_falling_body_contract",
    "parse_fictional_biological_treatment_contract",
    "parse_fictional_causal_contract",
    "parse_phase_threshold_contract",
    "parse_rpg_effect_contract",
    "parse_stipulated_clock_offset_contract",
    "parse_stipulated_unit_inventory_contract",
    "render_closed_world_contract",
]
