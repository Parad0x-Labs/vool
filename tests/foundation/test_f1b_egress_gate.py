"""F1-B provenance/exposure seam — canonical egress gate tests."""
from __future__ import annotations

import pytest

from core.egress_gate import (
    EgressRefused,
    EgressSegment,
    project_for_destination,
    project_messages_for_destination,
)


def _seg(**kw):
    base = dict(
        segment_id="s1",
        authority_class="memory",
        principal_scope="owner_local",
        exposure_class="LOCAL_ONLY",
    )
    base.update(kw)
    return EgressSegment(**base)


def test_local_only_dropped_not_redacted_at_cloud_destination():
    payload = {"role": "user", "content": "private memory"}
    out = project_messages_for_destination(
        [
            {"role": "user", "content": "public question"},
            {**payload, "_egress_segment": _seg()},
        ],
        destination_class="cloud_provider",
    )
    assert out == [{"role": "user", "content": "public question"}]


def test_unclassified_segment_stamp_refused_fail_closed():
    # RED MUTATION: a partial stamp must never default to public.
    with pytest.raises(EgressRefused):
        project_messages_for_destination(
            [{"role": "user", "content": "x", "_egress_segment": {"segment_id": "s"}}],
            destination_class="cloud_provider",
        )
    with pytest.raises(EgressRefused):
        project_messages_for_destination(
            [
                {
                    "role": "user",
                    "content": "x",
                    "_egress_segment": _seg(exposure_class="WHATEVER"),
                }
            ],
            destination_class="cloud_provider",
        )


def test_forged_segment_walkthrough_fails_unknown_destination_or_class():
    with pytest.raises(EgressRefused):
        project_for_destination([("x", None)], destination_class="cloud_provider")
    with pytest.raises(EgressRefused):
        project_for_destination([("x", _seg(authority_class="owner"))], destination_class="local_process")


def test_local_destination_keeps_everything_and_public_passes_cloud():
    msgs = [{"role": "assistant", "content": "hi", "_egress_segment": _seg()}]
    assert len(project_messages_for_destination(msgs, destination_class="local_process")) == 1
    public = [
        {
            "role": "user",
            "content": "q",
            "_egress_segment": _seg(
                authority_class="user", exposure_class="PUBLIC"
            ),
        }
    ]
    assert len(project_messages_for_destination(public, destination_class="cloud_provider")) == 1


def test_plain_first_party_messages_need_no_stamp():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
    assert project_messages_for_destination(msgs, destination_class="cloud_provider") == msgs
