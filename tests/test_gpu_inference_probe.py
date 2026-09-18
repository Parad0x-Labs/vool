from __future__ import annotations

from core.gpu_capability_state import (
    apply_persisted_cpu_fallback,
    gpu_capability_path,
    load_gpu_capability,
    save_gpu_capability,
)
from core.gpu_inference_probe import (
    GpuInferenceVerdict,
    classify_gpu_error,
    gpu_capability_advisory,
    verify_gpu_inference,
)

# The exact stderr/error text observed live on the broken GTX 1080 box. These strings are the
# contract the classifier must honor, so they are asserted verbatim.
_LIVE_DRIVER_TOO_OLD = (
    "llama-server process has terminated: exit status 0xc0000409 "
    "CUDA error: the provided PTX was compiled with an unsupported toolchain"
)
_LIVE_VRAM_INSUFFICIENT = (
    "cudaMalloc failed: out of memory\n"
    "alloc_tensor_range: failed to allocate CUDA0 buffer of size ...\n"
    "failed to allocate buffer for kv cache"
)

# Words the repo forbids in user-facing copy (hype / drama). Advisories must never contain them.
_BANNED_HYPE_WORDS = (
    "bulletproof",
    "blazing",
    "supercharge",
    "unleash",
    "revolutionary",
    "game-changer",
    "seamless",
    "effortless",
)


# --- classify_gpu_error -------------------------------------------------------------------------


def test_classify_driver_too_old_on_live_ptx_toolchain_string() -> None:
    assert classify_gpu_error(_LIVE_DRIVER_TOO_OLD) == "driver_too_old"


def test_classify_vram_insufficient_on_live_cudamalloc_kv_cache_string() -> None:
    assert classify_gpu_error(_LIVE_VRAM_INSUFFICIENT) == "vram_insufficient"


def test_classify_gpu_unsupported_on_bare_cuda_error() -> None:
    assert classify_gpu_error("CUDA error") == "gpu_unsupported"


def test_classify_returns_empty_for_non_gpu_text() -> None:
    assert classify_gpu_error("hello") == ""
    assert classify_gpu_error("") == ""
    assert classify_gpu_error(None) == ""  # type: ignore[arg-type]


def test_classify_is_case_insensitive() -> None:
    assert classify_gpu_error("The Provided PTX was rejected") == "driver_too_old"
    assert classify_gpu_error("CUDAMALLOC FAILED: OUT OF MEMORY") == "vram_insufficient"


def test_classify_precedence_oom_beats_generic_cuda_error() -> None:
    # Both an OOM phrase and a bare "cuda error" appear; OOM is the more actionable outcome.
    text = "CUDA error while running: cudaMalloc failed: out of memory"
    assert classify_gpu_error(text) == "vram_insufficient"


def test_classify_precedence_driver_beats_generic_cuda_error() -> None:
    # PTX/toolchain phrase present alongside a generic "cuda error" -> driver guidance wins.
    text = "CUDA error: the provided PTX was compiled with an unsupported toolchain"
    assert classify_gpu_error(text) == "driver_too_old"


def test_classify_narrowed_vram_keywords_avoid_false_positives() -> None:
    # Benign log lines that merely mention a kv cache or a non-CUDA allocation are NOT VRAM OOM.
    assert classify_gpu_error("the kv cache was warmed and ready") == ""
    assert classify_gpu_error("template execution failed to allocate a response widget") == ""


def test_classify_bare_ptxas_warning_is_not_driver_too_old() -> None:
    # A stray "ptxas" warning line must not, on its own, be read as a driver-too-old crash.
    assert classify_gpu_error("ptxas warning: registers were spilled") != "driver_too_old"
    # ...and when it appears alongside a real OOM, the more-actionable OOM outcome wins.
    assert classify_gpu_error("ptxas warning; cudaMalloc failed: out of memory") == "vram_insufficient"


# --- verify_gpu_inference (injected http_post_fn per path) ---------------------------------------


def _ok_post(url, payload, timeout):
    return 200, {"message": {"content": "OK"}}, ""


def _driver_post(url, payload, timeout):
    return 500, None, _LIVE_DRIVER_TOO_OLD


def _oom_post(url, payload, timeout):
    return 500, None, _LIVE_VRAM_INSUFFICIENT


def _timeout_post(url, payload, timeout):
    raise TimeoutError("the read operation timed out")


def test_verify_ok_when_gpu_returns_live_token() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_ok_post)
    assert isinstance(verdict, GpuInferenceVerdict)
    assert verdict.outcome == "ok"
    assert verdict.recommend_cpu_fallback is False
    assert verdict.recommended_num_gpu == 999  # default forced num_gpu preserved on success
    assert verdict.suggested_action == "none"


def test_verify_driver_too_old_maps_to_cpu_fallback() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_driver_post)
    assert verdict.outcome == "driver_too_old"
    assert verdict.recommend_cpu_fallback is True
    assert verdict.recommended_num_gpu == 0
    assert verdict.suggested_action == "update_driver"
    assert verdict.raw_excerpt  # first ~400 chars of the error captured


def test_verify_vram_insufficient_maps_to_cpu_fallback() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_oom_post)
    assert verdict.outcome == "vram_insufficient"
    assert verdict.recommend_cpu_fallback is True
    assert verdict.recommended_num_gpu == 0
    assert verdict.suggested_action == "free_vram_or_smaller_model"


def test_verify_timeout_classifies_and_never_raises() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_timeout_post)
    # A bare timeout has no recognized GPU signature -> default gpu_unsupported, CPU fallback.
    assert verdict.outcome == "gpu_unsupported"
    assert verdict.recommend_cpu_fallback is True
    assert verdict.recommended_num_gpu == 0
    # The hang/timeout must be reflected in the reason so it is distinguishable from a clean 500.
    lowered = verdict.reason.lower()
    assert "timed out" in lowered or "before completing" in lowered


def test_verify_no_gpu_short_circuits_without_calling_backend() -> None:
    calls = {"n": 0}

    def _spy_post(url, payload, timeout):
        calls["n"] += 1
        return 200, {"message": {"content": "OK"}}, ""

    verdict = verify_gpu_inference(model="qwen2.5:7b", gpu_present=False, http_post_fn=_spy_post)
    assert verdict.outcome == "no_gpu"
    # No GPU means CPU is already the plan, so there is nothing to warn about / fall back from.
    assert verdict.recommend_cpu_fallback is False
    assert calls["n"] == 0  # backend was never contacted


def test_verify_skipped_when_model_missing() -> None:
    verdict = verify_gpu_inference(model="", http_post_fn=_ok_post)
    assert verdict.outcome == "skipped"
    assert verdict.recommend_cpu_fallback is False


def test_verify_200_with_empty_content_is_not_ok() -> None:
    def _empty_post(url, payload, timeout):
        return 200, {"message": {"content": ""}}, ""

    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_empty_post)
    # An empty 200 body has no GPU-failure signature and is not transient -> gpu_unsupported.
    assert verdict.outcome == "gpu_unsupported"
    assert verdict.recommend_cpu_fallback is True


def test_verify_unrecognized_error_body_defaults_to_gpu_unsupported() -> None:
    def _weird_post(url, payload, timeout):
        return 500, None, "some unexpected failure with no known signature"

    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_weird_post)
    assert verdict.outcome == "gpu_unsupported"
    assert verdict.recommend_cpu_fallback is True


def test_verify_404_model_missing_is_inconclusive_not_cpu_fallback() -> None:
    # A not-yet-pulled model (404) is transient and must NOT durably pin the host to CPU.
    def _missing_post(url, payload, timeout):
        return 404, {"error": "model 'qwen2.5:7b' not found"}, "model 'qwen2.5:7b' not found"

    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_missing_post)
    assert verdict.outcome == "inconclusive"
    assert verdict.recommend_cpu_fallback is False


def test_verify_connection_refused_is_inconclusive_not_cpu_fallback() -> None:
    # Ollama not reachable is transient; it must not classify the GPU as broken.
    def _refused_post(url, payload, timeout):
        return 0, None, "connection refused"

    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_refused_post)
    assert verdict.outcome == "inconclusive"
    assert verdict.recommend_cpu_fallback is False


def test_verify_payload_forces_num_gpu_and_small_generation() -> None:
    captured: dict[str, object] = {}

    def _capture_post(url, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return 200, {"message": {"content": "OK"}}, ""

    verify_gpu_inference(
        model="qwen2.5:7b", num_gpu=999, num_ctx=2048, http_post_fn=_capture_post
    )
    payload = captured["payload"]
    assert str(captured["url"]).endswith("/api/chat")
    assert payload["options"]["num_gpu"] == 999
    assert payload["options"]["num_ctx"] == 2048
    assert payload["options"]["num_predict"] == 8
    assert payload["stream"] is False
    assert payload["think"] is False


# --- gpu_capability_advisory (plain, factual, no hype) -------------------------------------------


def _assert_no_hype(message: str) -> None:
    lowered = message.lower()
    for banned in _BANNED_HYPE_WORDS:
        assert banned not in lowered, f"advisory leaked hype word: {banned!r}"


def test_advisory_driver_too_old_names_reason_and_fix_and_cpu() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_driver_post)
    message = gpu_capability_advisory(
        verdict, driver_version="566.36", cpu_fallback_model="qwen2.5:3b"
    )
    lowered = message.lower()
    assert "driver" in lowered
    assert "update" in lowered  # tells the user the fix
    assert "cpu" in lowered
    assert "qwen2.5:3b" in message
    assert "566.36" in message
    _assert_no_hype(message)


def test_advisory_vram_insufficient_mentions_memory_and_cpu() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_oom_post)
    message = gpu_capability_advisory(verdict, vram_free_gb=5.9, cpu_fallback_model="qwen2.5:3b")
    lowered = message.lower()
    assert "memory" in lowered
    assert "cpu" in lowered
    assert "5.9" in message
    _assert_no_hype(message)


def test_advisory_gpu_unsupported_mentions_cpu() -> None:
    verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_timeout_post)
    message = gpu_capability_advisory(verdict, cpu_fallback_model="qwen2.5:3b")
    assert "cpu" in message.lower()
    _assert_no_hype(message)


def test_advisory_ok_and_no_gpu_are_neutral_or_empty() -> None:
    ok_verdict = verify_gpu_inference(model="qwen2.5:7b", http_post_fn=_ok_post)
    no_gpu_verdict = verify_gpu_inference(model="qwen2.5:7b", gpu_present=False, http_post_fn=_ok_post)
    ok_message = gpu_capability_advisory(ok_verdict)
    no_gpu_message = gpu_capability_advisory(no_gpu_verdict)
    # ok -> a short, non-empty neutral status; no_gpu -> nothing to warn about (empty).
    assert no_gpu_message == ""
    assert ok_message  # ok is a neutral one-line status, not empty
    assert "gpu" in ok_message.lower()
    _assert_no_hype(ok_message)


# --- gpu_capability_state persistence (slice E) --------------------------------------------------


def test_save_and_load_round_trip(tmp_path) -> None:
    record = {
        "outcome": "driver_too_old",
        "model": "qwen2.5:7b",
        "driver_version": "566.36",
        "applied_cpu_fallback": True,
    }
    save_gpu_capability(tmp_path, record)
    loaded = load_gpu_capability(tmp_path)
    assert loaded is not None
    assert loaded["outcome"] == "driver_too_old"
    assert loaded["model"] == "qwen2.5:7b"
    assert loaded["driver_version"] == "566.36"
    assert loaded["applied_cpu_fallback"] is True


def test_load_missing_file_returns_none(tmp_path) -> None:
    assert load_gpu_capability(tmp_path) is None


def test_load_corrupt_file_returns_none(tmp_path) -> None:
    path = gpu_capability_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")
    assert load_gpu_capability(tmp_path) is None


def test_load_rejects_wrong_schema(tmp_path) -> None:
    path = gpu_capability_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"schema": "vool.other.v9", "outcome": "ok"}', encoding="utf-8")
    assert load_gpu_capability(tmp_path) is None


def test_save_is_deterministic_no_timestamps(tmp_path) -> None:
    record = {"outcome": "ok", "model": "qwen2.5:7b", "applied_cpu_fallback": False}
    save_gpu_capability(tmp_path, record)
    first = gpu_capability_path(tmp_path).read_text(encoding="utf-8")
    save_gpu_capability(tmp_path, record)
    second = gpu_capability_path(tmp_path).read_text(encoding="utf-8")
    assert first == second  # no wall-clock timestamp makes the write byte-stable


def test_apply_persisted_cpu_fallback_sets_num_gpu_zero(tmp_path) -> None:
    save_gpu_capability(
        tmp_path,
        {"outcome": "driver_too_old", "model": "qwen2.5:7b", "applied_cpu_fallback": True},
    )
    env: dict[str, str] = {}
    applied = apply_persisted_cpu_fallback(tmp_path, env=env)
    assert applied is True
    assert env["VOOL_OLLAMA_NUM_GPU"] == "0"


def test_apply_persisted_cpu_fallback_noop_when_not_applied(tmp_path) -> None:
    save_gpu_capability(
        tmp_path,
        {"outcome": "ok", "model": "qwen2.5:7b", "applied_cpu_fallback": False},
    )
    env: dict[str, str] = {}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is False
    assert "VOOL_OLLAMA_NUM_GPU" not in env


def test_apply_persisted_cpu_fallback_never_overrides_operator_setting(tmp_path) -> None:
    save_gpu_capability(
        tmp_path,
        {"outcome": "driver_too_old", "model": "qwen2.5:7b", "applied_cpu_fallback": True},
    )
    env = {"VOOL_OLLAMA_NUM_GPU": "5"}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is False
    assert env["VOOL_OLLAMA_NUM_GPU"] == "5"  # explicit operator value untouched


def test_apply_persisted_cpu_fallback_noop_when_no_record(tmp_path) -> None:
    env: dict[str, str] = {}
    assert apply_persisted_cpu_fallback(tmp_path, env=env) is False
    assert "VOOL_OLLAMA_NUM_GPU" not in env
