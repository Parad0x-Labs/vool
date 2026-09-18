from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin


class _Harness(ProceedIntentSupportMixin):
    pass


def test_resume_request_key_normalizes_case_and_whitespace() -> None:
    agent = _Harness()

    assert agent._resume_request_key("  Keep   GOING \n") == "keep going"


def test_explicit_resume_requests_match_only_known_resume_phrases() -> None:
    agent = _Harness()

    assert agent._looks_like_explicit_resume_request("Resume please")
    assert agent._looks_like_explicit_resume_request("pick up where you left off")
    assert not agent._looks_like_explicit_resume_request("walk me through the options")


def test_proceed_message_matches_exact_and_embedded_proceed_markers() -> None:
    agent = _Harness()

    assert agent._is_proceed_message("carry on!!")
    assert agent._is_proceed_message("please just do it now")
    assert not agent._is_proceed_message("hello there")


def test_proceed_message_matches_research_and_hive_delivery_markers() -> None:
    agent = _Harness()

    assert agent._is_proceed_message("please do research and deliver it to the hive")
    assert agent._is_proceed_message("run research")
    assert not agent._is_proceed_message("show me the research plan first")


def test_resume_request_combines_explicit_resume_and_proceed_markers() -> None:
    agent = _Harness()

    assert agent._looks_like_resume_request("resume please")
    assert agent._looks_like_resume_request("go ahead")
    assert not agent._looks_like_resume_request("talk me through the options")
