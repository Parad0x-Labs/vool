"""The served UI always streams: the apps-server provider must accept ingress_context.

full-a11-demo-app pass-001 repair fence. The served door (core.web.api.service dispatch_post,
/api/chat + stream=true) calls its ``stream_agent_with_events_provider`` with
``ingress_context=<captured A0/A2 context>`` (F-01). The DEFAULT provider (core.web.api.runtime.
stream_agent_with_events) accepts it — which is why every in-process suite stayed green — but the
REAL server (apps.vool_api_server._dispatch_post) overrides the provider with a lambda that did
NOT, so every streamed turn through the actual starlette app raised TypeError and surfaced as a
bare "Internal Server Error" while the buffered lane worked. This fence drives the REAL
apps-server dispatch and fails with that exact TypeError on any head that drops the kwarg.
"""

from __future__ import annotations

import tempfile
import unittest.mock as mock


def _stream_body(**overrides):
    body = {
        "messages": [{"role": "user", "content": "demo turn"}],
        "session_id": "served-stream-ingress-001",
        "turn_id": "served-stream-t001",
        "stream": True,
        "stream_task_events": True,
        "mode": "manual",
    }
    body.update(overrides)
    return body


def test_apps_server_streamed_chat_accepts_ingress_context():
    from apps import vool_api_server as srv

    seen: dict = {}

    def fake_stream(user_text, *, session_id, source_context, model,
                    include_runtime_events=False, emit_task_events=False,
                    ingress_context=None, run_agent_provider=None, **_kw):
        seen["ingress_context"] = ingress_context
        seen["emit_task_events"] = emit_task_events
        yield b'{"message":{"content":"ok"}}\n'
        yield b'{"done":true}\n'

    with mock.patch.object(srv, "_stream_agent_with_events", fake_stream):
        response = srv._dispatch_post(
            path="/api/chat",
            body=_stream_body(),
            headers={"Host": "127.0.0.1"},
            runtime=srv._legacy_handler_runtime(),
            model_name=srv.MODEL_NAME,
            workspace_root_provider=lambda: tempfile.mkdtemp(prefix="served-stream-ingress-"),
            client_host="127.0.0.1",
        )

    assert response.status == 200, f"streamed /api/chat did not stream: {response.status} {getattr(response, 'body', b'')[:200]!r}"
    assert response.stream is not None, "streamed request must return a streaming response"
    chunks = list(response.stream)
    assert any(b'"done":true' in c for c in chunks), f"stream truncated: {chunks!r}"
    # The door's F-01 context actually reached the provider — not dropped on the floor.
    assert seen["ingress_context"] is not None, "ingress_context was dropped by the apps provider"
    assert seen["emit_task_events"] is True
