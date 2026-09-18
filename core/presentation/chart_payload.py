"""Bounded chart wire contract shared by typed projection and format admission.

The untrusted browser reader independently validates this same contract before drawing.
This validates representation, not the factual authority of supplied numbers.
"""
from __future__ import annotations

import json
import math
import re

from core.presentation.charts import donut_is_valid
from core.presentation.fences import CHART_FENCE_TAG, fenced_blocks
from core.presentation.model import Chart

MAX_TEXT = 20000
MAX_SAFE_INTEGER = 2**53 - 1


def _length(text: str) -> int:
    return len(text.encode('utf-16-le', errors='surrogatepass')) // 2


def _keys(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError('Unsupported chart fields')


def _label(value):
    if not isinstance(value, str) or _length(value) > 240:
        raise ValueError('Invalid chart label')


def _color(value):
    if not isinstance(value, str) or re.fullmatch(r'#[0-9a-fA-F]{6}', value) is None:
        raise ValueError('Invalid chart color')


def validate_chart_payload(spec: object) -> dict:
    _keys(spec, ('type', 'data', 'title', 'background', 'axis'))
    if spec.get('type') not in ('bar', 'line', 'pie', 'doughnut'):
        raise ValueError('Unsupported chart type')
    if 'axis' in spec and (spec['type'] != 'bar' or spec['axis'] not in ('x', 'y')):
        raise ValueError('Invalid chart axis')
    data = spec.get('data')
    _keys(data, ('labels', 'datasets'))
    labels, datasets = data.get('labels'), data.get('datasets')
    if not isinstance(labels, list) or not 1 <= len(labels) <= 200:
        raise ValueError('Invalid chart labels')
    for label in labels:
        _label(label)
    if not isinstance(datasets, list) or not 1 <= len(datasets) <= 8:
        raise ValueError('Invalid chart datasets')
    for dataset in datasets:
        _keys(dataset, ('label', 'data', 'color'))
        values = dataset.get('data')
        if not isinstance(values, list) or len(values) != len(labels):
            raise ValueError('Invalid chart values')
        for value in values:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Invalid chart values')
            if abs(value) > MAX_SAFE_INTEGER:
                raise ValueError('Chart values exceed safe browser precision')
        if spec['type'] in ('pie', 'doughnut') and (any(v < 0 for v in values) or not any(v > 0 for v in values)):
            raise ValueError('Pie values must include a positive amount and no negatives')
        if 'label' in dataset:
            _label(dataset['label'])
        if 'color' in dataset:
            _color(dataset['color'])
    if 'title' in spec:
        _label(spec['title'])
    if 'background' in spec:
        _color(spec['background'])
    return spec


def chart_fence(spec: dict) -> str:
    body = json.dumps(validate_chart_payload(spec), ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if _length(body) > MAX_TEXT:
        raise ValueError('Visual exceeds rendering limit')
    return '```' + CHART_FENCE_TAG + '\n' + body + '\n```'


def parse_chart_body(body: str) -> dict:
    if _length(body) > MAX_TEXT:
        raise ValueError('Visual exceeds rendering limit')
    def reject_constant(value):
        raise ValueError('Invalid JSON number: ' + value)
    return validate_chart_payload(json.loads(body, parse_constant=reject_constant))


def chart_bodies(text: str) -> list[str]:
    """The bodies of the top-level ```chart blocks, read with the chat page's own fence grammar.

    One reader (core.presentation.fences) for the validator, the repair and the renderers, so a
    block the page would draw and a block the runtime accepts can never be two different things.
    An unterminated chart fence is no chart at all; an unterminated block under another label
    ends the reading exactly as the page's parser would.
    """
    bodies = []
    for block in fenced_blocks(text):
        if not block.terminated:
            return [] if block.tag == CHART_FENCE_TAG else bodies
        if block.tag == CHART_FENCE_TAG:
            bodies.append(block.body)
    return bodies


def chart_payload(chart: Chart) -> dict:
    if not chart.series or len({p.unit for p in chart.series}) != 1:
        raise ValueError('A chart requires values with one common unit')
    if any(p.status is not None for p in chart.series):
        raise ValueError('Per-value status must remain visible in a table')
    types = {'bars_h':'bar', 'bars_v':'bar', 'line':'line', 'donut':'doughnut'}
    if chart.kind not in types:
        raise ValueError('Unsupported chart type')
    if chart.kind == 'donut':
        valid, reason = donut_is_valid(chart)
        if not valid:
            raise ValueError('Pie requires parts of a declared whole: ' + reason)
    spec = {'type':types[chart.kind], 'title':chart.title,
            'data':{'labels':[p.label for p in chart.series],
                    'datasets':[{'label':chart.series[0].unit, 'data':[p.value for p in chart.series]}]}}
    if chart.kind == 'bars_h':
        spec['axis'] = 'y'
    return validate_chart_payload(spec)
