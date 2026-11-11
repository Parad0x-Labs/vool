"""Private mailbox content never crosses to a cloud model.

Measured 2026-11-06 (operator's live session, build d6453c77): "how come u cant tell me whats
in my email inbox?" — email.read executed locally (8 private messages read), then the SYNTHESIS
crossed to a cloud model carrying the inbox rows — three cloud calls in one turn, senders and
subjects included. The person's mailbox content left the machine because nothing raised a
privacy ceiling when a private-data tool executed.

The law pinned here:
- the tool loop stamps the turn (`private_content_touch`, server-authored, in
  RESERVED_TRUST_KEYS) when an email tool executes successfully;
- the plan mint turns that stamp into the private-local-only ceiling — every model call from
  the stamp onward resolves to LOCAL providers only, exactly like Local Only mode;
- the stamp is a server fact: a caller body carrying it is stripped like every other trust key.
"""

from __future__ import annotations

from core.request_trust import RESERVED_TRUST_KEYS, strip_reserved_trust_keys


def test_the_stamp_is_server_authored_and_reserved() -> None:
    assert "private_content_touch" in RESERVED_TRUST_KEYS
    body_ctx = {"private_content_touch": "email", "surface": "api"}
    cleaned = strip_reserved_trust_keys(body_ctx)
    assert "private_content_touch" not in cleaned, (
        "a request body must never be able to forge the privacy ceiling"
    )


def test_turn_has_private_content_reads_the_stamp() -> None:
    from core.memory_first_router import turn_has_private_content

    assert turn_has_private_content({"private_content_touch": "email"}) is True
    assert turn_has_private_content({"private_content_touch": ""}) is False
    assert turn_has_private_content({}) is False
    assert turn_has_private_content(None) is False


def test_the_loop_stamps_email_executions_and_only_them() -> None:
    """The stamping rule, pinned at the source: email.* steps stamp, others don't."""
    from pathlib import Path

    source = Path("core/agent_runtime/research_tool_loop_facade.py").read_text()
    assert 'tool_name") or "").startswith("email.")' in source, (
        "the stamp keys on email.* tool executions"
    )
    assert '"private_content_touch": "email"' in source
    # Non-email tools must not stamp: the stamp block sits beside the support recording and
    # checks the tool name prefix explicitly.
    assert source.count('"private_content_touch": "email"') == 1


def test_the_plan_mint_honors_the_ceiling() -> None:
    """The mint's local_only carries the privacy ceiling alongside Local Only mode."""
    from pathlib import Path

    source = Path("core/memory_first_router.py").read_text()
    assert "privacy_ceiling_local = turn_has_private_content(source_context)" in source
    assert "local_only=turn_is_local_only(source_context) or privacy_ceiling_local" in source


def test_the_ceiling_law_is_stated_where_it_bites() -> None:
    """The mint site carries the measured incident: inbox rows crossed to a cloud model."""
    from pathlib import Path

    source = Path("core/memory_first_router.py").read_text()
    assert "SYNTHESIS crossed to a cloud model carrying the inbox rows" in source
