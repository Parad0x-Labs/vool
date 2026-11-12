"""The Settings key checks are real X reads, so they count against the owner's monthly X read limit.

Owner decision (19 Oct): checks that X may have served are counted; a definite refusal is not; a
used-up limit stops the check before anything is sent."""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_guided_setup import BEARER, KEYS, _post, fake_x


def _reads():
    from core import x_usage
    return x_usage.snapshot()['reads']


def test_a_successful_key_check_counts_one_read(fake_x):
    before = _reads()
    status, payload = _post({'action': 'save_bearer', 'bearer_token': BEARER})
    assert status == 200 and payload['ok'], payload
    assert _reads() == before + 1
    status, payload = _post({'action': 'keys_check', 'credentials': KEYS})
    assert status == 200 and payload['ok'], payload
    assert _reads() == before + 2


@pytest.mark.parametrize('answer,counted', [
    ((401, {'title': 'Unauthorized'}), False),
    ((429, {'title': 'Too Many Requests'}), False),
    ((500, {'title': 'Internal'}), True),
    ((200, b'not json'), True),
    ((200, {'errors': [{'title': 'No profile'}]}), True),
])
def test_only_a_definite_refusal_leaves_the_read_uncounted(fake_x, answer, counted):
    fake_x.script.append(answer)
    before = _reads()
    status, payload = _post({'action': 'save_bearer', 'bearer_token': BEARER})
    assert not payload.get('ok'), payload
    assert len(fake_x.requests) == 1
    assert _reads() == before + int(counted)


def test_a_used_up_read_limit_stops_the_check_before_sending(fake_x):
    from core import x_usage
    x_usage.set_limits(monthly_reads=1)
    assert _post({'action': 'save_bearer', 'bearer_token': BEARER})[1]['ok']
    status, payload = _post({'action': 'keys_check', 'credentials': KEYS})
    assert not payload.get('ok') and payload.get('state') == 'usage_limit_reached', payload
    assert [r['path'] for r in fake_x.requests] == ['/2/users/by/username/XDevelopers']
    assert _reads() == 1
