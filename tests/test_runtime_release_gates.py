"""Independent original/novel checks for the reported unresolved release gates."""
import uuid

import pytest


@pytest.fixture(autouse=True)
def isolated_offer_state(monkeypatch):
    from core import policy_engine
    from core.tool_offer_state import reset_offer_state
    policy = dict(policy_engine.load())
    policy['email'] = {**(policy.get('email') or {}), 'read_enabled': True, 'send_enabled': True}
    monkeypatch.setattr(policy_engine, '_POLICY_CACHE', policy)
    reset_offer_state()
    yield
    reset_offer_state()


def context():
    session = 'openclaw:review-' + uuid.uuid4().hex
    return dict(surface='openclaw', platform='openclaw', runtime_session_id=session,
                session_id=session, turn_id='turn-' + uuid.uuid4().hex)


def native(text, ctx):
    from core.tool_offer_assembly import assemble_tool_offer
    return set(assemble_tool_offer(user_text=text, task_class='unknown', source_context=ctx).intents)


@pytest.mark.parametrize('family,text', [
    ('email', 'Please continue with that.'),
    ('knowledge', 'Help me with the saved notes.'),
])
def test_prompt_catalog_does_not_take_expansion_away_from_callable_tools(family, text):
    from core.prompt_normalizer import _tool_intent_catalog_text
    from core.tool_offer_state import record_family_expansion
    baseline = native(text, context())
    control = context()
    record_family_expansion(control, family)
    added = native(text, control) - baseline
    assert added, 'Invalid probe: this expansion adds no tools even without a catalog consumer'
    ctx = context()
    record_family_expansion(ctx, family)
    _tool_intent_catalog_text(user_text=text, task_class='unknown', source_context=ctx)
    observed = native(text, ctx)
    assert added <= observed, {'lost_after_catalog': sorted(added-observed)}


@pytest.mark.parametrize('text', [
    'Pull up the most recent email from the kiln repair shop.',
    'Search my mail for the Harbor Paper invoice.',
    'Check my email from the orchard supplier.',
    'Read the latest email from the kiln repair shop.',
])
def test_first_turn_mailbox_request_has_a_callable_read_tool(text):
    offered = native(text, context())
    assert 'email.read' in offered, {'text': text, 'offered': sorted(offered)}
