"""Frozen owner review: new facts, unchanged expectations on both product heads.

Proof level: actual final-response guard boundary with supplied admitted capsule;
not a provider or complete run_once proof. No network, models, or credentials.
"""
import pytest

from core.agent_runtime.response import _validate_final_chat_output


CASES = [
    dict(id='R01-unrelated-subject-borrow', keep=False,
         question='What is my current heaviest clay haul?',
         reply='Your current heaviest clay haul is 79 kilograms.', value='79 kilograms',
         lines=['- user said: My heaviest clay haul is 34 kilograms. (stated: 2026-09-10)',
                '- user said: My heaviest winch lift is 79 kilograms. (stated: 2026-09-12)']),
    dict(id='R02-explicit-negation', keep=False,
         question='What is my current longest canoe route?',
         reply='Your current longest canoe route is 29 kilometres.', value='29 kilometres',
         lines=['- user said: My longest canoe route is not 29 kilometres; the corrected record is 21 kilometres. (stated: 2026-09-17)']),
    dict(id='R03-same-day-stale-correction', keep=False,
         question='What is my current highest Orchard Tiles score?',
         reply='Your current highest Orchard Tiles score is 47 points.', value='47 points',
         lines=['- user said: My highest Orchard Tiles score is 47 points. (stated: 2026-09-20)',
                '- user said: Correction: my highest Orchard Tiles score is 41 points, not 47 points. (stated: 2026-09-20)']),
    dict(id='R04-body-date-is-not-statement-date', keep=False,
         question='What is my current longest garden hose?',
         reply='Your current longest garden hose is 44 metres.', value='44 metres',
         lines=['- user said: My longest garden hose is 44 metres; I plan its replacement on 2028-03-12. (stated: 2026-09-02)',
                '- user said: My longest garden hose is now 46 metres. (stated: 2026-09-22)']),
    dict(id='R05-supported-record-control', keep=True,
         question='What is my current longest copper cable?',
         reply='Your current longest copper cable is 58 metres.', value='58 metres',
         lines=['- user said: My longest copper cable is 51 metres. (stated: 2026-09-04)',
                '- user said: My longest copper cable is now 58 metres. (stated: 2026-09-25)']),
    dict(id='R06-best-time-is-not-maximum', keep=True,
         question='What is my current best ridge sprint time?',
         reply='Your current best ridge sprint time is 6 minutes.', value='6 minutes',
         lines=['- user said: My best ridge sprint time is 8 minutes. (stated: 2026-09-28)',
                '- user said: My best ridge sprint time is now 6 minutes. (stated: 2026-09-28)']),
    dict(id='R07-corrected-record-remains-supported', keep=True,
         question='What is my current highest Orchard Tiles score?',
         reply='Your current highest Orchard Tiles score is 41 points.', value='41 points',
         lines=['- user said: My highest Orchard Tiles score is 47 points. (stated: 2026-09-20)',
                '- user said: Correction: my highest Orchard Tiles score is 41 points, not 47 points. (stated: 2026-09-20)']),
    dict(id='R08-actual-statement-date-control', keep=True,
         question='What is my current longest garden hose?',
         reply='Your current longest garden hose is 46 metres.', value='46 metres',
         lines=['- user said: My longest garden hose is 44 metres; I plan its replacement on 2028-03-12. (stated: 2026-09-02)',
                '- user said: My longest garden hose is now 46 metres. (stated: 2026-09-22)']),
    dict(id='R09-same-clause-generalization', keep=True,
         question='What temperature is typical in the drying room?',
         reply='The drying room is typically 24 degrees today.', value='24 degrees', lines=[]),
    dict(id='R10-neighbour-hedge-is-not-reading-evidence', keep=False,
         question='How warm is the drying room?',
         reply='The drying room is currently 24 degrees and the workshop is usually quiet.',
         value='24 degrees', lines=[]),
    dict(id='R11-dated-temperature-not-current-evidence', keep=False,
         question='What is the current aquarium temperature?',
         reply='The aquarium is currently 19 degrees.', value='19 degrees',
         lines=['- user said: The aquarium was 19 degrees last month. (stated: 2026-08-18)']),
    dict(id='R12-invented-record-value', keep=False,
         question='What is my current longest copper cable?',
         reply='Your current longest copper cable is 61 metres.', value='61 metres',
         lines=['- user said: My longest copper cable is 58 metres. (stated: 2026-09-25)']),
    dict(id='R13-assistant-attribution-is-not-user-record', keep=False,
         question='What is my current longest copper cable?',
         reply='Your current longest copper cable is 58 metres.', value='58 metres',
         lines=['- assistant said: Your longest copper cable is 58 metres. (stated: 2026-09-25)']),
    dict(id='R14-foreign-chat-evidence', keep=False, foreign=True,
         question='What is my current longest copper cable?',
         reply='Your current longest copper cable is 58 metres.', value='58 metres',
         lines=['- user said: My longest copper cable is 58 metres. (stated: 2026-09-25)']),
]


@pytest.fixture(autouse=True)
def isolated_telemetry():
    from core.context_retrieval import reset_retrieval_telemetry
    reset_retrieval_telemetry()
    yield
    reset_retrieval_telemetry()


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_owner_fresh_guard_contract(case):
    context = {'surface': 'openclaw', 'platform': 'openclaw', 'chat_id': 'owner-review',
               'conversation_history': [{'role': 'user', 'content': case['question']}]}
    if case['lines']:
        capsule = '<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n' + '\n'.join(case['lines']) + '\n</retrieved_context>'
        context['admitted_capsule_evidence'] = {
            'text': capsule,
            'chat_id': 'other-review-chat' if case.get('foreign') else 'owner-review',
            'source': 'canonical_runtime_transcript',
        }
    actual = _validate_final_chat_output(case['reply'], source_context=context)
    if case['keep']:
        assert actual == case['reply'], {'id': case['id'], 'expected': case['reply'], 'actual': actual}
    else:
        assert case['value'] not in actual, {'id': case['id'], 'forbidden': case['value'], 'actual': actual}
