"""R-8 / K-11 residue: every surface serves the sealed object only."""
from __future__ import annotations

import json

import pytest

import storage.db as sdb
from tests.asgi_harness import asgi_request


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r8.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


# --- channel gateway: no raw path --------------------------------------------

def test_channel_gateway_commitless_turn_serves_sealed_bytes(fresh_store):
    """RED target (H-6.1): the else-branch used to serve raw render_channel_response
    bytes with strip/collapse mutation; now it mints through finalize_answer."""
    from core.channel_gateway import ChannelRequest, process_channel_request

    class _Agent:
        def run_once(self, text, *, session_id_override=None, source_context=None):
            # Deliberately commitless (fast/deterministic lane shape).
            return {
                "task_id": "task-r8",
                "response": "Sealed   answer for the channel.",
                "mode": "advice_only",
                "confidence": 0.9,
                "prompt_assembly_report": {},
            }

    request = ChannelRequest(
        platform="telegram",
        user_id="u1",
        text="hello",
        channel_id="c1",
    )
    out = process_channel_request(_Agent(), request)
    # The served text is the SEALED canonical content — byte-identical to what
    # finalize_answer committed (the old raw path collapsed whitespace).
    assert out.response_text == "Sealed   answer for the channel."


# --- /api/generate serves committed bytes ------------------------------------

def test_generate_endpoint_serves_commit_bytes(fresh_store, monkeypatch):
    import functools

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission
    from tests.asgi_harness import asgi_request

    RAW = "generate canary answer"

    def stub(runtime, prompt, **kw):
        reset_admission()
        return admit_semantic_result({"response": RAW, "route_reason": "model_lane"})

    runtime = RuntimeServices(display_name="VOOL")
    app = create_app(runtime)
    app.state.post_dispatcher = functools.partial(
        dispatch_post, run_agent_provider=stub
    )
    status, _h, body = asgi_request(
        app,
        method="POST",
        path="/api/generate",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"prompt": "greet"}).encode(),
    )
    assert status == 200, body[:300]
    payload = json.loads(body)
    assert payload["response"] == RAW
    commit = payload.get("vool_response_commit") or {}
    assert str(commit.get("canonical_content") or "") == RAW


# --- swarm standing guard -----------------------------------------------------

def test_swarm_user_summary_never_serves_full_store_bytes(fresh_store):
    """Standing guard (H-6.4): swarm summaries expose only truncated
    derived_display previews — never full final_response_store bytes."""
    from core.vool_user_summary import _trim

    long_text = "X" * 5000
    trimmed = _trim(long_text, 160)
    assert len(trimmed) <= 200
    assert trimmed != long_text
