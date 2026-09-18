from __future__ import annotations

from core.agent_runtime import fast_live_info as agent_fast_live_info
from core.agent_runtime.fast_paths_builder import (
    extract_requested_builder_root,
    looks_like_builder_request,
    looks_like_exact_workspace_readback_request,
    looks_like_explicit_workspace_file_request,
    looks_like_generic_workspace_bootstrap_request,
)
from core.agent_runtime.fast_paths_companion import (
    dense_memory_next_step,
    looks_like_companion_continuation_request,
    looks_like_personalized_plan_request,
    looks_like_web0_builder_request,
    maybe_handle_companion_memory_fast_path,
    maybe_handle_web0_builder_fast_path,
    render_companion_continuation_response,
    render_personalized_plan_response,
)
from core.agent_runtime.fast_paths_machine import (
    looks_like_safe_machine_write_request,
    looks_like_supported_machine_directory_create_request,
    looks_like_supported_machine_read_request,
    maybe_handle_direct_machine_download_request,
    maybe_handle_direct_machine_read_request,
    maybe_handle_direct_machine_write_request,
    maybe_handle_safe_machine_write_guard,
    safe_machine_write_targets_workspace,
)
from core.agent_runtime.fast_paths_utility import (
    action_history_honesty_fast_path,
    contextual_time_followup_timezone,
    credit_status_fast_path,
    date_time_fast_path,
    direct_math_fast_path,
    evaluative_conversation_fast_path,
    explicit_heavy_model_block_response,
    extract_utility_timezone,
    heartbeat_poll_fast_path,
    looks_like_evaluative_turn,
    looks_like_malformed_time_followup,
    maybe_handle_direct_workspace_runtime_request,
    maybe_handle_folder_overview_request,
    maybe_handle_workspace_identity_request,
    recent_utility_context,
    smalltalk_fast_path,
    startup_sequence_fast_path,
    ui_command_fast_path,
    utility_now_for_timezone,
)
from core.agent_runtime.same_chat_recall import (
    same_chat_math_recall_fast_path,
    same_chat_transcript_recall_fast_path,
)

maybe_handle_live_info_fast_path = agent_fast_live_info.maybe_handle_live_info_fast_path
live_info_search_notes = agent_fast_live_info.live_info_search_notes
try_live_quote_note = agent_fast_live_info.try_live_quote_note
try_live_quote_notes = agent_fast_live_info.try_live_quote_notes
live_info_mode = agent_fast_live_info.live_info_mode
requires_ultra_fresh_insufficient_evidence = agent_fast_live_info.requires_ultra_fresh_insufficient_evidence
ultra_fresh_insufficient_evidence_response = agent_fast_live_info.ultra_fresh_insufficient_evidence_response
normalize_live_info_query = agent_fast_live_info.normalize_live_info_query
render_live_info_response = agent_fast_live_info.render_live_info_response
first_live_quote = agent_fast_live_info.first_live_quote
render_weather_response = agent_fast_live_info.render_weather_response
render_news_response = agent_fast_live_info.render_news_response
unresolved_price_lookup_response = agent_fast_live_info.unresolved_price_lookup_response
looks_like_grounded_price_lookup = agent_fast_live_info.looks_like_grounded_price_lookup
notes_include_grounded_price_signal = agent_fast_live_info.notes_include_grounded_price_signal
extract_price_lookup_subject = agent_fast_live_info.extract_price_lookup_subject
recover_price_lookup_query = agent_fast_live_info.recover_price_lookup_query
live_info_failure_text = agent_fast_live_info.live_info_failure_text


__all__ = [
    "action_history_honesty_fast_path",
    "contextual_time_followup_timezone",
    "credit_status_fast_path",
    "date_time_fast_path",
    "dense_memory_next_step",
    "direct_math_fast_path",
    "evaluative_conversation_fast_path",
    "explicit_heavy_model_block_response",
    "extract_price_lookup_subject",
    "extract_requested_builder_root",
    "extract_utility_timezone",
    "first_live_quote",
    "heartbeat_poll_fast_path",
    "live_info_failure_text",
    "live_info_mode",
    "live_info_search_notes",
    "looks_like_builder_request",
    "looks_like_companion_continuation_request",
    "looks_like_evaluative_turn",
    "looks_like_exact_workspace_readback_request",
    "looks_like_explicit_workspace_file_request",
    "looks_like_generic_workspace_bootstrap_request",
    "looks_like_grounded_price_lookup",
    "looks_like_malformed_time_followup",
    "looks_like_personalized_plan_request",
    "looks_like_safe_machine_write_request",
    "looks_like_supported_machine_directory_create_request",
    "looks_like_supported_machine_read_request",
    "looks_like_web0_builder_request",
    "maybe_handle_companion_memory_fast_path",
    "maybe_handle_direct_machine_download_request",
    "maybe_handle_direct_machine_read_request",
    "maybe_handle_direct_machine_write_request",
    "maybe_handle_direct_workspace_runtime_request",
    "maybe_handle_folder_overview_request",
    "maybe_handle_live_info_fast_path",
    "maybe_handle_safe_machine_write_guard",
    "maybe_handle_web0_builder_fast_path",
    "maybe_handle_workspace_identity_request",
    "normalize_live_info_query",
    "notes_include_grounded_price_signal",
    "recent_utility_context",
    "recover_price_lookup_query",
    "render_companion_continuation_response",
    "render_live_info_response",
    "render_news_response",
    "render_personalized_plan_response",
    "render_weather_response",
    "requires_ultra_fresh_insufficient_evidence",
    "safe_machine_write_targets_workspace",
    "same_chat_math_recall_fast_path",
    "same_chat_transcript_recall_fast_path",
    "smalltalk_fast_path",
    "startup_sequence_fast_path",
    "try_live_quote_note",
    "try_live_quote_notes",
    "ui_command_fast_path",
    "ultra_fresh_insufficient_evidence_response",
    "unresolved_price_lookup_response",
    "utility_now_for_timezone",
]
