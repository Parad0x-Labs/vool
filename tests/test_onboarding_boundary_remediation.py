"""Regression tests for the VOOL onboarding boundary-audit remediation.

Each test pins one finding from the 2026-07-10 boundary audit so the fix cannot silently regress.
"""

from __future__ import annotations

import uuid

from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty
from core.agent_runtime.fast_live_info_price import (
    looks_like_web0_or_x402_context,
    recover_price_lookup_query,
)
from core.agent_runtime.fast_paths_companion import looks_like_web0_builder_request
from core.agent_runtime.hive_topic_draft_intents import looks_like_hive_topic_create_request
from core.canonical_project_knowledge import retrieve_canonical_passages
from core.context_understanding import turn_declares_own_subject
from core.fact_extractor import _standing_direction_facts
from core.human_input_adapter import adapt_user_input
from core.reply_control_sanitizer import reveal_reply_control_prefix, strip_reply_control_tokens
from core.web0_project_grounding import web0_null_project_response


class _FakeAgent:
    def _looks_like_hive_topic_drafting_request(self, _text: str) -> bool:
        return False


# --- Finding 3: "make a five-step Web0 guide" wrongly routed to the builder editor URL ---


def test_web0_guide_request_is_not_a_builder_request() -> None:
    assert looks_like_web0_builder_request("make a five-step web0 guide") is False
    assert looks_like_web0_builder_request("explain web0 step by step") is False
    assert looks_like_web0_builder_request("give me a web0 how to overview") is False


def test_real_web0_build_request_still_matches() -> None:
    assert looks_like_web0_builder_request("build me a web0 landing page") is True
    assert looks_like_web0_builder_request("make a web0 site") is True
    assert looks_like_web0_builder_request("create a web0 web app") is True
    # Common build nouns beyond website/page must route here too.
    assert looks_like_web0_builder_request("build me a web0 app") is True
    assert looks_like_web0_builder_request("build a web0 dashboard") is True
    # A prose word appearing incidentally inside a real build request must NOT suppress it.
    assert looks_like_web0_builder_request("create a web0 page explaining pricing") is True
    assert looks_like_web0_builder_request("build a web0 landing page with a steps section") is True
    # Plural artifact nouns must still route to the builder.
    assert looks_like_web0_builder_request("build me web0 apps") is True
    assert looks_like_web0_builder_request("make web0 sites") is True


# --- Findings 26 / 30: x402 / .null prompts routed to a live crypto price ---


def test_x402_flow_prompt_does_not_trigger_price_lookup() -> None:
    ctx = {"conversation_history": [{"role": "assistant", "content": "Solana is $77 USD. Source: coingecko"}]}
    assert recover_price_lookup_query(
        "Use only these stages: request, quote, verify, receipt.", source_context=ctx
    ) == ""


def test_dot_null_payment_rails_prompt_does_not_trigger_price_lookup() -> None:
    ctx = {"conversation_history": [{"role": "assistant", "content": "Solana is $77 USD. Source: coingecko"}]}
    # "separate" must no longer match the "rate" price marker, and ".null"/"payment rails" guard it.
    assert recover_price_lookup_query(
        "Separate local app, .null identity, and payment rails.", source_context=ctx
    ) == ""


def test_web0_x402_context_guard() -> None:
    assert looks_like_web0_or_x402_context("Use only these stages: request, quote, verify, receipt.") is True
    assert looks_like_web0_or_x402_context("Separate local app, .null identity, and payment rails.") is True
    assert looks_like_web0_or_x402_context("what is the price of solana") is False


def test_genuine_price_questions_still_resolve() -> None:
    assert recover_price_lookup_query("how much is bitcoin", source_context=None) == "bitcoin price now"
    # A plain token price question (no Web0/x402 framing) still works.
    assert recover_price_lookup_query("what is the price of solana", source_context=None) == "solana price now"
    # A finance verb ("settle") must not be treated as an x402 signal for a real asset price query.
    assert recover_price_lookup_query("what price did bitcoin settle at", source_context=None) == "bitcoin price now"
    # A price follow-up recovers the subject from history.
    ctx = {"conversation_history": [{"role": "user", "content": "bitcoin price"}, {"role": "assistant", "content": "$100k"}]}
    assert recover_price_lookup_query("what's the current price?", source_context=ctx) == "bitcoin price now"


# --- Finding 31: "summarize without adding new topics" became a Hive-task create ---


def test_summary_without_new_topics_is_not_a_hive_create() -> None:
    agent = _FakeAgent()
    assert looks_like_hive_topic_create_request(agent, "summarize without adding new topics.") is False
    assert looks_like_hive_topic_create_request(agent, "did you add any new topic?") is False


def test_real_hive_create_still_matches() -> None:
    agent = _FakeAgent()
    # REVISED to the owner's product decision of 2026-09-17: a Hive create is intercepted only
    # when the request names the Hive product as the action's target, or uses an explicit
    # publish phrase. Unqualified task/topic wording ("create a new task: research X",
    # "start a topic to compare vector databases") is the user's own to-do or discussion
    # wording and reaches the selected model, which can ask what they mean.
    assert looks_like_hive_topic_create_request(agent, "create a hive task: research vector db options") is True
    assert looks_like_hive_topic_create_request(agent, "start a hive topic to compare vector databases") is True
    # A stacked determiner ("a new") must still match a real, product-named create.
    assert looks_like_hive_topic_create_request(agent, "start a new hive mind topic to compare vector databases") is True
    assert looks_like_hive_topic_create_request(agent, "create a new task in the hive: research X") is True
    assert looks_like_hive_topic_create_request(agent, "add this to the hive") is True
    # Unqualified creates do NOT intercept under the new contract.
    assert looks_like_hive_topic_create_request(agent, "create a new task: research X") is False
    assert looks_like_hive_topic_create_request(agent, "start a topic to compare vector databases") is False


# --- Findings 12 / 13: label save corrupted into a stale "Context subject: lease" record ---


def test_label_declaration_detected() -> None:
    assert turn_declares_own_subject("Keep this label: onboarding-context.") is True
    assert turn_declares_own_subject("Remember term: local-proof-path.") is True
    assert turn_declares_own_subject("Remember codeword: safe-chat-lane.") is True
    # Naming imperatives and value-only declarations (no literal meta-noun in the value) also count.
    assert turn_declares_own_subject("Call this safe-chat-lane.") is True
    assert turn_declares_own_subject("Name this thread safe-lane.") is True
    assert turn_declares_own_subject("Remember this: safe-chat-lane.") is True
    assert turn_declares_own_subject("Call it onboarding-context.") is True
    # A pronoun follow-up is NOT a self-subject declaration (reference resolution still applies),
    # even when it incidentally contains a verb + a meta-noun.
    assert turn_declares_own_subject("if that one dies other one can still have it right?") is False
    assert turn_declares_own_subject("use it for the same context") is False
    assert turn_declares_own_subject("store it under the same tag") is False
    # A definite article after a naming verb is an ordinary command, not a declaration.
    assert turn_declares_own_subject("call the client first") is False
    assert turn_declares_own_subject("name the winners") is False


def test_label_declaration_does_not_inherit_stale_subject() -> None:
    session_id = f"session-{uuid.uuid4().hex}"
    adapt_user_input("Thomas keeps the knowledge shard for telegram bot routing.", session_id=session_id)
    result = adapt_user_input("Keep this label: onboarding-context.", session_id=session_id)
    assert "Context subject:" not in result.reconstructed_text
    assert "lease" not in result.reconstructed_text.lower()


# --- Findings 17 / 25 / 27: leaked NO_REPLY control token ---


def test_strip_reply_control_tokens() -> None:
    assert strip_reply_control_tokens("NO_REPLY\n\nThis is a good question! Here is the answer.") == (
        "This is a good question! Here is the answer."
    )
    assert strip_reply_control_tokens("NO_REPLY") == ""
    assert strip_reply_control_tokens("NO_REPLY\nThis message indicates a lease.") == "This message indicates a lease."
    # A lowercase/mixed-case leaked token is also stripped (the guard is case-insensitive).
    assert strip_reply_control_tokens("no_reply\n\nreal answer") == "real answer"
    # A real word that merely starts with the token must be untouched.
    assert strip_reply_control_tokens("NO_REPLYING is not a token") == "NO_REPLYING is not a token"
    # An answer ABOUT the token (inline, not a standalone line) must NOT be mangled.
    assert strip_reply_control_tokens("NO_REPLY is OpenClaw's silence token.") == "NO_REPLY is OpenClaw's silence token."
    assert strip_reply_control_tokens("A normal answer.") == "A normal answer."


def test_reveal_reply_control_prefix_defers_bare_token() -> None:
    # A prefix ahead of real content is revealed...
    assert reveal_reply_control_prefix("NO_REPLY\n\nreal answer") == "real answer"
    # ...but a bare token is left for OpenClaw to suppress (vool must not fabricate content).
    assert reveal_reply_control_prefix("NO_REPLY") == "NO_REPLY"


def test_enforce_final_action_honesty_reveals_no_reply_prefix() -> None:
    out = enforce_final_action_honesty(
        {"response": "NO_REPLY\n\nThis phrase is important; I will remember it."},
        user_input="Remember this phrase: receipt-before-access.",
    )
    assert out["response"] == "This phrase is important; I will remember it."
    assert out.get("reply_control_sanitized") is True


# --- Finding 29: "do not mention private keys" contaminated later fresh sessions ---


def test_do_not_mention_is_not_a_standing_constraint() -> None:
    facts = _standing_direction_facts("Do not mention private keys. Explain wallet safety.")
    captured = {(f.block, f.content) for f in facts}
    assert not any("private key" in content.lower() for _block, content in captured)


def test_avoid_mentioning_is_not_a_standing_constraint() -> None:
    facts = _standing_direction_facts("avoid mentioning private keys")
    assert not any("private key" in f.content.lower() for f in facts)


def test_durable_prohibitions_are_still_captured() -> None:
    facts = _standing_direction_facts("never use emojis in replies")
    assert ("constraints", "Never use emojis in replies") in {(f.block, f.content) for f in facts}


# --- Finding 6: model told a beginner to enter their private key / seed phrase ---


def test_wallet_secret_solicitation_is_blocked() -> None:
    unsafe = (
        "To complete the x402 payment, enter your seed phrase and private key into the dialog. "
        "Successfully signed and sent the transaction."
    )
    out = enforce_final_action_honesty({"response": unsafe}, user_input="Give a concrete x402 example.")
    assert "never asks for" in out["response"].lower()
    assert out.get("action_honesty_validator", {}).get("reason") == "wallet_secret_safety"


def test_wallet_safety_warning_is_not_flagged() -> None:
    safe = "Wallet safety: VOOL never asks for your private key or seed phrase, and it can't move money on its own."
    out = enforce_final_action_honesty({"response": safe}, user_input="Is x402 safe?")
    assert out["response"] == safe


# --- Findings 19 / 21 / 32 / 34: grounding VOOL's own controls and runtime truth ---


def test_stopx402_question_is_grounded_not_generic_x402() -> None:
    out = web0_null_project_response("What does /stopx402 do? Do not execute it.")
    assert out is not None
    assert "freezes" in out["response"].lower()
    assert out["intent"] == "vool_stopx402_control"


def test_self_claim_settlement_is_corrected() -> None:
    out = web0_null_project_response("Can live settlement be self-claimed?")
    assert out is not None
    assert "cannot be self-claimed" in out["response"].lower()


def test_cloud_burst_default_policy_is_grounded() -> None:
    out = web0_null_project_response("What is your cloud burst policy by default?")
    assert out is not None
    assert "off" in out["response"].lower()
    assert out["intent"] == "vool_cloud_burst_policy"


def test_x402_recall_phrasing_hits_grounding() -> None:
    query = "What did we say x402 does?"
    assert web0_null_project_response(query) is None
    assert retrieve_canonical_passages(query)


def test_x402_definition_includes_the_flow_terms() -> None:
    passages = retrieve_canonical_passages("what is x402?")
    assert passages
    lowered = " ".join(passage.content.lower() for passage in passages)
    assert "x402" in lowered


# --- Beginner-testing audit net-new grounding/honesty findings (KAS #98) ---


def test_x402_autocharge_is_grounded_as_fail_closed() -> None:
    out = web0_null_project_response("Does x402 automatically charge me or is it a subscription?")
    assert out is not None
    assert out["intent"] == "vool_x402_autocharge"
    low = out["response"].lower()
    assert "does not auto-charge" in low or "not auto-charge" in low
    assert "subscription" in low


def test_memory_capability_answer_is_honest() -> None:
    out = web0_null_project_response("Does VOOL remember everything securely?")
    assert out is not None
    assert out["intent"] == "vool_memory_honesty"
    low = out["response"].lower()
    assert "does not" in low and "remember everything" in low
    assert "encrypted by default" in low


def test_agent_loop_is_grounded_not_generic() -> None:
    out = web0_null_project_response("What is the agent loop?")
    assert out is not None
    assert out["intent"] == "vool_agent_loop"
    low = out["response"].lower()
    assert "tool" in low and "iterate" in low


def test_proof_of_execution_is_grounded_not_blockchain() -> None:
    out = web0_null_project_response("What is proof of execution in VOOL?")
    assert out is not None
    assert out["intent"] == "vool_proof_of_execution"
    low = out["response"].lower()
    assert "receipt" in low and "not a generic blockchain" in low


def test_wallet_nature_is_grounded_not_generic_crypto() -> None:
    out = web0_null_project_response("What kind of wallet does VOOL use?")
    assert out is not None
    assert out["intent"] == "vool_wallet_nature"
    low = out["response"].lower()
    assert "solana" in low and "not a generic" in low


def test_new_grounding_responders_do_not_over_trigger() -> None:
    # A plain x402 definition is answered from canonical documents, not by an
    # unrelated deterministic auto-charge responder.
    assert web0_null_project_response("what is x402?") is None
    assert retrieve_canonical_passages("what is x402?")
    # A balance lookup is not a wallet-nature definition.
    assert web0_null_project_response("what is my wallet balance?") is None


# --- current-main 300-turn audit: reproduced findings ---


def test_null_definition_leads_positive_not_corrective() -> None:
    # Definitions are grounded from canonical repository passages and remain model-generated;
    # the deterministic responder is reserved for transactional and safety-control answers.
    neutral_query = "What is a .null name?"
    assert web0_null_project_response(neutral_query) is None
    assert retrieve_canonical_passages(neutral_query)

    contrast_query = "How are .null names different from DNS names?"
    assert web0_null_project_response(contrast_query) is None
    assert retrieve_canonical_passages(contrast_query)


def test_bare_leaked_no_reply_is_replaced_not_shown() -> None:
    from core.reply_control_sanitizer import is_bare_reply_control_token

    # The whole reply being just the token (bare, or trailed by punctuation/emoji) is a leak.
    assert is_bare_reply_control_token("NO_REPLY")
    assert is_bare_reply_control_token("NO_REPLY " + chr(0x1F937))
    assert is_bare_reply_control_token("no_reply.")
    # A sentence ABOUT the token is not a bare leak.
    assert not is_bare_reply_control_token("NO_REPLY is OpenClaw's silence token")
    # A leaked bare token reaching direct chat is replaced with an honest fallback, never shown raw.
    out = enforce_final_action_honesty({"response": "NO_REPLY"}, user_input="Can x402 spend without my consent?")
    assert out["response"].startswith("I don't have a clear answer")
    assert out.get("reply_control_sanitized") is True
    assert "NO_REPLY" not in out["response"]


def test_x402_approval_answer_is_user_consent_not_corporate() -> None:
    for prompt in ("Who approves an x402 payment?", "Can x402 spend money without my consent?"):
        out = web0_null_project_response(prompt)
        assert out is not None and out["intent"] == "vool_x402_consent"
        low = out["response"].lower()
        assert "you approve" in low
        assert "no organizational approver" in low or "cannot spend on its own" in low


def test_data_locality_answer_is_honest_no_provider_server() -> None:
    out = web0_null_project_response("What might go outside the local machine?")
    assert out is not None and out["intent"] == "vool_data_locality"
    low = out["response"].lower()
    assert "nothing leaves your machine" in low
    assert "does not send" in low and "provider server" in low
    # Must not over-trigger on an unrelated "leaves" question.
    assert web0_null_project_response("what leaves a good impression?") is None


# --- Review hardening: wallet-guard bypass/false-positive coverage (F6) ---


def _blocked(response: str) -> bool:
    out = enforce_final_action_honesty({"response": response}, user_input="Give a concrete x402 example.")
    return out.get("action_honesty_validator", {}).get("reason") == "wallet_secret_safety"


def test_wallet_guard_covers_solicitation_bypasses() -> None:
    # Incidental negation elsewhere in the sentence must NOT exempt a real solicitation.
    assert _blocked("To pay, enter your seed phrase into the x402 dialog, don't worry, it's safe.")
    # Verbs beyond enter/paste, verb inflections, and either ordering.
    assert _blocked("Copy your seed phrase and paste it into the dialog to sign.")
    assert _blocked("Reveal your private key to sign the transaction.")
    assert _blocked("Export your seed phrase and send it to me.")
    assert _blocked("Since phishing is common, confirm ownership by entering your private key now.")
    # A threat word in the sentence must NOT exempt a first-person solicitation (the phishing pattern).
    assert _blocked("To avoid getting scammed, paste your seed phrase into the recovery box.")
    assert _blocked("I never store your data, so go ahead and paste your seed phrase here.")


def test_wallet_guard_covers_false_payment_claims() -> None:
    assert _blocked("Your transaction has been broadcast to Solana and confirmed - payment complete.")
    assert _blocked("The USDC payment has been settled.")
    # A trailing "no need ..." clause must not exempt the fabricated settlement claim.
    assert _blocked("I have signed and sent the transaction; no need to do anything else.")
    # Novel completion phrasings still count as a false money-moved claim.
    assert _blocked("The transfer went through.")
    assert _blocked("The funds have left your wallet.")


def test_wallet_guard_allows_warnings_and_denials() -> None:
    # A directly-negated warning is not a solicitation.
    assert not _blocked("Never enter your seed phrase anywhere, and do not paste your private key.")
    # Descriptive / conditional third-party warnings are not solicitations.
    assert not _blocked("Anyone who asks you to paste your seed phrase is trying to scam you.")
    assert not _blocked("If a site tells you to enter your seed phrase, close it.")
    assert not _blocked("Should a website ever ask you to type your seed phrase, refuse.")
    # The guard's OWN thesis (VOOL never asks for your key) must never be blocked.
    assert not _blocked("VOOL will never ask you to reveal your private key.")
    assert not _blocked("Remember: VOOL never asks you to enter your recovery phrase into chat.")
    # Self-custody / educational / honest-custody guidance must NOT be discarded.
    assert not _blocked("A seed phrase is a list of 12 or 24 words that you write down to back up your wallet.")
    assert not _blocked("Back up your seed phrase and keep it somewhere safe offline.")
    assert not _blocked("Export your private key to a backup file and store it offline.")
    assert not _blocked("VOOL keeps your seed phrase private and only you have your recovery phrase.")
    assert not _blocked("With a hardware wallet you confirm each transaction on the device, and your private key never leaves it.")
    # Educational x402 protocol description (hypothetical) is not a false payment claim.
    assert not _blocked("In x402, once the transaction is settled, you get a receipt.")
    # Honest denials and non-payment "signed ... and sent" are not false claims.
    assert not _blocked("No funds were sent and no transaction was broadcast.")
    assert not _blocked("I signed off on the report and sent it to the whole team this morning.")


# --- Review hardening: recall phrasings must not return a canned ecosystem definition (F16) ---


def test_conversational_recall_does_not_return_canned_definition() -> None:
    assert web0_null_project_response("recap what we decided about web0") is None
    assert web0_null_project_response("remind me what we said about x402") is None
    assert web0_null_project_response("Vool, what did you just do?") is None
    # A recall addressed to "vool" with no specific stack term must NOT return the VOOL self-def.
    assert web0_null_project_response("vool, what did we say about my lease?") is None


# --- Review hardening: the self-knowledge doc endpoints must survive the injection budget (F15) ---


def test_self_knowledge_doc_fits_budget_and_keeps_endpoints() -> None:
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    doc = (repo_root / "docs" / "VOOL_SELF_KNOWLEDGE.md").read_text(encoding="utf-8")
    assert "/api/runtime/capabilities" in doc
    assert "/healthz" in doc
    # Must fit the bootstrap_context injection budget (max_chars=2800) so Endpoints is not truncated.
    assert len(doc) <= 2800
    assert doc.index("/api/runtime/capabilities") < 2800
    assert doc.index("/healthz") < 2800


# --- Review hardening: LLM-extracted content-avoidance must not become a durable constraint (F29) ---


def test_llm_extracted_content_avoidance_is_dropped(tmp_path) -> None:
    from core.fact_extractor import FactExtractor
    from core.vool_memory import VoolMemory

    memory = VoolMemory(runtime_home=tmp_path)
    raw = '{"facts": [{"action": "ADD", "block": "constraints", "content": "Do not mention private keys"}]}'
    FactExtractor(memory=memory, model_client=lambda _c: raw).run_sync(
        [{"role": "user", "content": "Do not mention private keys. Explain wallet safety."}]
    )
    assert "private key" not in str(memory.block_read("constraints") or "").lower()
    memory.close()
