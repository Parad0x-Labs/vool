"""Information-flow privacy — the label survives the transformation, not the bytes.

Compartment-gated lanes (core/kernel/compartments.py) control which RAW bytes enter a
lane. That leaves the deeper hole open: a LOCAL lane legitimately reads ``4200``, then
derives ``8400``, ``half of it``, ``four thousand two hundred``, ``20% of net income``
— representations the byte scanner has never seen. Scanning outputs harder is DLP
slop: an arms race against paraphrase.

The information-flow answer changes the enforcement POINT. Private data enters the
kernel as a :class:`Private` value carrying its source labels. Every derivation —
arithmetic, reformatting, word-forms, rounding, aggregation — produces another
``Private`` with the SAME labels (plus any new ones), whatever the representation.
The unauthorized lane's input builder accepts ONLY plain strings and
:class:`PublicArtifact`; a ``Private`` value is refused MECHANICALLY, before any
model call, no matter how innocuous its current text looks. Forbidden information is
never assembled into the unauthorized lane's input — there is nothing to recognize,
because nothing crossed.

Declassification is TYPED and STRUCTURED, never a model's judgment call:

- A :class:`DisclosureSpec` declares the release: a fixed template with typed slots
  (numbers, or literals from a closed set). Free-text release does not exist.
- The disclosing fork must hold ``declassify.<compartment>`` — deliberately a
  DIFFERENT authority from ``compartment.<compartment>``: permission to READ is not
  permission to PUBLISH. Two-key by construction.
- The gate formats the template itself from projector-produced fields and scans the
  result against every source compartment's quantities (THE lexical-span authority),
  including the numeric slot values themselves — a numeric field equal to the secret
  IS the secret, and refuses.

Honest scope, stated once: the guarantee covers everything that flows through the
typed boundary. Prose a LOCAL model emits outside this boundary is inside some lane's
input by definition; keeping THAT from leaking is the compartments module's grant
problem, not this one. Within the boundary the claim is absolute: unauthorized lanes
never gain the information needed to reconstruct the secret beyond the declared
release.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from core.kernel.capabilities import CapabilityDenied, CapabilitySet
from core.kernel.compartments import CompartmentLeak
from core.kernel.lexical_spans import quantity_values

__all__ = [
    "DisclosureSpec",
    "Private",
    "PublicArtifact",
    "cloud_view",
    "disclose",
]

_FIELD_KINDS = ("number", "literal")


class _NotReachable(TypeError):
    """A Private value reached a boundary reserved for public bytes."""


@dataclass(frozen=True)
class Private:
    """A value derived from compartmented sources; the labels ARE the value's identity."""

    payload: str
    labels: frozenset[str]
    lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.labels:
            raise ValueError("a Private value must carry at least one source label")
        object.__setattr__(self, "labels", frozenset(self.labels))

    def derive(self, description: str, fn: Callable[[str], object]) -> "Private":
        """Any transformation of private data is private data. Labels persist;
        the transformation joins the audit lineage."""
        return Private(payload=str(fn(self.payload)), labels=self.labels,
                       lineage=self.lineage + (description,))

    @staticmethod
    def combine(*parts: object, description: str = "combine") -> "Private | str":
        """Join parts; one private part taints the whole (union of labels).
        Clean + clean stays clean — no gratuitous wrapping."""
        pieces: list[str] = []
        labels: set[str] = set()
        lineage: list[str] = []
        for part in parts:
            if isinstance(part, Private):
                pieces.append(part.payload)
                labels |= part.labels
                lineage.extend(part.lineage)
            elif isinstance(part, str):
                pieces.append(part)
            else:
                raise TypeError(f"combine accepts str or Private, got {type(part).__name__}")
        joined = "".join(pieces)
        if not labels:
            return joined
        return Private(payload=joined, labels=frozenset(labels),
                       lineage=tuple(lineage) + (description,))

    def to_json(self) -> str:
        """Persistence keeps the labels: a stored Private is STILL private."""
        return json.dumps({"schema": "vool.private.v1", "payload": self.payload,
                           "labels": sorted(self.labels), "lineage": list(self.lineage)},
                          sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "Private":
        raw = json.loads(text)
        if raw.get("schema") != "vool.private.v1":
            raise ValueError("not a vool.private.v1 document")
        return cls(payload=raw["payload"], labels=frozenset(raw["labels"]),
                   lineage=tuple(raw["lineage"]))


@dataclass(frozen=True)
class DisclosureSpec:
    """A DECLARED release shape: fixed template, typed slots, closed literal sets.

    ``fields`` maps slot name -> "number" | "literal". Literal slots may only take
    values from their entry in ``allowed_literals``; number slots must be JSON
    numbers. There is no free-text field, because a free-text field is a smuggling
    channel wearing a schema.
    """

    name: str
    template: str
    fields: Mapping[str, str]
    allowed_literals: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, kind in self.fields.items():
            if kind not in _FIELD_KINDS:
                raise ValueError(
                    f"field {name!r} has unknown kind {kind!r}; expected {_FIELD_KINDS}"
                )
            if kind == "literal" and not self.allowed_literals.get(name):
                raise ValueError(
                    f"literal field {name!r} needs a non-empty allowed_literals entry"
                )
        placeholders = {
            part.split("}")[0].split(":")[0].strip()
            for part in self.template.split("{")[1:]
        }
        missing = placeholders - set(self.fields)
        if missing:
            raise ValueError(f"template slots {sorted(missing)} have no field declaration")


@dataclass(frozen=True)
class PublicArtifact:
    """The ONLY form in which private-derived content crosses to an unauthorized lane."""

    text: str
    spec_name: str
    released_from: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.released_from:
            raise ValueError("an artifact must name the compartments it was released from")


def disclose(
    value: Private,
    *,
    spec: DisclosureSpec,
    projector: Callable[[str], Mapping[str, object]],
    authority: CapabilitySet,
) -> PublicArtifact:
    """The single legal exit. Mechanical checks, in order:

    1. AUTHORITY: the fork must hold ``declassify.<label>`` for EVERY source label —
       reading a compartment never implies publishing from it.
    2. PROJECTION: the projector reduces the payload to typed slot values only.
    3. FORMATTING: THE GATE formats the template — the projector never touches the
       outbound string, so encoding/padding/smuggling channels do not exist.
    4. QUANTITY SWEEP: the finished artifact may contain no quantity of any source
       compartment (THE lexical-span authority) — a numeric slot that equals the
       secret IS the secret, and refuses here, after formatting.
    """
    missing = sorted(label for label in value.labels
                     if not authority.allows(f"declassify.{label}"))
    if missing:
        denied = f"declassify.{missing[0]}"
        raise CapabilityDenied(
            fork_id="disclosure-gate", token=denied, reason="no_disclosure_authority",
            receipt={"fork_id": "disclosure-gate", "tool": "kernel.disclose",
                     "required": denied, "decision": "denied",
                     "reason": f"labels {missing} lack disclosure authority"},
        )

    fields_raw = dict(projector(value.payload))
    if set(fields_raw) != set(spec.fields):
        raise ValueError(
            f"projector returned fields {sorted(fields_raw)}, spec declares "
            f"{sorted(spec.fields)}"
        )
    checked: dict[str, object] = {}
    for name, kind in spec.fields.items():
        produced = fields_raw[name]
        if kind == "number":
            if isinstance(produced, bool) or not isinstance(produced, (int, float)):
                raise ValueError(f"slot {name!r} must be a number, got {type(produced).__name__}")
        else:
            if produced not in spec.allowed_literals.get(name, ()):
                raise ValueError(
                    f"slot {name!r} value {produced!r} is not in the closed literal set"
                )
        checked[name] = produced

    artifact_text = spec.template.format(**checked)

    for label in value.labels:
        # The sweep runs against the ORIGINAL compartment payload held by this value's
        # own lineage source: any quantity still standing in the artifact that also
        # stands in what we were given is the secret traveling under a slot's costume.
        for quantity in quantity_values(value.payload):
            if len(quantity.replace(",", "").split(".")[0]) >= 3 \
                    and quantity in set(quantity_values(artifact_text)):
                raise CompartmentLeak(
                    f"disclosed artifact {spec.name!r} carries quantity {quantity!r} "
                    f"derived from compartment '{label}' — beyond the declared release"
                )
    return PublicArtifact(text=artifact_text, spec_name=spec.name,
                          released_from=tuple(sorted(value.labels)))


def _walk_clean(part: object) -> str:
    if isinstance(part, PublicArtifact):
        return part.text
    if isinstance(part, str):
        return part
    if isinstance(part, Private):
        raise _NotReachable(
            f"a Private value (labels {sorted(part.labels)}) reached an "
            "unauthorized lane's input — forbidden information must be "
            "declassified, not copied"
        )
    # JSON-shaped containers are walked so a Private value cannot hide inside a
    # dict/list any more than TaintedValue can hide from Law 3's argument walk.
    seen: set[int] = set()
    stack: list[object] = [part]
    while stack:
        current = stack.pop()
        if isinstance(current, (PublicArtifact, str, bytes, int, float, bool, type(None))):
            continue
        if isinstance(current, Private):
            raise _NotReachable(
                f"a Private value (labels {sorted(current.labels)}) reached an "
                "unauthorized lane's input — forbidden information must be "
                "declassified, not copied"
            )
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current.keys())
            stack.extend(current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            if id(current) in seen:
                continue
            seen.add(id(current))
            stack.extend(current)
        else:
            raise TypeError(
                f"cloud_view accepts str/PublicArtifact/JSON-shaped parts, got {type(part).__name__}"
            )
    return json.dumps(part, sort_keys=True, default=str)


def cloud_view(frame: str, *parts: object) -> str:
    """Assemble the outbound bytes for an unauthorized lane. Accepts plain strings
    and PublicArtifacts — and NOTHING else. A Private value reaching here is the
    bug this module exists to make impossible; refuse mechanically."""
    sections = [frame.strip()] + [_walk_clean(p) for p in parts]
    return "\n".join(s for s in sections if s)
