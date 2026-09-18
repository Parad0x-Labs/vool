"""Repeated greetings stay local; a greeting prefix cannot swallow a task."""
from unittest.mock import patch
import pytest
from core.agent_runtime.fast_paths_utility import smalltalk_fast_path

@pytest.mark.parametrize("text", ["gm gm", "hello, hello!", "good morning good morning"])
def test_repeated_whole_greeting_uses_builtin_reply(text):
    with patch("core.agent_runtime.fast_paths_utility.build_greeting_reply", return_value="Hello."):
        assert smalltalk_fast_path(None, text, source_surface="api", session_id="repeat") == "Hello."

@pytest.mark.parametrize("text", ["gm gm create a contact", "hello, hello! review this code", "good morning schedule a meeting"])
def test_substantive_remainder_is_not_a_greeting(text):
    assert smalltalk_fast_path(None, text, source_surface="api", session_id="repeat") is None
