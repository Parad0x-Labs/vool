"""VOOL's keep, recall and forget judgements, and each tool pick's own yes/no, are asked of a model they were proven on.

Memory wiring audit of efb8e474c (B1): every memory guard was tuned and proven on qwen3:8b only. The judgement followed
the model that answers the turn (0c3d4404) and Auto answered with the daily lane's pick (4424d7e8), so on the gate iMac
the guards ran on qwen3.5:9b and qwen2.5:7b, which no memory probe ever ran on, and told facts were dropped (gates 52
and 53). A proven model judges when it is installed and loaded or fits beside the turn's own; otherwise the turn's own
model does, as before, so nothing is pushed into swap."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import local_ollama_inventory, memory_first_router, memory_judge_record, system_resources
from core.local_ollama_inventory import InstalledOllamaModel

_GB = 1_000_000_000


def _machine(monkeypatch, *, installed: dict[str, float], resident: tuple[str, ...] = (), free: float | None = 16.0,
             ram: float = 0.0) -> None:
    monkeypatch.setattr(local_ollama_inventory, "installed_ollama_model_inventory", lambda **_k: tuple(
        InstalledOllamaModel(name=name, size_bytes=int(size * _GB)) for name, size in installed.items()))
    monkeypatch.setattr(memory_first_router, "_recent_resident_model_tags", lambda: resident)
    monkeypatch.setattr(memory_first_router, "_local_model_free_memory_gb", lambda: free)
    monkeypatch.setattr(system_resources, "snapshot", lambda: SimpleNamespace(available_ram_gb=ram))


def test_a_proven_turn_model_judges_its_own_turn(monkeypatch):
    _machine(monkeypatch, installed={"qwen3:8b": 5.2, "qwen3.5:9b": 6.6})
    assert memory_judge_record.judge_model_for("qwen3:8b") == "qwen3:8b"


def test_a_loaded_proven_model_judges_a_turn_another_model_answers(monkeypatch):
    _machine(monkeypatch, installed={"qwen3:8b": 5.2, "qwen3.5:9b": 6.6}, resident=("qwen3.5:9b", "qwen3:8b"), free=1.0)
    assert memory_judge_record.judge_model_for("qwen3.5:9b") == "qwen3:8b"


@pytest.mark.parametrize(("resident", "free", "expected"), [
    (("qwen2.5:7b",), 8.0, "qwen3:8b"),     # the turn's model is loaded: the judge needs 5.2 + 2 GB
    (("qwen2.5:7b",), 6.0, "qwen2.5:7b"),   # not room for the judge: the turn's own model judges
    ((), 12.5, "qwen3:8b"),                  # neither loaded: judge and turn model both fit
    ((), 9.0, "qwen2.5:7b"),                 # the turn's model still has to load: the judge would not fit beside it
])
def test_a_proven_model_not_loaded_judges_only_when_it_fits_beside_the_turns_model(monkeypatch, resident, free, expected):
    _machine(monkeypatch, installed={"qwen3:8b": 5.2, "qwen2.5:7b": 4.7}, resident=resident, free=free)
    assert memory_judge_record.judge_model_for("qwen2.5:7b") == expected


def test_without_a_proven_model_installed_the_turns_own_model_judges(monkeypatch):
    _machine(monkeypatch, installed={"qwen3.5:9b": 6.6}, free=20.0)
    assert memory_judge_record.judge_model_for("qwen3.5:9b") == "qwen3.5:9b"


def test_a_machine_with_no_gpu_reading_is_measured_by_its_free_ram(monkeypatch):
    _machine(monkeypatch, installed={"qwen3:8b": 5.2, "qwen3.5:9b": 6.6}, resident=("qwen3.5:9b",), free=None, ram=10.0)
    assert memory_judge_record.judge_model_for("qwen3.5:9b") == "qwen3:8b"
    _machine(monkeypatch, installed={"qwen3:8b": 5.2, "qwen3.5:9b": 6.6}, resident=("qwen3.5:9b",), free=None, ram=3.0)
    assert memory_judge_record.judge_model_for("qwen3.5:9b") == "qwen3.5:9b"


def test_an_unreadable_machine_keeps_the_turns_own_model(monkeypatch):
    def broken(**_k):
        raise OSError("ollama down")

    monkeypatch.setattr(local_ollama_inventory, "installed_ollama_model_inventory", broken)
    assert memory_judge_record.judge_model_for("qwen3.5:9b") == "qwen3.5:9b"
    assert memory_judge_record.judge_model_for("") == ""


def test_every_proven_model_names_the_evidence_it_passed_on():
    assert memory_judge_record.PROVEN_JUDGE_MODELS
    for tag, evidence in memory_judge_record.PROVEN_JUDGE_MODELS.items():
        assert tag == tag.strip().lower() and ":" in tag
        assert "probe" in evidence and "gate" in evidence


class _Manifest:
    model_name = "qwen3.5:9b"
    metadata = {"runtime_family": "ollama", "bundle_role": "general"}


def test_the_turn_judge_asks_the_proven_model_when_the_turn_is_answered_by_another(monkeypatch):
    """The hook every judgement goes through (`turn_judge._local_judge_for`): the arbiter reads `turn_model()` both to
    ask a loaded model at once and to hand an unloaded one to the turn's judge."""
    from core import intent_arbiter, request_trust
    from core.agent_runtime import turn_judge
    from core.clean_loop import brain

    _machine(monkeypatch, installed={"qwen3:8b": 5.2, "qwen3.5:9b": 6.6}, resident=("qwen3.5:9b",), free=10.0)
    monkeypatch.setattr(request_trust, "request_is_owner_local", lambda _c: True)
    monkeypatch.setattr(request_trust, "turn_speaks_for_owner", lambda _c, _s: True)
    monkeypatch.setattr(brain, "resolve_local_manifest", lambda _r, _c: _Manifest())
    asked: list[str] = []
    monkeypatch.setattr(intent_arbiter, "ask_local_model", lambda prompt, model, **_k: asked.append(model) or "yes")
    judge = turn_judge._local_judge_for(SimpleNamespace(memory_router=object()), {}, "s1")
    assert judge.turn_model() == ("qwen3:8b", "general")
    assert judge("is this a fact?") == "yes" and asked == ["qwen3:8b"]
