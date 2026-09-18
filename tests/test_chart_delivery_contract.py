"""Typed values must reach a real chart; textual fallback is not chart fulfillment."""
import json
import re

import pytest

from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.presentation.intent import RenderIntent
from core.presentation.model import Chart, RenderDocument, SeriesPoint
from core.presentation.render_markdown import MarkdownRenderer
from core.response_constraints import _presentation_answer_has_format


def payload_from(text):
    match = re.search(r'```chart\n(.*?)\n```', text, re.S)
    assert match, 'missing renderable chart; text is not a chart pass'
    return json.loads(match[1])


@pytest.mark.parametrize('labels,values,unit', [
    (['Silver','Gold'], [750,1250], 'GBP'),
    (['West depot','East depot'], [504,280], 'cartons'),
])
def test_chat_renderer_projects_typed_series_without_changing_values(labels, values, unit):
    chart = Chart([SeriesPoint(l,v,unit) for l,v in zip(labels,values,strict=True)],kind='bars_v')
    text = MarkdownRenderer(RenderIntent(surface='chat')).render(RenderDocument(blocks=[chart]))
    payload = payload_from(text)
    assert payload['type'] == 'bar'
    assert payload['data']['labels'] == labels
    assert payload['data']['datasets'][0]['data'] == values
    assert payload['data']['datasets'][0]['label'] == unit
    assert _presentation_answer_has_format(text, 'chart')


def execute_chart(expressions, shape):
    requests = [f'Calculate {expr}.' for expr in expressions]
    requests.append(f'Present these results as a {shape} chart.')
    plan = build_plan_from_clauses([
        ProposedClause(0, requests[0], 'calculation', ()),
        ProposedClause(1, requests[1], 'calculation', ()),
        ProposedClause(2, requests[2], 'result_presentation', (0,1)),
    ], original_request=' '.join(requests), plan_id='chart-delivery')
    assert plan.nodes[-1].operation == 'result_presentation'
    outcomes = run_conductor_plan(plan, context=NodeContext())
    return plan, outcomes


@pytest.mark.parametrize('expressions,expected,shape', [
    (['750 + 250','200 * 3'], [1000,600], 'bar'),
    (['24 * 7','31 * 9'], [168,279], 'line'),
])
def test_requested_chart_consumes_executed_results_and_keeps_calculation_table(expressions, expected, shape):
    _, outcomes = execute_chart(expressions, shape)
    result = outcomes[-1]
    assert result.fulfilled
    payload = payload_from(result.rendered)
    assert payload['type'] == shape
    assert payload['data']['datasets'][0]['data'] == expected
    assert [row['value'] for row in result.result['rows']] == expected
    assert '| Result | Calculation | Value |' in result.rendered
    assert result.result['source_node_ids'] == list(result.node.depends_on)


@pytest.mark.parametrize('text', [
    '| Item | Value |\n|---|---|\n| A | 12 |\n| B | 19 |',
    '```python\nprint(12 + 19)\n```',
    '```chart\n{"type":"bar","data":{"labels":["A","B"],"datasets":[{"data":[12,19]}]},"plugins":[1,2]}\n```',
    '```chart\n{"type":"bar","data":{"labels":["A","B"],"datasets":[{"data":[12,NaN]}]}}\n```',
])
def test_tables_unrelated_fences_and_invalid_specs_are_not_chart_completion(text):
    assert not _presentation_answer_has_format(text, 'chart')


def test_one_point_chart_is_a_valid_explicit_shape():
    text = '```chart\n{"type":"bar","data":{"labels":["Remaining"],"datasets":[{"data":[23]}]}}\n```'
    assert _presentation_answer_has_format(text,'chart')


def test_plain_markdown_retains_portable_text_rendering():
    chart = Chart([SeriesPoint('North',14,'kg'),SeriesPoint('South',21,'kg')],kind='bars_v')
    text = MarkdownRenderer().render(RenderDocument(blocks=[chart]))
    assert '```chart' not in text
    assert 'North' in text and 'South' in text


def test_nested_and_unclosed_chart_source_is_not_a_delivered_chart():
    valid = '```chart\n{"type":"bar","data":{"labels":["A"],"datasets":[{"data":[23]}]}}\n```'
    assert not _presentation_answer_has_format('```markdown\n' + valid + '\n```', 'chart')
    assert not _presentation_answer_has_format(valid[:-3], 'chart')
    assert _presentation_answer_has_format('```\n```\n' + valid, 'chart')


def test_precision_loss_does_not_get_a_chart_pass_or_drop_the_value():
    chart = Chart([SeriesPoint('Count', 9007199254740993, 'items')],kind='bars_v')
    text = MarkdownRenderer(RenderIntent(surface='chat')).render(RenderDocument(blocks=[chart]))
    assert 'Chart unavailable' in text
    assert '9007199254740993' in text
    assert not _presentation_answer_has_format(text,'chart')


@pytest.mark.parametrize('value', [123456789012345, 0.123456789012345])
def test_portable_chart_table_keeps_full_numeric_value(value):
    chart = Chart([SeriesPoint('Measurement',value,'units')],kind='table')
    text = MarkdownRenderer().render(RenderDocument(blocks=[chart]))
    assert str(value) in text


def test_mixed_unit_fallback_does_not_relabel_every_value_with_the_first_unit():
    chart = Chart([SeriesPoint('Mass',12,'kg'),SeriesPoint('Cost',20,'USD')],kind='bars_v')
    text = MarkdownRenderer(RenderIntent(surface='chat')).render(RenderDocument(blocks=[chart]))
    assert '12 kg' in text and '20 USD' in text
    assert not _presentation_answer_has_format(text,'chart')


def test_requested_pie_without_declared_whole_retains_values_and_is_partial():
    requests = ['Calculate 7 * 9.', 'Calculate 8 * 4.', 'Present these results as a pie chart.']
    plan = build_plan_from_clauses([
        ProposedClause(0, requests[0], 'calculation', ()),
        ProposedClause(1, requests[1], 'calculation', ()),
        ProposedClause(2, requests[2], 'result_presentation', (0,1)),
    ], original_request=' '.join(requests), plan_id='unknown-whole')
    result = run_conductor_plan(plan, context=NodeContext())[-1]
    assert result.partially_fulfilled and not result.fulfilled
    assert [row['value'] for row in result.result['rows']] == [63,32]
    assert 'declared whole' in result.rendered
    assert not _presentation_answer_has_format(result.rendered,'chart')
