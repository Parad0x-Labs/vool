"""One operator budget supports two real daemon requests against a synthetic provider."""

from tests.usepod.test_usepod_money_law_served import LANE_ID, MODEL, _answer_text, _session
from tests.usepod.test_usepod_money_law_served import served as served


def test_one_budget_two_chat_requests_and_persistent_price_queue(served):
    daemon = served.daemon
    status, proposal = daemon.call(
        "POST",
        "/api/cloud/usepod/spend-approval/propose",
        {"per_call_atomic": 500000, "max_total_atomic": 3000000, "budget_mode": "daily"},
    )
    assert status == 200, proposal
    aid = proposal["approval_id"]
    status, _ = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": aid})
    assert status == 403
    status, approved = daemon.call(
        "POST",
        "/api/mode",
        {"op": "resolve_approval", "approval_id": aid, "decision": "allow", "session_id": "openclaw:settings-operator"},
    )
    assert status == 200, approved
    status, grant = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": aid})
    assert status == 200, grant
    for index, text in enumerate(
        ("Write a warm birthday wish for a colleague.", "Write a short thank-you for helping with a move.")
    ):
        status, answer = daemon.call(
            "POST",
            "/api/chat",
            {
                "model": LANE_ID,
                "messages": [{"role": "user", "content": text}],
                "session_id": _session("provider-budget-" + str(index)),
                "stream": False,
            },
        )
        assert status == 200 and "synthetic reply" in _answer_text(answer), answer
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery?q=" + MODEL)
    assert status == 200 and view["spend_readiness"]["available"], view
    assert view["spend_approval"]["grant"]["grant_id"] == grant["grant_id"]
    status, rule = daemon.call(
        "POST",
        "/api/cloud/usepod/price-wait/save",
        {"model_id": MODEL, "max_input_usdc": "0.000001", "max_output_usdc": "0.000001", "poll_minutes": 1},
    )
    assert status == 200, rule
    session = _session("provider-budget-wait")
    status, item = daemon.call(
        "POST",
        "/api/chat/queue",
        {"op": "enqueue", "session_id": session, "text": "Write a poem about rain", "price_wait_model": MODEL},
    )
    assert status == 200, item
    status, claimed = daemon.call("POST", "/api/chat/queue", {"op": "claim", "session_id": session})
    assert status == 200 and claimed["item"] is None and claimed["waiting"]["state"] == "waiting_for_price", claimed
    status, pending = daemon.call("POST", "/api/cloud/usepod/price-wait/pending", {})
    assert status == 200 and session in pending["sessions"], pending
    status, cancelled = daemon.call(
        "POST",
        "/api/chat/queue",
        {"op": "cancel", "session_id": session, "queue_item_id": item["item"]["queue_item_id"]},
    )
    assert status == 200 and cancelled["cancelled"]
    # A new eligible task crosses the same durable claim and real chat doors.
    status, rule = daemon.call(
        "POST",
        "/api/cloud/usepod/price-wait/save",
        {"model_id": MODEL, "max_input_usdc": "0.6", "max_output_usdc": "1.8", "poll_minutes": 1},
    )
    assert status == 200, rule
    text = "Write a cheerful two-line poem about sunshine."
    status, item = daemon.call(
        "POST", "/api/chat/queue", {"op": "enqueue", "session_id": session, "text": text, "price_wait_model": MODEL}
    )
    assert status == 200, item
    status, claimed = daemon.call("POST", "/api/chat/queue", {"op": "claim", "session_id": session})
    assert status == 200 and claimed["item"]["queue_item_id"] == item["item"]["queue_item_id"], claimed
    status, answer = daemon.call(
        "POST",
        "/api/chat",
        {
            "model": LANE_ID,
            "model_selection": "pin",
            "messages": [{"role": "user", "content": text}],
            "session_id": session,
            "queue_item_id": item["item"]["queue_item_id"],
            "stream": False,
        },
    )
    assert status == 200 and "synthetic reply" in _answer_text(answer), answer
    status, completed = daemon.call(
        "POST",
        "/api/chat/queue",
        {
            "op": "complete",
            "session_id": session,
            "queue_item_id": item["item"]["queue_item_id"],
            "status": "completed",
        },
    )
    assert status == 200, completed
    status, _ = daemon.call("POST", "/api/money/grants/revoke", {"grant_id": grant["grant_id"]})
    assert status == 200
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery?q=" + MODEL)
    assert not view["spend_readiness"]["available"]
