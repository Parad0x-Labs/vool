from __future__ import annotations

import unittest

from core.parent_orchestrator import _matching_parent


class ParentReferenceMatchingTests(unittest.TestCase):
    def test_exact_parent_reference_match(self) -> None:
        parent_task_id = "71db39c1-61f3-4ac5-9151-6ffbc85f3197"
        self.assertTrue(_matching_parent(parent_task_id, parent_task_id))

    def test_long_prefix_collision_is_not_match(self) -> None:
        a = "0123456789abcdefAAAA-task"
        b = "0123456789abcdefBBBB-task"
        self.assertFalse(_matching_parent(a, b))

    def test_legacy_short_reference_still_supported(self) -> None:
        self.assertTrue(_matching_parent("parent-42", "parent-42-child-task"))


if __name__ == "__main__":
    unittest.main()
