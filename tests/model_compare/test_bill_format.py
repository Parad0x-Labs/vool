from __future__ import annotations

import json
import subprocess


def test_actual_bill_formatter_preserves_currency_and_refuses_unknown_values():
    """Formatting only: this does not stand in for a paid receipt or a ledger."""
    from core.disclosure_fragment import render_disclosure_fragment

    fragment = render_disclosure_fragment()
    source = fragment[fragment.index('  function formatBill('):fragment.index('  function answerColumn(')]
    inputs = [
        {'billed_amount': .0021, 'billed_currency': 'USD'},
        {'billed_amount': .0021, 'billed_currency': 'USDC'},
        {'billed_amount': 0, 'billed_currency': 'USDC'},
        {'billed_usd': 0},
        {'billed_usd': .001},
        {'billed_amount': None, 'billed_currency': 'USDC', 'billed_usd': .5},
        {'billed_amount': .0021, 'billed_currency': 'unrecognized'},
        {'billed_amount': True, 'billed_currency': 'USD'},
        {'billed_amount': -.1, 'billed_currency': 'USD'},
        {'billed_usd': None}, {},
    ]
    script = source+'''
const values=JSON.parse(require('fs').readFileSync(0,'utf8'));
values.push({billed_amount:Infinity,billed_currency:'USDC'});
process.stdout.write(JSON.stringify(values.map(value=>formatBill(value,'Unknown'))));
'''
    result = subprocess.run(['node', '-e', script], input=json.dumps(inputs), capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['0.0021 USD', '0.0021 USDC', '0 USDC', '0 USD', '0.001 USD',
                                        *['Unknown']*7]
