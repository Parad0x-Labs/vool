"""Present declared computed results without granting an author numeric authority."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from core.conductor.capabilities import OperationCapability, OperationEffect
from core.conductor.registry import NodeContext, OperationSpec, computed_dependency_bindings
from core.incomplete_answer import ProviderOutputIncompleteError
from core.presentation.chart_payload import chart_fence, chart_payload
from core.presentation.intent import RenderIntent
from core.presentation.model import Chart, Paragraph, RenderDocument, SeriesPoint, Span, Table
from core.presentation.render_markdown import MarkdownRenderer
from core.response_constraints import parse_clause_response_constraint
from core.runtime_task_outcome import FulfillmentStatus
from core.turn_ir import ClauseKind


def _expand(text: str) -> list[dict[str, Any]]:
    constraint = parse_clause_response_constraint(text)
    if constraint is None or constraint.presentation_format not in {"table", "chart"}:
        return []
    kind = ('donut' if re.search(r'\b(?:pie|donut|doughnut)\b', text, re.I) else
            'line' if re.search(r'\bline\s+chart\b', text, re.I) else
            'bars_h' if re.search(r'\bhorizontal\b', text, re.I) else 'bars_v')
    return [{"entity": "result_presentation", "clause": text,
             "presentation_format": constraint.presentation_format, "chart_kind": kind,
             "explanation_requested": bool(re.search(r"\b(?:explain|explanation|summari[sz]e|summary)\b", text, re.I))}]


_SYSTEM = (
    "result-presentation.v2: explain the supplied computed results briefly. "
    "Do not recalculate or invent observations. Return JSON with an explanation array. "
    "Each item has exactly two keys: text (prose without digits, possibly empty) and ref "
    "(one listed result symbol, or null for prose alone). The runtime prints text then the "
    "referenced value. Use ref for every numerical statement; never put reference names, "
    "XML or placeholders inside text. The runtime already supplies the table or chart; "
    "do not recreate it. Produce only the requested short explanation."
)


def _project(context: NodeContext) -> tuple[list[dict[str, Any]], list[str]]:
    from core.conductor.operations import _readable_expression

    dependencies = dict(context.dependency_results)
    # The ordinary calculator exports one value; general computation exports
    # steps. Normalize the former without changing either source result.
    normalized = {
        node_id: ({**source, "steps": [{"label": source.get("statement") or "Result",
                                         "value": source["value"]}]}
                  if "steps" not in source and "value" in source else source)
        for node_id, source in dependencies.items()
    }
    bindings = computed_dependency_bindings(normalized)
    calculations = {}
    facts = {fact.label: fact.value for fact in getattr(context.shared_context, "facts", ())}
    for dependency, source in enumerate(normalized.values(), start=1):
        steps = source.get("steps")
        if not isinstance(steps, list):
            continue
        labels = {str(step.get("step_id") or f"step_{i}"): str(step.get("label") or "")
                  for i, step in enumerate(steps, 1) if isinstance(step, Mapping)}
        values = {**facts, **dict(source.get("expression_bindings") or {})}
        for position, step in enumerate(steps, start=1):
            if not isinstance(step, Mapping):
                continue
            expression = _readable_expression(str(step.get("expression") or source.get("statement") or ""), values)
            calculations[f"dependency_{dependency}_value_{position}"] = re.sub(
                r"\bstep_\d+\b", lambda match, labels=labels: f"({labels[match[0]]})" if labels.get(match[0]) else match[0], expression)
    rows = []
    problems = []
    for node_id, source in normalized.items():
        problems.extend(str(item) for item in source.get("cannot_determine", []) if item)
        represented = sum(item["node_id"] == node_id for item in bindings.values())
        expected = source.get("steps")
        if not represented or not isinstance(expected, list) or represented != len(expected):
            problems.append("A declared source contains a missing or invalid computed value.")
    for symbol, item in bindings.items():
        rows.append({"ref": symbol, "node_id": item["node_id"], "step_id": item["step_id"],
                     "label": item["label"], "value": item["value"],
                     "unit": item["unit"], "calculation": calculations[symbol]})
    return rows, problems


def _charts(rows: list[dict[str, Any]], kind: str) -> list[Chart]:
    groups = {}
    for row in rows:
        groups.setdefault(row['unit'], []).append(SeriesPoint(row['label'], row['value'], row['unit']))
    charts = [Chart(points, kind=kind, title=unit or 'Computed results') for unit, points in groups.items()]
    if not charts:
        raise ValueError('No computed values are available to chart')
    for chart in charts:
        chart_fence(chart_payload(chart))
    return charts


def _run(node: Any, context: NodeContext) -> dict[str, Any]:
    dependencies = dict(context.dependency_results)
    if tuple(dependencies) != tuple(node.depends_on) or not dependencies:
        raise ValueError("presentation requires its declared result scope")
    rows, problems = _project(context)
    presentation_format = node.arguments.get('presentation_format', 'table')
    chart_kind = node.arguments.get('chart_kind', 'bars_v')
    chart_error = ''
    if presentation_format == 'chart':
        try:
            _charts(rows, chart_kind)
        except (ValueError, TypeError, OverflowError) as exc:
            chart_error = str(exc)
            problems.append('Requested chart unavailable: ' + chart_error)
    bindings = {row["ref"]: row for row in rows}
    explanation = []
    if node.arguments.get("explanation_requested"):
        if context.run_generation is None and context.run_structured_generation is None:
            problems.append("The requested explanation could not be generated.")
        else:
            try:
                schema = {"type": "object", "required": ["explanation"], "additionalProperties": False,
                          "properties": {"explanation": {"type": "array", "minItems": 1,
                              "items": {"type": "object", "required": ["text", "ref"],
                                        "additionalProperties": False, "properties": {
                                            "text": {"type": "string", "pattern": "^[^0-9]*$"},
                                            "ref": {"type": ["string", "null"], "enum": [None, *bindings]}}}}}}
                reply = context.generate_json(_SYSTEM, json.dumps({
                    "request": node.arguments["clause"], "results": rows}, ensure_ascii=True), schema)
                payload = json.loads(reply)
                parts = payload.get("explanation")
                if not isinstance(parts, list) or not parts:
                    raise ValueError("missing explanation")
                for part in parts:
                    if isinstance(part, dict) and set(part) == {"text", "ref"}:
                        text, ref = part["text"], part["ref"]
                        if not isinstance(text, str) or re.search(r"\d", text):
                            raise ValueError("unbound explanation text")
                        if ref is not None and (not isinstance(ref, str) or ref not in bindings):
                            raise ValueError("unbound explanation reference")
                        if not text.strip() and ref is None:
                            raise ValueError("empty explanation segment")
                        if text.strip():
                            explanation.append({"text": text})
                        if ref is not None:
                            explanation.append({"ref": ref})
                    elif isinstance(part, str) and part.strip() and not re.search(r"\d", part):
                        explanation.append({"text": part})
                    elif isinstance(part, dict) and set(part) == {"ref"} and part["ref"] in bindings:
                        explanation.append({"ref": part["ref"]})
                    else:
                        raise ValueError("unbound explanation content")
            except ProviderOutputIncompleteError:
                explanation = []
                problems.append("The provider stopped before completing the requested explanation.")
            except Exception:
                explanation = []
                problems.append("The requested explanation supplied no valid result-bound content.")
    return {"rows": rows, "explanation": explanation, "cannot_determine": problems,
            "presentation_format": presentation_format, "chart_kind": chart_kind, "chart_error": chart_error,
            "source_node_ids": list(dependencies)}


def _render(_node: Any, result: Mapping[str, Any]) -> str:
    from core.conductor.operations import _format_number

    rows = list(result["rows"])
    blocks = []
    renderer = MarkdownRenderer(RenderIntent(surface='chat'))
    if result.get('presentation_format') == 'chart' and not result.get('chart_error'):
        blocks.extend(_charts(rows, result.get('chart_kind', 'bars_v')))
    if rows:
        blocks.append(Table(headers=["Result", "Calculation", "Value"], rows=[
            [row["label"], renderer.render(RenderDocument(blocks=[
                Paragraph(runs=[Span(row["calculation"], style="code")])])),
             (_format_number(row["value"]) + " " + row["unit"]).strip()] for row in rows]))
    by_ref = {row["ref"]: row for row in rows}
    spans = []
    for item in result["explanation"]:
        if "text" in item:
            text = item["text"]
        else:
            row = by_ref[item["ref"]]
            text = (_format_number(row["value"]) + " " + row["unit"]).strip()
        if spans and text and spans[-1].text:
            previous = spans[-1].text[-1]
            if (previous.isalnum() or previous in ".!?)]%") and (text[0].isalnum() or text[0] in "+-("):
                spans.append(Span(" "))
        spans.append(Span(text))
    if spans:
        blocks.append(Paragraph(runs=spans))
    for problem in result["cannot_determine"]:
        blocks.append(Paragraph(runs=[Span("Not determined: " + problem)]))
    return renderer.render(RenderDocument(blocks=blocks))


def _represented_dependencies(node: Any, result: Mapping[str, Any], context: NodeContext) -> dict[str, str]:
    if tuple(context.dependency_results) != tuple(node.depends_on):
        return {}
    rows, problems = _project(context)
    if problems or rows != result.get("rows"):
        return {}
    if result.get("source_node_ids") != list(node.depends_on):
        return {}
    for key, default in (('presentation_format','table'), ('chart_kind','bars_v')):
        if result.get(key, default) != node.arguments.get(key, default):
            return {}
    segments = {}
    for node_id in node.depends_on:
        subset = [row for row in rows if row["node_id"] == node_id]
        if not subset:
            return {}
        table = _render(node, {"rows": subset, "explanation": [], "cannot_determine": []})
        segments[node_id] = "\n".join(table.splitlines()[2:])
    return segments


def _fulfillment(result: Mapping[str, Any]) -> FulfillmentStatus:
    if not result.get("rows"):
        return FulfillmentStatus.FAILED
    return (FulfillmentStatus.PARTIALLY_FULFILLED if result.get("cannot_determine")
            else FulfillmentStatus.FULFILLED)


RESULT_PRESENTATION_OPERATION = OperationSpec(
    name="result_presentation",
    description="present earlier computed results in a table or requested chart and, when requested, explain them; declare all relevant calculation dependencies",
    expand_arguments=_expand, run=_run, render=_render,
    required_result_fields=("rows", "explanation", "source_node_ids", "cannot_determine"),
    needs_generation=True, model_free_arguments=lambda args: not args.get("explanation_requested"),
    is_derived=True, serves_unclaimed_clause=True, assess_fulfillment=_fulfillment,
    represented_dependency_segments=_represented_dependencies,
    capability=OperationCapability(
        effect=OperationEffect.TRANSFORMED_CONTENT, domain="computed result presentation",
        accepted_kinds=frozenset({ClauseKind.TRANSFORM, ClauseKind.UNKNOWN}),
        accepted_dependency_effects=frozenset({OperationEffect.COMPUTED_VALUE})),
)
