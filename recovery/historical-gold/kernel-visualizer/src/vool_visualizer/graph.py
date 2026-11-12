"""Layered-DAG layout for a turn.

Pure geometric/mapping helper: turns each turn into (nodes, edges, layer_by_id)
with nodes assigned to horizontal layers. No state, no side effects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from vool_visualizer.models import Effect, Obligation, Turn


def _preview(value: object, limit: int = 300) -> str:
    if isinstance(value, str):
        s = value
    elif isinstance(value, (list, dict)):
        s = json.dumps(value, ensure_ascii=False)
    else:
        s = str(value)
    return (s[:limit] + "…") if len(s) > limit else s


# Effect-id prefix -> layer label (the vertical pipeline the UI draws).
def effect_layer(effect_id: str) -> str:
    if ".extract" in effect_id:
        return "extract"
    if effect_id.startswith("model.derive"):
        return "derive"
    if effect_id.startswith("model.synthesize"):
        return "synthesize"
    if effect_id.startswith("model.verify"):
        return "verify"
    if effect_id == "kernel.today":
        return "context"
    if effect_id.startswith("web."):
        return "web"
    if effect_id.startswith("kernel.arith") or effect_id.startswith("machine."):
        return "compute"
    if effect_id == "kernel.fastpath":
        return "verify"
    if effect_id.startswith("model."):
        return "model"
    if effect_id == "session.recall":
        return "recall"
    if effect_id.endswith(".compose") or effect_id.endswith(".repair"):
        return "repair"
    return "other"


LAYERS = [
    "context", "extract", "derive", "synthesize", "web", "compute",
    "verify", "repair", "recall", "render",
]


@dataclass
class Node:
    id: str
    label: str
    layer: str
    kind: str            # 'effect' | 'obligation' | 'commit' | 'stamp' | 'answer'
    ok: bool = True
    detail: str = ""
    step: int = 0
    effect_id: str = ""


@dataclass
class Edge:
    src: str
    dst: str


@dataclass
class Graph:
    nodes: list[Node]
    edges: list[Edge]
    layer_index: dict[str, int]


def build_graph(t: Turn) -> Graph:
    nodes: list[Node] = []
    edges: list[Edge] = []
    # per-obligation mapping for arrows effect->obligation
    ob_candidates = [o.id for o in t.terminal_state] or ["ob1"]

    # stamps node (capability gates)
    nodes.append(Node(id="stamps", label=f"stamps {t.stamps}", layer="context",
                      kind="stamp", detail=str(t.stamps)))
    nodes.append(Node(id="question", label="user input", layer="context",
                      kind="answer", detail=t.question[:400]))
    edges.append(Edge("question", "stamp"))

    # effect nodes in tape order, edges chain forward in-layer and cross to
    # obligation/terminal on effect close.
    prev_effect_id: str | None = None
    for e in t.effects:
        nid = f"e{e.step}"
        layer = effect_layer(e.effect_id)
        label = e.effect_id
        detail = str(e.error) if e.error else _preview(e.result)
        nodes.append(Node(nid, label, layer, kind="effect", ok=e.ok, detail=detail))
        if prev_effect_id:
            edges.append(Edge(prev_effect_id, nid))
        prev_effect_id = nid
        # negative effect -> stamp/obligation dependency
        if e.error:
            edges.append(Edge("stamp", nid))

    # obligation terminal nodes
    for o in t.terminal_state:
        nid = f"ob:{o.id}"
        nodes.append(Node(nid, f"{o.id} · {o.lane} · {o.label}", layer="commit",
                          kind="obligation", detail=o.status))
        edges.append(Edge("stamp", nid))

    # answer node
    if t.answer_present and t.answer:
        nodes.append(Node("answer", f"answer #{t.turn_index}", kind="answer",
                          layer="commit", detail=t.answer[:500]))

    layer_index = {L: i for i, L in enumerate(LAYERS)}
    return Graph(nodes, edges, layer_index)
