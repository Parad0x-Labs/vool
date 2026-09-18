from __future__ import annotations

import unittest

from tools.web.ddg_instant import best_text_blob


class DDGInstantTests(unittest.TestCase):
    def test_best_text_blob_prefers_abstract(self) -> None:
        self.assertEqual(best_text_blob({"AbstractText": "hello world"}), "hello world")


if __name__ == "__main__":
    unittest.main()
