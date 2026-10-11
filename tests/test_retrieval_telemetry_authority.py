"""Write diagnostics cannot inherit a later retrieval namespace."""
from core import context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory import revocation
from core.runtime_paths import configure_runtime_home


def test_prior_write_diagnostics_do_not_acquire_the_next_chats_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    configure_runtime_home(tmp_path)
    try:
        ensure_chat_namespace("current-chat")
        op = revocation.begin("earlier-chat", "CLOVER-9321", str(tmp_path))
        revocation.complete(op, str(tmp_path))
        cr.reset_retrieval_telemetry()
        cr.update_retrieval_telemetry(memory_store_reason="CLOVER-9321", memory_store_untrusted="CLOVER-9321")
        cr.inject_retrieved("current-chat", "Hello", [{"role":"user", "content":"Hello"}], env={"VOOL_CONTEXT_CAPSULE_V2":"0"}, source_context={"chat_id":"current-chat", "runtime_home":str(tmp_path)})
        telemetry=cr.get_last_retrieval_telemetry()
        assert "CLOVER-9321" not in str(telemetry)
        assert "memory_store_untrusted" not in telemetry
    finally:
        cr.reset_retrieval_telemetry()
        configure_runtime_home(None)


def test_current_write_status_survives_retrieval_without_source_text(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    configure_runtime_home(tmp_path)
    try:
        ensure_chat_namespace("current-chat")
        cr.reset_retrieval_telemetry()
        cr.update_retrieval_telemetry(memory_store_status="stored", memory_store_reason="source_evidence_retained", memory_store_count=2)
        cr.inject_retrieved("current-chat", "Hello", [{"role":"user", "content":"Hello"}], env={"VOOL_CONTEXT_CAPSULE_V2":"0"}, source_context={"chat_id":"current-chat", "runtime_home":str(tmp_path)})
        telemetry=cr.get_last_retrieval_telemetry()
        assert telemetry["memory_store_status"] == "stored"
        assert telemetry["memory_store_reason"] == "source_evidence_retained"
        assert telemetry["memory_store_count"] == 2
    finally:
        cr.reset_retrieval_telemetry()
        configure_runtime_home(None)
