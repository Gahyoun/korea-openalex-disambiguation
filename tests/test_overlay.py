from __future__ import annotations

import unittest

from koad.overlay import _stage2_consensus


class OverlayTests(unittest.TestCase):
    def test_one_high_confidence_provider_can_resolve_review(self):
        decision, confidence, rule = _stage2_consensus([
            {"decision": "true_paper", "confidence": "high"}
        ])
        self.assertEqual((decision, confidence), ("true_paper", "medium"))
        self.assertEqual(rule, "single_provider_high_confidence")

    def test_provider_tie_remains_review(self):
        decision, _, rule = _stage2_consensus([
            {"decision": "true_paper", "confidence": "high"},
            {"decision": "namesake_paper", "confidence": "high"},
        ])
        self.assertIsNone(decision)
        self.assertEqual(rule, "provider_disagreement")


if __name__ == "__main__":
    unittest.main()
