"""A judge call on a machine with no GPU gets its ceiling scaled like every other local-model budget.

CI Intel smoke on bb5351e0e (i7 runner, qwen3:8b on the CPU): the memory question beside a tool pick ran out its 60 s
and the Reminders pick's own yes/no its 15 s, so a recall question kept a wrong Reminders pick and its tool loop ran past
the 900 s smoke step. The ceilings were measured on GPU hosts (core.local_compute_budget).
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("cpu_only", [True, False])
def test_the_judge_ceiling_follows_the_machine(monkeypatch, cpu_only):
    import requests

    from core import intent_arbiter, local_compute_budget

    seen = []

    class Answer:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"answer": "no"}'}}

    monkeypatch.setattr(local_compute_budget, "local_inference_is_cpu_only", lambda: cpu_only)
    monkeypatch.setattr(intent_arbiter, "arbiter_enabled", lambda: True)
    monkeypatch.setattr(requests, "post", lambda *a, **k: seen.append(k.get("timeout")) or Answer())
    intent_arbiter.arbitrate_followup("", "", "", prompt="Is this a question? yes or no", model="qwen3:8b",
                                      patient=True, timeout_s=60, parse=lambda content: "no")
    assert seen, "no judge call went out"
    assert seen[-1][1] == (60 * local_compute_budget.CPU_ONLY_FACTOR if cpu_only else 60)
