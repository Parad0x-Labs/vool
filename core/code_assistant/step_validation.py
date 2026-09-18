"""Validate inner proposals before permission prompts or idempotency journal allocation."""
from __future__ import annotations

from typing import Any


def step_argument_error(arguments: dict[str, Any]) -> str:
    from core.runtime_tool_contracts import runtime_tool_contract_map
    from core.tool_argument_aliases import bind_known_argument_aliases

    intent = str(arguments.get('intent') or '').strip()
    raw = arguments.get('arguments', {})
    if not isinstance(raw, dict):
        return 'code.task.step arguments must be an object containing the inner tool arguments.'
    if intent.startswith('code.task.'):
        return 'A code task step cannot nest another code task call; select the inner workspace or sandbox tool.'
    contract = runtime_tool_contract_map().get(intent)
    if contract is None:
        return ''  # The task admission authority owns unknown-tool refusals.
    inner = bind_known_argument_aliases(raw, input_schema=contract.input_schema)
    problems = []
    unknown = sorted(str(key) for key in inner if key not in contract.input_schema)
    if unknown:
        problems.append('unsupported arguments: ' + ', '.join(unknown))
    declared = contract.json_schema or {}
    required = declared.get('required') if declared else None
    for key, spec in contract.input_schema.items():
        mandatory = key in required if required is not None else 'optional' not in spec.lower()
        if key not in inner or inner[key] is None:
            if mandatory:
                problems.append(f'missing required argument: {key}')
            continue
        value = inner[key]
        kind = spec.split()[0].lower()
        expected = {'string': str, 'integer': int, 'number': (int, float), 'boolean': bool, 'object': dict}.get(kind)
        if expected is not None and (not isinstance(value, expected) or (kind in {'integer', 'number'} and isinstance(value, bool))):
            problems.append(f'{key} must be {kind}')
        elif key == contract.claim.target_argument and isinstance(value, str) and not value.strip():
            problems.append(f'{key} must name a non-empty target')
    if not problems:
        return ''
    signature = ', '.join(f'{key}: {spec}' for key, spec in contract.input_schema.items())
    return f'`{intent}`: {"; ".join(problems)}. Supply these inside code.task.step.arguments: {signature}.'
