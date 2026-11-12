from __future__ import annotations

import json

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.test_results import _results, social_x


def test_corrupt_results_history_refuses_before_a_billed_read(social_x):
    from core.runtime_paths import data_path

    path=data_path('x_results.json');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'accounts': {'1234567890': {'556': []}}}))
    before=path.read_text()
    out=_results(['556'])
    assert not out.ok and out.status=='store_or_metrics_unreadable'
    assert social_x['requests']==[]
    assert path.read_text()==before


def test_project_pause_stops_global_rating_context_in_that_project(vault_home):
    from core import x_feedback

    x_feedback.save('global',9,'A restrained release post.',comment='Keep this clear.')
    assert x_feedback.summary('project:alpha')
    x_feedback.set_paused('project:alpha',True)
    assert x_feedback.summary('project:alpha')==''
    assert x_feedback.summary('global')


def test_pausing_ratings_stops_their_promoted_voice_evidence(vault_home):
    from core import x_feedback, x_voice_profile

    row=x_feedback.save('global',9,'A crisp build checkpoint announcement.')
    assert x_feedback.use_as_voice('global',row['id'])['ok']
    assert 'crisp build' in x_voice_profile.voice_segment_for_turn('Write a post on X in my voice.')
    x_feedback.set_paused('global',True)
    assert 'crisp build' not in x_voice_profile.voice_segment_for_turn('Write a post on X in my voice.')


def test_corrupt_feedback_rows_never_enter_context(vault_home):
    from core import x_feedback
    from core.runtime_paths import active_vool_home

    path=active_vool_home()/x_feedback.STORE_FILENAME;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'scopes': {'global': {'paused':False,'entries':[{'rating':10,'text':'unvalidated'}]}}}))
    assert not x_feedback.inspect('global')['ok']
    assert x_feedback.summary('global')==''
