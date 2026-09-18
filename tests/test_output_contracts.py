from __future__ import annotations

import unittest

from core.model_output_contracts import validate_contract
from core.output_validator import validate_provider_output


class OutputContractsTests(unittest.TestCase):
    def test_structured_json_output_passes_validation(self) -> None:
        result = validate_contract("json_object", '{"status":"ok","reason":"local"}')
        self.assertTrue(result.ok)
        self.assertEqual(result.structured_output["status"], "ok")

    def test_action_plan_requires_summary_and_steps(self) -> None:
        result = validate_contract("action_plan", '{"summary":"Do it","steps":["one","two"]}')
        self.assertTrue(result.ok)
        self.assertIn("- one", result.normalized_text)

    def test_summary_block_requires_summary(self) -> None:
        result = validate_contract("summary_block", '{"summary":"Fresh status","bullets":["item"]}')
        self.assertTrue(result.ok)
        self.assertEqual(result.structured_output["summary"], "Fresh status")

    def test_malformed_structured_output_is_rejected(self) -> None:
        result = validate_provider_output(
            provider_id="local-qwen-http:qwen",
            output_mode="action_plan",
            raw_text='{"summary":"Missing steps"}',
        )
        self.assertFalse(result.ok)
        self.assertIn("missing_keys", str(result.error))
        self.assertGreater(result.trust_penalty, 0.0)


if __name__ == "__main__":
    unittest.main()
