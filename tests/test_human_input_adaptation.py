from __future__ import annotations

import unittest
import uuid

from core.human_input_adapter import adapt_user_input, learn_user_shorthand
from core.input_normalizer import normalize_user_text
from core.task_router import classify
from storage.dialogue_memory import record_dialogue_turn
from storage.migrations import run_migrations


class HumanInputAdaptationTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_normalizer_handles_shorthand_and_typos(self) -> None:
        result = normalize_user_text("pls hlp me harden tg bot so no passwrods leak")
        self.assertIn("please", result.normalized_text)
        self.assertIn("help", result.normalized_text)
        self.assertIn("telegram", result.normalized_text)
        self.assertIn("passwords", result.normalized_text)
        self.assertIn("shorthand_heavy", result.quality_flags)
        self.assertIn("typo_heavy", result.quality_flags)

    def test_normalizer_does_not_rewrite_please_as_domain_lease(self) -> None:
        result = normalize_user_text("50 times 3 please")

        self.assertEqual(result.normalized_text, "50 times 3 please")
        self.assertNotIn("please", result.replacements)

        prefixed = normalize_user_text("please calculate (9 + 3) * 2")
        self.assertEqual(prefixed.normalized_text, "please calculate(9 + 3) * 2")

    def test_reference_resolution_uses_session_subject(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        first = adapt_user_input(
            "Thomas keeps the knowledge shard for telegram bot routing.",
            session_id=session_id,
        )
        self.assertIn("knowledge shard", first.topic_hints)

        second = adapt_user_input(
            "if that one dies other one can still have it right?",
            session_id=session_id,
        )
        self.assertTrue(second.reference_targets)
        self.assertIn("knowledge shard", " ".join(second.reference_targets))
        self.assertEqual(second.reconstructed_text, second.normalized_text)
        self.assertNotIn("Context subject:", second.reconstructed_text)
        self.assertGreater(second.understanding_confidence, 0.45)

    def test_deictic_explanation_followup_keeps_the_previous_answer_in_context(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        adapt_user_input(
            "Why can a five-minute walk help when someone feels stuck?",
            session_id=session_id,
        )
        record_dialogue_turn(
            session_id,
            raw_input="It creates a short mental reset and a change of perspective.",
            normalized_input="It creates a short mental reset and a change of perspective.",
            reconstructed_input="It creates a short mental reset and a change of perspective.",
            speaker_role="assistant",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
        )

        followup = adapt_user_input(
            "In one sentence, what part of that explanation matters most?",
            session_id=session_id,
        )

        self.assertTrue(followup.is_continuation)
        self.assertEqual(followup.reconstructed_text, followup.normalized_text)
        self.assertNotIn("Context subject:", followup.reconstructed_text)

    def test_explicit_prior_answer_reason_followup_is_a_continuation(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        adapt_user_input(
            "Why might someone keep a paper notebook even when they use a phone?",
            session_id=session_id,
        )
        record_dialogue_turn(
            session_id,
            raw_input="It can reduce screen distractions and preserve a personal connection to writing.",
            normalized_input="It can reduce screen distractions and preserve a personal connection to writing.",
            reconstructed_input="It can reduce screen distractions and preserve a personal connection to writing.",
            speaker_role="assistant",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
        )

        followup = adapt_user_input(
            "What was the most human reason you gave there?",
            session_id=session_id,
        )

        self.assertTrue(followup.is_continuation)
        self.assertEqual(followup.reconstructed_text, followup.normalized_text)

    def test_response_shaping_followup_is_a_continuation(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        adapt_user_input(
            "Why can small routines feel reassuring?",
            session_id=session_id,
        )
        record_dialogue_turn(
            session_id,
            raw_input="They offer predictable moments and reduce overwhelm.",
            normalized_input="They offer predictable moments and reduce overwhelm.",
            reconstructed_input="They offer predictable moments and reduce overwhelm.",
            speaker_role="assistant",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
        )

        followup = adapt_user_input(
            "Make that exactly one sentence.",
            session_id=session_id,
        )

        self.assertTrue(followup.is_continuation)
        self.assertEqual(followup.reconstructed_text, followup.normalized_text)

    def test_current_chat_preference_does_not_resolve_remember_it_to_old_subject(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        adapt_user_input("Why might someone enjoy rain on a window?", session_id=session_id)

        preference = adapt_user_input(
            "For this chat, I prefer sketches to polished diagrams. Please remember it.",
            session_id=session_id,
        )

        self.assertEqual(preference.topic_hints, ["preference"])
        self.assertEqual(preference.reference_targets, [])
        self.assertFalse(preference.is_continuation)
        self.assertNotIn("ambiguous_reference", preference.quality_flags)

    def test_changed_mind_preference_does_not_import_old_topic_or_format(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        adapt_user_input(
            "Use exactly two words to describe a quiet library.",
            session_id=session_id,
        )
        adapt_user_input("Why might someone enjoy rain on a window?", session_id=session_id)

        preference = adapt_user_input(
            "I changed my mind: I prefer polished diagrams after all. Please remember that.",
            session_id=session_id,
        )

        self.assertEqual(preference.topic_hints, ["preference"])
        self.assertEqual(preference.reference_targets, [])
        self.assertFalse(preference.is_continuation)
        self.assertNotIn("ambiguous_reference", preference.quality_flags)

    def test_multisentence_preference_update_does_not_import_old_topic_or_format(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        adapt_user_input(
            "Answer in one sentence: why do people enjoy quiet mornings?",
            session_id=session_id,
        )

        preference = adapt_user_input(
            "Actually, I prefer concise bullet lists for this chat. Please keep that preference.",
            session_id=session_id,
        )

        self.assertEqual(preference.topic_hints, ["preference"])
        self.assertEqual(preference.reference_targets, [])
        self.assertFalse(preference.is_continuation)
        self.assertNotIn("ambiguous_reference", preference.quality_flags)

    def test_session_lexicon_improves_classification(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        learn_user_shorthand("mng", "meet and greet", session_id=session_id)
        interpreted = adapt_user_input(
            "pls make mng server for swarm entry",
            session_id=session_id,
        )
        self.assertIn("meet and greet", interpreted.normalized_text)
        classification = classify(interpreted.reconstructed_text, context=interpreted.as_context())
        self.assertEqual(classification["task_class"], "system_design")

    def test_normalizer_preserves_structured_workspace_file_prompt(self) -> None:
        prompt = "Inside /tmp/vool_local_tooling_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH"
        result = normalize_user_text(prompt)

        self.assertEqual(result.normalized_text, prompt)
        self.assertEqual(result.replacements, {})

    def test_normalizer_preserves_multiline_code_prompt(self) -> None:
        prompt = (
            "Inside /tmp/vool_local_tooling_truth/alpha create adder.py with exactly this code:\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        result = normalize_user_text(prompt)

        self.assertEqual(result.normalized_text, prompt.strip())
        self.assertIn("adder.py", result.normalized_text)
        self.assertIn("->", result.normalized_text)

    def test_adapt_user_input_preserves_multiline_code_prompt(self) -> None:
        prompt = (
            "Inside /tmp/vool_local_tooling_truth/alpha create adder.py with exactly this code:\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        interpreted = adapt_user_input(prompt, session_id=f"session-{uuid.uuid4().hex}")

        self.assertEqual(interpreted.normalized_text, prompt.strip())
        self.assertEqual(interpreted.reconstructed_text, prompt.strip())

    def test_structured_workspace_prompts_do_not_infer_semantic_topics_from_paths(self) -> None:
        session_id = f"session-{uuid.uuid4().hex}"
        first = adapt_user_input(
            "Create a folder named alpha inside /tmp/openclaw_workspace_truth.",
            session_id=session_id,
        )
        second = adapt_user_input(
            "Inside /tmp/openclaw_workspace_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH",
            session_id=session_id,
        )

        self.assertEqual(first.topic_hints, [])
        self.assertEqual(first.reference_targets, [])
        self.assertNotIn("Context subject:", first.reconstructed_text)
        self.assertEqual(second.topic_hints, [])
        self.assertEqual(second.reference_targets, [])
        self.assertEqual(
            second.reconstructed_text,
            "Inside /tmp/openclaw_workspace_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH",
        )


if __name__ == "__main__":
    unittest.main()
